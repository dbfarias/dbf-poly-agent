"""Comprehensive tests for OrderManager — order lifecycle management."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.order_manager import ORDER_TIMEOUT_SECONDS, OrderManager
from bot.data.models import Trade
from bot.polymarket.types import (
    OrderBook,
    OrderBookEntry,
    OrderSide,
    TradeSignal,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_signal(
    strategy: str = "time_decay",
    market_id: str = "mkt1",
    token_id: str = "token1",
    question: str = "Will X happen?",
    outcome: str = "Yes",
    side: OrderSide = OrderSide.BUY,
    estimated_prob: float = 0.92,
    market_price: float = 0.86,
    edge: float = 0.06,
    size_usd: float = 5.0,
    confidence: float = 0.85,
    metadata: dict | None = None,
) -> TradeSignal:
    return TradeSignal(
        strategy=strategy,
        market_id=market_id,
        token_id=token_id,
        question=question,
        outcome=outcome,
        side=side,
        estimated_prob=estimated_prob,
        market_price=market_price,
        edge=edge,
        size_usd=size_usd,
        confidence=confidence,
        metadata=metadata or {"category": "crypto"},
    )


def make_trade(
    trade_id: int = 1,
    market_id: str = "mkt1",
    token_id: str = "token1",
    status: str = "filled",
    side: str = "BUY",
    price: float = 0.86,
    size: float = 5.81,
    order_id: str = "order-123",
) -> Trade:
    trade = Trade(
        market_id=market_id,
        token_id=token_id,
        order_id=order_id,
        side=side,
        price=price,
        size=size,
        filled_size=size if status == "filled" else 0,
        cost_usd=size * price,
        strategy="time_decay",
        status=status,
        is_paper=True,
    )
    trade.id = trade_id
    return trade


def make_order_book(
    best_bid: float | None = None,
    best_ask: float | None = None,
) -> OrderBook:
    bids = [OrderBookEntry(price=best_bid, size=100.0)] if best_bid is not None else []
    asks = [OrderBookEntry(price=best_ask, size=100.0)] if best_ask is not None else []
    return OrderBook(bids=bids, asks=asks)


def _mock_session():
    """Create a mock async session context manager."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


def _build_manager(
    is_paper: bool = True,
    proxy_address: str | None = "0xwallet",
) -> tuple[OrderManager, MagicMock, AsyncMock]:
    """Create an OrderManager with mocked clob_client and data_api."""
    clob = MagicMock()
    clob.is_paper = is_paper
    clob.get_address.return_value = proxy_address
    clob.place_order = AsyncMock()
    clob.cancel_order = AsyncMock()
    clob.get_order_book = AsyncMock()

    data_api = AsyncMock()

    return OrderManager(clob, data_api), clob, data_api


# ---------------------------------------------------------------------------
# execute_signal — paper mode (immediate fill)
# ---------------------------------------------------------------------------


class TestExecuteSignalPaperMode:
    @pytest.mark.asyncio
    async def test_paper_mode_returns_filled_trade(self):
        """Paper mode orders should return immediately as filled."""
        manager, clob, _ = _build_manager(is_paper=True)
        signal = make_signal()
        created_trade = make_trade(status="filled")

        clob.place_order.return_value = {
            "orderID": "paper-001",
            "status": "MATCHED",
        }

        mock_session = _mock_session()
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created_trade)

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.notify_trade", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_order_placed", new_callable=AsyncMock),
        ):
            result = await manager.execute_signal(signal)

        assert result is not None
        assert result.status == "filled"
        assert manager.pending_count == 0

    @pytest.mark.asyncio
    async def test_paper_mode_uses_signal_market_price(self):
        """In paper mode, _get_fill_price returns signal.market_price directly."""
        manager, clob, _ = _build_manager(is_paper=True)
        signal = make_signal(market_price=0.86)

        price = await manager._get_fill_price(signal)
        assert price == 0.86

    @pytest.mark.asyncio
    async def test_paper_mode_does_not_track_pending(self):
        """Paper mode orders are never added to pending tracking."""
        manager, clob, _ = _build_manager(is_paper=True)
        signal = make_signal()
        created_trade = make_trade(status="filled")

        clob.place_order.return_value = {
            "orderID": "paper-002",
            "status": "MATCHED",
        }

        mock_session = _mock_session()
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created_trade)

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.notify_trade", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_order_placed", new_callable=AsyncMock),
        ):
            await manager.execute_signal(signal)

        assert manager.pending_count == 0
        assert len(manager.pending_market_ids) == 0
        assert manager.pending_capital == 0.0


# ---------------------------------------------------------------------------
# execute_signal — live mode (pending order)
# ---------------------------------------------------------------------------


