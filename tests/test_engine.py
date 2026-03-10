"""Tests for TradingEngine — main orchestration loop."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.engine import TradingEngine
from bot.config import CapitalTier, trading_day
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
    avg_price: float = 0.50,
    unrealized_pnl: float | None = None,
    created_at: datetime | None = None,
) -> Position:
    pnl = unrealized_pnl if unrealized_pnl is not None else (current_price - avg_price) * size
    pos = Position(
        market_id=market_id,
        token_id=token_id,
        question="Will X?",
        outcome="Yes",
        category="crypto",
        strategy=strategy,
        side="BUY",
        size=size,
        avg_price=avg_price,
        current_price=current_price,
        cost_basis=avg_price * size,
        unrealized_pnl=pnl,
        is_open=True,
    )
    if created_at is not None:
        pos.created_at = created_at
    return pos


def _make_engine(**kwargs):
    """Construct a TradingEngine with all external clients patched out."""
    with patch("bot.agent.engine.PolymarketClient"), \
         patch("bot.agent.engine.GammaClient"), \
         patch("bot.agent.engine.DataApiClient"), \
         patch("bot.agent.engine.MarketCache"), \
         patch("bot.agent.engine.WebSocketManager"), \
         patch("bot.agent.engine.HeartbeatManager"):
        engine = TradingEngine()
        for attr, val in kwargs.items():
            setattr(engine, attr, val)
        _rewire_closer(engine)
        return engine


def _rewire_closer(engine):
    """Update closer references after mocking engine components."""
    engine.closer.order_manager = engine.order_manager
    engine.closer.portfolio = engine.portfolio
    engine.closer.risk_manager = engine.risk_manager


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
            migrations_path = (
                "bot.data.settings_store.SettingsStore.run_migrations"
            )
            pause_path = (
                "bot.data.settings_store.StateStore.load_trading_paused"
            )
            with patch.object(engine, "_seed_strategy_metrics", new_callable=AsyncMock), \
                 patch.object(engine, "_restore_state", new_callable=AsyncMock), \
                 patch(settings_path, new_callable=AsyncMock, return_value=0), \
                 patch(migrations_path, new_callable=AsyncMock, return_value=0), \
                 patch(pause_path, new_callable=AsyncMock, return_value=False):
                await engine.initialize()

            mock_clob.initialize.assert_called_once()
            mock_gamma.initialize.assert_called_once()
            mock_data_api.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_logs_when_settings_restored(self):
        """initialize() should log when settings are restored from DB (count > 0)."""
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

            settings_path = "bot.data.settings_store.SettingsStore.load_and_apply"
            migrations_path = "bot.data.settings_store.SettingsStore.run_migrations"
            pause_path = "bot.data.settings_store.StateStore.load_trading_paused"
            with patch.object(engine, "_seed_strategy_metrics", new_callable=AsyncMock), \
                 patch.object(engine, "_restore_state", new_callable=AsyncMock), \
                 patch(settings_path, new_callable=AsyncMock, return_value=3), \
                 patch(migrations_path, new_callable=AsyncMock, return_value=0), \
                 patch(pause_path, new_callable=AsyncMock, return_value=False), \
                 patch("bot.agent.engine.logger") as mock_logger:
                await engine.initialize()

            # logger.info called at least once for settings_restored_from_db
            calls = [str(c) for c in mock_logger.info.call_args_list]
            assert any("settings_restored" in c for c in calls)


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
            engine.clob_client = AsyncMock()

            await engine.shutdown()

            assert engine._running is False
            engine.heartbeat.stop.assert_called_once()
            engine.ws_manager.disconnect.assert_called_once()
            engine.gamma_client.close.assert_called_once()
            engine.data_api.close.assert_called_once()
            engine.clob_client.close.assert_called_once()


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
        """Spread > 10 cents should fail for standard strategies."""
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
                    bids=[OrderBookEntry(price=0.70, size=100)],
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
    async def test_tight_spread_but_low_bid_fails(self):
        """Spread within limit but bid too low relative to fair price → no exit liquidity."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            engine.clob_client = AsyncMock()
            # Spread = 0.83 - 0.80 = 0.03 (within 0.05 limit)
            # best_bid = 0.80, fair_price = 0.86
            # 0.80 < 0.86 * 0.50 = 0.43? No, 0.80 > 0.43 — won't trigger
            # Use: fair_price=0.50, best_bid=0.20, spread=0.04 (tight enough)
            # 0.20 < 0.50 * 0.50 = 0.25 — triggers low bid check
            engine.clob_client.get_order_book = AsyncMock(
                return_value=OrderBook(
                    asset_id="token1",
                    bids=[OrderBookEntry(price=0.20, size=100)],
                    asks=[OrderBookEntry(price=0.23, size=100)],  # spread=0.03 < 0.05
                )
            )

            with patch("bot.agent.engine.log_liquidity_rejected", new_callable=AsyncMock):
                signal = make_signal(market_price=0.50)  # fair_price=0.50; bid 0.20 < 0.25
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
            _rewire_closer(engine)

            signal = make_signal(market_id="mkt_fill")
            await engine.closer.handle_order_fill(signal, shares=10.0, actual_price=signal.market_price)

            engine.portfolio.record_trade_open.assert_called_once()
            call_kwargs = engine.portfolio.record_trade_open.call_args.kwargs
            assert call_kwargs["market_id"] == "mkt_fill"
            assert call_kwargs["size"] == 10.0

    @pytest.mark.asyncio
    async def test_fill_callback_uses_actual_price(self):
        """handle_order_fill should record at actual_price (CLOB fill price)."""
        engine = _make_engine()
        engine.portfolio = AsyncMock()
        engine.portfolio.record_trade_open = AsyncMock()
        _rewire_closer(engine)

        signal = make_signal(market_id="mkt_price", market_price=0.77)
        await engine.closer.handle_order_fill(signal, shares=5.0, actual_price=0.75)

        call_kwargs = engine.portfolio.record_trade_open.call_args.kwargs
        assert call_kwargs["price"] == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_fill_callback_passes_category_from_metadata(self):
        """_handle_order_fill extracts category from signal.metadata."""
        engine = _make_engine()
        engine.portfolio = AsyncMock()
        engine.portfolio.record_trade_open = AsyncMock()
        _rewire_closer(engine)

        signal = make_signal(metadata={"category": "sports"})
        await engine.closer.handle_order_fill(signal, shares=6.0, actual_price=signal.market_price)

        call_kwargs = engine.portfolio.record_trade_open.call_args.kwargs
        assert call_kwargs["category"] == "sports"

    @pytest.mark.asyncio
    async def test_fill_callback_missing_category_defaults_empty(self):
        """_handle_order_fill uses empty string when metadata has no category."""
        engine = _make_engine()
        engine.portfolio = AsyncMock()
        engine.portfolio.record_trade_open = AsyncMock()
        _rewire_closer(engine)

        # Build a signal with no category key in metadata
        signal = TradeSignal(
            strategy="time_decay",
            market_id="mkt_nocat",
            token_id="token1",
            question="Will X happen?",
            outcome="Yes",
            side=OrderSide.BUY,
            estimated_prob=0.90,
            market_price=0.85,
            edge=0.05,
            size_usd=5.0,
            confidence=0.80,
            metadata={},  # explicitly empty — no "category" key
        )
        await engine.closer.handle_order_fill(signal, shares=5.0, actual_price=signal.market_price)

        call_kwargs = engine.portfolio.record_trade_open.call_args.kwargs
        assert call_kwargs["category"] == ""


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
        result = self._call(1.5, 1.5)
        assert result >= 1.5, "Penalty should not be reduced"

    def test_behind_winning_relaxes(self):
        """Behind target + winning strategy: relax the edge requirement."""
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

            trade = MagicMock()
            trade.status = "filled"
            trade.id = 1
            engine.order_manager.close_position = AsyncMock(return_value=trade)

            mock_repo = AsyncMock()
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            pos = make_position()

            _rewire_closer(engine)

            with patch("bot.agent.position_closer.log_exit_triggered", new_callable=AsyncMock), \
                 patch("bot.agent.position_closer.log_position_closed", new_callable=AsyncMock), \
                 patch("bot.agent.position_closer.async_session", return_value=mock_session), \
                 patch("bot.data.repositories.TradeRepository", return_value=mock_repo):
                await engine.closer.close_position(pos)

            engine.portfolio.record_trade_close.assert_called_once()
            mock_repo.update_status.assert_awaited_once()

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

            trade = MagicMock()
            trade.status = "pending"
            engine.order_manager.close_position = AsyncMock(return_value=trade)
            _rewire_closer(engine)

            pos = make_position()

            with patch("bot.agent.position_closer.log_exit_triggered", new_callable=AsyncMock):
                await engine.closer.close_position(pos)

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
            _rewire_closer(engine)

            pos = make_position()

            with patch("bot.agent.position_closer.log_exit_triggered", new_callable=AsyncMock):
                await engine.closer.close_position(pos)

            engine.portfolio.record_trade_close.assert_not_called()

    @pytest.mark.asyncio
    async def test_sell_fill_callback_records_close(self):
        """_handle_sell_fill callback should record the trade close and update Trade PnL."""
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
            _rewire_closer(engine)

            mock_repo = AsyncMock()
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            with patch("bot.agent.position_closer.log_position_closed", new_callable=AsyncMock), \
                 patch("bot.agent.position_closer.async_session", return_value=mock_session), \
                 patch("bot.data.repositories.TradeRepository", return_value=mock_repo):
                await engine.closer.handle_sell_fill("mkt1", 0.45, trade_id=99, shares=10.0)

            engine.portfolio.record_trade_close.assert_called_once_with("mkt1", 0.45)
            engine.risk_manager.update_daily_pnl.assert_called_once_with(-0.3)
            mock_repo.update_status.assert_awaited_once_with(99, "filled", pnl=-0.3)

    @pytest.mark.asyncio
    async def test_close_position_records_correct_pnl(self):
        """_close_position passes current_price to record_trade_close and writes PnL to Trade."""
        engine = _make_engine()
        engine.portfolio = AsyncMock()
        engine.portfolio.record_trade_close = AsyncMock(return_value=1.5)
        engine.risk_manager = MagicMock()
        engine.order_manager = AsyncMock()

        trade = MagicMock()
        trade.status = "filled"
        trade.id = 42
        engine.order_manager.close_position = AsyncMock(return_value=trade)
        _rewire_closer(engine)

        mock_repo = AsyncMock()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        pos = make_position(market_id="mkt_pnl", current_price=0.88)

        with patch("bot.agent.position_closer.log_exit_triggered", new_callable=AsyncMock), \
             patch("bot.agent.position_closer.log_position_closed", new_callable=AsyncMock), \
             patch("bot.agent.position_closer.async_session", return_value=mock_session), \
             patch("bot.data.repositories.TradeRepository", return_value=mock_repo):
            await engine.closer.close_position(pos)

        engine.portfolio.record_trade_close.assert_called_once_with("mkt_pnl", 0.88)
        engine.risk_manager.update_daily_pnl.assert_called_once_with(1.5)
        mock_repo.update_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sell_fill_updates_daily_pnl_with_positive_pnl(self):
        """_handle_sell_fill passes positive PnL to risk_manager."""
        engine = _make_engine()
        engine.portfolio = AsyncMock()
        engine.portfolio.record_trade_close = AsyncMock(return_value=2.0)
        engine.risk_manager = MagicMock()
        _rewire_closer(engine)

        mock_repo = AsyncMock()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("bot.agent.position_closer.log_position_closed", new_callable=AsyncMock), \
             patch("bot.agent.position_closer.async_session", return_value=mock_session), \
             patch("bot.data.repositories.TradeRepository", return_value=mock_repo):
            await engine.closer.handle_sell_fill("mkt_win", 0.95, trade_id=77, shares=5.0)

        engine.risk_manager.update_daily_pnl.assert_called_once_with(2.0)
        mock_repo.update_status.assert_awaited_once_with(77, "filled", pnl=2.0)


