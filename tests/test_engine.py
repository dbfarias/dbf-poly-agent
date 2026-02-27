"""Tests for TradingEngine — main orchestration loop."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.engine import TradingEngine
from bot.config import CapitalTier
from bot.data.models import Position
from bot.polymarket.types import OrderBook, OrderBookEntry, OrderSide, TradeSignal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_signal(
    market_id: str = "mkt1",
    edge: float = 0.06,
    estimated_prob: float = 0.92,
    market_price: float = 0.86,
    strategy: str = "time_decay",
    metadata: dict | None = None,
) -> TradeSignal:
    return TradeSignal(
        strategy=strategy,
        market_id=market_id,
        token_id="token1",
        question="Will X happen?",
        outcome="Yes",
        side=OrderSide.BUY,
        estimated_prob=estimated_prob,
        market_price=market_price,
        edge=edge,
        size_usd=5.0,
        confidence=0.85,
        metadata=metadata or {"category": "crypto"},
    )


def make_position(
    market_id: str = "mkt1",
    token_id: str = "token1",
    current_price: float = 0.55,
    size: float = 10.0,
    strategy: str = "time_decay",
) -> Position:
    return Position(
        market_id=market_id,
        token_id=token_id,
        question="Will X?",
        outcome="Yes",
        category="crypto",
        strategy=strategy,
        side="BUY",
        size=size,
        avg_price=0.50,
        current_price=current_price,
        cost_basis=5.0,
        unrealized_pnl=(current_price - 0.50) * size,
        is_open=True,
    )


# ---------------------------------------------------------------------------
# TradingEngine Initialization
# ---------------------------------------------------------------------------


class TestEngineInit:
    def test_initial_state(self):
        """Engine should start in non-running state."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            assert engine.is_running is False
            assert engine._cycle_count == 0

    @pytest.mark.asyncio
    async def test_initialize_calls_clients(self):
        """initialize() should call initialize on all sub-clients."""
        with patch("bot.agent.engine.PolymarketClient") as mock_clob_cls, \
             patch("bot.agent.engine.GammaClient") as mock_gamma_cls, \
             patch("bot.agent.engine.DataApiClient") as mock_api_cls, \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"), \
             patch("bot.agent.engine.PerformanceLearner"):
            mock_clob = AsyncMock()
            mock_gamma = AsyncMock()
            mock_data_api = AsyncMock()
            mock_clob_cls.return_value = mock_clob
            mock_gamma_cls.return_value = mock_gamma
            mock_api_cls.return_value = mock_data_api

            engine = TradingEngine()
            engine.portfolio = AsyncMock()
            engine.portfolio.sync = AsyncMock()
            engine.portfolio.total_equity = 10.0
            engine.portfolio.tier = CapitalTier.TIER1
            engine.portfolio.open_position_count = 0
            engine.order_manager = AsyncMock()

            settings_path = (
                "bot.data.settings_store.SettingsStore.load_and_apply"
            )
            with patch.object(engine, "_seed_strategy_metrics", new_callable=AsyncMock), \
                 patch(settings_path, new_callable=AsyncMock, return_value=0):
                await engine.initialize()

            mock_clob.initialize.assert_called_once()
            mock_gamma.initialize.assert_called_once()
            mock_data_api.initialize.assert_called_once()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_status_keys(self):
        """get_status should return all expected fields."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache") as mock_cache_cls, \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.portfolio = MagicMock()
            engine.portfolio.get_overview.return_value = {"equity": 10}
            engine.portfolio.total_equity = 10.0
            engine.risk_manager = MagicMock()
            engine.risk_manager.get_risk_metrics.return_value = {"tier": "tier1"}
            engine.order_manager = MagicMock()
            engine.order_manager.pending_count = 0
            mock_cache_cls.return_value.stats = {"hits": 0}
            engine.cache = mock_cache_cls.return_value

            status = engine.get_status()
            assert "running" in status
            assert "cycle_count" in status
            assert "mode" in status
            assert "portfolio" in status
            assert "risk" in status
            assert "pending_orders" in status


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_stops_components(self):
        """shutdown() should stop heartbeat, disconnect WS, close clients."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine._running = True
            engine.heartbeat = AsyncMock()
            engine.ws_manager = AsyncMock()
            engine.gamma_client = AsyncMock()
            engine.data_api = AsyncMock()

            await engine.shutdown()

            assert engine._running is False
            engine.heartbeat.stop.assert_called_once()
            engine.ws_manager.disconnect.assert_called_once()
            engine.gamma_client.close.assert_called_once()
            engine.data_api.close.assert_called_once()


