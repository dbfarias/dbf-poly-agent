"""Weather trading strategy: exploit NOAA forecast accuracy vs Polymarket odds.

Inspired by gopfan2 (+$2M) and AlterEgo's open-source weatherbot.
Uses slug-based direct market lookup + temperature bucket matching
for precise entry signals. Zero LLM cost.

Key innovations over naive approach:
- Direct slug lookup: highest-temperature-in-{city}-on-{month}-{day}-{year}
- Bucket matching: parse "between 44-45°F", "48°F or higher", "40°F or below"
- 4-day lookahead (today + 3 days)
- Min hours to resolution filter (skip markets resolving < 2h)
- Airport-station coordinates for forecast accuracy (via weather_fetcher)
"""

import json
import re
from datetime import datetime, timedelta, timezone

import structlog

from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()

# Month names for slug construction
_MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]

# City slug aliases — Polymarket uses these in URL slugs
_CITY_SLUGS: dict[str, str] = {
    "new york": "nyc",
    "nyc": "nyc",
    "chicago": "chicago",
    "miami": "miami",
    "dallas": "dallas",
    "seattle": "seattle",
    "atlanta": "atlanta",
}

# Broad weather detection for fallback scan path
_WEATHER_DETECT = re.compile(
    r"\b(?:temperature|temp|°[FC]|degrees?\s*[FC]|weather|forecast|"
    r"high(?:est)?\s+of|low(?:est)?\s+of|heat\s*wave|cold\s*snap|"
    r"highest\s+temp|lowest\s+temp|precipitation|rainfall|snowfall)\b",
    re.IGNORECASE,
)


def parse_temp_range(question: str) -> tuple[float, float] | None:
    """Extract temperature range from a market question (bucket matching).

    Returns (low, high) tuple in °F, or None if unparseable.
    Examples:
        "40°F or below"      → (-999, 40)
        "48°F or higher"     → (48, 999)
        "between 44-45°F"    → (44, 45)
        "between 44-45 °F"   → (44, 45)
    """
    if not question:
        return None

    q_lower = question.lower()

    # "X°F or below" / "X°F or lower"
    if "or below" in q_lower or "or lower" in q_lower:
        m = re.search(r"(\d+)\s*°?\s*F\s+or\s+(?:below|lower)", question, re.IGNORECASE)
        if m:
            return (-999.0, float(m.group(1)))

    # "X°F or higher" / "X°F or above"
    if "or higher" in q_lower or "or above" in q_lower:
        m = re.search(r"(\d+)\s*°?\s*F\s+or\s+(?:higher|above)", question, re.IGNORECASE)
        if m:
            return (float(m.group(1)), 999.0)

    # "between X-Y°F" or "between X - Y °F"
    m = re.search(r"between\s+(\d+)\s*[-–]\s*(\d+)\s*°?\s*F", question, re.IGNORECASE)
    if m:
        return (float(m.group(1)), float(m.group(2)))

    return None


def _hours_until_resolution(event: dict) -> float:
    """Calculate hours until event resolution."""
    try:
        end_date = event.get("endDate") or event.get("end_date_iso")
        if not end_date:
            return 999.0
        end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        delta = (end_dt - datetime.now(timezone.utc)).total_seconds() / 3600
        return max(0.0, delta)
    except Exception:
        return 999.0


def _build_weather_slug(city_slug: str, date: datetime) -> str:
    """Build Polymarket weather event slug.

    Format: highest-temperature-in-{city}-on-{month}-{day}-{year}
    """
    month = _MONTHS[date.month - 1]
    return f"highest-temperature-in-{city_slug}-on-{month}-{date.day}-{date.year}"