# ---------------------------------------------------------------------------
# H5 — Background task exception handling
# ---------------------------------------------------------------------------


class TestBackgroundTaskExceptionHandler:
    def test_task_exception_handler_logs_error(self):
        """_task_exception_handler should log the exception from failed tasks."""
        with patch("bot.agent.engine.PolymarketClient"), \
             patch("bot.agent.engine.GammaClient"), \
             patch("bot.agent.engine.DataApiClient"), \
             patch("bot.agent.engine.MarketCache"), \
             patch("bot.agent.engine.WebSocketManager"), \
             patch("bot.agent.engine.HeartbeatManager"):
            engine = TradingEngine()
            task = MagicMock()
            task.cancelled.return_value = False
            task.exception.return_value = RuntimeError("test error")
            task.get_name.return_value = "test_task"

            with patch("bot.agent.engine.logger") as mock_logger:
                engine._task_exception_handler(task)
                mock_logger.error.assert_called_once()

    def test_task_exception_handler_ignores_cancelled(self):
        """_task_exception_handler should return early for cancelled tasks."""
        engine = _make_engine()
        task = MagicMock()
        task.cancelled.return_value = True

        with patch("bot.agent.engine.logger") as mock_logger:
            engine._task_exception_handler(task)
            mock_logger.error.assert_not_called()

    def test_task_exception_handler_no_exception_no_log(self):
        """_task_exception_handler should not log when task has no exception."""
        engine = _make_engine()
        task = MagicMock()
        task.cancelled.return_value = False
        task.exception.return_value = None

        with patch("bot.agent.engine.logger") as mock_logger:
            engine._task_exception_handler(task)
            mock_logger.error.assert_not_called()