class TestExecuteSignalLiveMode:
    @pytest.mark.asyncio
    async def test_live_unmatched_order_tracked_as_pending(self):
        """Live orders that are not immediately matched become pending."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(market_price=0.86, size_usd=5.0)
        created_trade = make_trade(trade_id=42, status="pending")

        # Order book with acceptable ask
        book = make_order_book(best_ask=0.87)
        clob.get_order_book.return_value = book

        clob.place_order.return_value = {
            "orderID": "live-001",
            "status": "LIVE",
        }

        mock_session = _mock_session()
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created_trade)

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.notify_trade", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_order_placed", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_price_adjustment", new_callable=AsyncMock),
            patch("bot.agent.order_manager.settings") as mock_settings,
        ):
            mock_settings.is_paper = False
            result = await manager.execute_signal(signal)

        assert result is not None
        assert manager.pending_count == 1
        assert "mkt1" in manager.pending_market_ids
        assert manager.pending_capital > 0

    @pytest.mark.asyncio
    async def test_live_matched_order_not_tracked_as_pending(self):
        """Live orders that are immediately MATCHED are not tracked as pending."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(market_price=0.86, size_usd=5.0)
        created_trade = make_trade(status="filled")

        book = make_order_book(best_ask=0.87)
        clob.get_order_book.return_value = book

        clob.place_order.return_value = {
            "orderID": "live-002",
            "status": "MATCHED",
        }

        mock_session = _mock_session()
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created_trade)

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.notify_trade", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_order_placed", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_price_adjustment", new_callable=AsyncMock),
            patch("bot.agent.order_manager.settings") as mock_settings,
        ):
            mock_settings.is_paper = False
            result = await manager.execute_signal(signal)

        assert result is not None
        assert manager.pending_count == 0


# ---------------------------------------------------------------------------
# execute_signal — API error
# ---------------------------------------------------------------------------


class TestExecuteSignalErrors:
    @pytest.mark.asyncio
    async def test_api_error_returns_none(self):
        """When the CLOB returns an error, execute_signal returns None."""
        manager, clob, _ = _build_manager(is_paper=True)
        signal = make_signal()

        clob.place_order.return_value = {"error": "Insufficient funds"}

        with (
            patch("bot.agent.order_manager.notify_trade", new_callable=AsyncMock),
        ):
            result = await manager.execute_signal(signal)

        assert result is None
        assert manager.pending_count == 0

    @pytest.mark.asyncio
    async def test_api_success_false_returns_none(self):
        """When the CLOB returns success=False, execute_signal returns None."""
        manager, clob, _ = _build_manager(is_paper=True)
        signal = make_signal()

        clob.place_order.return_value = {
            "success": False,
            "errorMsg": "Rate limited",
        }

        with (
            patch("bot.agent.order_manager.notify_trade", new_callable=AsyncMock),
        ):
            result = await manager.execute_signal(signal)

        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_price_zero_returns_none(self):
        """Signals with zero or negative price are rejected."""
        manager, clob, _ = _build_manager(is_paper=True)
        signal = make_signal(market_price=0.0)

        result = await manager.execute_signal(signal)
        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_price_negative_returns_none(self):
        """Signals with negative price are rejected."""
        manager, clob, _ = _build_manager(is_paper=True)
        signal = make_signal(market_price=-0.5)

        result = await manager.execute_signal(signal)
        assert result is None


# ---------------------------------------------------------------------------
# execute_signal — below minimum shares bump
# ---------------------------------------------------------------------------


class TestExecuteSignalMinShares:
    @pytest.mark.asyncio
    async def test_live_mode_bumps_to_min_1_share(self):
        """In live mode, orders below 1 share are bumped to 1."""
        manager, clob, _ = _build_manager(is_paper=False)
        # Very small size that produces less than 1 share at ask price
        signal = make_signal(market_price=0.90, size_usd=0.50)

        book = make_order_book(best_ask=0.91)
        clob.get_order_book.return_value = book

        created_trade = make_trade(status="pending")
        clob.place_order.return_value = {
            "orderID": "live-bump",
            "status": "LIVE",
        }

        mock_session = _mock_session()
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created_trade)

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.notify_trade", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_order_placed", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_price_adjustment", new_callable=AsyncMock),
            patch("bot.agent.order_manager.settings") as mock_settings,
        ):
            mock_settings.is_paper = False
            await manager.execute_signal(signal)

        # Verify place_order was called with size=1.0 (rounded to 2 decimals)
        call_kwargs = clob.place_order.call_args
        assert call_kwargs.kwargs["size"] == 1.0

    @pytest.mark.asyncio
    async def test_live_mode_allows_small_trades(self):
        """Live mode allows trades of $1-$2 without bumping to $5."""
        manager, clob, _ = _build_manager(is_paper=False)
        # $2 at $0.91 = ~2.20 shares, above 1-share min
        signal = make_signal(market_price=0.90, size_usd=2.0)

        book = make_order_book(best_ask=0.91)
        clob.get_order_book.return_value = book

        created_trade = make_trade(status="pending")
        clob.place_order.return_value = {
            "orderID": "live-small",
            "status": "LIVE",
        }

        mock_session = _mock_session()
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created_trade)

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.notify_trade", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_order_placed", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_price_adjustment", new_callable=AsyncMock),
            patch("bot.agent.order_manager.settings") as mock_settings,
        ):
            mock_settings.is_paper = False
            await manager.execute_signal(signal)

        # 2.0 / 0.91 ≈ 2.20 shares — should not be bumped to 5
        call_kwargs = clob.place_order.call_args
        assert call_kwargs.kwargs["size"] == 2.20  # round(2.197..., 2)

    @pytest.mark.asyncio
    async def test_paper_mode_min_shares_is_1(self):
        """In paper mode, minimum shares is 1 instead of 5."""
        manager, clob, _ = _build_manager(is_paper=True)
        # At price 0.86, 2.0 / 0.86 = ~2.3 shares, above paper min of 1
        signal = make_signal(market_price=0.86, size_usd=2.0)

        created_trade = make_trade(status="filled")
        clob.place_order.return_value = {
            "orderID": "paper-bump",
            "status": "MATCHED",
        }

        mock_session = _mock_session()
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created_trade)

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.notify_trade", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_order_placed", new_callable=AsyncMock),
        ):
            result = await manager.execute_signal(signal)

        assert result is not None
        # Paper mode: shares = 2.0 / 0.86 ~ 2.33 -> above min 1, no bumping
        call_kwargs = clob.place_order.call_args
        assert call_kwargs.kwargs["size"] > 1.0


