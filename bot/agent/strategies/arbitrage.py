"""Arbitrage strategy: exploit pricing inconsistencies.

Types of arbitrage:
1. YES+NO < $1.00 on the same market (guaranteed profit)
2. Multi-outcome sum != $1.00
3. Correlated markets with inconsistent pricing
"""

import structlog

from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()

MIN_ARB_EDGE = 0.01  # Minimum 1% edge for arbitrage
MIN_VOLUME = 1000.0


class ArbitrageStrategy(BaseStrategy):
    """Detect and exploit pricing arbitrage opportunities."""

    name = "arbitrage"
    min_tier = CapitalTier.TIER1

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan for arbitrage opportunities."""
        signals = []

        for market in markets:
            signal = self._check_yes_no_arb(market)
            if signal:
                signals.append(signal)

        self.logger.info("arbitrage_scan_complete", signals_found=len(signals))
        return signals

    def _check_yes_no_arb(self, market: GammaMarket) -> TradeSignal | None:
        """Check if YES + NO < $1.00 (or significantly so after fees)."""
        yes_price = market.yes_price
        no_price = market.no_price
        token_ids = market.token_ids

        if yes_price is None or no_price is None or len(token_ids) < 2:
            return None

        if market.volume < MIN_VOLUME:
            return None

        total = yes_price + no_price

        # If sum < 1.00, buying both guarantees profit
        if total < (1.0 - MIN_ARB_EDGE):
            edge = 1.0 - total
            # Buy the cheaper side
            if yes_price <= no_price:
                buy_side = OrderSide.BUY
                token_id = token_ids[0]
                price = yes_price
                outcome = "Yes"
            else:
                buy_side = OrderSide.BUY
                token_id = token_ids[1]
                price = no_price
                outcome = "No"

            return TradeSignal(
                strategy=self.name,
                market_id=market.id,
                token_id=token_id,
                question=market.question,
                side=buy_side,
                outcome=outcome,
                estimated_prob=1.0 - price + edge / 2,
                market_price=price,
                edge=edge,
                size_usd=0.0,
                confidence=0.95,  # Arbitrage is high confidence
                reasoning=(
                    f"YES+NO arbitrage: YES=${yes_price:.3f} + NO=${no_price:.3f} = "
                    f"${total:.3f}. Gap: ${edge:.3f} ({edge:.1%})"
                ),
                metadata={"arb_type": "yes_no", "total_price": total},
            )

        return None

    async def should_exit(self, market_id: str, current_price: float) -> bool:
        """Arbitrage positions should be held to resolution."""
        return False