# ---------------------------------------------------------------------------
# Heartbeat critical callback
# ---------------------------------------------------------------------------


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
        clob._clob_client.get_ok = MagicMock(side_effect=[
            Exception("1"), Exception("2"), Exception("3"), Exception("4"), None
        ])

        hb = HeartbeatManager(clob)
        callback = AsyncMock()
        hb.set_on_critical_callback(callback)

        for _ in range(5):
            await hb._heartbeat_once()

        callback.assert_not_called()
        assert hb._miss_count == 0


# ---------------------------------------------------------------------------
# _maybe_daily_summary
# ---------------------------------------------------------------------------


class TestMaybeDailySummary:
    @pytest.mark.asyncio
    async def test_daily_summary_not_sent_if_already_sent_today(self):
        """_maybe_daily_summary skips if last summary was today."""
        engine = _make_engine()
        engine.portfolio = MagicMock()
        engine._last_daily_summary = trading_day()

        with patch("bot.agent.engine.notify_daily_summary", new_callable=AsyncMock) as mock_notify:
            await engine._maybe_daily_summary()
            mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_daily_summary_does_not_duplicate_on_same_day(self):
        """_maybe_daily_summary must not re-send if called twice on the same day."""
        engine = _make_engine()
        engine.portfolio = MagicMock()
        engine.portfolio.get_overview.return_value = {
            "total_equity": 12.0,
            "realized_pnl_today": 0.5,
        }
        # Mark today as already sent
        engine._last_daily_summary = trading_day()

        with patch("bot.agent.engine.notify_daily_summary", new_callable=AsyncMock) as mock_notify:
            await engine._maybe_daily_summary()
            await engine._maybe_daily_summary()
            mock_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_daily_summary_sends_at_midnight_window(self):
        """_maybe_daily_summary sends at local midnight (03:00 UTC with offset=-3)."""
        engine = _make_engine()
        engine.portfolio = MagicMock()
        engine.portfolio.get_overview.return_value = {
            "total_equity": 11.8,
            "realized_pnl_today": 0.3,
            "day_start_equity": 11.5,
        }
        engine._last_daily_summary = "1999-01-01"  # Force "not sent today" state

        with patch("bot.agent.engine.notify_daily_summary", new_callable=AsyncMock) as mock_notify, \
             patch("bot.agent.engine.datetime") as mock_dt_module, \
             patch("bot.agent.engine.trading_day", return_value="2099-06-01"), \
             patch("bot.agent.engine.async_session") as mock_session_ctx:
            # 03:00 UTC = midnight BRT (offset -3), local_hour = (3 + (-3)) % 24 = 0
            mock_now = MagicMock()
            mock_now.strftime.return_value = "2099-06-01"
            mock_now.hour = 3
            mock_now.minute = 0
            mock_dt_module.now.return_value = mock_now

            # Mock the DB session for get_today_stats
            mock_repo = MagicMock()
            mock_repo.get_today_stats = AsyncMock(return_value={
                "trades_today": 5,
                "win_rate_today": 0.60,
            })
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__.return_value = mock_session
            with patch("bot.data.repositories.TradeRepository.get_today_stats", new=mock_repo.get_today_stats):
                await engine._maybe_daily_summary()

        # daily_pnl = equity - day_start = 11.8 - 11.5 = 0.3
        call_kwargs = mock_notify.call_args.kwargs
        assert call_kwargs["equity"] == 11.8
        assert call_kwargs["daily_pnl"] == pytest.approx(0.3)
        assert abs(call_kwargs["daily_return"] - 0.3 / 11.5) < 0.001
        assert call_kwargs["trades"] == 5
        assert call_kwargs["win_rate"] == 0.60