# ---------------------------------------------------------------------------
# monitor_orders — fill verified via positions
# ---------------------------------------------------------------------------


class TestMonitorOrdersFillVerified:
    @pytest.mark.asyncio
    async def test_fill_verified_updates_status_to_filled(self):
        """When position exists on Polymarket, pending order is confirmed filled."""
        manager, clob, data_api = _build_manager(is_paper=False)

        signal = make_signal(token_id="token-filled")
        manager._pending_orders["order-fill-1"] = {
            "trade_id": 10,
            "created_at": datetime.utcnow(),
            "signal": signal,
            "shares": 5.0,
        }

        # Simulate position existing on-chain
        mock_position = MagicMock()
        mock_position.token_id = "token-filled"
        mock_position.size = 5.0
        data_api.get_positions.return_value = [mock_position]

        mock_session = _mock_session()
        mock_repo = AsyncMock()

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.log_order_filled", new_callable=AsyncMock),
        ):
            await manager.monitor_orders()

        mock_repo.update_status.assert_awaited_once_with(10, "filled")
        assert manager.pending_count == 0

    @pytest.mark.asyncio
    async def test_fill_verification_failure_skips_processing(self):
        """When data API raises an exception, monitor_orders returns early."""
        manager, clob, data_api = _build_manager(is_paper=False)

        signal = make_signal()
        manager._pending_orders["order-err-1"] = {
            "trade_id": 20,
            "created_at": datetime.utcnow(),
            "signal": signal,
            "shares": 5.0,
        }

        data_api.get_positions.side_effect = RuntimeError("API down")

        await manager.monitor_orders()

        # Order should still be pending (not removed)
        assert manager.pending_count == 1


# ---------------------------------------------------------------------------
# monitor_orders — order expired after timeout
# ---------------------------------------------------------------------------


class TestMonitorOrdersExpired:
    @pytest.mark.asyncio
    async def test_expired_order_is_cancelled_and_removed(self):
        """Orders older than ORDER_TIMEOUT_SECONDS are cancelled and expired."""
        manager, clob, data_api = _build_manager(is_paper=False)

        signal = make_signal(token_id="token-expired")
        created_at = datetime.utcnow() - timedelta(seconds=ORDER_TIMEOUT_SECONDS + 60)
        manager._pending_orders["order-expired-1"] = {
            "trade_id": 30,
            "created_at": created_at,
            "signal": signal,
            "shares": 5.0,
        }

        # No matching position on-chain
        data_api.get_positions.return_value = []

        mock_session = _mock_session()
        mock_repo = AsyncMock()

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.log_order_expired", new_callable=AsyncMock),
        ):
            await manager.monitor_orders()

        clob.cancel_order.assert_awaited_once_with("order-expired-1")
        mock_repo.update_status.assert_awaited_once_with(30, "expired")
        assert manager.pending_count == 0

    @pytest.mark.asyncio
    async def test_non_expired_order_stays_pending(self):
        """Orders within timeout window remain pending."""
        manager, clob, data_api = _build_manager(is_paper=False)

        signal = make_signal(token_id="token-young")
        manager._pending_orders["order-young-1"] = {
            "trade_id": 31,
            "created_at": datetime.utcnow(),  # just now
            "signal": signal,
            "shares": 5.0,
        }

        # No matching position
        data_api.get_positions.return_value = []

        await manager.monitor_orders()

        assert manager.pending_count == 1
        clob.cancel_order.assert_not_awaited()


# ---------------------------------------------------------------------------
# monitor_orders — on_fill_callback invoked
# ---------------------------------------------------------------------------