class WeatherTradingStrategy(BaseStrategy):
    """Trade weather markets using NOAA forecast data + slug-based lookup."""

    name = "weather_trading"
    MIN_HOLD_SECONDS = 1800  # 30 min

    # Entry thresholds — buy YES tokens priced below this
    ENTRY_THRESHOLD = 0.50
    # Exit threshold — sell when YES price rises above this
    EXIT_THRESHOLD = 0.65
    MIN_EDGE = 0.05
    CONFIDENCE_THRESHOLD = 0.40
    MIN_HOURS_TO_RESOLUTION = 2.0
    LOOKAHEAD_DAYS = 4  # today + 3 days
    MAX_SIGNALS_PER_SCAN = 5
    EXIT_STOP_LOSS_PCT = 0.10
    EXIT_TAKE_PROFIT_PCT = 0.05
    EXIT_MIN_HOLD_HOURS = 2.0
    EXIT_MAX_AGE_HOURS = 48.0

    _MUTABLE_PARAMS = {
        "ENTRY_THRESHOLD": {"type": float, "min": 0.01, "max": 0.50},
        "EXIT_THRESHOLD": {"type": float, "min": 0.20, "max": 0.90},
        "MIN_EDGE": {"type": float, "min": 0.0, "max": 0.5},
        "CONFIDENCE_THRESHOLD": {"type": float, "min": 0.3, "max": 1.0},
        "MIN_HOURS_TO_RESOLUTION": {"type": float, "min": 0.0, "max": 24.0},
        "LOOKAHEAD_DAYS": {"type": int, "min": 1, "max": 7},
        "MAX_SIGNALS_PER_SCAN": {"type": int, "min": 1, "max": 20},
        "MIN_HOLD_SECONDS": {"type": int, "min": 0, "max": 14400},
        "EXIT_STOP_LOSS_PCT": {"type": float, "min": 0.01, "max": 0.30},
        "EXIT_TAKE_PROFIT_PCT": {"type": float, "min": 0.01, "max": 0.30},
        "EXIT_MIN_HOLD_HOURS": {"type": float, "min": 0.0, "max": 24.0},
        "EXIT_MAX_AGE_HOURS": {"type": float, "min": 1.0, "max": 168.0},
    }

    def __init__(self, *args, weather_fetcher=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._weather_fetcher = weather_fetcher

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan weather markets using slug-based direct lookup.

        Primary path: construct slug for each city × date, fetch event,
        match forecast to correct temperature bucket, buy underpriced YES.

        Fallback: scan provided markets list for weather questions.
        """
        if self._weather_fetcher is None:
            return []

        signals: list[TradeSignal] = []

        # Primary path: slug-based direct lookup
        slug_signals = await self._scan_via_slugs()
        signals.extend(slug_signals)

        # Fallback: scan provided markets for any weather markets not caught by slugs
        seen_market_ids = {s.market_id for s in signals}
        for market in markets:
            if market.id in seen_market_ids:
                continue
            signal = await self._evaluate_legacy_market(market)
            if signal is not None:
                signals.append(signal)

        # Sort by edge * confidence, limit
        signals.sort(key=lambda s: s.edge * s.confidence, reverse=True)
        signals = signals[:self.MAX_SIGNALS_PER_SCAN]

        self.logger.info(
            "weather_scan_complete",
            slug_signals=len(slug_signals),
            total_signals=len(signals),
        )
        return signals

    async def _scan_via_slugs(self) -> list[TradeSignal]:
        """Scan weather markets via predictable Polymarket slugs.

        For each supported city × next N days:
        1. Construct slug: highest-temperature-in-{city}-on-{month}-{day}-{year}
        2. Fetch event from Gamma API
        3. Get NOAA forecast for city
        4. Find bucket matching forecast temp
        5. If bucket YES price < ENTRY_THRESHOLD, emit signal
        """
        signals: list[TradeSignal] = []
        now = datetime.now(timezone.utc)

        for city_key, city_slug in _CITY_SLUGS.items():
            # Get forecast for city (cached 30min)
            forecast = await self._weather_fetcher.get_forecast(city_key)
            if not forecast:
                continue

            # Build date→temp lookup from forecast periods
            forecast_by_date: dict[str, float] = {}
            forecast_confidence: dict[str, float] = {}
            for period in forecast:
                if period.period == "day" and period.date not in forecast_by_date:
                    forecast_by_date[period.date] = period.temp_f
                    forecast_confidence[period.date] = period.confidence

            # Scan today + next N-1 days
            for day_offset in range(self.LOOKAHEAD_DAYS):
                target_date = now + timedelta(days=day_offset)
                date_str = target_date.strftime("%Y-%m-%d")

                temp = forecast_by_date.get(date_str)
                if temp is None:
                    continue

                confidence = forecast_confidence.get(date_str, 0.6)
                if confidence < self.CONFIDENCE_THRESHOLD:
                    continue

                # Fetch event by slug
                slug = _build_weather_slug(city_slug, target_date)
                event = await self.gamma.get_event_by_slug(slug)
                if not event:
                    self.logger.debug(
                        "weather_slug_no_event",
                        slug=slug, city=city_key, date=date_str,
                    )
                    continue

                # Check hours to resolution
                hours_left = _hours_until_resolution(event)
                if hours_left < self.MIN_HOURS_TO_RESOLUTION:
                    self.logger.debug(
                        "weather_too_close_to_resolution",
                        city=city_key, date=date_str,
                        hours_left=round(hours_left, 1),
                    )
                    continue

                # Find matching temperature bucket
                signal = self._match_bucket(
                    event, city_key, date_str, temp, confidence,
                )
                if signal is not None:
                    signals.append(signal)

        return signals

    def _match_bucket(
        self,
        event: dict,
        city: str,
        date: str,
        forecast_temp: float,
        confidence: float,
    ) -> TradeSignal | None:
        """Find the temperature bucket matching the forecast and evaluate price.

        Iterates through event's markets, parses each question for temp range,
        and checks if forecast falls within. If YES price is below ENTRY_THRESHOLD,
        generates a BUY signal.
        """
        for market in event.get("markets", []):
            question = market.get("question", "")
            temp_range = parse_temp_range(question)
            if temp_range is None:
                continue

            low, high = temp_range
            if not (low <= forecast_temp <= high):
                continue

            # Found the matching bucket — check price
            try:
                prices = json.loads(market.get("outcomePrices", "[0.5,0.5]"))
                yes_price = float(prices[0])
            except (json.JSONDecodeError, ValueError, IndexError, TypeError):
                continue

            # Extract token IDs
            try:
                token_ids = json.loads(market.get("clobTokenIds", "[]"))
            except (json.JSONDecodeError, ValueError, TypeError):
                token_ids = []

            if not token_ids:
                continue

            market_id = market.get("conditionId") or market.get("id", "")
            if not market_id:
                continue

            self.logger.info(
                "weather_bucket_matched",
                city=city, date=date,
                forecast=forecast_temp,
                bucket=f"{low}-{high}°F",
                yes_price=yes_price,
                question=question[:80],
            )

            # Calculate edge: how underpriced is this bucket?
            # Forecast says this bucket should win → fair value ~0.70-0.90
            # depending on confidence. Edge = fair_value - market_price.
            fair_value = min(0.90, 0.50 + confidence * 0.40)
            edge = fair_value - yes_price

            if yes_price >= self.ENTRY_THRESHOLD:
                self.logger.info(
                    "weather_price_above_threshold",
                    price=yes_price, threshold=self.ENTRY_THRESHOLD,
                    edge=round(edge, 3),
                )
                return None

            if edge < self.MIN_EDGE:
                self.logger.info(
                    "weather_edge_too_low",
                    price=yes_price, edge=round(edge, 3),
                    min_edge=self.MIN_EDGE, fair_value=round(fair_value, 3),
                )
                return None

            signal_confidence = min(0.95, 0.6 + confidence * 0.3)

            return TradeSignal(
                strategy=self.name,
                market_id=market_id,
                token_id=token_ids[0],
                question=question,
                side=OrderSide.BUY,
                outcome="Yes",
                estimated_prob=min(0.95, yes_price + edge),
                market_price=yes_price,
                edge=edge,
                size_usd=0.0,
                confidence=signal_confidence,
                reasoning=(
                    f"Weather slug: BUY Yes @ ${yes_price:.3f}. "
                    f"NOAA forecast {forecast_temp:.0f}°F → bucket {low}-{high}°F. "
                    f"Fair value ~{fair_value:.2f}, edge {edge:.2f}. "
                    f"Confidence: {confidence:.0%}"
                ),
                metadata={
                    "city": city,
                    "date": date,
                    "forecast_temp": forecast_temp,
                    "bucket_low": low,
                    "bucket_high": high,
                    "fair_value": fair_value,
                    "forecast_confidence": confidence,
                    "source": "slug_lookup",
                },
            )

        self.logger.debug(
            "weather_no_bucket_match",
            city=city, date=date, forecast=forecast_temp,
        )
        return None

    async def _evaluate_legacy_market(
        self, market: GammaMarket,
    ) -> TradeSignal | None:
        """Fallback: evaluate a market from the generic scan pipeline.

        Used for weather markets not covered by slug-based lookup
        (e.g., different question formats, non-standard cities).
        """
        if not _WEATHER_DETECT.search(market.question):
            return None

        # Try bucket parsing on this market's question
        temp_range = parse_temp_range(market.question)
        if temp_range is None:
            return None

        # Try to extract city from question
        city = self._extract_city(market.question)
        if city is None:
            return None

        # Get forecast
        forecast = await self._weather_fetcher.get_forecast(city)
        if not forecast:
            return None

        # Use first daytime period as best estimate
        best_period = next(
            (p for p in forecast if p.period == "day"), None,
        )
        if best_period is None and forecast:
            best_period = forecast[0]
        if best_period is None:
            return None

        forecast_temp = best_period.temp_f
        confidence = best_period.confidence

        if confidence < self.CONFIDENCE_THRESHOLD:
            return None

        low, high = temp_range
        if not (low <= forecast_temp <= high):
            # Forecast doesn't match this bucket — not a signal
            return None

        # Price check
        yes_price = market.yes_price
        if yes_price is None or yes_price >= self.ENTRY_THRESHOLD:
            return None

        token_ids = market.token_ids
        if not token_ids:
            return None

        fair_value = min(0.90, 0.50 + confidence * 0.40)
        edge = fair_value - yes_price

        if edge < self.MIN_EDGE:
            return None

        signal_confidence = min(0.95, 0.6 + confidence * 0.3)

        return TradeSignal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_ids[0],
            question=market.question,
            side=OrderSide.BUY,
            outcome="Yes",
            estimated_prob=min(0.95, yes_price + edge),
            market_price=yes_price,
            edge=edge,
            size_usd=0.0,
            confidence=signal_confidence,
            reasoning=(
                f"Weather fallback: BUY Yes @ ${yes_price:.3f}. "
                f"NOAA forecast {forecast_temp:.0f}°F in bucket {low}-{high}°F. "
                f"Confidence: {confidence:.0%}"
            ),
            metadata={
                "city": city,
                "forecast_temp": forecast_temp,
                "bucket_low": low,
                "bucket_high": high,
                "forecast_confidence": confidence,
                "source": "legacy_scan",
            },
        )

    @staticmethod
    def _extract_city(question: str) -> str | None:
        """Extract city name from a weather market question."""
        q_lower = question.lower()
        # Check for known city names in question
        known_cities = [
            "new york", "nyc", "chicago", "miami", "dallas",
            "seattle", "atlanta", "los angeles", "houston",
            "phoenix", "philadelphia", "san antonio", "san diego",
            "denver", "boston", "san francisco", "minneapolis",
            "detroit", "washington", "dc",
        ]
        for city in known_cities:
            if city in q_lower:
                return city
        # Try regex: "temperature in CITY"
        m = re.search(
            r"temp(?:erature)?\s+in\s+([A-Za-z\s]+?)(?:\s+on|\s+be|\s*$)",
            question, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip().lower()
        return None

    async def should_exit(
        self, market_id: str, current_price: float, **kwargs,
    ) -> str | bool:
        """Exit on stop-loss, take-profit, or max-age."""
        avg_price = kwargs.get("avg_price", 0.0)
        created_at = kwargs.get("created_at")

        # Stop-loss
        if avg_price > 0:
            loss_pct = (avg_price - current_price) / avg_price
            if loss_pct >= self.EXIT_STOP_LOSS_PCT:
                return f"stop_loss ({loss_pct:.0%} loss)"

        # Take-profit after minimum hold
        if avg_price > 0 and created_at is not None:
            profit_pct = (current_price - avg_price) / avg_price
            if profit_pct >= self.EXIT_TAKE_PROFIT_PCT:
                now = datetime.now(timezone.utc)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                held_hours = (now - created_at).total_seconds() / 3600
                if held_hours >= self.EXIT_MIN_HOLD_HOURS:
                    return (
                        f"take_profit (+{profit_pct:.1%} after {held_hours:.0f}h)"
                    )

        # Exit threshold (price rose enough)
        if current_price >= self.EXIT_THRESHOLD:
            return f"exit_threshold (price ${current_price:.3f} >= ${self.EXIT_THRESHOLD:.2f})"

        # Max age
        if created_at is not None:
            now = datetime.now(timezone.utc)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            held_hours = (now - created_at).total_seconds() / 3600
            if held_hours >= self.EXIT_MAX_AGE_HOURS:
                return f"max_age ({held_hours:.0f}h)"

        return False
