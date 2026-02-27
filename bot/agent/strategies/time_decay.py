"""High-probability strategy: buy outcomes with strong implied probability.

Core idea: Markets with high implied probability (>90%) tend to resolve
as expected. Buy YES tokens at 0.60-0.97, collect $1.00 at resolution.

Uses a DYNAMIC time horizon based on daily target urgency:
- Ahead of target → only immediate markets (< 24h)
- On pace → short-term (< 72h)
- Behind target → expand to medium-term (< 168h)

Shorter markets always get higher priority via time-weighted scoring.
"""

from datetime import datetime, timezone

import structlog

from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()

# Time horizon bands (hours)
HOURS_IMMEDIATE = 24.0   # Resolves today — highest priority
HOURS_SHORT = 72.0       # Resolves in 1-3 days
HOURS_MEDIUM = 168.0     # Resolves in 3-7 days — only when behind target

# Urgency → max hours mapping
# urgency 0.7 (ahead) → 24h only
# urgency 1.0 (on pace) → 72h
# urgency 1.3+ (behind) → 168h
URGENCY_HORIZON = {
    0.7: HOURS_IMMEDIATE,
    1.0: HOURS_SHORT,
    1.3: HOURS_MEDIUM,
    1.5: HOURS_MEDIUM,
}

# Strategy parameters
MIN_IMPLIED_PROB = 0.70
MAX_PRICE = 0.97
MIN_PRICE = 0.60
MIN_EDGE = 0.015  # 1.5% minimum edge
CONFIDENCE_BASE = 0.75


def _max_hours_for_urgency(urgency: float) -> float:
    """Compute max allowed hours based on urgency level.

    Linear interpolation between urgency breakpoints.
    """
    if urgency <= 0.7:
        return HOURS_IMMEDIATE
    elif urgency <= 1.0:
        # Interpolate 24h → 72h as urgency goes 0.7 → 1.0
        t = (urgency - 0.7) / 0.3
        return HOURS_IMMEDIATE + t * (HOURS_SHORT - HOURS_IMMEDIATE)
    elif urgency <= 1.3:
        # Interpolate 72h → 168h as urgency goes 1.0 → 1.3
        t = (urgency - 1.0) / 0.3
        return HOURS_SHORT + t * (HOURS_MEDIUM - HOURS_SHORT)
    else:
        return HOURS_MEDIUM


