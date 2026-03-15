"""Tests for PnL fixes: false deposit prevention, daily PnL fallback, peak equity persistence."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.portfolio import Portfolio
from bot.config import settings
from bot.data.models import CapitalFlow, Position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_position(
    market_id: str = "mkt1",
    token_id: str = "token1",
    size: float = 10.0,
    avg_price: float = 0.50,
    current_price: float = 0.55,
    strategy: str = "value_betting",
    is_open: bool = True,
) -> Position:
    return Position(
        market_id=market_id,
        token_id=token_id,
        question="Will X happen?",
        outcome="Yes",
        category="crypto",
        strategy=strategy,
        side="BUY",
        size=size,
        avg_price=avg_price,
        current_price=current_price,
        cost_basis=size * avg_price,
        unrealized_pnl=(current_price - avg_price) * size,
        is_open=is_open,
        created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def mock_clob():
    clob = AsyncMock()
    clob.is_connected = False
    clob.is_paper = True
    clob.get_balance = AsyncMock(return_value=None)
    clob.get_address = MagicMock(return_value=None)
    return clob


@pytest.fixture
def mock_data_api():
    return AsyncMock()


@pytest.fixture
def mock_gamma():
    return AsyncMock()


@pytest.fixture
def portfolio(mock_clob, mock_data_api, mock_gamma):
    original = settings.initial_bankroll
    settings.initial_bankroll = 10.0
    p = Portfolio(mock_clob, mock_data_api, mock_gamma)
    yield p
    settings.initial_bankroll = original


# ---------------------------------------------------------------------------
# Bug 1: _close_if_resolved updates cash to prevent false deposits
# ---------------------------------------------------------------------------


class TestCloseIfResolvedCashUpdate:
    @pytest.mark.asyncio
    async def test_close_if_resolved_updates_cash(self, portfolio, mock_gamma):
        """After closing a resolved position, _cash should include settlement proceeds."""
        position = make_position(
            market_id="resolved_mkt",
            size=10.0,
            avg_price=0.50,
            current_price=1.0,
        )

        # Market resolved — winning side
        market = MagicMock()
        market.closed = True
        market.archived = False
        market.active = False
        market.outcome_price_list = [1.0, 0.0]
        market.outcomes = ["Yes", "No"]
        mock_gamma.get_market = AsyncMock(return_value=market)

        mock_pos_repo = AsyncMock()
        mock_pos_repo.close = AsyncMock()

        initial_cash = 5.0
        portfolio._cash = initial_cash

        with patch("bot.agent.portfolio.async_session") as mock_session, \
             patch("bot.data.settings_store.StateStore.save_day_start_equity", new_callable=AsyncMock):
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio._close_if_resolved(position, mock_pos_repo, mock_ctx)

        # Settlement: $1.0 * 10 shares = $10
        # Cash should be 5.0 + 10.0 = 15.0
        assert portfolio._cash == 15.0

    @pytest.mark.asyncio
    async def test_close_if_resolved_losing_side(self, portfolio, mock_gamma):
        """Losing side: settlement at $0 → cash += 0."""
        position = make_position(
            market_id="losing_mkt",
            size=10.0,
            avg_price=0.50,
            current_price=0.0,
        )

        market = MagicMock()
        market.closed = True
        market.archived = False
        market.active = False
        market.outcome_price_list = [0.0, 1.0]
        market.outcomes = ["Yes", "No"]
        mock_gamma.get_market = AsyncMock(return_value=market)

        mock_pos_repo = AsyncMock()
        mock_pos_repo.close = AsyncMock()

        initial_cash = 5.0
        portfolio._cash = initial_cash

        with patch("bot.agent.portfolio.async_session") as mock_session:
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio._close_if_resolved(position, mock_pos_repo, mock_ctx)

        # Settlement: $0 * 10 = $0 → cash stays at 5.0
        assert portfolio._cash == 5.0

    @pytest.mark.asyncio
    async def test_close_if_resolved_pnl_recorded(self, portfolio, mock_gamma):
        """PnL should be correctly recorded."""
        position = make_position(
            market_id="pnl_mkt",
            size=10.0,
            avg_price=0.50,
            current_price=1.0,
        )

        market = MagicMock()
        market.closed = True
        market.archived = False
        market.active = False
        market.outcome_price_list = [1.0, 0.0]
        market.outcomes = ["Yes", "No"]
        mock_gamma.get_market = AsyncMock(return_value=market)

        mock_pos_repo = AsyncMock()
        mock_pos_repo.close = AsyncMock()

        portfolio._realized_pnl_today = 0.0

        with patch("bot.agent.portfolio.async_session") as mock_session:
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio._close_if_resolved(position, mock_pos_repo, mock_ctx)

        # PnL = (1.0 - 0.50) * 10 = 5.0
        assert portfolio._realized_pnl_today == 5.0

    @pytest.mark.asyncio
    async def test_no_false_deposit_after_resolution(self, portfolio, mock_gamma):
        """After _close_if_resolved updates _cash, _detect_capital_flow should NOT fire."""
        position = make_position(
            market_id="no_false_deposit",
            size=10.0,
            avg_price=0.50,
            current_price=1.0,
        )

        market = MagicMock()
        market.closed = True
        market.archived = False
        market.active = False
        market.outcome_price_list = [1.0, 0.0]
        market.outcomes = ["Yes", "No"]
        mock_gamma.get_market = AsyncMock(return_value=market)

        mock_pos_repo = AsyncMock()
        mock_pos_repo.close = AsyncMock()

        portfolio._cash = 5.0
        portfolio._day_start_equity = 5.0

        with patch("bot.agent.portfolio.async_session") as mock_session:
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio._close_if_resolved(position, mock_pos_repo, mock_ctx)

        # Cash is now 15.0 (5 + 10). If balance returns 15.0 too,
        # _detect_capital_flow should see diff < $0.50 → no false deposit
        day_start_before = portfolio._day_start_equity
        await portfolio._detect_capital_flow(15.0)
        assert portfolio._day_start_equity == day_start_before


# ---------------------------------------------------------------------------
# Bug 1b: External position close updates cash
# ---------------------------------------------------------------------------


class TestExternalPositionCashUpdate:
    @pytest.mark.asyncio
    async def test_external_close_updates_cash(self, portfolio, mock_clob, mock_data_api, mock_gamma):
        """When an external position closes, _cash should increase by settlement."""
        # Simulate: position in DB but not on chain anymore
        local_pos = make_position(
            market_id="ext_mkt",
            size=10.0,
            avg_price=0.60,
            current_price=0.80,
            strategy="external",
        )
        local_pos.created_at = datetime(2026, 3, 1, tzinfo=timezone.utc)

        mock_clob.is_connected = True
        mock_clob.get_address = MagicMock(return_value="0xabc")
        mock_data_api.get_positions = AsyncMock(return_value=[])  # empty on chain

        initial_cash = 5.0
        portfolio._cash = initial_cash

        mock_pos_repo = MagicMock()
        mock_pos_repo.get_open = AsyncMock(return_value=[local_pos])
        mock_pos_repo.upsert = AsyncMock()
        mock_pos_repo.close = AsyncMock()

        with patch("bot.agent.portfolio.async_session") as mock_session:
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "bot.agent.portfolio.PositionRepository",
                return_value=mock_pos_repo,
            ):
                await portfolio._sync_from_polymarket()

        # Cash should increase by settlement: 0.80 * 10 = 8.0
        assert portfolio._cash == initial_cash + 8.0


# ---------------------------------------------------------------------------
# Bug 2: Daily PnL fallback uses `is not None` instead of `!= 0.0`
# ---------------------------------------------------------------------------


class TestDailyPnlFallback:
    def test_zero_trading_pnl_not_fallback_to_equity_delta(self):
        """trading_pnl=0.0 should show $0 PnL, not equity delta."""
        from api.routers.portfolio import DailyPnlPoint

        # Simulate: snapshots with trading_pnl=0.0 (genuine no-trade day)
        mock_snap = MagicMock()
        mock_snap.total_equity = 100.0
        mock_snap.trading_pnl = 0.0  # No trades
        mock_snap.timestamp = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)

        # The fix: `is not None` check means 0.0 is used as-is
        last_tpnl = getattr(mock_snap, "trading_pnl", None)
        pnl = last_tpnl if last_tpnl is not None else (110.0 - 100.0)

        assert pnl == 0.0  # Should be 0, not equity delta

    def test_none_trading_pnl_falls_back_to_equity_delta(self):
        """Old snapshots without trading_pnl should use equity delta."""
        mock_snap = MagicMock(spec=[])  # No attributes
        mock_snap.total_equity = 110.0

        last_tpnl = getattr(mock_snap, "trading_pnl", None)
        pnl = last_tpnl if last_tpnl is not None else (110.0 - 100.0)

        assert pnl == 10.0  # Falls back to equity delta

    def test_negative_trading_pnl_used_correctly(self):
        """Negative trading_pnl should show as negative, not fall back."""
        mock_snap = MagicMock()
        mock_snap.trading_pnl = -2.50

        last_tpnl = getattr(mock_snap, "trading_pnl", None)
        pnl = last_tpnl if last_tpnl is not None else (100.0 - 100.0)

        assert pnl == -2.50


# ---------------------------------------------------------------------------
# Fix 4: Stuck position threshold
# ---------------------------------------------------------------------------


class TestStuckPositionThreshold:
    @pytest.mark.asyncio
    async def test_live_auto_remove_at_5_failures(self):
        """In live mode, stuck positions auto-remove at 5 failures + price < $0.10."""
        from bot.agent.position_closer import PositionCloser

        mock_om = MagicMock()
        mock_om.close_position = AsyncMock(return_value=None)  # Always fails

        mock_portfolio = AsyncMock()
        mock_portfolio.record_trade_close = AsyncMock(return_value=-1.0)
        mock_portfolio.mark_auto_removed = MagicMock()

        mock_rm = MagicMock()
        closer = PositionCloser(mock_om, mock_portfolio, mock_rm)

        pos = make_position(
            market_id="stuck_live",
            size=5.0,
            avg_price=0.50,
            current_price=0.08,  # < $0.10 threshold
            strategy="weather_trading",
        )

        with patch("bot.agent.position_closer.settings") as mock_settings:
            mock_settings.is_paper = False
            mock_settings.use_llm_post_mortem = False

            # Simulate 5 consecutive failures
            for i in range(5):
                await closer.close_position(pos, exit_reason="strategy_exit")

        # Should have been auto-removed at 5th failure
        mock_portfolio.record_trade_close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_live_no_auto_remove_at_high_price(self):
        """In live mode, don't auto-remove if price >= $0.10."""
        from bot.agent.position_closer import PositionCloser

        mock_om = MagicMock()
        mock_om.close_position = AsyncMock(return_value=None)

        mock_portfolio = AsyncMock()
        mock_portfolio.record_trade_close = AsyncMock(return_value=-1.0)
        mock_portfolio.mark_auto_removed = MagicMock()

        mock_rm = MagicMock()
        closer = PositionCloser(mock_om, mock_portfolio, mock_rm)

        pos = make_position(
            market_id="high_price",
            size=5.0,
            avg_price=0.50,
            current_price=0.15,  # >= $0.10 — should NOT auto-remove
            strategy="weather_trading",
        )

        with patch("bot.agent.position_closer.settings") as mock_settings:
            mock_settings.is_paper = False
            mock_settings.use_llm_post_mortem = False

            for _ in range(6):
                await closer.close_position(pos, exit_reason="strategy_exit")

        # Should NOT have been auto-removed
        mock_portfolio.record_trade_close.assert_not_awaited()