# ---------------------------------------------------------------------------
# _try_rebalance
# ---------------------------------------------------------------------------


class TestTryRebalance:
    @pytest.mark.asyncio
    async def test_rebalance_skips_low_edge_signal(self):
        """_try_rebalance returns None when signal.edge < min_rebalance_edge."""
        engine = _make_engine()
        engine.portfolio = MagicMock()
        engine.portfolio.positions = []
        _rewire_closer(engine)

        signal = make_signal(edge=0.01)  # Below 0.03 threshold
        result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)
        assert result is None

    @pytest.mark.asyncio
    async def test_rebalance_skips_when_no_losing_positions(self):
        """_try_rebalance returns None when all positions are winners."""
        engine = _make_engine()

        winning_pos = make_position(
            market_id="mkt_win",
            avg_price=0.50,
            current_price=0.70,
            unrealized_pnl=2.0,  # positive PnL = winner
            created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        engine.portfolio = MagicMock()
        engine.portfolio.positions = [winning_pos]
        _rewire_closer(engine)

        signal = make_signal(edge=0.05)
        result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)
        assert result is None

    @pytest.mark.asyncio
    async def test_rebalance_closes_worst_loser(self):
        """_try_rebalance closes the position with worst (most negative) PnL%."""
        engine = _make_engine()
        engine.order_manager = AsyncMock()

        close_trade = MagicMock()
        close_trade.status = "filled"
        engine.order_manager.close_position = AsyncMock(return_value=close_trade)

        old_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
        loser = make_position(
            market_id="mkt_loser",
            avg_price=0.80,
            current_price=0.40,
            size=10.0,
            unrealized_pnl=-4.0,
            created_at=old_time,
        )
        engine.portfolio = MagicMock()
        engine.portfolio.positions = [loser]
        _rewire_closer(engine)

        signal = make_signal(edge=0.05)

        with patch("bot.agent.position_closer.log_rebalance", new_callable=AsyncMock):
            result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)

        assert result is not None
        closed_pos, rebal_trade = result
        assert closed_pos is loser
        assert rebal_trade.status == "filled"
        engine.order_manager.close_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_rebalance_returns_none_when_close_fails(self):
        """_try_rebalance returns None when order_manager cannot close the position."""
        engine = _make_engine()
        engine.order_manager = AsyncMock()
        engine.order_manager.close_position = AsyncMock(return_value=None)

        old_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
        loser = make_position(
            market_id="mkt_fail",
            avg_price=0.80,
            current_price=0.40,
            size=10.0,
            unrealized_pnl=-4.0,
            created_at=old_time,
        )
        engine.portfolio = MagicMock()
        engine.portfolio.positions = [loser]
        _rewire_closer(engine)

        signal = make_signal(edge=0.05)
        result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)
        assert result is None

    @pytest.mark.asyncio
    async def test_rebalance_skips_positions_held_too_briefly(self):
        """_try_rebalance ignores positions held less than 5 minutes."""
        engine = _make_engine()

        # Position just created (within 5-minute window)
        fresh_loser = make_position(
            market_id="mkt_fresh",
            avg_price=0.80,
            current_price=0.40,
            size=10.0,
            unrealized_pnl=-4.0,
            created_at=datetime.now(timezone.utc),  # just now
        )
        engine.portfolio = MagicMock()
        engine.portfolio.positions = [fresh_loser]
        _rewire_closer(engine)

        signal = make_signal(edge=0.05)
        result = await engine.closer.try_rebalance(signal, engine.portfolio.positions)
        assert result is None


# ---------------------------------------------------------------------------
# _mark_scan_traded
# ---------------------------------------------------------------------------


class TestMarkScanTraded:
    @pytest.mark.asyncio
    async def test_mark_scan_traded_handles_exception_gracefully(self):
        """_mark_scan_traded should not propagate exceptions."""
        engine = _make_engine()

        signal = make_signal(market_id="mkt_scan", strategy="time_decay")

        with patch("bot.agent.engine.async_session") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(side_effect=Exception("DB error"))
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            # Should not raise
            await engine._mark_scan_traded(signal)

    @pytest.mark.asyncio
    async def test_mark_scan_traded_calls_repo(self):
        """_mark_scan_traded calls TradeRepository.mark_scan_traded."""
        engine = _make_engine()
        signal = make_signal(market_id="mkt_mark", strategy="arbitrage")

        mock_repo = AsyncMock()
        mock_repo.mark_scan_traded = AsyncMock()

        # TradeRepository is imported locally inside _mark_scan_traded
        with patch("bot.agent.engine.async_session") as mock_session, \
             patch("bot.data.repositories.TradeRepository", return_value=mock_repo):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = mock_ctx

            await engine._mark_scan_traded(signal)

            mock_repo.mark_scan_traded.assert_called_once_with("mkt_mark", "arbitrage")


# ---------------------------------------------------------------------------
# run() — main loop
# ---------------------------------------------------------------------------