class TestMonitorOrdersCallback:
    @pytest.mark.asyncio
    async def test_on_fill_callback_invoked_on_fill(self):
        """The on_fill_callback is called when a pending order is confirmed filled."""
        manager, clob, data_api = _build_manager(is_paper=False)

        callback = AsyncMock()
        manager.set_on_fill_callback(callback)

        signal = make_signal(token_id="token-cb")
        manager._pending_orders["order-cb-1"] = {
            "trade_id": 40,
            "created_at": datetime.utcnow(),
            "signal": signal,
            "shares": 7.5,
        }

        mock_position = MagicMock()
        mock_position.token_id = "token-cb"
        mock_position.size = 7.5
        data_api.get_positions.return_value = [mock_position]

        mock_session = _mock_session()
        mock_repo = AsyncMock()

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.log_order_filled", new_callable=AsyncMock),
        ):
            await manager.monitor_orders()

        callback.assert_awaited_once_with(signal, 7.5)

    @pytest.mark.asyncio
    async def test_callback_error_does_not_prevent_removal(self):
        """If the callback raises, the order is still removed from pending."""
        manager, clob, data_api = _build_manager(is_paper=False)

        callback = AsyncMock(side_effect=RuntimeError("callback boom"))
        manager.set_on_fill_callback(callback)

        signal = make_signal(token_id="token-cb-err")
        manager._pending_orders["order-cb-err-1"] = {
            "trade_id": 41,
            "created_at": datetime.utcnow(),
            "signal": signal,
            "shares": 5.0,
        }

        mock_position = MagicMock()
        mock_position.token_id = "token-cb-err"
        mock_position.size = 5.0
        data_api.get_positions.return_value = [mock_position]

        mock_session = _mock_session()
        mock_repo = AsyncMock()

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.log_order_filled", new_callable=AsyncMock),
        ):
            await manager.monitor_orders()

        # Order should still be removed despite callback failure
        assert manager.pending_count == 0

    @pytest.mark.asyncio
    async def test_no_callback_set_skips_invocation(self):
        """When no callback is set, fill processing proceeds without error."""
        manager, clob, data_api = _build_manager(is_paper=False)
        # Deliberately do NOT set a callback

        signal = make_signal(token_id="token-nocb")
        manager._pending_orders["order-nocb-1"] = {
            "trade_id": 42,
            "created_at": datetime.utcnow(),
            "signal": signal,
            "shares": 5.0,
        }

        mock_position = MagicMock()
        mock_position.token_id = "token-nocb"
        mock_position.size = 5.0
        data_api.get_positions.return_value = [mock_position]

        mock_session = _mock_session()
        mock_repo = AsyncMock()

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.log_order_filled", new_callable=AsyncMock),
        ):
            await manager.monitor_orders()

        assert manager.pending_count == 0


# ---------------------------------------------------------------------------
# monitor_orders — empty pending
# ---------------------------------------------------------------------------


class TestMonitorOrdersEmpty:
    @pytest.mark.asyncio
    async def test_no_pending_orders_returns_immediately(self):
        """monitor_orders with no pending orders does nothing."""
        manager, clob, data_api = _build_manager(is_paper=False)

        await manager.monitor_orders()

        clob.get_address.assert_not_called()
        data_api.get_positions.assert_not_awaited()


# ---------------------------------------------------------------------------
# close_position — success
# ---------------------------------------------------------------------------


class TestClosePosition:
    @pytest.mark.asyncio
    async def test_close_position_paper_mode_success(self):
        """Paper mode close_position returns a filled trade immediately."""
        manager, clob, _ = _build_manager(is_paper=True)

        clob.place_order.return_value = {
            "orderID": "close-paper-1",
            "status": "MATCHED",
        }

        created_trade = make_trade(
            trade_id=50,
            status="filled",
            side="SELL",
        )
        mock_session = _mock_session()
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created_trade)

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.settings") as mock_settings,
        ):
            mock_settings.is_paper = True
            result = await manager.close_position(
                market_id="mkt1",
                token_id="token1",
                size=10.0,
                current_price=0.90,
            )

        assert result is not None
        # Verify SELL side was used
        call_kwargs = clob.place_order.call_args
        assert call_kwargs.kwargs["side"] == OrderSide.SELL

    @pytest.mark.asyncio
    async def test_close_position_live_mode_uses_best_bid(self):
        """Live mode close_position adjusts price to best bid."""
        manager, clob, _ = _build_manager(is_paper=False)

        book = make_order_book(best_bid=0.88)
        clob.get_order_book.return_value = book

        clob.place_order.return_value = {
            "orderID": "close-live-1",
            "status": "LIVE",
        }

        created_trade = make_trade(trade_id=51, status="pending", side="SELL")
        mock_session = _mock_session()
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created_trade)

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.settings") as mock_settings,
        ):
            mock_settings.is_paper = False
            result = await manager.close_position(
                market_id="mkt1",
                token_id="token1",
                size=10.0,
                current_price=0.90,
            )

        assert result is not None
        call_kwargs = clob.place_order.call_args
        assert call_kwargs.kwargs["price"] == 0.88

    @pytest.mark.asyncio
    async def test_close_position_api_error_returns_none(self):
        """close_position returns None if CLOB returns an error."""
        manager, clob, _ = _build_manager(is_paper=True)

        clob.place_order.return_value = {"error": "Order rejected"}

        result = await manager.close_position(
            market_id="mkt1",
            token_id="token1",
            size=10.0,
            current_price=0.90,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_close_position_live_no_bid_uses_current_price(self):
        """If no best_bid in order book, falls back to current_price."""
        manager, clob, _ = _build_manager(is_paper=False)

        book = make_order_book()  # no bids
        clob.get_order_book.return_value = book

        clob.place_order.return_value = {
            "orderID": "close-nobid",
            "status": "LIVE",
        }

        created_trade = make_trade(trade_id=52, status="pending", side="SELL")
        mock_session = _mock_session()
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created_trade)

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.settings") as mock_settings,
        ):
            mock_settings.is_paper = False
            result = await manager.close_position(
                market_id="mkt1",
                token_id="token1",
                size=10.0,
                current_price=0.90,
            )

        assert result is not None
        call_kwargs = clob.place_order.call_args
        assert call_kwargs.kwargs["price"] == 0.90


# ---------------------------------------------------------------------------
# close_position — below minimum size rejected
# ---------------------------------------------------------------------------