# ---------------------------------------------------------------------------
# Check Liquidity
# ---------------------------------------------------------------------------


class TestCheckLiquidity:
    @pytest.mark.asyncio
    async def test_good_liquidity_passes(self):
        """Tight spread and good bid should pass."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.clob_client = AsyncMock()
            engine.clob_client.get_order_book = AsyncMock(
                return_value=OrderBook(
                    asset_id="token1",
                    bids=[OrderBookEntry(price=0.84, size=100)],
                    asks=[OrderBookEntry(price=0.86, size=100)],
                )
            )

            signal = make_signal(market_price=0.86)
            result = await engine._check_liquidity(signal)
            assert result is True

    @pytest.mark.asyncio
    async def test_wide_spread_fails(self):
        """Spread > 5 cents should fail."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.clob_client = AsyncMock()
            engine.clob_client.get_order_book = AsyncMock(
                return_value=OrderBook(
                    asset_id="token1",
                    bids=[OrderBookEntry(price=0.80, size=100)],
                    asks=[OrderBookEntry(price=0.90, size=100)],
                )
            )

            with patch("bot.agent.engine.log_liquidity_rejected", new_callable=AsyncMock):
                signal = make_signal(market_price=0.85)
                result = await engine._check_liquidity(signal)
            assert result is False

    @pytest.mark.asyncio
    async def test_no_bid_with_low_price_fails(self):
        """No exit liquidity when best bid far from fair price."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.clob_client = AsyncMock()
            engine.clob_client.get_order_book = AsyncMock(
                return_value=OrderBook(
                    asset_id="token1",
                    bids=[OrderBookEntry(price=0.20, size=100)],
                    asks=[OrderBookEntry(price=0.52, size=100)],
                )
            )

            with patch("bot.agent.engine.log_liquidity_rejected", new_callable=AsyncMock):
                signal = make_signal(market_price=0.50)
                result = await engine._check_liquidity(signal)
            assert result is False

    @pytest.mark.asyncio
    async def test_order_book_error_fails(self):
        """On exception, liquidity check should return False."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.clob_client = AsyncMock()
            engine.clob_client.get_order_book = AsyncMock(side_effect=Exception("timeout"))

            signal = make_signal()
            result = await engine._check_liquidity(signal)
            assert result is False

    @pytest.mark.asyncio
    async def test_empty_book_spread_none_fails(self):
        """Empty order book (no bids, no asks) → spread is None → fails."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.clob_client = AsyncMock()
            engine.clob_client.get_order_book = AsyncMock(
                return_value=OrderBook(asset_id="token1")
            )

            with patch("bot.agent.engine.log_liquidity_rejected", new_callable=AsyncMock):
                signal = make_signal()
                result = await engine._check_liquidity(signal)
            assert result is False


# ---------------------------------------------------------------------------
# Handle Order Fill Callback
# ---------------------------------------------------------------------------


class TestHandleOrderFill:
    @pytest.mark.asyncio
    async def test_fill_callback_records_position(self):
        """_handle_order_fill should call portfolio.record_trade_open."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.portfolio = AsyncMock()
            engine.portfolio.record_trade_open = AsyncMock()

            signal = make_signal(market_id="mkt_fill")
            await engine._handle_order_fill(signal, shares=10.0)

            engine.portfolio.record_trade_open.assert_called_once()
            call_kwargs = engine.portfolio.record_trade_open.call_args.kwargs
            assert call_kwargs["market_id"] == "mkt_fill"
            assert call_kwargs["size"] == 10.0


# ---------------------------------------------------------------------------
# Maybe Snapshot
# ---------------------------------------------------------------------------


