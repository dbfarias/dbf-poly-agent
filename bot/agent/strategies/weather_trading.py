"""Weather trading strategy v3: laddering, tail bets, ECMWF ensemble.

Uses NOAA + Open-Meteo + ECMWF ensemble (2-of-3 agreement),
bucket-boundary uncertainty modeling, temperature laddering (buy 3-5
adjacent buckets instead of 1), and tail bucket trading for asymmetric
payoffs. Zero LLM cost.

Key improvements over v2:
- Temperature laddering: buy top N buckets with decaying size
- Tail bucket trading: cheap buckets ($0.01-0.05) for 20-100x payoffs
- ECMWF ensemble: 3-model agreement (NOAA + Open-Meteo GFS + ECMWF)
- Expanded city coverage: 25 global cities
"""

import json
import math
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
    # US cities
    "new york": "nyc",
    "nyc": "nyc",
    "chicago": "chicago",
    "miami": "miami",
    "dallas": "dallas",
    "seattle": "seattle",
    "atlanta": "atlanta",
    "los angeles": "la",
    "la": "la",
    "san francisco": "sf",
    "denver": "denver",
    "boston": "boston",
    "houston": "houston",
    "phoenix": "phoenix",
    # International cities
    "london": "london",
    "tokyo": "tokyo",
    "shanghai": "shanghai",
    "buenos aires": "buenos-aires",
    "ankara": "ankara",
    "sydney": "sydney",
    "mumbai": "mumbai",
    "são paulo": "sao-paulo",
    "sao paulo": "sao-paulo",
    "dubai": "dubai",
    "paris": "paris",
    "berlin": "berlin",
}

# Broad weather detection for fallback scan path
_WEATHER_DETECT = re.compile(
    r"\b(?:temperature|temp|°[FC]|degrees?\s*[FC]|weather|forecast|"
    r"high(?:est)?\s+of|low(?:est)?\s+of|heat\s*wave|cold\s*snap|"
    r"highest\s+temp|lowest\s+temp|precipitation|rainfall|snowfall)\b",
    re.IGNORECASE,
)

# Typical forecast uncertainty (std dev in °F) by horizon
_UNCERTAINTY_BY_HOURS: list[tuple[float, float]] = [
    (12, 2.0),   # Same day: ±2°F std dev
    (24, 2.5),   # Day 1
    (48, 3.5),   # Day 2
    (72, 4.5),   # Day 3
    (96, 5.5),   # Day 4
]
_DEFAULT_UNCERTAINTY = 6.0  # Day 5+


def _normal_cdf(x: float) -> float:
    """Standard normal CDF approximation (error < 1.5e-7)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bucket_probability(
    forecast_temp: float,
    bucket_low: float,
    bucket_high: float,
    sigma: float,
) -> float:
    """Probability that actual temp falls in [bucket_low, bucket_high].

    Models temperature as N(forecast_temp, sigma²).
    For open-ended buckets (-999 or 999), treats as one-sided.
    """
    if sigma <= 0:
        # Degenerate: no uncertainty
        return 1.0 if bucket_low <= forecast_temp <= bucket_high else 0.0

    # Handle open-ended buckets
    if bucket_low <= -900:
        # "X°F or below" → P(T <= bucket_high)
        return _normal_cdf((bucket_high - forecast_temp) / sigma)
    if bucket_high >= 900:
        # "X°F or higher" → P(T >= bucket_low) = 1 - P(T < bucket_low)
        return 1.0 - _normal_cdf((bucket_low - forecast_temp) / sigma)

    # Bounded bucket: P(low <= T <= high)
    p_high = _normal_cdf((bucket_high - forecast_temp) / sigma)
    p_low = _normal_cdf((bucket_low - forecast_temp) / sigma)
    return max(0.0, p_high - p_low)


def parse_temp_range(question: str) -> tuple[float, float] | None:
    """Extract temperature range from a market question (bucket matching).

    Returns (low, high) tuple in °F, or None if unparseable.
    Examples:
        "40°F or below"      → (-999, 40)
        "48°F or higher"     → (48, 999)
        "between 44-45°F"    → (44, 45)
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
    """Build Polymarket weather event slug."""
    month = _MONTHS[date.month - 1]
    return f"highest-temperature-in-{city_slug}-on-{month}-{date.day}-{date.year}"