class TestClosePositionMinSize:
    @pytest.mark.asyncio
    async def test_live_below_min_size_rejected(self):
        """Live mode rejects close_position if size < MIN_SELL_SHARES (5)."""
        manager, clob, _ = _build_manager(is_paper=False)

        result = await manager.close_position(
            market_id="mkt1",
            token_id="token1",
            size=3.0,  # below 5
            current_price=0.90,
        )

        assert result is None
        clob.place_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_paper_below_min_size_allowed(self):
        """Paper mode allows close_position even with size < 5."""
        manager, clob, _ = _build_manager(is_paper=True)

        clob.place_order.return_value = {
            "orderID": "close-small",
            "status": "MATCHED",
        }

        created_trade = make_trade(trade_id=60, status="filled", side="SELL")
        mock_session = _mock_session()
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created_trade)

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.settings") as mock_settings,
        ):
            mock_settings.is_paper = True
            result = await manager.close_position(
                market_id="mkt1",
                token_id="token1",
                size=2.0,
                current_price=0.90,
            )

        assert result is not None


# ---------------------------------------------------------------------------
# _get_fill_price — paper mode returns signal price
# ---------------------------------------------------------------------------


class TestGetFillPricePaperMode:
    @pytest.mark.asyncio
    async def test_paper_mode_returns_signal_price(self):
        """Paper mode returns signal.market_price without order book lookup."""
        manager, clob, _ = _build_manager(is_paper=True)
        signal = make_signal(market_price=0.88)

        price = await manager._get_fill_price(signal)

        assert price == 0.88
        clob.get_order_book.assert_not_awaited()


# ---------------------------------------------------------------------------
# _get_fill_price — adjusts to best ask for BUY
# ---------------------------------------------------------------------------


class TestGetFillPriceBuyAdjust:
    @pytest.mark.asyncio
    async def test_adjusts_to_best_ask_for_buy(self):
        """BUY orders adjust price to best_ask when within slippage limit."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(
            side=OrderSide.BUY,
            market_price=0.86,
            estimated_prob=0.92,
            edge=0.06,
        )

        book = make_order_book(best_ask=0.87)  # 1 cent slippage, acceptable
        clob.get_order_book.return_value = book

        with (
            patch("bot.agent.order_manager.log_price_adjustment", new_callable=AsyncMock),
        ):
            price = await manager._get_fill_price(signal)

        assert price == 0.87

    @pytest.mark.asyncio
    async def test_adjusts_to_best_bid_for_sell(self):
        """SELL orders adjust price to best_bid when within slippage limit."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(
            side=OrderSide.SELL,
            market_price=0.86,
            estimated_prob=0.80,
            edge=0.06,
        )

        book = make_order_book(best_bid=0.85)  # 1 cent slippage, acceptable
        clob.get_order_book.return_value = book

        price = await manager._get_fill_price(signal)
        assert price == 0.85


# ---------------------------------------------------------------------------
# _get_fill_price — rejects excessive slippage
# ---------------------------------------------------------------------------