class TimeDecayStrategy(BaseStrategy):
    """Buy high-probability outcomes, prefer markets near resolution."""

    name = "time_decay"
    min_tier = CapitalTier.TIER1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Adaptive parameters (adjusted by learner)
        self._min_edge = MIN_EDGE
        self._max_price = MAX_PRICE
        self._confidence_adjustment: dict[str, float] = {}
        self._urgency: float = 1.0

    def adjust_params(self, adjustments: dict) -> None:
        """Apply learner adjustments to time decay parameters.

        Accepts:
        - edge_multipliers: dict[(strategy, category), float]
        - category_confidences: dict[category, float]
        - calibration: dict[bucket, float]
        - urgency_multiplier: float (from daily target progress)
        """
        calibration = adjustments.get("calibration", {})

        # If high-confidence trades are poorly calibrated, tighten MAX_PRICE
        high_cal = calibration.get("95-99", 1.0)
        if high_cal < 0.7:
            self._max_price = 0.96
            self.logger.info(
                "time_decay_max_price_adjusted",
                max_price=self._max_price,
                calibration_95_99=high_cal,
            )
        else:
            self._max_price = MAX_PRICE

        # Store category confidences for use in scan
        self._confidence_adjustment = adjustments.get("category_confidences", {})

        # Store urgency for dynamic time horizon
        self._urgency = adjustments.get("urgency_multiplier", 1.0)

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan for high-probability outcomes."""
        signals = []
        now = datetime.now(timezone.utc)
        max_hours = _max_hours_for_urgency(self._urgency)

        for market in markets:
            signal = await self._evaluate_market(market, now, max_hours)
            if signal:
                signals.append(signal)

        # Sort by time-weighted score: shorter resolution + higher edge = top priority
        signals.sort(key=lambda s: self._score_signal(s), reverse=True)

        self.logger.info(
            "time_decay_scan_complete",
            signals_found=len(signals),
            max_hours=round(max_hours, 0),
            urgency=round(self._urgency, 2),
        )
        return signals

    async def _evaluate_market(
        self, market: GammaMarket, now: datetime, max_hours: float
    ) -> TradeSignal | None:
        """Evaluate a single market for high-probability opportunity."""
        # Must have an end date
        end = market.end_date
        if end is None:
            return None
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        # Check hours to resolution (dynamic based on urgency)
        hours_left = (end - now).total_seconds() / 3600
        if hours_left <= 0 or hours_left > max_hours:
            return None

        # Check token IDs
        token_ids = market.token_ids
        if not token_ids:
            return None

        # Evaluate each outcome
        prices = market.outcome_price_list
        outcomes = market.outcomes
        if not prices or not outcomes:
            return None

        for i, (outcome, price) in enumerate(zip(outcomes, prices)):
            if i >= len(token_ids):
                break

            # Is this a high-probability outcome?
            if price < MIN_PRICE or price > self._max_price:
                continue

            # Estimate real probability
            estimated_prob = self._estimate_probability(price, hours_left)

            if estimated_prob < MIN_IMPLIED_PROB:
                continue

            edge_val = estimated_prob - price
            if edge_val < MIN_EDGE:
                continue

            # Calculate confidence based on multiple factors
            confidence = self._calculate_confidence(price, hours_left)

            # Apply category confidence from learner
            cat_confidence = self._confidence_adjustment.get(
                market.category, 1.0
            )
            confidence = min(0.99, confidence * cat_confidence)

            return TradeSignal(
                strategy=self.name,
                market_id=market.id,
                token_id=token_ids[i],
                question=market.question,
                side=OrderSide.BUY,
                outcome=outcome,
                estimated_prob=estimated_prob,
                market_price=price,
                edge=edge_val,
                size_usd=0.0,  # Will be set by risk manager
                confidence=confidence,
                reasoning=(
                    f"High-prob: {outcome} at ${price:.2f} with "
                    f"{hours_left:.0f}h to resolution. "
                    f"Est. prob: {estimated_prob:.1%}, Edge: {edge_val:.1%}"
                ),
                metadata={
                    "category": market.category,
                    "hours_to_resolution": hours_left,
                },
            )

        return None

    def _estimate_probability(
        self, market_price: float, hours_left: float
    ) -> float:
        """Estimate real probability from market data.

        High price on an active market = strong consensus.
        Closer to resolution = less uncertainty remains.
        """
        base_prob = market_price

        # Time factor: less time = price is more accurate (scales 0 to 0.05)
        time_factor = max(0, 1.0 - hours_left / HOURS_MEDIUM) * 0.05

        # Near-certainty bonus: very high price + close to resolution
        if market_price >= 0.95 and hours_left <= 24:
            near_certainty = 0.04
        elif market_price >= 0.93 and hours_left <= 72:
            near_certainty = 0.03
        elif market_price >= 0.90 and hours_left <= 168:
            near_certainty = 0.02
        else:
            near_certainty = 0.0

        estimated = base_prob + time_factor + near_certainty
        return min(0.99, estimated)

    def _calculate_confidence(self, price: float, hours_left: float) -> float:
        """Calculate strategy confidence (0-1).

        Strongly rewards shorter time horizons.
        """
        confidence = CONFIDENCE_BASE

        # Higher price = more confident
        if price >= 0.95:
            confidence += 0.10
        elif price >= 0.92:
            confidence += 0.06
        elif price >= 0.90:
            confidence += 0.03

        # Time-based confidence: shorter = much more confident
        if hours_left <= 12:
            confidence += 0.12   # Resolves in hours
        elif hours_left <= 24:
            confidence += 0.10   # Resolves today
        elif hours_left <= 48:
            confidence += 0.08   # Resolves tomorrow
        elif hours_left <= 72:
            confidence += 0.05   # 2-3 days
        elif hours_left <= 168:
            confidence += 0.02   # 3-7 days

        return min(0.99, confidence)

    @staticmethod
    def _score_signal(signal: TradeSignal) -> float:
        """Score a signal for sorting: shorter resolution + higher edge = better.

        Returns a composite score where time is weighted 60%, edge 40%.
        A market resolving in 6h with 2% edge beats one in 5 days with 3% edge.
        """
        hours = signal.metadata.get("hours_to_resolution", HOURS_MEDIUM)
        # Time score: 1.0 for immediate, 0.0 for 168h
        time_score = max(0.0, 1.0 - hours / HOURS_MEDIUM)
        # Edge score: normalized to ~0-1 range (3% = ~1.0)
        edge_score = min(1.0, signal.edge / 0.03)
        return time_score * 0.6 + edge_score * 0.4

    async def should_exit(self, market_id: str, current_price: float) -> bool:
        """Exit if price drops significantly (something unexpected happened)."""
        if current_price < 0.70:
            self.logger.warning(
                "time_decay_exit_triggered",
                market_id=market_id,
                price=current_price,
                reason="price_drop_below_threshold",
            )
            return True
        return False