class TestEngineRun:
    @pytest.mark.asyncio
    async def test_run_sets_running_flag(self):
        """run() should set _running to True at start."""
        engine = _make_engine()
        engine.heartbeat = AsyncMock()
        engine.heartbeat.start = AsyncMock(return_value=None)
        engine.ws_manager = AsyncMock()
        engine.ws_manager.connect = AsyncMock(return_value=None)

        call_count = 0

        async def fake_cycle():
            nonlocal call_count
            call_count += 1
            engine._running = False  # Stop after one cycle

        engine._trading_cycle = fake_cycle

        with patch("bot.agent.engine.asyncio.sleep", new_callable=AsyncMock), \
             patch("bot.agent.engine.asyncio.create_task") as mock_create_task:
            mock_task = MagicMock()
            mock_task.add_done_callback = MagicMock()
            mock_create_task.return_value = mock_task

            await engine.run()

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_run_handles_cycle_exception(self):
        """run() should catch exceptions from _trading_cycle and continue."""
        engine = _make_engine()
        engine.heartbeat = AsyncMock()
        engine.ws_manager = AsyncMock()

        call_count = 0

        async def failing_cycle():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Cycle error")
            engine._running = False

        engine._trading_cycle = failing_cycle

        with patch("bot.agent.engine.asyncio.sleep", new_callable=AsyncMock), \
             patch("bot.agent.engine.asyncio.create_task") as mock_create_task, \
             patch("bot.agent.engine.notify_error", new_callable=AsyncMock):
            mock_task = MagicMock()
            mock_task.add_done_callback = MagicMock()
            mock_create_task.return_value = mock_task

            await engine.run()

        assert call_count == 2


# ---------------------------------------------------------------------------
# _seed_strategy_metrics
# ---------------------------------------------------------------------------


class TestSeedStrategyMetrics:
    @pytest.mark.asyncio
    async def test_seed_creates_missing_metrics(self):
        """_seed_strategy_metrics should create a record for each strategy with no existing metric."""
        engine = _make_engine()

        # Give the analyzer real strategy names
        mock_s1 = MagicMock()
        mock_s1.name = "time_decay"
        mock_s2 = MagicMock()
        mock_s2.name = "arbitrage"
        engine.analyzer = MagicMock()
        engine.analyzer.strategies = [mock_s1, mock_s2]

        mock_repo = AsyncMock()
        mock_repo.get_all_latest = AsyncMock(return_value=[])  # No existing metrics
        mock_repo.upsert = AsyncMock()

        with patch("bot.agent.engine.async_session") as mock_session, \
             patch("bot.agent.engine.StrategyMetricRepository", return_value=mock_repo):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = mock_ctx

            await engine._seed_strategy_metrics()

        assert mock_repo.upsert.call_count == 2

    @pytest.mark.asyncio
    async def test_seed_skips_already_existing_metrics(self):
        """_seed_strategy_metrics should not upsert a strategy that already has a metric."""
        engine = _make_engine()

        mock_s1 = MagicMock()
        mock_s1.name = "time_decay"
        engine.analyzer = MagicMock()
        engine.analyzer.strategies = [mock_s1]

        existing_metric = MagicMock()
        existing_metric.strategy = "time_decay"

        mock_repo = AsyncMock()
        mock_repo.get_all_latest = AsyncMock(return_value=[existing_metric])
        mock_repo.upsert = AsyncMock()

        with patch("bot.agent.engine.async_session") as mock_session, \
             patch("bot.agent.engine.StrategyMetricRepository", return_value=mock_repo):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = mock_ctx

            await engine._seed_strategy_metrics()

        mock_repo.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_partial_existing_creates_only_missing(self):
        """_seed_strategy_metrics creates only the missing strategy's metric."""
        engine = _make_engine()

        mock_s1 = MagicMock()
        mock_s1.name = "time_decay"
        mock_s2 = MagicMock()
        mock_s2.name = "arbitrage"
        engine.analyzer = MagicMock()
        engine.analyzer.strategies = [mock_s1, mock_s2]

        existing = MagicMock()
        existing.strategy = "time_decay"  # time_decay already exists

        mock_repo = AsyncMock()
        mock_repo.get_all_latest = AsyncMock(return_value=[existing])
        mock_repo.upsert = AsyncMock()

        with patch("bot.agent.engine.async_session") as mock_session, \
             patch("bot.agent.engine.StrategyMetricRepository", return_value=mock_repo):
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.return_value = mock_ctx

            await engine._seed_strategy_metrics()

        # Only arbitrage should be upserted
        assert mock_repo.upsert.call_count == 1
        call_arg = mock_repo.upsert.call_args[0][0]
        assert call_arg.strategy == "arbitrage"


# ---------------------------------------------------------------------------
# Notification wiring: strategy pause, daily target, risk limits
# ---------------------------------------------------------------------------


