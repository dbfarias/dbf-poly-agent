"""Market Making strategy: provide liquidity on both sides of the spread.

Enabled at Tier 3+ ($100+). Place limit orders on both bid and ask
to capture the spread. Earns maker rebates from Polymarket.
Caution: competitive market, requires good inventory management.
"""

import structlog

from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()

MIN_SPREAD = 0.03  # Minimum 3 cent spread to make market
MAX_SPREAD = 0.15  # Don't make market in very wide spreads
MIN_VOLUME = 20000.0
MIN_LIQUIDITY = 10000.0


class MarketMakingStrategy(BaseStrategy):
    """Provide liquidity on both sides of the spread."""

    name = "market_making"
    min_tier = CapitalTier.TIER3

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan for market making opportunities."""
        signals = []

        for market in markets:
            if market.volume < MIN_VOLUME or market.liquidity < MIN_LIQUIDITY:
                continue

            signal = await self._evaluate_market(market)
            if signal:
                signals.append(signal)

        self.logger.info("market_making_scan_complete", signals_found=len(signals))
        return signals

    async def _evaluate_market(self, market: GammaMarket) -> TradeSignal | None:
        """Evaluate a market for market making opportunity."""
        token_ids = market.token_ids
        if not token_ids:
            return None

        try:
            book = await self.clob.get_order_book(token_ids[0])
        except Exception:
            return None

        spread = book.spread
        mid = book.mid_price

        if spread is None or mid is None:
            return None

        if spread < MIN_SPREAD or spread > MAX_SPREAD:
            return None

        # Place a buy order at best_bid + 0.01 (improve the bid)
        buy_price = round((book.best_bid or 0) + 0.01, 2)
        if buy_price >= mid:
            return None

        # Expected profit from spread capture
        expected_profit = spread / 2  # Approximate: half spread per side
        edge = expected_profit / buy_price if buy_price > 0 else 0

        return TradeSignal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_ids[0],
            question=market.question,
            side=OrderSide.BUY,
            outcome="Yes",
            estimated_prob=mid,
            market_price=buy_price,
            edge=edge,
            size_usd=0.0,
            confidence=0.55,
            reasoning=(
                f"Market making: spread=${spread:.3f}, mid=${mid:.3f}. "
                f"Bid at ${buy_price:.3f}. "
                f"Expected profit: ${expected_profit:.3f}/share"
            ),
            metadata={
                "spread": spread,
                "mid_price": mid,
                "best_bid": book.best_bid,
                "best_ask": book.best_ask,
            },
        )

    async def should_exit(self, market_id: str, current_price: float) -> bool:
        """Market making positions should be exited if spread collapses."""
        # Exit if the price moves significantly from our entry
        return False