class TestMaybeSnapshot:
    @pytest.mark.asyncio
    async def test_first_snapshot_taken(self):
        """First call to _maybe_snapshot should always take a snapshot."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.portfolio = AsyncMock()
            engine.portfolio.take_snapshot = AsyncMock()
            engine._last_snapshot = None

            await engine._maybe_snapshot()

            engine.portfolio.take_snapshot.assert_called_once()
            assert engine._last_snapshot is not None

    @pytest.mark.asyncio
    async def test_snapshot_skipped_if_recent(self):
        """Should not take snapshot if within interval."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.portfolio = AsyncMock()
            engine.portfolio.take_snapshot = AsyncMock()
            engine._last_snapshot = datetime.now(timezone.utc)

            await engine._maybe_snapshot()

            engine.portfolio.take_snapshot.assert_not_called()


# ---------------------------------------------------------------------------
# Register Strategy
# ---------------------------------------------------------------------------


class TestRegisterStrategy:
    def test_register_adds_to_analyzer(self):
        """register_strategy should add strategy to analyzer.strategies."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.analyzer = MagicMock()
            engine.analyzer.strategies = []

            mock_strategy = MagicMock()
            mock_strategy.name = "test_strategy"
            engine.register_strategy(mock_strategy)

            assert len(engine.analyzer.strategies) == 1
            assert engine.analyzer.strategies[0].name == "test_strategy"


# ---------------------------------------------------------------------------
# C3 — Urgency + Edge Multiplier Interaction
# ---------------------------------------------------------------------------


class TestApplyUrgencyToEdgeMultiplier:
    """_apply_urgency_to_edge_multiplier must NOT cancel learner penalties."""

    def _call(self, edge_multiplier: float, urgency: float) -> float:
        from bot.agent.engine import _apply_urgency_to_edge_multiplier
        return _apply_urgency_to_edge_multiplier(edge_multiplier, urgency)

    def test_behind_losing_keeps_penalty(self):
        """Behind target + losing strategy: penalty must remain."""
        # edge_multiplier=1.5 (losing), urgency=1.5 (behind)
        result = self._call(1.5, 1.5)
        assert result >= 1.5, "Penalty should not be reduced"

    def test_behind_winning_relaxes(self):
        """Behind target + winning strategy: relax the edge requirement."""
        # edge_multiplier=0.8 (winning), urgency=1.5 (behind)
        result = self._call(0.8, 1.5)
        assert result < 0.8, "Winning strategy should relax when behind"

    def test_behind_neutral_relaxes(self):
        """Behind target + neutral strategy: relax edge requirement."""
        result = self._call(1.0, 1.3)
        assert result < 1.0

    def test_ahead_tightens(self):
        """Ahead of target: always tighten (raise bar)."""
        result = self._call(0.8, 0.7)
        assert result > 0.8

    def test_neutral_no_change(self):
        """Urgency=1.0: no change to edge multiplier."""
        result = self._call(1.0, 1.0)
        assert result == 1.0

    def test_clamped_lower(self):
        """Result clamped to minimum 0.5."""
        result = self._call(0.5, 1.5)
        assert result >= 0.5

    def test_clamped_upper(self):
        """Result clamped to maximum 2.0."""
        result = self._call(2.0, 0.7)
        assert result <= 2.0

    def test_behind_with_penalty_stays_at_penalty(self):
        """Specific regression: urgency=1.5, penalty=1.5 must NOT cancel to 1.0."""
        result = self._call(1.5, 1.5)
        assert result != pytest.approx(1.0), "1.5/1.5=1.0 bug must be fixed"


# ---------------------------------------------------------------------------
# C4 — Sell Confirmation: defer close until CLOB confirms
# ---------------------------------------------------------------------------


class TestSellConfirmation:
    @pytest.mark.asyncio
    async def test_paper_records_close_immediately(self):
        """In paper mode, sell is always filled — record close immediately."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.portfolio = AsyncMock()
            engine.portfolio.record_trade_close = AsyncMock(return_value=0.5)
            engine.risk_manager = MagicMock()
            engine.order_manager = AsyncMock()

            # Paper mode: close_position returns filled trade
            trade = MagicMock()
            trade.status = "filled"
            engine.order_manager.close_position = AsyncMock(return_value=trade)

            pos = make_position()

            with patch("bot.agent.engine.log_exit_triggered", new_callable=AsyncMock), \
                 patch("bot.agent.engine.log_position_closed", new_callable=AsyncMock):
                await engine._close_position(pos)

            engine.portfolio.record_trade_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_live_pending_defers_close(self):
        """In live mode, pending sell should NOT record close immediately."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.portfolio = AsyncMock()
            engine.portfolio.record_trade_close = AsyncMock(return_value=0.5)
            engine.risk_manager = MagicMock()
            engine.order_manager = AsyncMock()

            # Live mode: close_position returns pending trade
            trade = MagicMock()
            trade.status = "pending"
            engine.order_manager.close_position = AsyncMock(return_value=trade)

            pos = make_position()

            with patch("bot.agent.engine.log_exit_triggered", new_callable=AsyncMock):
                await engine._close_position(pos)

            engine.portfolio.record_trade_close.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_position_none_trade(self):
        """If close_position returns None, no PnL is recorded."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.portfolio = AsyncMock()
            engine.risk_manager = MagicMock()
            engine.order_manager = AsyncMock()
            engine.order_manager.close_position = AsyncMock(return_value=None)

            pos = make_position()

            with patch("bot.agent.engine.log_exit_triggered", new_callable=AsyncMock):
                await engine._close_position(pos)

            engine.portfolio.record_trade_close.assert_not_called()

    @pytest.mark.asyncio
    async def test_sell_fill_callback_records_close(self):
        """_handle_sell_fill callback should record the trade close."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.portfolio = AsyncMock()
            engine.portfolio.record_trade_close = AsyncMock(return_value=-0.3)
            engine.risk_manager = MagicMock()

            with patch("bot.agent.engine.log_position_closed", new_callable=AsyncMock):
                await engine._handle_sell_fill("mkt1", 0.45)

            engine.portfolio.record_trade_close.assert_called_once_with("mkt1", 0.45)
            engine.risk_manager.update_daily_pnl.assert_called_once_with(-0.3)


# ---------------------------------------------------------------------------
# H5 — Background task exception handling
# ---------------------------------------------------------------------------


class TestBackgroundTaskExceptionHandler:
    def test_task_exception_handler_logs_error(self):
        """_task_exception_handler should log the exception from failed tasks."""
        import asyncio
        from unittest.mock import patch as mock_patch

        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            # Create a mock task that has an exception
            task = MagicMock()
            task.cancelled.return_value = False
            task.exception.return_value = RuntimeError("test error")
            task.get_name.return_value = "test_task"

            # Should not raise
            with mock_patch("bot.agent.engine.logger") as mock_logger:
                engine._task_exception_handler(task)
                mock_logger.error.assert_called_once()


class TestHeartbeatCriticalCallback:
    @pytest.mark.asyncio
    async def test_heartbeat_pauses_after_threshold(self):
        """HeartbeatManager should invoke critical callback after 5 failures."""
        from bot.polymarket.heartbeat import HeartbeatManager

        clob = AsyncMock()
        clob.is_paper = False
        clob._clob_client = MagicMock()
        clob._clob_client.get_ok = MagicMock(side_effect=Exception("conn error"))

        hb = HeartbeatManager(clob)
        callback = AsyncMock()
        hb.set_on_critical_callback(callback)

        # Simulate 5 heartbeat failures
        for _ in range(5):
            await hb._heartbeat_once()

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_heartbeat_resets_on_success(self):
        """Successful heartbeat should reset miss count."""
        from bot.polymarket.heartbeat import HeartbeatManager

        clob = AsyncMock()
        clob.is_paper = False
        clob._clob_client = MagicMock()
        # First 4 fail, then succeed
        clob._clob_client.get_ok = MagicMock(side_effect=[
            Exception("1"), Exception("2"), Exception("3"), Exception("4"), None
        ])

        hb = HeartbeatManager(clob)
        callback = AsyncMock()
        hb.set_on_critical_callback(callback)

        for _ in range(5):
            await hb._heartbeat_once()

        # Should not have triggered — success at 5th call resets count
        callback.assert_not_called()
        assert hb._miss_count == 0