class TestEngineNotifications:
    """Test that the engine sends notifications + activity logs for key events."""

    def _setup_engine(self):
        """Create a patched engine with mock portfolio and risk manager."""
        engine = _make_engine()
        engine.portfolio = MagicMock()
        engine.portfolio.total_equity = 10.0
        engine.portfolio._realized_pnl_today = 0.15
        engine.portfolio._day_start_equity = 10.0
        engine.risk_manager = MagicMock()
        engine.risk_manager._daily_pnl = -1.5
        engine.risk_manager.get_risk_metrics.return_value = {
            "daily_loss_limit_pct": 0.10,
            "current_drawdown_pct": 0.25,
            "max_drawdown_limit_pct": 0.20,
        }
        return engine

    async def test_maybe_notify_risk_limit_daily_loss(self):
        """_maybe_notify_risk_limit fires once for daily loss."""
        engine = self._setup_engine()

        with patch("bot.agent.engine.log_risk_limit_hit", new_callable=AsyncMock) as mock_log, \
             patch("bot.agent.engine.notify_risk_limit", new_callable=AsyncMock) as mock_notify:
            await engine._maybe_notify_risk_limit("Daily loss limit reached: $-1.50")
            mock_log.assert_called_once()
            mock_notify.assert_called_once()
            # First positional arg is limit_type
            assert mock_log.call_args[0][0] == "daily_loss"

    async def test_maybe_notify_risk_limit_drawdown(self):
        """_maybe_notify_risk_limit fires once for max drawdown."""
        engine = self._setup_engine()

        with patch("bot.agent.engine.log_risk_limit_hit", new_callable=AsyncMock) as mock_log, \
             patch("bot.agent.engine.notify_risk_limit", new_callable=AsyncMock) as mock_notify:
            await engine._maybe_notify_risk_limit("Max drawdown exceeded: 25.0% > 20.0%")
            mock_log.assert_called_once()
            mock_notify.assert_called_once()

    async def test_risk_limit_notifies_once_per_day(self):
        """Second call on the same day should NOT send another notification."""
        engine = self._setup_engine()

        with patch("bot.agent.engine.log_risk_limit_hit", new_callable=AsyncMock) as mock_log, \
             patch("bot.agent.engine.notify_risk_limit", new_callable=AsyncMock) as mock_notify:
            await engine._maybe_notify_risk_limit("Daily loss limit reached")
            await engine._maybe_notify_risk_limit("Daily loss limit reached")
            # Should be called only once despite two invocations
            assert mock_log.call_count == 1
            assert mock_notify.call_count == 1

    async def test_risk_limit_ignores_unrelated_reasons(self):
        """Reasons that don't mention daily loss or drawdown should be ignored."""
        engine = self._setup_engine()

        with patch("bot.agent.engine.log_risk_limit_hit", new_callable=AsyncMock) as mock_log, \
             patch("bot.agent.engine.notify_risk_limit", new_callable=AsyncMock) as mock_notify:
            await engine._maybe_notify_risk_limit("Max positions reached: 3 >= 3")
            mock_log.assert_not_called()
            mock_notify.assert_not_called()

    async def test_strategy_pause_notification_wiring(self):
        """Engine should send notifications for newly paused strategies."""
        engine = self._setup_engine()
        engine.learner = MagicMock()
        engine.learner._newly_paused = [("time_decay", 0.20, -2.5)]

        with patch("bot.agent.engine.log_strategy_paused", new_callable=AsyncMock) as mock_log, \
             patch("bot.agent.engine.notify_strategy_paused", new_callable=AsyncMock) as mock_notify:
            for s_name, s_wr, s_pnl in engine.learner._newly_paused:
                await mock_log(s_name, s_wr, s_pnl)
                await mock_notify(s_name, f"Win rate {s_wr:.0%}, PnL ${s_pnl:+.2f}")

            mock_log.assert_called_once_with("time_decay", 0.20, -2.5)
            mock_notify.assert_called_once()
            assert "time_decay" in mock_notify.call_args[0][0]

    async def test_init_has_notification_tracking_attrs(self):
        """Engine init should have _target_notified_day and _risk_limit_notified."""
        engine = _make_engine()
        assert engine._target_notified_day == ""
        assert engine._risk_limit_notified == {}


# ---------------------------------------------------------------------------
# Trading cycle integration tests
# ---------------------------------------------------------------------------


def _make_learner_adjustments(
    paused_strategies: set[str] | None = None,
    urgency_multiplier: float = 1.0,
):
    """Create a mock learner adjustments object."""
    adj = MagicMock()
    adj.paused_strategies = paused_strategies or set()
    adj.urgency_multiplier = urgency_multiplier
    adj.daily_progress = 0.0
    adj.edge_multipliers = {}
    adj.category_confidences = {}
    adj.category_min_edges = {}
    adj.calibration = {}
    return adj


