"""Weather trading strategy: exploit NOAA forecast accuracy vs Polymarket odds.

Inspired by gopfan2 who made +$2M on weather markets using NOAA data.
NOAA forecasts are 85-90% accurate for 1-3 day horizons, while Polymarket
weather markets are often mispriced by retail participants.
"""

import re
from datetime import datetime, timezone

import structlog

from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()

# Broad detection: is this a weather question at all?
_WEATHER_DETECT = re.compile(
    r"\b(?:temperature|temp|°[FC]|degrees?\s*[FC]|weather|forecast|"
    r"high(?:est)?\s+of|low(?:est)?\s+of|heat\s*wave|cold\s*snap|"
    r"highest\s+temp|lowest\s+temp|precipitation|rainfall|snowfall)\b",
    re.IGNORECASE,
)


def _is_weather_question(question: str) -> bool:
    """Check if a question is weather-related."""
    return bool(_WEATHER_DETECT.search(question))

# Parse weather questions — extract city, threshold, direction
# Examples:
#   "Will the high temperature in NYC on March 15 be above 55°F?"
#   "Will the temperature in Chicago exceed 80 degrees on March 20?"
#   "Will NYC's high temp be over 60°F on March 12?"
_WEATHER_Q_PATTERNS = [
    # Polymarket format: "highest temperature in CITY be X°F or higher on DATE"
    re.compile(
        r"(?:highest|high|lowest|low)?\s*temp(?:erature)?\s+in\s+(.+?)\s+"
        r"(?:be\s+)?(\d+(?:\.\d+)?)\s*°?\s*([fFcC])?\s*or\s+higher",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:highest|high|lowest|low)?\s*temp(?:erature)?\s+in\s+(.+?)\s+"
        r"(?:be\s+)?(\d+(?:\.\d+)?)\s*°?\s*([fFcC])?\s*or\s+lower",
        re.IGNORECASE,
    ),
    # Polymarket exact: "highest temperature in CITY be X°C on DATE"
    re.compile(
        r"(?:highest|high|lowest|low)?\s*temp(?:erature)?\s+in\s+(.+?)\s+"
        r"(?:be\s+)?(\d+(?:\.\d+)?)\s*°\s*([fFcC])\s+on\s+",
        re.IGNORECASE,
    ),
    # Range: "highest temperature in CITY be between X-Y°F on DATE"
    re.compile(
        r"(?:highest|high|lowest|low)?\s*temp(?:erature)?\s+in\s+(.+?)\s+"
        r"(?:be\s+)?(?:between\s+)?(\d+(?:\.\d+)?)\s*[-–]\s*\d+\s*°?\s*([fFcC])?",
        re.IGNORECASE,
    ),
    # Classic: "high temperature in CITY ... above/below X°F/°C"
    re.compile(
        r"(?:highest|high|low|lowest)?\s*temp(?:erature)?\s+in\s+(.+?)\s+"
        r"(?:on|for)\s+.+?\s+(?:be\s+)?(?:above|over|exceed|reach)\s+"
        r"(\d+(?:\.\d+)?)\s*°?\s*([fFcC])?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:highest|high|low|lowest)?\s*temp(?:erature)?\s+in\s+(.+?)\s+"
        r"(?:on|for)\s+.+?\s+(?:be\s+)?(?:below|under)\s+"
        r"(\d+(?:\.\d+)?)\s*°?\s*([fFcC])?",
        re.IGNORECASE,
    ),
    # "CITY's high/highest temp ... above/below X"
    re.compile(
        r"(.+?)'s\s+(?:highest|high|low|lowest)?\s*temp\w*\s+.*?"
        r"(?:above|over|exceed|reach)\s+(\d+(?:\.\d+)?)\s*°?\s*([fFcC])?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(.+?)'s\s+(?:highest|high|low|lowest)?\s*temp\w*\s+.*?"
        r"(?:below|under)\s+(\d+(?:\.\d+)?)\s*°?\s*([fFcC])?",
        re.IGNORECASE,
    ),
    # Generic fallback
    re.compile(
        r"temp(?:erature)?\s+.*?in\s+(.+?)\s+.*?"
        r"(?:above|over|exceed|reach)\s+(\d+(?:\.\d+)?)\s*°?\s*([fFcC])?",
        re.IGNORECASE,
    ),
    re.compile(
        r"temp(?:erature)?\s+.*?in\s+(.+?)\s+.*?"
        r"(?:below|under)\s+(\d+(?:\.\d+)?)\s*°?\s*([fFcC])?",
        re.IGNORECASE,
    ),
]

