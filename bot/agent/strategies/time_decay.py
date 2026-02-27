"""High-probability strategy: buy outcomes with strong implied probability.

Core idea: Markets with high implied probability (>90%) tend to resolve
as expected. Buy YES tokens at 0.90-0.97, collect $1.00 at resolution
for 3-10% profit. Closer to resolution = higher confidence.

Works across all timeframes but assigns higher confidence and edge
to markets closer to resolution.
"""

from datetime import datetime, timezone

import structlog

from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()

# Strategy parameters
MAX_HOURS_TO_RESOLUTION = 168.0  # 7 days — short-term focus for daily profit targets
MIN_IMPLIED_PROB = 0.70
MAX_PRICE = 0.97  # Don't buy above this (too little profit margin)
MIN_PRICE = 0.60  # Include medium-probability markets for volume + better risk/reward
MIN_EDGE = 0.015  # 1.5% minimum edge
CONFIDENCE_BASE = 0.75  # Base confidence for this strategy


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

    def adjust_params(self, adjustments: dict) -> None:
        """Apply learner adjustments to time decay parameters.

        Accepts:
        - edge_multipliers: dict[(strategy, category), float]
        - category_confidences: dict[category, float]
        - calibration: dict[bucket, float]
        """
        calibration = adjustments.get("calibration", {})

        # If high-confidence trades are poorly calibrated, tighten MAX_PRICE
        high_cal = calibration.get("95-99", 1.0)
        if high_cal < 0.7:
            # Overconfident at high prices — lower max to avoid near-certainty traps
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

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan for high-probability outcomes."""
        signals = []
        now = datetime.now(timezone.utc)

        for market in markets:
            signal = await self._evaluate_market(market, now)
            if signal:
                signals.append(signal)

        # Sort by edge (highest first)
        signals.sort(key=lambda s: s.edge, reverse=True)
        self.logger.info("time_decay_scan_complete", signals_found=len(signals))
        return signals

    async def _evaluate_market(
        self, market: GammaMarket, now: datetime
    ) -> TradeSignal | None:
        """Evaluate a single market for high-probability opportunity."""
        # Must have an end date
        end = market.end_date
        if end is None:
            return None
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        # Check hours to resolution
        hours_left = (end - now).total_seconds() / 3600
        if hours_left <= 0 or hours_left > MAX_HOURS_TO_RESOLUTION:
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

        # Time factor: less time = price is more accurate (scales 0 to 0.04)
        time_factor = max(0, 1.0 - hours_left / MAX_HOURS_TO_RESOLUTION) * 0.04

        # Near-certainty bonus: very high price + close to resolution
        if market_price >= 0.95 and hours_left <= 72:
            near_certainty = 0.03
        elif market_price >= 0.93 and hours_left <= 168:
            near_certainty = 0.02
        else:
            near_certainty = 0.0

        estimated = base_prob + time_factor + near_certainty
        return min(0.99, estimated)

    def _calculate_confidence(self, price: float, hours_left: float) -> float:
        """Calculate strategy confidence (0-1)."""
        confidence = CONFIDENCE_BASE

        # Higher price = more confident
        if price >= 0.95:
            confidence += 0.10
        elif price >= 0.92:
            confidence += 0.06
        elif price >= 0.90:
            confidence += 0.03

        # Less time = more confident
        if hours_left <= 48:
            confidence += 0.08
        elif hours_left <= 168:  # 1 week
            confidence += 0.04
        elif hours_left <= 336:  # 2 weeks
            confidence += 0.02

        return min(0.99, confidence)

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
