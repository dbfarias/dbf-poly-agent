"""Time Decay strategy: buy near-certain outcomes close to resolution.

Core idea: Markets that resolve within <48h with high implied probability
(>85%) tend to resolve as expected. Buy YES tokens at 0.90-0.97, collect
$1.00 at resolution for 3-10% profit.

Example: "BTC above $50K on March 1?" with BTC at $95K on Feb 28.
YES is trading at $0.97. Buy YES, wait for resolution, collect $1.00.
"""

from datetime import datetime, timezone

import structlog

from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()

# Strategy parameters
MAX_HOURS_TO_RESOLUTION = 48.0
MIN_IMPLIED_PROB = 0.85
MAX_PRICE = 0.97  # Don't buy above this (too little profit)
MIN_PRICE = 0.80  # Don't buy below this (too uncertain)
MIN_VOLUME = 5000.0  # Minimum market volume
MIN_LIQUIDITY = 1000.0  # Minimum market liquidity
CONFIDENCE_BASE = 0.80  # Base confidence for this strategy


class TimeDecayStrategy(BaseStrategy):
    """Buy near-certain outcomes close to market resolution."""

    name = "time_decay"
    min_tier = CapitalTier.TIER1

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan for near-resolution markets with high-probability outcomes."""
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
        """Evaluate a single market for time decay opportunity."""
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

        # Check volume and liquidity
        if market.volume < MIN_VOLUME or market.liquidity < MIN_LIQUIDITY:
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
            if price < MIN_PRICE or price > MAX_PRICE:
                continue

            # Estimate real probability
            # Near resolution, high price = very likely outcome
            # The closer to resolution and higher the price, the more certain
            estimated_prob = self._estimate_probability(price, hours_left, market.volume)

            if estimated_prob < MIN_IMPLIED_PROB:
                continue

            edge_val = estimated_prob - price
            if edge_val < 0.02:  # Need at least 2% edge
                continue

            # Calculate confidence based on multiple factors
            confidence = self._calculate_confidence(
                price, hours_left, market.volume, market.liquidity
            )

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
                    f"Time decay: {outcome} at ${price:.2f} with "
                    f"{hours_left:.1f}h to resolution. "
                    f"Est. prob: {estimated_prob:.1%}, Edge: {edge_val:.1%}"
                ),
                metadata={
                    "hours_to_resolution": hours_left,
                    "volume": market.volume,
                    "liquidity": market.liquidity,
                },
            )

        return None

    def _estimate_probability(
        self, market_price: float, hours_left: float, volume: float
    ) -> float:
        """Estimate real probability from market data.

        Higher volume markets are more efficient, so price ≈ probability.
        As resolution nears, remaining uncertainty decreases.
        """
        # Start with market price as base
        base_prob = market_price

        # Time factor: less time = price is more accurate
        time_factor = max(0, 1.0 - hours_left / MAX_HOURS_TO_RESOLUTION) * 0.02

        # Volume factor: higher volume = more efficient pricing
        volume_factor = min(0.02, (volume / 100000) * 0.02)

        # Conservative estimate: slightly above market price
        estimated = base_prob + time_factor + volume_factor
        return min(0.99, estimated)

    def _calculate_confidence(
        self, price: float, hours_left: float, volume: float, liquidity: float
    ) -> float:
        """Calculate strategy confidence (0-1)."""
        confidence = CONFIDENCE_BASE

        # Higher price = more confident
        if price >= 0.95:
            confidence += 0.10
        elif price >= 0.90:
            confidence += 0.05

        # Less time = more confident
        if hours_left <= 12:
            confidence += 0.05
        elif hours_left <= 24:
            confidence += 0.02

        # Higher volume = more confident
        if volume >= 50000:
            confidence += 0.05
        elif volume >= 10000:
            confidence += 0.02

        return min(0.99, confidence)

    async def should_exit(self, market_id: str, current_price: float) -> bool:
        """Exit if price drops significantly (something unexpected happened)."""
        # If price drops below 0.70, something is wrong — exit
        if current_price < 0.70:
            self.logger.warning(
                "time_decay_exit_triggered",
                market_id=market_id,
                price=current_price,
                reason="price_drop_below_threshold",
            )
            return True
        return False