# Direction keywords
_ABOVE_KEYWORDS = re.compile(r"\b(above|over|exceed|reach|higher)\b", re.IGNORECASE)
_BELOW_KEYWORDS = re.compile(r"\b(below|under|lower)\b", re.IGNORECASE)


class WeatherTradingStrategy(BaseStrategy):
    """Trade weather markets using NOAA forecast data."""

    name = "weather_trading"
    min_tier = CapitalTier.TIER1
    MIN_HOLD_SECONDS = 1800  # 30 min

    MIN_EDGE = 0.03
    CONFIDENCE_THRESHOLD = 0.5
    MAX_PRICE_BUY_YES = 0.30  # gopfan2: buy YES when cheap
    MIN_PRICE_BUY_NO = 0.70  # buy NO when YES is expensive
    TEMP_MARGIN_F = 5.0  # min °F difference from threshold
    EXIT_STOP_LOSS_PCT = 0.10
    EXIT_TAKE_PROFIT_PCT = 0.05
    EXIT_MIN_HOLD_HOURS = 2.0
    EXIT_MAX_AGE_HOURS = 48.0

    _MUTABLE_PARAMS = {
        "MIN_EDGE": {"type": float, "min": 0.0, "max": 0.5},
        "CONFIDENCE_THRESHOLD": {"type": float, "min": 0.3, "max": 1.0},
        "MAX_PRICE_BUY_YES": {"type": float, "min": 0.05, "max": 0.50},
        "MIN_PRICE_BUY_NO": {"type": float, "min": 0.50, "max": 0.95},
        "TEMP_MARGIN_F": {"type": float, "min": 1.0, "max": 20.0},
        "MIN_HOLD_SECONDS": {"type": int, "min": 0, "max": 14400},
        "EXIT_STOP_LOSS_PCT": {"type": float, "min": 0.01, "max": 0.30},
        "EXIT_TAKE_PROFIT_PCT": {"type": float, "min": 0.01, "max": 0.30},
        "EXIT_MIN_HOLD_HOURS": {"type": float, "min": 0.0, "max": 24.0},
        "EXIT_MAX_AGE_HOURS": {"type": float, "min": 1.0, "max": 168.0},
    }

    def __init__(self, *args, weather_fetcher=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._weather_fetcher = weather_fetcher

    @staticmethod
    def _parse_weather_question(question: str) -> dict | None:
        """Parse a weather market question to extract city, threshold, direction.

        Returns dict with keys: city, threshold (in °F), direction ("above"/"below")
        or None if parsing fails.
        """
        for pattern in _WEATHER_Q_PATTERNS:
            match = pattern.search(question)
            if match:
                city_raw = re.sub(
                    r"^(?:will|does|is|the|a)\s+",
                    "",
                    match.group(1).strip().rstrip("'s").strip(),
                    flags=re.IGNORECASE,
                ).strip()
                threshold = float(match.group(2))

                # Check temperature unit (group 3 if captured)
                unit = match.group(3) if match.lastindex and match.lastindex >= 3 else None
                if unit and unit.upper() == "C":
                    # Convert Celsius threshold to Fahrenheit for comparison
                    threshold = threshold * 9.0 / 5.0 + 32.0

                # Determine direction from question text
                q_lower = question.lower()
                if "or lower" in q_lower or _BELOW_KEYWORDS.search(question):
                    direction = "below"
                elif "or higher" in q_lower or _ABOVE_KEYWORDS.search(question):
                    direction = "above"
                elif "between" in q_lower:
                    # Range market: threshold is low end, treat as "above"
                    direction = "above"
                else:
                    # Exact value market (e.g., "be 14°C on March 10")
                    # Treat as "above" — if forecast > threshold, YES wins
                    direction = "above"

                return {
                    "city": city_raw.lower(),
                    "threshold": threshold,
                    "direction": direction,
                }

        return None

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan markets for weather trading opportunities."""
        if self._weather_fetcher is None:
            return []

        weather_count = sum(1 for m in markets if _is_weather_question(m.question))
        if weather_count > 0:
            self.logger.info(
                "weather_markets_in_batch", count=weather_count, total=len(markets),
            )

        signals: list[TradeSignal] = []

        for market in markets:
            signal = await self._evaluate_market(market)
            if signal is not None:
                signals.append(signal)

        signals.sort(
            key=lambda s: s.edge * s.confidence, reverse=True,
        )
        self.logger.info(
            "weather_scan_complete", signals_found=len(signals),
        )
        return signals

    async def _evaluate_market(
        self, market: GammaMarket,
    ) -> TradeSignal | None:
        """Evaluate a single market for weather trading signal."""
        # Only weather markets
        if not _is_weather_question(market.question):
            return None

        # Parse the question
        parsed = self._parse_weather_question(market.question)
        if parsed is None:
            self.logger.debug("weather_parse_failed", question=market.question[:80])
            return None

        city = parsed["city"]
        threshold = parsed["threshold"]
        direction = parsed["direction"]

        # Get forecast
        forecast = await self._weather_fetcher.get_forecast(city)
        if not forecast:
            self.logger.info(
                "weather_no_forecast", city=city, question=market.question[:60],
            )
            return None

        # Use the first daytime period as best estimate
        # (most weather markets ask about daytime highs)
        best_period = None
        for period in forecast:
            if period.period == "day":
                best_period = period
                break
        if best_period is None and forecast:
            best_period = forecast[0]
        if best_period is None:
            return None

        forecast_temp = best_period.temp_f
        forecast_confidence = best_period.confidence

        if forecast_confidence < self.CONFIDENCE_THRESHOLD:
            return None

        # Determine if forecast supports YES or NO
        temp_diff = forecast_temp - threshold
        if direction == "above":
            # "above X" → YES wins if forecast > threshold
            forecast_supports_yes = temp_diff > 0
        else:
            # "below X" → YES wins if forecast < threshold
            forecast_supports_yes = temp_diff < 0

        # Check temperature margin
        if abs(temp_diff) < self.TEMP_MARGIN_F:
            self.logger.info(
                "weather_margin_too_small",
                city=city, forecast=forecast_temp, threshold=threshold,
                diff=round(abs(temp_diff), 1), min_margin=self.TEMP_MARGIN_F,
            )
            return None

        # Calculate edge
        divergence_factor = min(1.0, abs(temp_diff) / 20.0)
        edge = forecast_confidence * divergence_factor

        if edge < self.MIN_EDGE:
            return None

        # Determine trade side using gopfan2 rules
        yes_price = market.yes_price
        if yes_price is None:
            return None

        token_ids = market.token_ids
        if not token_ids:
            return None

        if forecast_supports_yes and yes_price < self.MAX_PRICE_BUY_YES:
            # BUY YES — forecast agrees, price is cheap
            side = OrderSide.BUY
            token_id = token_ids[0]
            outcome = "Yes"
            price = yes_price
        elif (
            not forecast_supports_yes
            and yes_price > self.MIN_PRICE_BUY_NO
            and len(token_ids) >= 2
        ):
            # BUY NO — forecast disagrees with YES, YES price is high
            side = OrderSide.BUY
            token_id = token_ids[1]
            outcome = "No"
            price = 1.0 - yes_price
        else:
            # Price not in favorable zone
            return None

        confidence = min(0.95, 0.6 + forecast_confidence * 0.3)

        return TradeSignal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            question=market.question,
            side=side,
            outcome=outcome,
            estimated_prob=min(0.95, price + edge),
            market_price=price,
            edge=edge,
            size_usd=0.0,
            confidence=confidence,
            reasoning=(
                f"Weather: {outcome} at ${price:.3f}. "
                f"NOAA forecast {forecast_temp:.0f}°F vs threshold {threshold:.0f}°F "
                f"({direction}). Margin: {abs(temp_diff):.0f}°F, "
                f"confidence: {forecast_confidence:.0%}"
            ),
            metadata={
                "city": city,
                "forecast_temp": forecast_temp,
                "threshold": threshold,
                "direction": direction,
                "temp_diff": temp_diff,
                "forecast_confidence": forecast_confidence,
            },
        )

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

        # Max age
        if created_at is not None:
            now = datetime.now(timezone.utc)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            held_hours = (now - created_at).total_seconds() / 3600
            if held_hours >= self.EXIT_MAX_AGE_HOURS:
                return f"max_age ({held_hours:.0f}h)"

        return False