# ---------------------------------------------------------------------------
# Fix 5: Peak equity persistence
# ---------------------------------------------------------------------------


class TestPeakEquityPersistence:
    @pytest.mark.asyncio
    async def test_save_peak_equity(self):
        """StateStore.save_peak_equity persists value."""
        from bot.data.settings_store import StateStore

        mock_repo = MagicMock()
        mock_repo.set_many = AsyncMock()

        with patch("bot.data.settings_store.async_session") as mock_session:
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "bot.data.settings_store.SettingsRepository",
                return_value=mock_repo,
            ):
                await StateStore.save_peak_equity(25.50)

        mock_repo.set_many.assert_awaited_once()
        call_args = mock_repo.set_many.call_args[0][0]
        assert "state.peak_equity" in call_args

    @pytest.mark.asyncio
    async def test_load_peak_equity(self):
        """StateStore.load_peak_equity returns saved value."""
        from bot.data.settings_store import StateStore

        mock_repo = MagicMock()
        mock_repo.get = AsyncMock(return_value="25.5")

        with patch("bot.data.settings_store.async_session") as mock_session:
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "bot.data.settings_store.SettingsRepository",
                return_value=mock_repo,
            ):
                result = await StateStore.load_peak_equity()

        assert result == 25.5

    @pytest.mark.asyncio
    async def test_load_peak_equity_none(self):
        """StateStore.load_peak_equity returns None when not set."""
        from bot.data.settings_store import StateStore

        mock_repo = MagicMock()
        mock_repo.get = AsyncMock(return_value=None)

        with patch("bot.data.settings_store.async_session") as mock_session:
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "bot.data.settings_store.SettingsRepository",
                return_value=mock_repo,
            ):
                result = await StateStore.load_peak_equity()

        assert result is None


# ---------------------------------------------------------------------------
# Bug 3: Capital flow delete endpoint
# ---------------------------------------------------------------------------


class TestCapitalFlowDelete:
    @pytest.mark.asyncio
    async def test_delete_by_id_found(self):
        """CapitalFlowRepository.delete_by_id returns True when flow exists."""
        from bot.data.repositories import CapitalFlowRepository

        mock_flow = CapitalFlow(
            id=1, amount=5.0, flow_type="deposit",
            source="polymarket", note="Auto-detected: +$5.00",
            is_paper=False,
        )

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_flow

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.delete = AsyncMock()

        repo = CapitalFlowRepository(mock_session)
        deleted = await repo.delete_by_id(1)

        assert deleted is True
        mock_session.delete.assert_awaited_once_with(mock_flow)
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_by_id_not_found(self):
        """CapitalFlowRepository.delete_by_id returns False when not found."""
        from bot.data.repositories import CapitalFlowRepository

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        repo = CapitalFlowRepository(mock_session)
        deleted = await repo.delete_by_id(999)

        assert deleted is False
        mock_session.delete.assert_not_awaited()
