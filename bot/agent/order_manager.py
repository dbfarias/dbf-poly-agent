"""Order lifecycle management: creation, monitoring, cancellation."""

from datetime import datetime

import structlog

from bot.config import settings
from bot.data.database import async_session
from bot.data.models import Trade
from bot.data.repositories import TradeRepository
from bot.polymarket.client import PolymarketClient
from bot.polymarket.types import OrderSide, TradeSignal
from bot.utils.notifications import notify_trade

logger = structlog.get_logger()

ORDER_TIMEOUT_SECONDS = 300  # Cancel unfilled orders after 5 minutes


class OrderManager:
    """Manages the full lifecycle of orders."""

    def __init__(self, clob_client: PolymarketClient):
        self.clob = clob_client
        self._pending_orders: dict[str, dict] = {}

    async def execute_signal(self, signal: TradeSignal) -> Trade | None:
        """Execute a trade signal by placing an order."""
        logger.info(
            "executing_signal",
            strategy=signal.strategy,
            market_id=signal.market_id,
            side=signal.side.value,
            price=signal.market_price,
            size=signal.size_usd,
        )

        # Calculate order size in shares
        if signal.market_price <= 0:
            logger.error("invalid_price", price=signal.market_price)
            return None

        shares = signal.size_usd / signal.market_price

        # Place the order
        result = await self.clob.place_order(
            token_id=signal.token_id,
            side=signal.side,
            price=signal.market_price,
            size=round(shares, 2),
        )

        if "error" in result:
            logger.warning("order_rejected", error=result["error"])
            return None

        order_id = result.get("orderID", result.get("order_id", ""))

        # Record in database
        trade = Trade(
            market_id=signal.market_id,
            token_id=signal.token_id,
            question=signal.question,
            outcome=signal.outcome,
            order_id=order_id,
            side=signal.side.value,
            price=signal.market_price,
            size=shares,
            filled_size=result.get("filled_size", shares if self.clob.is_paper else 0),
            cost_usd=signal.size_usd,
            strategy=signal.strategy,
            edge=signal.edge,
            estimated_prob=signal.estimated_prob,
            confidence=signal.confidence,
            reasoning=signal.reasoning,
            status="filled" if self.clob.is_paper else "pending",
            is_paper=settings.is_paper,
        )

        async with async_session() as session:
            repo = TradeRepository(session)
            trade = await repo.create(trade)

        # Track pending orders for monitoring
        if not self.clob.is_paper:
            self._pending_orders[order_id] = {
                "trade_id": trade.id,
                "created_at": datetime.utcnow(),
                "signal": signal,
            }

        # Send notification
        await notify_trade(
            action="opened",
            strategy=signal.strategy,
            question=signal.question,
            side=signal.side.value,
            price=signal.market_price,
            size=signal.size_usd,
        )

        logger.info(
            "trade_recorded",
            trade_id=trade.id,
            order_id=order_id,
            status=trade.status,
        )
        return trade

    async def monitor_orders(self) -> None:
        """Check pending orders and update their status."""
        if not self._pending_orders:
            return

        try:
            open_orders = await self.clob.get_open_orders()
            open_order_ids = {o.get("orderID", o.get("order_id", "")) for o in open_orders}
        except Exception as e:
            logger.error("order_monitor_failed", error=str(e))
            return

        now = datetime.utcnow()
        to_remove = []

        for order_id, info in self._pending_orders.items():
            # Check if order was filled (no longer in open orders)
            if order_id not in open_order_ids:
                async with async_session() as session:
                    repo = TradeRepository(session)
                    await repo.update_status(info["trade_id"], "filled")
                to_remove.append(order_id)
                logger.info("order_filled", order_id=order_id, trade_id=info["trade_id"])
                continue

            # Check for timeout
            age = (now - info["created_at"]).total_seconds()
            if age > ORDER_TIMEOUT_SECONDS:
                await self.clob.cancel_order(order_id)
                async with async_session() as session:
                    repo = TradeRepository(session)
                    await repo.update_status(info["trade_id"], "cancelled")
                to_remove.append(order_id)
                logger.info("order_timed_out", order_id=order_id, age_seconds=age)

        for oid in to_remove:
            self._pending_orders.pop(oid, None)

    async def close_position(
        self, market_id: str, token_id: str, size: float, current_price: float
    ) -> Trade | None:
        """Close a position by selling."""
        result = await self.clob.place_order(
            token_id=token_id,
            side=OrderSide.SELL,
            price=current_price,
            size=size,
        )

        if "error" in result:
            logger.warning("close_order_rejected", error=result["error"])
            return None

        order_id = result.get("orderID", result.get("order_id", ""))
        trade = Trade(
            market_id=market_id,
            token_id=token_id,
            order_id=order_id,
            side=OrderSide.SELL.value,
            price=current_price,
            size=size,
            filled_size=size if self.clob.is_paper else 0,
            cost_usd=size * current_price,
            strategy="exit",
            status="filled" if self.clob.is_paper else "pending",
            is_paper=settings.is_paper,
        )

        async with async_session() as session:
            repo = TradeRepository(session)
            trade = await repo.create(trade)

        return trade

    @property
    def pending_count(self) -> int:
        return len(self._pending_orders)
