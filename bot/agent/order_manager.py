"""Order lifecycle management: creation, monitoring, cancellation."""

from collections.abc import Awaitable, Callable
from datetime import datetime

import structlog

from bot.config import settings
from bot.data.database import async_session
from bot.data.models import Trade
from bot.data.repositories import TradeRepository
from bot.polymarket.client import PolymarketClient
from bot.polymarket.data_api import DataApiClient
from bot.polymarket.types import OrderSide, TradeSignal
from bot.utils.notifications import notify_trade

logger = structlog.get_logger()

ORDER_TIMEOUT_SECONDS = 300  # Cancel unfilled orders after 5 minutes

# Callback type: (signal, shares) -> None
OnFillCallback = Callable[[TradeSignal, float], Awaitable[None]]


class OrderManager:
    """Manages the full lifecycle of orders."""

    def __init__(self, clob_client: PolymarketClient, data_api: DataApiClient):
        self.clob = clob_client
        self.data_api = data_api
        self._pending_orders: dict[str, dict] = {}
        self._on_fill_callback: OnFillCallback | None = None

    def set_on_fill_callback(self, callback: OnFillCallback) -> None:
        """Set callback invoked when a pending order is confirmed filled."""
        self._on_fill_callback = callback

    @property
    def pending_market_ids(self) -> set[str]:
        """Market IDs with pending orders on the CLOB."""
        return {info["signal"].market_id for info in self._pending_orders.values()}

    @property
    def pending_capital(self) -> float:
        """Total capital locked by pending CLOB orders."""
        return sum(info["signal"].size_usd for info in self._pending_orders.values())

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

        # Adjust price to order book ask/bid for immediate fills
        actual_price = await self._get_fill_price(signal)
        if actual_price is None:
            return None

        shares = signal.size_usd / actual_price

        # Ensure minimum shares for live mode (Polymarket requires >= 5)
        min_shares = 5.0 if not self.clob.is_paper else 1.0
        if shares < min_shares:
            shares = min_shares
            signal.size_usd = shares * actual_price
            logger.info(
                "size_bumped_to_min_shares",
                shares=shares,
                actual_price=actual_price,
                adjusted_size_usd=signal.size_usd,
            )

        # Place the order
        result = await self.clob.place_order(
            token_id=signal.token_id,
            side=signal.side,
            price=actual_price,
            size=round(shares, 2),
        )

        if "error" in result:
            logger.warning("order_rejected", error=result["error"])
            return None

        # Check for API-level failure
        if result.get("success") is False:
            logger.warning(
                "order_api_rejected",
                error=result.get("errorMsg", "unknown"),
            )
            return None

        order_id = result.get("orderID", result.get("order_id", ""))

        # Determine fill status from API response
        # Polymarket CLOB returns status: "matched" for immediate fills
        is_filled = (
            self.clob.is_paper
            or str(result.get("status", "")).upper() == "MATCHED"
        )

        # Record in database
        trade = Trade(
            market_id=signal.market_id,
            token_id=signal.token_id,
            question=signal.question,
            outcome=signal.outcome,
            order_id=order_id,
            side=signal.side.value,
            price=actual_price,
            size=shares,
            filled_size=shares if is_filled else 0,
            cost_usd=signal.size_usd,
            strategy=signal.strategy,
            edge=signal.edge,
            estimated_prob=signal.estimated_prob,
            confidence=signal.confidence,
            reasoning=signal.reasoning,
            status="filled" if is_filled else "pending",
            is_paper=settings.is_paper,
        )

        async with async_session() as session:
            repo = TradeRepository(session)
            trade = await repo.create(trade)

        # Track pending orders for monitoring (only live unfilled orders)
        if not is_filled:
            self._pending_orders[order_id] = {
                "trade_id": trade.id,
                "created_at": datetime.utcnow(),
                "signal": signal,
                "shares": shares,
            }

        # Send notification
        await notify_trade(
            action="opened" if is_filled else "pending",
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
            is_filled=is_filled,
        )
        return trade

    async def monitor_orders(self) -> None:
        """Check pending orders and verify fills against Polymarket.

        Instead of assuming 'not in open orders = filled', we verify
        by checking actual positions on Polymarket via the data API.
        """
        if not self._pending_orders:
            return

        # Fetch actual positions from Polymarket to verify fills
        address = self.clob.get_address()
        real_token_ids: set[str] = set()
        if address:
            try:
                positions = await self.data_api.get_positions(address)
                real_token_ids = {p.token_id for p in positions if p.size > 0}
            except Exception as e:
                logger.error("fill_verification_failed", error=str(e))
                return

        now = datetime.utcnow()
        to_remove = []

        for order_id, info in self._pending_orders.items():
            signal = info["signal"]
            age = (now - info["created_at"]).total_seconds()

            # Check if position actually exists on Polymarket
            if signal.token_id in real_token_ids:
                async with async_session() as session:
                    repo = TradeRepository(session)
                    await repo.update_status(info["trade_id"], "filled")
                to_remove.append(order_id)
                logger.info(
                    "order_fill_verified",
                    order_id=order_id,
                    trade_id=info["trade_id"],
                    token_id=signal.token_id[:20],
                )

                # Create position via callback
                if self._on_fill_callback:
                    try:
                        await self._on_fill_callback(signal, info["shares"])
                    except Exception as e:
                        logger.error(
                            "on_fill_callback_failed",
                            order_id=order_id,
                            error=str(e),
                        )
                continue

            # Check for timeout - order not filled within time limit
            if age > ORDER_TIMEOUT_SECONDS:
                # Try to cancel (might already be gone)
                await self.clob.cancel_order(order_id)
                async with async_session() as session:
                    repo = TradeRepository(session)
                    await repo.update_status(info["trade_id"], "expired")
                to_remove.append(order_id)
                logger.info(
                    "order_expired_no_fill",
                    order_id=order_id,
                    trade_id=info["trade_id"],
                    age_seconds=age,
                )

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

    async def _get_fill_price(self, signal: TradeSignal) -> float | None:
        """Get the actual price from the order book to ensure fills.

        For BUY orders: use best_ask (price someone is willing to sell at).
        For SELL orders: use best_bid (price someone is willing to buy at).
        Falls back to signal.market_price in paper mode or on error.

        Returns None if slippage too high or edge evaporates at real price.
        """
        if self.clob.is_paper:
            return signal.market_price

        max_slippage = 0.03  # 3 cents max slippage from signal price
        min_edge_after_slippage = 0.005  # 0.5% minimum edge at real price

        try:
            book = await self.clob.get_order_book(signal.token_id)

            if signal.side == OrderSide.BUY and book.best_ask is not None:
                actual_price = book.best_ask
                slippage = actual_price - signal.market_price

                if slippage > max_slippage:
                    logger.info(
                        "excessive_slippage",
                        market_id=signal.market_id,
                        signal_price=signal.market_price,
                        ask_price=actual_price,
                        slippage=slippage,
                    )
                    return None

                # Verify edge still exists at actual fill price
                adjusted_edge = signal.estimated_prob - actual_price
                if adjusted_edge < min_edge_after_slippage:
                    logger.info(
                        "edge_evaporated_at_ask",
                        market_id=signal.market_id,
                        signal_price=signal.market_price,
                        ask_price=actual_price,
                        original_edge=signal.edge,
                        adjusted_edge=adjusted_edge,
                    )
                    return None

                logger.info(
                    "price_adjusted_to_ask",
                    market_id=signal.market_id,
                    signal_price=signal.market_price,
                    ask_price=actual_price,
                    slippage=slippage,
                )
                return actual_price

            elif signal.side == OrderSide.SELL and book.best_bid is not None:
                actual_price = book.best_bid
                slippage = signal.market_price - actual_price

                if slippage > max_slippage:
                    logger.info(
                        "excessive_slippage_sell",
                        market_id=signal.market_id,
                        signal_price=signal.market_price,
                        bid_price=actual_price,
                        slippage=slippage,
                    )
                    return None

                logger.info(
                    "price_adjusted_to_bid",
                    market_id=signal.market_id,
                    signal_price=signal.market_price,
                    bid_price=actual_price,
                    slippage=slippage,
                )
                return actual_price

            # No asks/bids available — illiquid market
            logger.info(
                "no_orderbook_depth",
                market_id=signal.market_id,
                side=signal.side.value,
            )
            return None

        except Exception as e:
            logger.warning(
                "orderbook_price_adjustment_failed",
                market_id=signal.market_id,
                error=str(e),
            )
            # Fall back to signal price on error
            return signal.market_price