class TestGetFillPriceExcessiveSlippage:
    @pytest.mark.asyncio
    async def test_buy_excessive_slippage_returns_none(self):
        """BUY with ask > signal_price + 3 cents is rejected."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(
            side=OrderSide.BUY,
            market_price=0.86,
            estimated_prob=0.92,
            edge=0.06,
        )

        # Ask at 0.90 = 4 cents slippage > 3 cents max
        book = make_order_book(best_ask=0.90)
        clob.get_order_book.return_value = book

        with (
            patch("bot.agent.order_manager.log_signal_rejected", new_callable=AsyncMock),
        ):
            price = await manager._get_fill_price(signal)

        assert price is None

    @pytest.mark.asyncio
    async def test_sell_excessive_slippage_returns_none(self):
        """SELL with bid < signal_price - 3 cents is rejected."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(
            side=OrderSide.SELL,
            market_price=0.86,
            estimated_prob=0.80,
            edge=0.06,
        )

        # Bid at 0.82 = 4 cents slippage > 3 cents max
        book = make_order_book(best_bid=0.82)
        clob.get_order_book.return_value = book

        price = await manager._get_fill_price(signal)
        assert price is None

    @pytest.mark.asyncio
    async def test_buy_just_under_max_slippage_accepted(self):
        """BUY with ask just under max slippage is accepted."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(
            side=OrderSide.BUY,
            market_price=0.86,
            estimated_prob=0.92,
            edge=0.06,
        )

        # Ask at 0.88 = 2 cents slippage, well within 3 cent limit
        book = make_order_book(best_ask=0.88)
        clob.get_order_book.return_value = book

        with (
            patch("bot.agent.order_manager.log_price_adjustment", new_callable=AsyncMock),
        ):
            price = await manager._get_fill_price(signal)

        assert price == 0.88

    @pytest.mark.asyncio
    async def test_buy_at_boundary_slippage_rejected_due_to_float_precision(self):
        """BUY with ask at exactly 3 cents is rejected due to float precision.

        0.89 - 0.86 = 0.030000000000000027 > 0.03 (strict comparison).
        """
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(
            side=OrderSide.BUY,
            market_price=0.86,
            estimated_prob=0.92,
            edge=0.06,
        )

        book = make_order_book(best_ask=0.89)
        clob.get_order_book.return_value = book

        with (
            patch("bot.agent.order_manager.log_signal_rejected", new_callable=AsyncMock),
        ):
            price = await manager._get_fill_price(signal)

        assert price is None


# ---------------------------------------------------------------------------
# _get_fill_price — rejects when edge evaporates at ask
# ---------------------------------------------------------------------------


class TestGetFillPriceEdgeEvaporated:
    @pytest.mark.asyncio
    async def test_edge_below_min_threshold_returns_none(self):
        """When adjusted edge at ask < 0.5%, signal is rejected."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(
            side=OrderSide.BUY,
            market_price=0.86,
            estimated_prob=0.88,  # low prob
            edge=0.02,  # small edge
        )

        # Ask at 0.88 => adjusted_edge = 0.88 - 0.88 = 0.00 < 0.005
        book = make_order_book(best_ask=0.88)
        clob.get_order_book.return_value = book

        with (
            patch("bot.agent.order_manager.log_signal_rejected", new_callable=AsyncMock),
        ):
            price = await manager._get_fill_price(signal)

        assert price is None

    @pytest.mark.asyncio
    async def test_edge_exactly_at_threshold_accepted(self):
        """When adjusted edge at ask == 0.5%, signal is accepted."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(
            side=OrderSide.BUY,
            market_price=0.86,
            estimated_prob=0.875,  # edge at ask = 0.875 - 0.87 = 0.005
            edge=0.015,
        )

        book = make_order_book(best_ask=0.87)
        clob.get_order_book.return_value = book

        with (
            patch("bot.agent.order_manager.log_price_adjustment", new_callable=AsyncMock),
        ):
            price = await manager._get_fill_price(signal)

        assert price == 0.87

    @pytest.mark.asyncio
    async def test_edge_just_below_threshold_rejected(self):
        """When adjusted edge at ask is just below 0.5%, signal is rejected."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(
            side=OrderSide.BUY,
            market_price=0.86,
            estimated_prob=0.874,  # edge at ask = 0.874 - 0.87 = 0.004 < 0.005
            edge=0.014,
        )

        book = make_order_book(best_ask=0.87)
        clob.get_order_book.return_value = book

        with (
            patch("bot.agent.order_manager.log_signal_rejected", new_callable=AsyncMock),
        ):
            price = await manager._get_fill_price(signal)

        assert price is None


# ---------------------------------------------------------------------------
# _get_fill_price — no order book depth returns None
# ---------------------------------------------------------------------------


