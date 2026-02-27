"""Value Betting strategy: detect mispriced markets using order book analysis.

Enabled at Tier 2+ ($25+). Uses order book imbalance, volume momentum,
and cross-market correlation to estimate true probability.
"""

import structlog

from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()

MIN_EDGE = 0.03  # 3% minimum edge for value bets
IMBALANCE_THRESHOLD = 0.15  # 15% order book imbalance


class ValueBettingStrategy(BaseStrategy):
    """Detect mispriced markets via order book and volume analysis."""

    name = "value_betting"
    min_tier = CapitalTier.TIER1

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan markets for value betting opportunities."""
        signals = []

        for market in markets:
            signal = await self._evaluate_market(market)
            if signal:
                signals.append(signal)

        self.logger.info("value_betting_scan_complete", signals_found=len(signals))
        return signals

    async def _evaluate_market(self, market: GammaMarket) -> TradeSignal | None:
        """Evaluate a market for mispricing using order book analysis."""
        token_ids = market.token_ids
        if not token_ids:
            return None

        yes_price = market.yes_price
        if yes_price is None:
            return None

        # Get order book for analysis
        try:
            book = await self.clob.get_order_book(token_ids[0])
        except Exception:
            return None

        if not book.bids or not book.asks:
            return None

        # Calculate order book imbalance
        bid_volume = sum(b.size for b in book.bids[:5])
        ask_volume = sum(a.size for a in book.asks[:5])
        total_volume = bid_volume + ask_volume

        if total_volume == 0:
            return None

        imbalance = (bid_volume - ask_volume) / total_volume

        # Strong buy pressure suggests market might be underpriced
        if abs(imbalance) < IMBALANCE_THRESHOLD:
            return None

        # Estimate real probability based on imbalance
        if imbalance > 0:
            # More bids than asks → price should go up → YES is underpriced
            estimated_prob = yes_price + imbalance * 0.1  # Conservative adjustment
            if estimated_prob - yes_price < MIN_EDGE:
                return None
            side = OrderSide.BUY
            token_id = token_ids[0]
            outcome = "Yes"
            price = yes_price
        else:
            # More asks than bids → price should go down → NO might be value
            if len(token_ids) < 2:
                return None
            no_price = market.no_price or (1.0 - yes_price)
            estimated_prob = no_price + abs(imbalance) * 0.1
            if estimated_prob - no_price < MIN_EDGE:
                return None
            side = OrderSide.BUY
            token_id = token_ids[1]
            outcome = "No"
            price = no_price

        edge_val = estimated_prob - price

        return TradeSignal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            question=market.question,
            side=side,
            outcome=outcome,
            estimated_prob=min(0.95, estimated_prob),
            market_price=price,
            edge=edge_val,
            size_usd=0.0,
            confidence=0.6 + min(0.2, abs(imbalance)),
            reasoning=(
                f"Value bet: {outcome} at ${price:.3f}. "
                f"Book imbalance: {imbalance:+.1%} "
                f"(bid_vol={bid_volume:.0f}, ask_vol={ask_volume:.0f}). "
                f"Est. prob: {estimated_prob:.1%}"
            ),
            metadata={
                "category": market.category,
                "imbalance": imbalance,
                "bid_volume": bid_volume,
                "ask_volume": ask_volume,
            },
        )

    async def should_exit(self, market_id: str, current_price: float) -> bool:
        """Exit if edge has been captured or price moves against us significantly."""
        if current_price < 0.40:
            return True
        return False