class TestTradingCycleIntegration:
    """Integration tests for _trading_cycle, _evaluate_signals, and _process_exits."""

    @pytest.mark.asyncio
    async def test_full_buy_flow(self):
        """Signal -> approved -> filled -> position recorded."""
        engine = _make_engine()
        engine.portfolio = AsyncMock()
        engine.portfolio.cash = 20.0
        engine.portfolio.total_equity = 20.0
        engine.portfolio.positions = []
        engine.portfolio.tier = CapitalTier.TIER1
        engine.portfolio.open_position_count = 0

        engine.analyzer = AsyncMock()
        engine.analyzer.NEAR_WORTHLESS_PRICE = 0.10
        signal = make_signal(market_id="mkt_buy", strategy="time_decay")
        engine.analyzer.scan_markets = AsyncMock(return_value=[signal])

        engine.risk_manager = MagicMock()
        engine.risk_manager.evaluate_signal = AsyncMock(
            return_value=(True, 5.0, "approved")
        )

        filled_trade = MagicMock()
        filled_trade.status = "filled"
        filled_trade.id = 1
        filled_trade.size = 10.0
        filled_trade.price = 0.50
        filled_trade.cost_usd = 5.0
        engine.order_manager = AsyncMock()
        engine.order_manager.execute_signal = AsyncMock(return_value=filled_trade)
        engine.order_manager.pending_count = 0
        engine.order_manager.pending_market_ids = set()

        engine._learner_adjustments = _make_learner_adjustments()
        engine.learner = MagicMock()
        engine.learner.get_edge_multiplier = MagicMock(return_value=1.0)
        engine.research_cache = MagicMock()
        engine.research_cache.get = MagicMock(return_value=None)

        _rewire_closer(engine)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            signals_found, approved, placed = await engine._evaluate_signals(
                CapitalTier.TIER1
            )

        assert signals_found == 1
        assert approved == 1
        assert placed == 1
        engine.order_manager.execute_signal.assert_called_once_with(signal)
        engine.portfolio.record_trade_open.assert_called_once()
        call_kwargs = engine.portfolio.record_trade_open.call_args.kwargs
        assert call_kwargs["market_id"] == "mkt_buy"
        assert call_kwargs["strategy"] == "time_decay"
        assert call_kwargs["size"] == 10.0
        assert call_kwargs["price"] == 0.50

    @pytest.mark.asyncio
    async def test_skip_paused_strategy(self):
        """Signal from a paused strategy is skipped."""
        engine = _make_engine()
        engine.portfolio = AsyncMock()
        engine.portfolio.cash = 20.0
        engine.portfolio.total_equity = 20.0
        engine.portfolio.positions = []
        engine.portfolio.tier = CapitalTier.TIER1

        engine.analyzer = AsyncMock()
        engine.analyzer.NEAR_WORTHLESS_PRICE = 0.10
        signal = make_signal(market_id="mkt_paused", strategy="value_betting")
        engine.analyzer.scan_markets = AsyncMock(return_value=[signal])

        engine.risk_manager = MagicMock()
        engine.risk_manager.evaluate_signal = AsyncMock()

        engine.order_manager = AsyncMock()
        engine.order_manager.pending_count = 0
        engine.order_manager.pending_market_ids = set()

        engine._learner_adjustments = _make_learner_adjustments(
            paused_strategies={"value_betting"}
        )
        engine.learner = MagicMock()
        engine.research_cache = MagicMock()
        engine.research_cache.get = MagicMock(return_value=None)

        _rewire_closer(engine)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock):
            signals_found, approved, placed = await engine._evaluate_signals(
                CapitalTier.TIER1
            )

        assert signals_found == 1
        assert approved == 0
        assert placed == 0
        engine.risk_manager.evaluate_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_market_in_cooldown(self):
        """Signal for a market in cooldown is skipped."""
        engine = _make_engine()
        engine.portfolio = AsyncMock()
        engine.portfolio.cash = 20.0
        engine.portfolio.total_equity = 20.0
        engine.portfolio.positions = []
        engine.portfolio.tier = CapitalTier.TIER1

        engine.analyzer = AsyncMock()
        engine.analyzer.NEAR_WORTHLESS_PRICE = 0.10
        signal = make_signal(market_id="mkt_cool", strategy="time_decay")
        engine.analyzer.scan_markets = AsyncMock(return_value=[signal])

        engine.risk_manager = MagicMock()
        engine.risk_manager.evaluate_signal = AsyncMock()

        engine.order_manager = AsyncMock()
        engine.order_manager.pending_count = 0
        engine.order_manager.pending_market_ids = set()

        engine._learner_adjustments = _make_learner_adjustments()
        engine.learner = MagicMock()
        engine.research_cache = MagicMock()
        engine.research_cache.get = MagicMock(return_value=None)

        # Set cooldown for this market far in the future
        engine._market_cooldown["mkt_cool"] = datetime(
            2099, 1, 1, tzinfo=timezone.utc
        )

        _rewire_closer(engine)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock):
            signals_found, approved, placed = await engine._evaluate_signals(
                CapitalTier.TIER1
            )

        assert signals_found == 1
        assert approved == 0
        assert placed == 0
        engine.risk_manager.evaluate_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_pending_market(self):
        """Signal for a market with a pending order is skipped."""
        engine = _make_engine()
        engine.portfolio = AsyncMock()
        engine.portfolio.cash = 20.0
        engine.portfolio.total_equity = 20.0
        engine.portfolio.positions = []
        engine.portfolio.tier = CapitalTier.TIER1

        engine.analyzer = AsyncMock()
        engine.analyzer.NEAR_WORTHLESS_PRICE = 0.10
        signal = make_signal(market_id="mkt_pending", strategy="time_decay")
        engine.analyzer.scan_markets = AsyncMock(return_value=[signal])

        engine.risk_manager = MagicMock()
        engine.risk_manager.evaluate_signal = AsyncMock()

        engine.order_manager = AsyncMock()
        engine.order_manager.pending_count = 1
        engine.order_manager.pending_market_ids = {"mkt_pending"}

        engine._learner_adjustments = _make_learner_adjustments()
        engine.learner = MagicMock()
        engine.research_cache = MagicMock()
        engine.research_cache.get = MagicMock(return_value=None)

        _rewire_closer(engine)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock):
            signals_found, approved, placed = await engine._evaluate_signals(
                CapitalTier.TIER1
            )

        assert signals_found == 1
        assert approved == 0
        assert placed == 0
        engine.risk_manager.evaluate_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_cycle_committed_tracking(self):
        """Second signal gets reduced bankroll (cycle_committed tracks spending)."""
        engine = _make_engine()
        engine.portfolio = AsyncMock()
        engine.portfolio.cash = 50.0
        engine.portfolio.total_equity = 50.0
        engine.portfolio.positions = []
        engine.portfolio.tier = CapitalTier.TIER1

        signal1 = make_signal(market_id="mkt_a", strategy="time_decay")
        signal2 = make_signal(market_id="mkt_b", strategy="arbitrage")
        engine.analyzer = AsyncMock()
        engine.analyzer.NEAR_WORTHLESS_PRICE = 0.10
        engine.analyzer.scan_markets = AsyncMock(return_value=[signal1, signal2])

        # Track bankroll passed to evaluate_signal
        bankroll_calls: list[float] = []

        async def capture_evaluate(
            signal, bankroll, open_positions, tier, pending_count, edge_multiplier,
            urgency=1.0, calibration=None,
        ):
            bankroll_calls.append(bankroll)
            return (True, 5.0, "approved")

        engine.risk_manager = MagicMock()
        engine.risk_manager.evaluate_signal = AsyncMock(side_effect=capture_evaluate)

        filled_trade = MagicMock()
        filled_trade.status = "filled"
        filled_trade.id = 1
        filled_trade.size = 10.0
        filled_trade.price = 0.50
        filled_trade.cost_usd = 5.0
        engine.order_manager = AsyncMock()
        engine.order_manager.execute_signal = AsyncMock(return_value=filled_trade)
        engine.order_manager.pending_count = 0
        engine.order_manager.pending_market_ids = set()

        engine._learner_adjustments = _make_learner_adjustments()
        engine.learner = MagicMock()
        engine.learner.get_edge_multiplier = MagicMock(return_value=1.0)
        engine.research_cache = MagicMock()
        engine.research_cache.get = MagicMock(return_value=None)

        _rewire_closer(engine)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            signals_found, approved, placed = await engine._evaluate_signals(
                CapitalTier.TIER1
            )

        assert signals_found == 2
        assert approved == 2
        assert placed == 2
        # First signal: full bankroll 50.0
        assert bankroll_calls[0] == 50.0
        # Second signal: reduced by cost_usd of first trade (50.0 - 5.0 = 45.0)
        assert bankroll_calls[1] == 45.0

    @pytest.mark.asyncio
    async def test_process_exits_closes_positions(self):
        """_process_exits calls closer.close_position for exiting positions."""
        engine = _make_engine()
        engine.portfolio = AsyncMock()

        pos = make_position(market_id="mkt_exit", current_price=0.90)
        engine.portfolio.positions = [pos]

        engine.analyzer = AsyncMock()
        engine.analyzer.NEAR_WORTHLESS_PRICE = 0.10
        engine.analyzer.check_exits = AsyncMock(
            return_value=[("mkt_exit", "stop_loss (15% loss)")]
        )

        engine.order_manager = AsyncMock()
        engine.risk_manager = MagicMock()
        engine.closer = AsyncMock()
        engine.closer.close_position = AsyncMock()

        await engine._process_exits(CapitalTier.TIER1)

        engine.analyzer.check_exits.assert_called_once_with(
            [pos], CapitalTier.TIER1
        )
        engine.closer.close_position.assert_called_once_with(
            pos, exit_reason="stop_loss (15% loss)"
        )

    @pytest.mark.asyncio
    async def test_evaluate_signals_returns_counts(self):
        """_evaluate_signals returns (signals_found, approved, placed) tuple."""
        engine = _make_engine()
        engine.portfolio = AsyncMock()
        engine.portfolio.cash = 20.0
        engine.portfolio.total_equity = 20.0
        engine.portfolio.positions = []
        engine.portfolio.tier = CapitalTier.TIER1

        signal1 = make_signal(market_id="mkt_r1", strategy="time_decay")
        signal2 = make_signal(market_id="mkt_r2", strategy="arbitrage")
        engine.analyzer = AsyncMock()
        engine.analyzer.NEAR_WORTHLESS_PRICE = 0.10
        engine.analyzer.scan_markets = AsyncMock(return_value=[signal1, signal2])

        # First signal approved, second rejected
        engine.risk_manager = MagicMock()
        engine.risk_manager.evaluate_signal = AsyncMock(
            side_effect=[
                (True, 5.0, "approved"),
                (False, 0.0, "Max positions reached"),
            ]
        )

        filled_trade = MagicMock()
        filled_trade.status = "filled"
        filled_trade.id = 1
        filled_trade.size = 10.0
        filled_trade.price = 0.50
        filled_trade.cost_usd = 5.0
        engine.order_manager = AsyncMock()
        engine.order_manager.execute_signal = AsyncMock(return_value=filled_trade)
        engine.order_manager.pending_count = 0
        engine.order_manager.pending_market_ids = set()

        engine._learner_adjustments = _make_learner_adjustments()
        engine.learner = MagicMock()
        engine.learner.get_edge_multiplier = MagicMock(return_value=1.0)
        engine.research_cache = MagicMock()
        engine.research_cache.get = MagicMock(return_value=None)

        # No open positions to rebalance
        engine.closer = AsyncMock()
        engine.closer.try_rebalance = AsyncMock(return_value=None)

        with patch("bot.agent.engine.log_signal_found", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_signal_rejected", new_callable=AsyncMock), \
             patch("bot.agent.engine.log_risk_limit_hit", new_callable=AsyncMock), \
             patch("bot.agent.engine.notify_risk_limit", new_callable=AsyncMock), \
             patch("bot.agent.engine.event_bus") as mock_bus, \
             patch.object(engine, "_check_liquidity", new_callable=AsyncMock, return_value=True), \
             patch.object(engine, "_mark_scan_traded", new_callable=AsyncMock):
            mock_bus.emit = AsyncMock()
            result = await engine._evaluate_signals(CapitalTier.TIER1)

        assert isinstance(result, tuple)
        assert len(result) == 3
        signals_found, approved, placed = result
        assert signals_found == 2
        assert approved == 1
        assert placed == 1


# ---------------------------------------------------------------------------
# run() finally block — persists state
# ---------------------------------------------------------------------------


class TestRunPersistsState:
    """run() must call _persist_state in the finally block."""

    @staticmethod
    def _patch_background_tasks(engine):
        """Replace background task coroutines with proper async stubs."""
        async def _noop():
            await asyncio.sleep(999)

        engine.heartbeat.start = _noop
        engine.ws_manager.connect = _noop
        engine.research_engine.start = _noop

    @pytest.mark.asyncio
    async def test_run_persists_state_on_cancel(self):
        """run() calls _persist_state when task is cancelled."""
        engine = _make_engine()
        engine._persist_state = AsyncMock()
        self._patch_background_tasks(engine)

        async def cycle_then_cancel():
            raise asyncio.CancelledError

        engine._trading_cycle = AsyncMock(side_effect=cycle_then_cancel)

        with pytest.raises(asyncio.CancelledError):
            await engine.run()

        engine._persist_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_persists_state_on_normal_stop(self):
        """run() calls _persist_state when _running is set to False."""
        engine = _make_engine()
        engine._persist_state = AsyncMock()
        self._patch_background_tasks(engine)

        call_count = 0

        async def stop_after_one():
            nonlocal call_count
            call_count += 1
            engine._running = False

        engine._trading_cycle = AsyncMock(side_effect=stop_after_one)

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.scan_interval_seconds = 0
            mock_settings.trading_mode.value = "paper"
            await engine.run()

        engine._persist_state.assert_called_once()
        assert call_count == 1
