"""Market Making strategy: provide liquidity on both sides of the spread.

Enabled at Tier 3+ ($100+). Place limit orders on both bid and ask
to capture the spread. Earns maker rebates from Polymarket.
Caution: competitive market, requires good inventory management.
"""

import re

import structlog

from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()

MIN_SPREAD = 0.03  # Minimum 3 cent spread to make market
MAX_SPREAD = 0.15  # Don't make market in very wide spreads


class MarketMakingStrategy(BaseStrategy):
    """Provide liquidity on both sides of the spread."""

    name = "market_making"

    # MM positions are managed actively
    MIN_HOLD_SECONDS = 60  # 1 min

    _MUTABLE_PARAMS = {
        "MIN_SPREAD": {"type": float, "min": 0.0, "max": 0.5},
        "MAX_SPREAD": {"type": float, "min": 0.0, "max": 0.5},
        "MIN_HOLD_SECONDS": {"type": int, "min": 0, "max": 14400},
        "TAKE_PROFIT_PCT": {"type": float, "min": 0.01, "max": 0.50},
        "STOP_LOSS_PCT": {"type": float, "min": 0.01, "max": 0.30},
        "SWING_EXIT_PRICE": {"type": float, "min": 0.50, "max": 0.95},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.MIN_SPREAD = MIN_SPREAD
        self.MAX_SPREAD = MAX_SPREAD

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan for market making opportunities."""
        signals = []

        for market in markets:
            signal = await self._evaluate_market(market)
            if signal:
                signals.append(signal)

        self.logger.info("market_making_scan_complete", signals_found=len(signals))
        return signals

    # Reject short-term crypto markets — they resolve to $0/$1 before
    # we can capture spread. Market making needs time to exit.
    _CRYPTO_SHORT_REJECT = re.compile(
        r"\b(bitcoin|btc|ethereum|eth|solana|sol)\b.*\b(up or down)\b"
        r"|\b(up or down)\b.*\b(bitcoin|btc|ethereum|eth|solana|sol)\b",
        re.IGNORECASE,
    )

    async def _evaluate_market(self, market: GammaMarket) -> TradeSignal | None:
        """Evaluate a market for market making opportunity."""
        # Skip crypto Up/Down short-term markets (resolve in minutes)
        if self._CRYPTO_SHORT_REJECT.search(market.question):
            return None

        # Skip sports markets — MM has no sports knowledge, only sees spread.
        # Sports should be traded by strategies that use odds data.
        from bot.research.sports_fetcher import is_sports_market
        if is_sports_market(market.question):
            return None

        token_ids = market.token_ids
        if not token_ids:
            return None

        try:
            book = await self.get_order_book(token_ids[0])
        except Exception:
            return None

        spread = book.spread
        mid = book.mid_price

        if spread is None or mid is None:
            return None

        if spread < self.MIN_SPREAD or spread > self.MAX_SPREAD:
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
                "category": market.category,
                "spread": spread,
                "mid_price": mid,
                "best_bid": book.best_bid,
                "best_ask": book.best_ask,
            },
        )

    # Exit parameters
    TAKE_PROFIT_PCT = 0.30  # 30% gain → sell
    STOP_LOSS_PCT = 0.15  # 15% loss → cut
    SWING_EXIT_PRICE = 0.65  # Absolute price threshold for locking gains

    async def should_exit(self, market_id: str, current_price: float, **kwargs) -> str | bool:
        """Exit on take-profit, swing exit, or stop-loss."""
        avg_price = kwargs.get("avg_price", 0.0)
        if avg_price <= 0:
            return False

        profit_pct = (current_price - avg_price) / avg_price

        # Stop-loss
        if profit_pct <= -self.STOP_LOSS_PCT:
            logger.warning(
                "mm_exit_stop_loss",
                market_id=market_id,
                loss_pct=f"{profit_pct:.1%}",
            )
            return "stop_loss"

        # Swing exit: lock in gains at high absolute price
        if current_price >= self.SWING_EXIT_PRICE and profit_pct > 0:
            logger.info(
                "mm_exit_swing",
                market_id=market_id,
                current_price=current_price,
                profit_pct=f"{profit_pct:.1%}",
            )
            return "swing_exit"

        # Take-profit on % gain
        if profit_pct >= self.TAKE_PROFIT_PCT:
            logger.info(
                "mm_exit_take_profit",
                market_id=market_id,
                profit_pct=f"{profit_pct:.1%}",
            )
            return "take_profit"

        return False