class TestGetFillPriceNoDepth:
    @pytest.mark.asyncio
    async def test_buy_no_asks_returns_none(self):
        """BUY with no asks in order book returns None (illiquid market)."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(side=OrderSide.BUY, market_price=0.86)

        book = make_order_book()  # empty book
        clob.get_order_book.return_value = book

        price = await manager._get_fill_price(signal)
        assert price is None

    @pytest.mark.asyncio
    async def test_sell_no_bids_returns_none(self):
        """SELL with no bids in order book returns None (illiquid market)."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(side=OrderSide.SELL, market_price=0.86)

        book = make_order_book()  # empty book
        clob.get_order_book.return_value = book

        price = await manager._get_fill_price(signal)
        assert price is None

    @pytest.mark.asyncio
    async def test_buy_with_bids_but_no_asks_returns_none(self):
        """BUY order but only bids available (no asks) returns None."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(side=OrderSide.BUY, market_price=0.86)

        book = make_order_book(best_bid=0.85)  # bids only, no asks
        clob.get_order_book.return_value = book

        price = await manager._get_fill_price(signal)
        assert price is None

    @pytest.mark.asyncio
    async def test_order_book_exception_falls_back_to_signal_price(self):
        """When get_order_book raises, _get_fill_price falls back to signal price."""
        manager, clob, _ = _build_manager(is_paper=False)
        signal = make_signal(market_price=0.86)

        clob.get_order_book.side_effect = RuntimeError("Network error")

        price = await manager._get_fill_price(signal)
        assert price == 0.86  # fallback to signal price


# ---------------------------------------------------------------------------
# pending_market_ids / pending_capital / pending_count properties
# ---------------------------------------------------------------------------


class TestPendingProperties:
    def test_pending_market_ids_empty(self):
        """No pending orders yields empty set."""
        manager, _, _ = _build_manager()
        assert manager.pending_market_ids == set()

    def test_pending_market_ids_with_orders(self):
        """Returns set of unique market IDs from pending orders."""
        manager, _, _ = _build_manager()

        signal_a = make_signal(market_id="mkt-a")
        signal_b = make_signal(market_id="mkt-b")
        signal_a_dup = make_signal(market_id="mkt-a")

        manager._pending_orders["o1"] = {
            "trade_id": 1,
            "created_at": datetime.utcnow(),
            "signal": signal_a,
            "shares": 5.0,
        }
        manager._pending_orders["o2"] = {
            "trade_id": 2,
            "created_at": datetime.utcnow(),
            "signal": signal_b,
            "shares": 5.0,
        }
        manager._pending_orders["o3"] = {
            "trade_id": 3,
            "created_at": datetime.utcnow(),
            "signal": signal_a_dup,
            "shares": 5.0,
        }

        assert manager.pending_market_ids == {"mkt-a", "mkt-b"}

    def test_pending_capital_empty(self):
        """No pending orders yields zero capital."""
        manager, _, _ = _build_manager()
        assert manager.pending_capital == 0.0

    def test_pending_capital_with_orders(self):
        """Sums size_usd from all pending orders."""
        manager, _, _ = _build_manager()

        signal_a = make_signal(size_usd=5.0)
        signal_b = make_signal(size_usd=10.0)

        manager._pending_orders["o1"] = {
            "trade_id": 1,
            "created_at": datetime.utcnow(),
            "signal": signal_a,
            "shares": 5.0,
        }
        manager._pending_orders["o2"] = {
            "trade_id": 2,
            "created_at": datetime.utcnow(),
            "signal": signal_b,
            "shares": 10.0,
        }

        assert manager.pending_capital == 15.0

    def test_pending_count_empty(self):
        """No pending orders yields count zero."""
        manager, _, _ = _build_manager()
        assert manager.pending_count == 0

    def test_pending_count_with_orders(self):
        """Returns correct count of pending orders."""
        manager, _, _ = _build_manager()

        for i in range(3):
            manager._pending_orders[f"o{i}"] = {
                "trade_id": i,
                "created_at": datetime.utcnow(),
                "signal": make_signal(),
                "shares": 5.0,
            }

        assert manager.pending_count == 3


# ---------------------------------------------------------------------------
# set_on_fill_callback
# ---------------------------------------------------------------------------


class TestSetOnFillCallback:
    def test_callback_is_stored(self):
        """set_on_fill_callback stores the callback for later invocation."""
        manager, _, _ = _build_manager()

        async def my_callback(signal, shares):
            pass

        manager.set_on_fill_callback(my_callback)
        assert manager._on_fill_callback is my_callback

    def test_callback_can_be_replaced(self):
        """Calling set_on_fill_callback again replaces the previous callback."""
        manager, _, _ = _build_manager()

        async def cb1(signal, shares):
            pass

        async def cb2(signal, shares):
            pass

        manager.set_on_fill_callback(cb1)
        manager.set_on_fill_callback(cb2)
        assert manager._on_fill_callback is cb2


# ---------------------------------------------------------------------------
# Integration-style: full execute -> monitor cycle
# ---------------------------------------------------------------------------


class TestExecuteAndMonitorCycle:
    @pytest.mark.asyncio
    async def test_execute_then_fill_via_monitor(self):
        """End-to-end: execute creates pending, monitor confirms fill."""
        manager, clob, data_api = _build_manager(is_paper=False)

        signal = make_signal(
            token_id="token-cycle",
            market_id="mkt-cycle",
            market_price=0.86,
            estimated_prob=0.92,
            edge=0.06,
            size_usd=5.0,
        )

        book = make_order_book(best_ask=0.87)
        clob.get_order_book.return_value = book

        # Phase 1: execute_signal
        created_trade = make_trade(trade_id=100, status="pending")
        clob.place_order.return_value = {
            "orderID": "cycle-001",
            "status": "LIVE",
        }

        mock_session = _mock_session()
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created_trade)

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.notify_trade", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_order_placed", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_price_adjustment", new_callable=AsyncMock),
            patch("bot.agent.order_manager.settings") as mock_settings,
        ):
            mock_settings.is_paper = False
            trade = await manager.execute_signal(signal)

        assert trade is not None
        assert manager.pending_count == 1

        # Phase 2: monitor_orders confirms fill
        mock_position = MagicMock()
        mock_position.token_id = "token-cycle"
        mock_position.size = 5.0
        data_api.get_positions.return_value = [mock_position]

        callback = AsyncMock()
        manager.set_on_fill_callback(callback)

        mock_session2 = _mock_session()
        mock_repo2 = AsyncMock()

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session2),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo2),
            patch("bot.agent.order_manager.log_order_filled", new_callable=AsyncMock),
        ):
            await manager.monitor_orders()

        assert manager.pending_count == 0
        mock_repo2.update_status.assert_awaited_once_with(100, "filled")
        callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_then_expire_via_monitor(self):
        """End-to-end: execute creates pending, monitor expires after timeout."""
        manager, clob, data_api = _build_manager(is_paper=False)

        signal = make_signal(
            token_id="token-expire",
            market_id="mkt-expire",
            market_price=0.86,
            estimated_prob=0.92,
            edge=0.06,
            size_usd=5.0,
        )

        book = make_order_book(best_ask=0.87)
        clob.get_order_book.return_value = book

        created_trade = make_trade(trade_id=101, status="pending")
        clob.place_order.return_value = {
            "orderID": "expire-001",
            "status": "LIVE",
        }

        mock_session = _mock_session()
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=created_trade)

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
            patch("bot.agent.order_manager.notify_trade", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_order_placed", new_callable=AsyncMock),
            patch("bot.agent.order_manager.log_price_adjustment", new_callable=AsyncMock),
            patch("bot.agent.order_manager.settings") as mock_settings,
        ):
            mock_settings.is_paper = False
            await manager.execute_signal(signal)

        assert manager.pending_count == 1

        # Artificially age the pending order past timeout
        order_info = manager._pending_orders["expire-001"]
        order_info["created_at"] = datetime.utcnow() - timedelta(
            seconds=ORDER_TIMEOUT_SECONDS + 10
        )

        # No matching position
        data_api.get_positions.return_value = []

        mock_session2 = _mock_session()
        mock_repo2 = AsyncMock()

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session2),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo2),
            patch("bot.agent.order_manager.log_order_expired", new_callable=AsyncMock),
        ):
            await manager.monitor_orders()

        assert manager.pending_count == 0
        clob.cancel_order.assert_awaited_once_with("expire-001")
        mock_repo2.update_status.assert_awaited_once_with(101, "expired")


# ---------------------------------------------------------------------------
# SELL order monitoring
# ---------------------------------------------------------------------------


class TestSellOrderMonitoring:
    """Tests for SELL order fill detection and timeout."""

    @pytest.mark.asyncio
    async def test_sell_fill_confirmed_when_token_gone(self):
        """SELL is filled when token no longer in Polymarket positions."""
        manager, clob, data_api = _build_manager(is_paper=False)
        # Position no longer on Polymarket = SELL filled
        data_api.get_positions.return_value = []

        manager._pending_orders["sell-001"] = {
            "trade_id": 50,
            "created_at": datetime.utcnow() - timedelta(seconds=30),
            "signal": None,
            "shares": 10.0,
            "is_sell": True,
            "token_id": "tok_sold",
        }

        mock_repo = AsyncMock()
        mock_session = _mock_session()

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
        ):
            await manager.monitor_orders()

        assert manager.pending_count == 0
        mock_repo.update_status.assert_awaited_once_with(50, "filled")

    @pytest.mark.asyncio
    async def test_sell_not_filled_when_token_still_present(self):
        """SELL is pending when token is still in Polymarket positions."""
        manager, clob, data_api = _build_manager(is_paper=False)
        pos_mock = MagicMock()
        pos_mock.token_id = "tok_still_held"
        pos_mock.size = 10.0
        data_api.get_positions.return_value = [pos_mock]

        manager._pending_orders["sell-002"] = {
            "trade_id": 51,
            "created_at": datetime.utcnow() - timedelta(seconds=30),
            "signal": None,
            "shares": 10.0,
            "is_sell": True,
            "token_id": "tok_still_held",
        }

        await manager.monitor_orders()

        assert manager.pending_count == 1  # Still pending

    @pytest.mark.asyncio
    async def test_sell_order_expires_on_timeout(self):
        """SELL order expires when timeout exceeded."""
        manager, clob, data_api = _build_manager(is_paper=False)
        pos_mock = MagicMock()
        pos_mock.token_id = "tok_timeout"
        pos_mock.size = 10.0
        data_api.get_positions.return_value = [pos_mock]

        manager._pending_orders["sell-003"] = {
            "trade_id": 52,
            "created_at": datetime.utcnow() - timedelta(seconds=ORDER_TIMEOUT_SECONDS + 10),
            "signal": None,
            "shares": 10.0,
            "is_sell": True,
            "token_id": "tok_timeout",
        }

        mock_repo = AsyncMock()
        mock_session = _mock_session()

        with (
            patch("bot.agent.order_manager.async_session", return_value=mock_session),
            patch("bot.agent.order_manager.TradeRepository", return_value=mock_repo),
        ):
            await manager.monitor_orders()

        assert manager.pending_count == 0
        clob.cancel_order.assert_awaited_once_with("sell-003")
        mock_repo.update_status.assert_awaited_once_with(52, "expired")

    @pytest.mark.asyncio
    async def test_monitor_skips_when_no_address(self):
        """Monitor aborts gracefully when wallet address unavailable."""
        manager, clob, data_api = _build_manager(
            is_paper=False, proxy_address=None,
        )
        manager._pending_orders["sell-004"] = {
            "trade_id": 53,
            "created_at": datetime.utcnow(),
            "signal": None,
            "shares": 5.0,
            "is_sell": True,
            "token_id": "tok_orphan",
        }

        await manager.monitor_orders()

        # Should abort without falsely marking as filled
        assert manager.pending_count == 1
        data_api.get_positions.assert_not_called()

    def test_pending_market_ids_excludes_sell_orders(self):
        """pending_market_ids only includes BUY orders (signal != None)."""
        manager, _, _ = _build_manager()
        signal = make_signal(market_id="buy_mkt")
        manager._pending_orders["buy-001"] = {
            "trade_id": 60,
            "created_at": datetime.utcnow(),
            "signal": signal,
            "shares": 5.0,
        }
        manager._pending_orders["sell-001"] = {
            "trade_id": 61,
            "created_at": datetime.utcnow(),
            "signal": None,
            "shares": 10.0,
            "is_sell": True,
            "token_id": "tok_sell",
        }

        assert manager.pending_market_ids == {"buy_mkt"}

    def test_pending_capital_excludes_sell_orders(self):
        """pending_capital only counts BUY orders (signal != None)."""
        manager, _, _ = _build_manager()
        signal = make_signal(market_id="buy_mkt", size_usd=5.0)
        manager._pending_orders["buy-001"] = {
            "trade_id": 60,
            "created_at": datetime.utcnow(),
            "signal": signal,
            "shares": 5.0,
        }
        manager._pending_orders["sell-001"] = {
            "trade_id": 61,
            "created_at": datetime.utcnow(),
            "signal": None,
            "shares": 10.0,
            "is_sell": True,
            "token_id": "tok_sell",
        }

        assert manager.pending_capital == 5.0