def _get_uncertainty(hours_ahead: float) -> float:
    """Get temperature uncertainty (std dev °F) based on forecast horizon."""
    for hours_limit, sigma in _UNCERTAINTY_BY_HOURS:
        if hours_ahead <= hours_limit:
            return sigma
    return _DEFAULT_UNCERTAINTY


class WeatherTradingStrategy(BaseStrategy):
    """Trade weather markets using NOAA + Open-Meteo + ECMWF ensemble.

    v3 features: temperature laddering, tail bucket trading,
    ECMWF 3-model ensemble, 25 global cities.
    """

    name = "weather_trading"
    MIN_HOLD_SECONDS = 1800  # 30 min

    # Entry thresholds
    ENTRY_THRESHOLD = 0.50
    EXIT_THRESHOLD = 0.70
    MIN_EDGE = 0.07  # 7% min edge (AlterEgo uses ~7% avg)
    CONFIDENCE_THRESHOLD = 0.40
    MIN_HOURS_TO_RESOLUTION = 2.0
    LOOKAHEAD_DAYS = 4  # today + 3 days
    MAX_SIGNALS_PER_SCAN = 8  # increased for laddering (was 3)
    EXIT_STOP_LOSS_PCT = 0.12
    EXIT_TAKE_PROFIT_PCT = 0.08
    EXIT_MIN_HOLD_HOURS = 1.0
    EXIT_MAX_AGE_HOURS = 36.0

    # Gaussian probability params
    MIN_BUCKET_PROB = 0.55  # Only trade if P(bucket) > 55%
    ENSEMBLE_REQUIRED = True  # Require 2-of-3 models to agree
    MAX_BOUNDARY_PROXIMITY_F = 1.5  # Skip if forecast within 1.5F of edge

    # v3: Temperature laddering — buy top N adjacent buckets
    LADDER_WIDTH: int = 4  # max buckets per ladder
    LADDER_DECAY: float = 0.6  # each rank gets 60% of previous size

    # v3: Tail bucket trading — cheap buckets for asymmetric payoffs
    TAIL_MAX_PRICE: float = 0.05  # max price for tail buckets
    TAIL_MIN_PROB: float = 0.02  # min model probability for tail
    TAIL_SIZE_PCT: float = 0.02  # 2% of normal position size
    MAX_TAILS_PER_MARKET: int = 2  # max tail bets per event

    _MUTABLE_PARAMS = {
        "ENTRY_THRESHOLD": {"type": float, "min": 0.01, "max": 0.50},
        "EXIT_THRESHOLD": {"type": float, "min": 0.20, "max": 0.90},
        "MIN_EDGE": {"type": float, "min": 0.0, "max": 0.5},
        "CONFIDENCE_THRESHOLD": {"type": float, "min": 0.3, "max": 1.0},
        "MIN_HOURS_TO_RESOLUTION": {"type": float, "min": 0.0, "max": 24.0},
        "LOOKAHEAD_DAYS": {"type": int, "min": 1, "max": 7},
        "MAX_SIGNALS_PER_SCAN": {"type": int, "min": 1, "max": 30},
        "MIN_HOLD_SECONDS": {"type": int, "min": 0, "max": 14400},
        "EXIT_STOP_LOSS_PCT": {"type": float, "min": 0.01, "max": 0.30},
        "EXIT_TAKE_PROFIT_PCT": {"type": float, "min": 0.01, "max": 0.30},
        "EXIT_MIN_HOLD_HOURS": {"type": float, "min": 0.0, "max": 24.0},
        "EXIT_MAX_AGE_HOURS": {"type": float, "min": 1.0, "max": 168.0},
        "MIN_BUCKET_PROB": {"type": float, "min": 0.30, "max": 0.90},
        "ENSEMBLE_REQUIRED": {"type": bool},
        "MAX_BOUNDARY_PROXIMITY_F": {"type": float, "min": 0.0, "max": 5.0},
        "LADDER_WIDTH": {"type": int, "min": 1, "max": 8},
        "LADDER_DECAY": {"type": float, "min": 0.1, "max": 1.0},
        "TAIL_MAX_PRICE": {"type": float, "min": 0.01, "max": 0.10},
        "TAIL_MIN_PROB": {"type": float, "min": 0.005, "max": 0.10},
        "TAIL_SIZE_PCT": {"type": float, "min": 0.005, "max": 0.10},
        "MAX_TAILS_PER_MARKET": {"type": int, "min": 0, "max": 5},
    }

    def __init__(self, *args, weather_fetcher=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._weather_fetcher = weather_fetcher
        # Per-scan cache for Open-Meteo results (avoid rate limiting)
        self._om_cache: dict[str, list | None] = {}
        # Per-scan cache for ECMWF results
        self._ecmwf_cache: dict[str, list | None] = {}

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan weather markets using slug-based direct lookup."""
        if self._weather_fetcher is None:
            return []

        # Clear per-scan caches
        self._om_cache.clear()
        self._ecmwf_cache.clear()

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

    async def _get_ensemble_forecast(
        self, city: str, date_str: str,
    ) -> tuple[float | None, float | None, float | None]:
        """Get forecast from NOAA, Open-Meteo GFS, and ECMWF.

        Returns (noaa_temp, open_meteo_temp, ecmwf_temp) — any can be None.
        Uses per-scan caches to avoid rate limiting.
        """
        noaa_temp = None
        om_temp = None
        ecmwf_temp = None

        # NOAA forecast (primary)
        forecast = await self._weather_fetcher.get_forecast(city)
        if forecast:
            for period in forecast:
                if period.period == "day" and period.date == date_str:
                    noaa_temp = period.temp_f
                    break

        city_key = city.strip().lower()
        coords = self._weather_fetcher.CITIES.get(city_key)

        # Open-Meteo GFS forecast (secondary) — cached per city per scan
        if city_key not in self._om_cache:
            if coords:
                try:
                    self._om_cache[city_key] = await self._weather_fetcher._fetch_open_meteo(
                        city_key, coords,
                    )
                except Exception:
                    self._om_cache[city_key] = None

        om_periods = self._om_cache.get(city_key)
        if om_periods:
            for period in om_periods:
                if period.period == "day" and period.date == date_str:
                    om_temp = period.temp_f
                    break

        # ECMWF forecast (tertiary) — cached per city per scan
        if city_key not in self._ecmwf_cache:
            if coords and hasattr(self._weather_fetcher, "fetch_ecmwf_forecast"):
                try:
                    self._ecmwf_cache[city_key] = (
                        await self._weather_fetcher.fetch_ecmwf_forecast(
                            coords[0], coords[1],
                        )
                    )
                except Exception:
                    self._ecmwf_cache[city_key] = None
            else:
                self._ecmwf_cache[city_key] = None

        ecmwf_periods = self._ecmwf_cache.get(city_key)
        if ecmwf_periods:
            for period in ecmwf_periods:
                if period.period == "day" and period.date == date_str:
                    ecmwf_temp = period.temp_f
                    break

        return noaa_temp, om_temp, ecmwf_temp

    async def _scan_via_slugs(self) -> list[TradeSignal]:
        """Scan weather markets via predictable Polymarket slugs."""
        signals: list[TradeSignal] = []
        now = datetime.now(timezone.utc)

        for city_key, city_slug in _CITY_SLUGS.items():
            # Get primary forecast
            forecast = await self._weather_fetcher.get_forecast(city_key)
            if not forecast:
                continue

            # Build date→temp+confidence lookup
            forecast_by_date: dict[str, float] = {}
            forecast_confidence: dict[str, float] = {}
            for period in forecast:
                if period.period == "day" and period.date not in forecast_by_date:
                    forecast_by_date[period.date] = period.temp_f
                    forecast_confidence[period.date] = period.confidence

            for day_offset in range(self.LOOKAHEAD_DAYS):
                target_date = now + timedelta(days=day_offset)
                date_str = target_date.strftime("%Y-%m-%d")

                noaa_temp = forecast_by_date.get(date_str)
                if noaa_temp is None:
                    continue

                confidence = forecast_confidence.get(date_str, 0.6)
                if confidence < self.CONFIDENCE_THRESHOLD:
                    continue

                # Ensemble check: get forecasts from up to 3 models
                if self.ENSEMBLE_REQUIRED:
                    _, om_temp, ecmwf_temp = await self._get_ensemble_forecast(
                        city_key, date_str,
                    )
                    result = self._compute_ensemble_temp(
                        noaa_temp, om_temp, ecmwf_temp,
                        city_key, date_str, day_offset * 24.0,
                    )
                    if result is None:
                        continue
                    forecast_temp, confidence_adj = result
                    confidence *= confidence_adj
                else:
                    forecast_temp = noaa_temp

                # Fetch event by slug
                slug = _build_weather_slug(city_slug, target_date)
                event = await self.gamma.get_event_by_slug(slug)
                if not event:
                    continue

                hours_left = _hours_until_resolution(event)
                if hours_left < self.MIN_HOURS_TO_RESOLUTION:
                    continue

                # Calculate forecast uncertainty for this horizon
                hours_ahead = day_offset * 24.0
                sigma = _get_uncertainty(hours_ahead)

                # Temperature laddering: find top N buckets
                ladder = self._match_bucket_ladder(
                    event, city_key, date_str, forecast_temp,
                    confidence, sigma, hours_ahead,
                )
                signals.extend(ladder)

                # Tail bucket trading: cheap asymmetric bets
                tails = self._find_tail_buckets(
                    event, city_key, date_str, forecast_temp,
                    confidence, sigma, hours_ahead,
                )
                signals.extend(tails)

        return signals

    def _compute_ensemble_temp(
        self,
        noaa: float | None,
        om: float | None,
        ecmwf: float | None,
        city: str,
        date: str,
        hours_ahead: float,
    ) -> tuple[float, float] | None:
        """Compute ensemble forecast from up to 3 models.

        Returns (forecast_temp, confidence_multiplier) or None if
        insufficient agreement. Requires 2-of-3 models within sigma.
        """
        temps = [t for t in (noaa, om, ecmwf) if t is not None]
        if not temps:
            return None

        # Single model — use it but penalize confidence
        if len(temps) == 1:
            return temps[0], 0.85

        sigma = _get_uncertainty(hours_ahead)

        # 2 models: both must agree within sigma
        if len(temps) == 2:
            if abs(temps[0] - temps[1]) > sigma:
                self.logger.info(
                    "weather_ensemble_disagree",
                    city=city, date=date,
                    temps=[round(t, 1) for t in temps],
                    sigma=sigma,
                )
                return None
            return sum(temps) / len(temps), 1.0

        # 3 models: require 2-of-3 within sigma of each other
        pairs = [(0, 1), (0, 2), (1, 2)]
        agreeing: list[float] = []
        for i, j in pairs:
            if abs(temps[i] - temps[j]) <= sigma:
                agreeing = [temps[i], temps[j]]
                # Include third if it also agrees with the pair
                k = 3 - i - j
                if abs(temps[k] - sum(agreeing) / 2) <= sigma:
                    agreeing.append(temps[k])
                break

        if not agreeing:
            self.logger.info(
                "weather_ensemble_disagree",
                city=city, date=date,
                temps=[round(t, 1) for t in temps],
                sigma=sigma,
            )
            return None

        avg = sum(agreeing) / len(agreeing)
        # 3-of-3 agreement → full confidence; 2-of-3 → slight penalty
        multiplier = 1.0 if len(agreeing) == 3 else 0.95
        return avg, multiplier

    def _evaluate_bucket(
        self, market: dict, forecast_temp: float, sigma: float,
    ) -> tuple[str, list[str], float, float, float, float] | None:
        """Evaluate a single bucket market for trading opportunity.

        Returns (market_id, token_ids, yes_price, low, high, bucket_prob)
        or None if the bucket should be skipped.
        """
        question = market.get("question", "")
        temp_range = parse_temp_range(question)
        if temp_range is None:
            return None

        low, high = temp_range

        # Bucket boundary awareness
        if self.MAX_BOUNDARY_PROXIMITY_F > 0:
            if low > -900 and abs(forecast_temp - low) < self.MAX_BOUNDARY_PROXIMITY_F:
                return None
            if high < 900 and abs(forecast_temp - high) < self.MAX_BOUNDARY_PROXIMITY_F:
                return None

        bucket_prob = bucket_probability(forecast_temp, low, high, sigma)

        try:
            prices = json.loads(market.get("outcomePrices", "[0.5,0.5]"))
            yes_price = float(prices[0])
        except (json.JSONDecodeError, ValueError, IndexError, TypeError):
            return None

        try:
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
        except (json.JSONDecodeError, ValueError, TypeError):
            token_ids = []
        if not token_ids:
            return None

        market_id = market.get("conditionId") or market.get("id", "")
        if not market_id:
            return None

        return market_id, token_ids, yes_price, low, high, bucket_prob

    def _match_bucket_ladder(
        self,
        event: dict,
        city: str,
        date: str,
        forecast_temp: float,
        confidence: float,
        sigma: float,
        hours_ahead: float,
    ) -> list[TradeSignal]:
        """Find top N buckets by edge for temperature laddering."""
        candidates: list[dict] = []

        for market in event.get("markets", []):
            result = self._evaluate_bucket(market, forecast_temp, sigma)
            if result is None:
                continue

            market_id, token_ids, yes_price, low, high, bucket_prob = result

            if bucket_prob < self.MIN_BUCKET_PROB:
                continue
            if yes_price >= self.ENTRY_THRESHOLD:
                continue

            fair_value = min(0.95, bucket_prob * confidence)
            edge = fair_value - yes_price
            if edge < self.MIN_EDGE:
                continue

            candidates.append({
                "market_id": market_id,
                "token_id": token_ids[0],
                "question": market.get("question", ""),
                "yes_price": yes_price,
                "low": low,
                "high": high,
                "bucket_prob": bucket_prob,
                "fair_value": fair_value,
                "edge": edge,
            })

        # Sort by edge descending, take top LADDER_WIDTH
        candidates.sort(key=lambda c: c["edge"], reverse=True)
        ladder = candidates[:self.LADDER_WIDTH]

        return self._build_ladder_signals(
            ladder, city, date, forecast_temp, confidence, sigma,
        )

    def _build_ladder_signals(
        self,
        ladder: list[dict],
        city: str,
        date: str,
        forecast_temp: float,
        confidence: float,
        sigma: float,
    ) -> list[TradeSignal]:
        """Convert ranked bucket candidates into sized ladder signals."""
        signals: list[TradeSignal] = []
        for rank, cand in enumerate(ladder, 1):
            # Decay factor: rank 1 → 1.0, rank 2 → LADDER_DECAY, etc.
            weight = self.LADDER_DECAY ** (rank - 1)
            signal_confidence = min(
                0.95,
                0.5 + cand["bucket_prob"] * 0.3 + confidence * 0.15,
            )

            self.logger.info(
                "weather_ladder_bucket",
                city=city, date=date, rank=rank,
                forecast=round(forecast_temp, 1),
                bucket=f"{cand['low']}-{cand['high']}F",
                bucket_prob=round(cand["bucket_prob"], 3),
                edge=round(cand["edge"], 3),
                weight=round(weight, 2),
            )

            signals.append(TradeSignal(
                strategy=self.name,
                market_id=cand["market_id"],
                token_id=cand["token_id"],
                question=cand["question"],
                side=OrderSide.BUY,
                outcome="Yes",
                estimated_prob=min(0.95, cand["yes_price"] + cand["edge"]),
                market_price=cand["yes_price"],
                edge=cand["edge"],
                size_usd=0.0,
                confidence=signal_confidence,
                reasoning=(
                    f"Weather v3 ladder #{rank}: BUY Yes @ ${cand['yes_price']:.3f}. "
                    f"Forecast {forecast_temp:.0f}F -> bucket "
                    f"{cand['low']}-{cand['high']}F. "
                    f"P={cand['bucket_prob']:.0%}, edge {cand['edge']:.2f}"
                ),
                metadata={
                    "city": city,
                    "date": date,
                    "forecast_temp": forecast_temp,
                    "bucket_low": cand["low"],
                    "bucket_high": cand["high"],
                    "bucket_prob": cand["bucket_prob"],
                    "fair_value": cand["fair_value"],
                    "sigma": sigma,
                    "forecast_confidence": confidence,
                    "source": "slug_lookup_v3",
                    "ladder_rank": rank,
                    "ladder_weight": weight,
                },
            ))

        return signals

    def _find_tail_buckets(
        self,
        event: dict,
        city: str,
        date: str,
        forecast_temp: float,
        confidence: float,
        sigma: float,
        hours_ahead: float,
    ) -> list[TradeSignal]:
        """Find cheap tail buckets for asymmetric payoffs (20-100x)."""
        tails: list[dict] = []

        for market in event.get("markets", []):
            result = self._evaluate_bucket(market, forecast_temp, sigma)
            if result is None:
                continue

            market_id, token_ids, yes_price, low, high, bucket_prob = result

            # Tail criteria: cheap price but non-zero model probability
            if yes_price > self.TAIL_MAX_PRICE:
                continue
            if bucket_prob < self.TAIL_MIN_PROB:
                continue
            # Exclude buckets that would also qualify for ladder
            if bucket_prob >= self.MIN_BUCKET_PROB:
                continue

            edge = bucket_prob - yes_price
            if edge <= 0:
                continue

            tails.append({
                "market_id": market_id,
                "token_id": token_ids[0],
                "question": market.get("question", ""),
                "yes_price": yes_price,
                "low": low,
                "high": high,
                "bucket_prob": bucket_prob,
                "edge": edge,
            })

        # Sort by expected value (prob / price ratio), limit
        tails.sort(key=lambda t: t["bucket_prob"] / max(t["yes_price"], 0.001), reverse=True)
        tails = tails[:self.MAX_TAILS_PER_MARKET]

        return self._build_tail_signals(
            tails, city, date, forecast_temp, confidence, sigma,
        )

    def _build_tail_signals(
        self,
        tails: list[dict],
        city: str,
        date: str,
        forecast_temp: float,
        confidence: float,
        sigma: float,
    ) -> list[TradeSignal]:
        """Convert tail bucket candidates into small-sized signals."""
        signals: list[TradeSignal] = []
        for tail in tails:
            payoff_ratio = 1.0 / max(tail["yes_price"], 0.01)

            self.logger.info(
                "weather_tail_bucket",
                city=city, date=date,
                bucket=f"{tail['low']}-{tail['high']}F",
                price=tail["yes_price"],
                prob=round(tail["bucket_prob"], 3),
                payoff=f"{payoff_ratio:.0f}x",
            )

            signals.append(TradeSignal(
                strategy=self.name,
                market_id=tail["market_id"],
                token_id=tail["token_id"],
                question=tail["question"],
                side=OrderSide.BUY,
                outcome="Yes",
                estimated_prob=tail["bucket_prob"],
                market_price=tail["yes_price"],
                edge=tail["edge"],
                size_usd=0.0,
                confidence=min(0.50, 0.2 + tail["bucket_prob"] * 2),
                reasoning=(
                    f"Weather v3 TAIL: BUY Yes @ ${tail['yes_price']:.3f}. "
                    f"Bucket {tail['low']}-{tail['high']}F, "
                    f"P={tail['bucket_prob']:.1%}, {payoff_ratio:.0f}x payoff"
                ),
                metadata={
                    "city": city,
                    "date": date,
                    "forecast_temp": forecast_temp,
                    "bucket_low": tail["low"],
                    "bucket_high": tail["high"],
                    "bucket_prob": tail["bucket_prob"],
                    "sigma": sigma,
                    "forecast_confidence": confidence,
                    "source": "tail_v3",
                    "tail_bet": True,
                    "tail_size_pct": self.TAIL_SIZE_PCT,
                },
            ))

        return signals

    def _match_bucket(
        self,
        event: dict,
        city: str,
        date: str,
        forecast_temp: float,
        confidence: float,
        sigma: float,
        hours_ahead: float,
    ) -> TradeSignal | None:
        """Find the single best bucket — legacy compat wrapper for ladder.

        Returns the top-ranked ladder signal or None.
        """
        saved_width = self.LADDER_WIDTH
        self.LADDER_WIDTH = 1
        try:
            ladder = self._match_bucket_ladder(
                event, city, date, forecast_temp,
                confidence, sigma, hours_ahead,
            )
            return ladder[0] if ladder else None
        finally:
            self.LADDER_WIDTH = saved_width

    async def _evaluate_legacy_market(
        self, market: GammaMarket,
    ) -> TradeSignal | None:
        """Fallback: evaluate a market from the generic scan pipeline."""
        if not _WEATHER_DETECT.search(market.question):
            return None

        temp_range = parse_temp_range(market.question)
        if temp_range is None:
            return None

        city = self._extract_city(market.question)
        if city is None:
            return None

        forecast = await self._weather_fetcher.get_forecast(city)
        if not forecast:
            return None

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

        # Bucket boundary check
        if self.MAX_BOUNDARY_PROXIMITY_F > 0:
            if low > -900 and abs(forecast_temp - low) < self.MAX_BOUNDARY_PROXIMITY_F:
                return None
            if high < 900 and abs(forecast_temp - high) < self.MAX_BOUNDARY_PROXIMITY_F:
                return None

        # Use Gaussian probability
        sigma = 3.0  # default for legacy path
        bucket_prob = bucket_probability(forecast_temp, low, high, sigma)
        if bucket_prob < self.MIN_BUCKET_PROB:
            return None

        yes_price = market.yes_price
        if yes_price is None or yes_price >= self.ENTRY_THRESHOLD:
            return None

        token_ids = market.token_ids
        if not token_ids:
            return None

        fair_value = min(0.95, bucket_prob * confidence)
        edge = fair_value - yes_price

        if edge < self.MIN_EDGE:
            return None

        signal_confidence = min(0.95, 0.5 + bucket_prob * 0.3 + confidence * 0.15)

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
                f"Weather v2 fallback: BUY Yes @ ${yes_price:.3f}. "
                f"NOAA forecast {forecast_temp:.0f}°F in bucket {low}-{high}°F. "
                f"P(bucket)={bucket_prob:.0%}, edge {edge:.2f}"
            ),
            metadata={
                "city": city,
                "forecast_temp": forecast_temp,
                "bucket_low": low,
                "bucket_high": high,
                "bucket_prob": bucket_prob,
                "forecast_confidence": confidence,
                "source": "legacy_scan_v2",
            },
        )

    @staticmethod
    def _extract_city(question: str) -> str | None:
        """Extract city name from a weather market question."""
        q_lower = question.lower()
        known_cities = [
            # US
            "new york", "nyc", "chicago", "miami", "dallas",
            "seattle", "atlanta", "los angeles", "houston",
            "phoenix", "philadelphia", "san antonio", "san diego",
            "denver", "boston", "san francisco", "minneapolis",
            "detroit", "washington", "dc",
            # International
            "london", "tokyo", "shanghai", "buenos aires", "ankara",
            "sydney", "mumbai", "dubai", "paris", "berlin",
            "sao paulo",
        ]
        for city in known_cities:
            if city in q_lower:
                return city
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
        """Exit on stop-loss, take-profit, swing, or max-age.

        Tail bets (avg_price < TAIL_MAX_PRICE) only exit on swing
        threshold ($0.70+) or max age — they are held to resolution.
        """
        avg_price = kwargs.get("avg_price", 0.0)
        created_at = kwargs.get("created_at")

        # Tail bets: hold to resolution. Only exit if price reaches
        # swing threshold (near resolution) or max age expires.
        if avg_price > 0 and avg_price <= self.TAIL_MAX_PRICE:
            if current_price >= self.EXIT_THRESHOLD:
                return f"tail_swing_exit (${current_price:.3f} >= ${self.EXIT_THRESHOLD:.2f})"
            if created_at is not None:
                now = datetime.now(timezone.utc)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                held = (now - created_at).total_seconds() / 3600
                if held >= self.EXIT_MAX_AGE_HOURS:
                    return f"tail_max_age ({held:.0f}h)"
            return False

        # Stop-loss
        if avg_price > 0:
            loss_pct = (avg_price - current_price) / avg_price
            if loss_pct >= self.EXIT_STOP_LOSS_PCT:
                return f"stop_loss ({loss_pct:.0%} loss)"

        # Swing exit at high price
        if current_price >= self.EXIT_THRESHOLD:
            return f"exit_threshold (price ${current_price:.3f} >= ${self.EXIT_THRESHOLD:.2f})"

        # Take-profit after minimum hold.
        # Weather markets have wide spreads (~$0.03-0.05), so the actual
        # sell price (best bid) will be significantly below the mid/ask
        # price used here. We add a spread buffer to ensure the fill
        # price still yields real profit after slippage.
        spread_buffer = 0.15  # require 15% extra to absorb spread
        effective_tp = self.EXIT_TAKE_PROFIT_PCT + spread_buffer
        if avg_price > 0 and created_at is not None:
            profit_pct = (current_price - avg_price) / avg_price
            if profit_pct >= effective_tp:
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
