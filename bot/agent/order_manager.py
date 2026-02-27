"""Order lifecycle management: creation, monitoring, cancellation."""

from collections.abc import Awaitable, Callable
from datetime import datetime

import structlog

from bot.config import settings
from bot.data.activity import (
    log_order_expired,
    log_order_filled,
    log_order_placed,
    log_price_adjustment,
    log_signal_rejected,
)
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
        return {
            info["signal"].market_id
            for info in self._pending_orders.values()
            if info.get("signal") is not None
        }

    @property
    def pending_capital(self) -> float:
        """Total capital locked by pending CLOB orders."""
        return sum(
            info["signal"].size_usd
            for info in self._pending_orders.values()
            if info.get("signal") is not None
        )

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

        # Ensure minimum 5 shares (Polymarket CLOB minimum order size)
        min_shares = self.MIN_ORDER_SIZE if not self.clob.is_paper else 1.0
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

        await log_order_placed(
            strategy=signal.strategy,
            market_id=signal.market_id,
            question=signal.question,
            side=signal.side.value,
            price=actual_price,
            size_usd=signal.size_usd,
            shares=shares,
            status=trade.status,
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
        if not address:
            logger.warning("monitor_orders_skipped_no_address")
            return

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
            is_sell = info.get("is_sell", False)
            age = (now - info["created_at"]).total_seconds()

            # Determine token_id from signal or stored field
            token_id = info.get("token_id") or (signal.token_id if signal else None)
            if not token_id:
                continue

            # For SELL orders: position GONE from Polymarket = filled
            # For BUY orders: position EXISTS on Polymarket = filled
            if is_sell:
                fill_confirmed = token_id not in real_token_ids
            else:
                fill_confirmed = token_id in real_token_ids

            if fill_confirmed:
                async with async_session() as session:
                    repo = TradeRepository(session)
                    await repo.update_status(info["trade_id"], "filled")
                to_remove.append(order_id)
                logger.info(
                    "order_fill_verified",
                    order_id=order_id,
                    trade_id=info["trade_id"],
                    token_id=token_id[:20],
                    is_sell=is_sell,
                )
                if signal:
                    await log_order_filled(
                        market_id=signal.market_id,
                        order_id=order_id,
                        strategy=signal.strategy,
                    )

                # Create position via callback (BUY orders only)
                if not is_sell and self._on_fill_callback and signal:
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
                    is_sell=is_sell,
                )
                if signal:
                    await log_order_expired(
                        market_id=signal.market_id,
                        order_id=order_id,
                        age_seconds=age,
                    )

        for oid in to_remove:
            self._pending_orders.pop(oid, None)

    MIN_ORDER_SIZE = 5.0  # Polymarket CLOB minimum order size

    async def close_position(
        self,
        market_id: str,
        token_id: str,
        size: float,
        current_price: float,
        question: str = "",
        outcome: str = "",
        category: str = "",
        strategy: str = "exit",
    ) -> Trade | None:
        """Close a position by selling."""
        # Polymarket requires minimum 5 shares — reject if below
        if not self.clob.is_paper and size < self.MIN_ORDER_SIZE:
            logger.warning(
                "position_too_small_to_sell",
                market_id=market_id,
                size=size,
                min_size=self.MIN_ORDER_SIZE,
            )
            return None

        # Get best bid for immediate fill
        sell_price = current_price
        if not self.clob.is_paper:
            try:
                book = await self.clob.get_order_book(token_id)
                if book.best_bid is not None:
                    sell_price = book.best_bid
            except Exception as e:
                logger.warning(
                    "close_position_orderbook_failed",
                    token_id=token_id,
                    error=str(e),
                )

        result = await self.clob.place_order(
            token_id=token_id,
            side=OrderSide.SELL,
            price=sell_price,
            size=size,
        )

        if "error" in result:
            logger.warning("close_order_rejected", error=result["error"])
            return None

        if result.get("success") is False:
            logger.warning(
                "close_order_api_rejected",
                error=result.get("errorMsg", "unknown"),
            )
            return None

        order_id = result.get("orderID", result.get("order_id", ""))
        trade = Trade(
            market_id=market_id,
            token_id=token_id,
            question=question,
            outcome=outcome,
            category=category,
            order_id=order_id,
            side=OrderSide.SELL.value,
            price=sell_price,
            size=size,
            filled_size=size if self.clob.is_paper else 0,
            cost_usd=size * sell_price,
            strategy=strategy,
            status="filled" if self.clob.is_paper else "pending",
            is_paper=settings.is_paper,
        )

        async with async_session() as session:
            repo = TradeRepository(session)
            trade = await repo.create(trade)

        # Track pending SELL orders for monitoring (live unfilled only)
        is_filled = self.clob.is_paper or str(result.get("status", "")).upper() == "MATCHED"
        if not is_filled and order_id:
            self._pending_orders[order_id] = {
                "trade_id": trade.id,
                "created_at": datetime.utcnow(),
                "signal": None,  # No signal for SELL orders
                "shares": size,
                "is_sell": True,
                "token_id": token_id,
            }

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
                    await log_signal_rejected(
                        strategy=signal.strategy,
                        market_id=signal.market_id,
                        question=signal.question,
                        reason=(
                            f"Excessive slippage: ${slippage:.3f}"
                            f" (ask ${actual_price:.3f} vs signal"
                            f" ${signal.market_price:.3f})"
                        ),
                        edge=signal.edge,
                        price=actual_price,
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
                    await log_signal_rejected(
                        strategy=signal.strategy,
                        market_id=signal.market_id,
                        question=signal.question,
                        reason=(
                            f"Edge evaporated at ask:"
                            f" {adjusted_edge:.1%} <"
                            f" {min_edge_after_slippage:.1%}"
                            f" (ask ${actual_price:.3f})"
                        ),
                        edge=adjusted_edge,
                        price=actual_price,
                    )
                    return None

                logger.info(
                    "price_adjusted_to_ask",
                    market_id=signal.market_id,
                    signal_price=signal.market_price,
                    ask_price=actual_price,
                    slippage=slippage,
                )
                await log_price_adjustment(
                    market_id=signal.market_id,
                    strategy=signal.strategy,
                    signal_price=signal.market_price,
                    actual_price=actual_price,
                    reason="Adjusted to CLOB ask price",
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
