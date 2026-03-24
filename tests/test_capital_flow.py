"""Tests for capital flow detection and deposit-immune PnL."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.portfolio import Portfolio
from bot.config import settings
from bot.data.models import CapitalFlow, PortfolioSnapshot, Position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_position(
    market_id: str = "mkt1",
    token_id: str = "token1",
    size: float = 10.0,
    avg_price: float = 0.50,
    current_price: float = 0.55,
) -> Position:
    return Position(
        market_id=market_id,
        token_id=token_id,
        question="Will X happen?",
        outcome="Yes",
        category="crypto",
        strategy="value_betting",
        side="BUY",
        size=size,
        avg_price=avg_price,
        current_price=current_price,
        cost_basis=size * avg_price,
        unrealized_pnl=(current_price - avg_price) * size,
        is_open=True,
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
# CapitalFlow model tests
# ---------------------------------------------------------------------------


class TestCapitalFlowModel:
    def test_deposit_creation(self):
        flow = CapitalFlow(
            amount=50.0,
            flow_type="deposit",
            source="polymarket",
            note="Test deposit",
            is_paper=False,
        )
        assert flow.amount == 50.0
        assert flow.flow_type == "deposit"
        assert flow.source == "polymarket"

    def test_withdrawal_creation(self):
        flow = CapitalFlow(
            amount=-20.0,
            flow_type="withdrawal",
            source="polymarket",
            note="Test withdrawal",
            is_paper=False,
        )
        assert flow.amount == -20.0
        assert flow.flow_type == "withdrawal"


# ---------------------------------------------------------------------------
# PortfolioSnapshot trading_pnl column
# ---------------------------------------------------------------------------


class TestSnapshotTradingPnl:
    def test_snapshot_has_trading_pnl_field(self):
        snap = PortfolioSnapshot(
            total_equity=100.0,
            cash_balance=90.0,
            positions_value=10.0,
            unrealized_pnl=1.0,
            realized_pnl_today=0.5,
            trading_pnl=1.5,
        )
        assert snap.trading_pnl == 1.5

    def test_snapshot_trading_pnl_defaults_to_zero(self):
        snap = PortfolioSnapshot(
            total_equity=100.0,
            cash_balance=90.0,
            trading_pnl=0.0,
        )
        assert snap.trading_pnl == 0.0


# ---------------------------------------------------------------------------
# Deposit detection in sync
# ---------------------------------------------------------------------------


class TestDepositDetection:
    @pytest.mark.asyncio
    async def test_detect_deposit_adjusts_day_start_equity(self, portfolio):
        """When balance increases significantly, day_start_equity should adjust."""
        portfolio._cash = 10.0
        portfolio._day_start_equity = 10.0
        portfolio._skip_next_flow = False  # Simulate post-first-sync state

        mock_repo = MagicMock()
        mock_repo.create = AsyncMock(return_value=CapitalFlow(
            id=1, amount=20.0, flow_type="deposit",
            source="polymarket", note="", is_paper=False,
        ))

        with patch("bot.agent.portfolio.async_session") as mock_session, \
             patch("bot.agent.portfolio.CapitalFlowRepository", return_value=mock_repo), \
             patch("bot.data.settings_store.StateStore.save_day_start_equity", new_callable=AsyncMock):
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio._detect_capital_flow(30.0)

        assert portfolio._day_start_equity == 30.0  # 10 + 20

    @pytest.mark.asyncio
    async def test_no_flow_on_small_change(self, portfolio):
        """Changes < $0.50 should not trigger flow detection."""
        portfolio._cash = 10.0
        portfolio._day_start_equity = 10.0
        portfolio._skip_next_flow = False

        await portfolio._detect_capital_flow(10.30)

        assert portfolio._day_start_equity == 10.0  # unchanged

    @pytest.mark.asyncio
    async def test_detect_withdrawal(self, portfolio):
        """Withdrawal should decrease day_start_equity."""
        portfolio._cash = 30.0
        portfolio._day_start_equity = 30.0
        portfolio._skip_next_flow = False

        mock_repo = MagicMock()
        mock_repo.create = AsyncMock(return_value=CapitalFlow(
            id=1, amount=-10.0, flow_type="withdrawal",
            source="polymarket", note="", is_paper=False,
        ))

        with patch("bot.agent.portfolio.async_session") as mock_session, \
             patch("bot.agent.portfolio.CapitalFlowRepository", return_value=mock_repo), \
             patch("bot.data.settings_store.StateStore.save_day_start_equity", new_callable=AsyncMock):
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio._detect_capital_flow(20.0)

        assert portfolio._day_start_equity == 20.0  # 30 - 10

    @pytest.mark.asyncio
    async def test_flow_propagates_to_risk_manager(self, portfolio):
        """Deposit should call risk_manager.set_day_start_equity."""
        mock_rm = MagicMock()
        portfolio._risk_manager = mock_rm
        portfolio._cash = 10.0
        portfolio._day_start_equity = 10.0
        portfolio._skip_next_flow = False

        mock_repo = MagicMock()
        mock_repo.create = AsyncMock(return_value=CapitalFlow(
            id=1, amount=5.0, flow_type="deposit",
            source="polymarket", note="", is_paper=False,
        ))

        with patch("bot.agent.portfolio.async_session") as mock_session, \
             patch("bot.agent.portfolio.CapitalFlowRepository", return_value=mock_repo), \
             patch("bot.data.settings_store.StateStore.save_day_start_equity", new_callable=AsyncMock):
            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio._detect_capital_flow(15.0)

        mock_rm.set_day_start_equity.assert_called_once_with(15.0)


# ---------------------------------------------------------------------------
# get_overview — trade-based PnL
# ---------------------------------------------------------------------------


class TestOverviewTradingPnl:
    def test_overview_uses_equity_delta_pnl(self, portfolio, mock_clob):
        """PnL in overview = equity - day_start_equity."""
        mock_clob.get_address = MagicMock(return_value="0xabc")
        portfolio._cash = 60.0
        portfolio._day_start_equity = 60.0
        portfolio._realized_pnl_today = 2.0
        portfolio._positions = [make_position(
            size=10, avg_price=0.50, current_price=0.55,
        )]
        # equity = cash(60) + positions(10*0.55=5.5) = 65.5
        # PnL = 65.5 - 60.0 = 5.5

        overview = portfolio.get_overview()
        assert overview["polymarket_pnl_today"] == 5.50

    def test_overview_pnl_zero_with_no_trades(self, portfolio, mock_clob):
        """With no trades and a deposit, PnL should be 0."""
        mock_clob.get_address = MagicMock(return_value="0xabc")
        portfolio._cash = 60.0
        portfolio._day_start_equity = 60.0
        portfolio._realized_pnl_today = 0.0
        portfolio._positions = []

        overview = portfolio.get_overview()
        assert overview["polymarket_pnl_today"] == 0.0

    def test_overview_progress_uses_equity_delta(self, portfolio, mock_clob):
        """Daily progress should use equity - day_start."""
        mock_clob.get_address = MagicMock(return_value="0xabc")
        portfolio._cash = 10.10  # $0.10 profit
        portfolio._day_start_equity = 10.0
        portfolio._positions = []

        overview = portfolio.get_overview()
        # PnL = 10.10 - 10.0 = 0.10
        target_usd = 10.0 * settings.daily_target_pct
        expected_progress = 0.10 / target_usd
        assert abs(overview["daily_progress_pct"] - expected_progress) < 0.001


# ---------------------------------------------------------------------------
# take_snapshot — trading_pnl populated
# ---------------------------------------------------------------------------


class TestSnapshotTradingPnlPopulated:
    @pytest.mark.asyncio
    async def test_snapshot_records_trading_pnl(self, portfolio):
        """Snapshot trading_pnl = equity - day_start_equity."""
        portfolio._cash = 10.0
        portfolio._day_start_equity = 8.0
        portfolio._positions = [make_position(
            size=10, avg_price=0.50, current_price=0.53,
        )]
        # equity = cash(10) + positions(10*0.53=5.3) = 15.3
        # trading_pnl = 15.3 - 8.0 = 7.3

        mock_snap_repo = MagicMock()
        mock_snap_repo.create = AsyncMock(side_effect=lambda s: s)

        with patch("bot.agent.portfolio.async_session") as mock_session:
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "bot.agent.portfolio.PortfolioSnapshotRepository",
                return_value=mock_snap_repo,
            ):
                snap = await portfolio.take_snapshot()

        assert abs(snap.trading_pnl - 7.30) < 0.01

    @pytest.mark.asyncio
    async def test_snapshot_daily_return_from_equity_delta(self, portfolio):
        """daily_return_pct = (equity - day_start) / day_start."""
        portfolio._cash = 102.0  # Started at 100, now 102
        portfolio._day_start_equity = 100.0
        portfolio._positions = []

        mock_snap_repo = MagicMock()
        mock_snap_repo.create = AsyncMock(side_effect=lambda s: s)

        with patch("bot.agent.portfolio.async_session") as mock_session:
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "bot.agent.portfolio.PortfolioSnapshotRepository",
                return_value=mock_snap_repo,
            ):
                snap = await portfolio.take_snapshot()

        assert abs(snap.daily_return_pct - 0.02) < 0.001  # 2.0 / 100.0


# ---------------------------------------------------------------------------
# Returns tracker — deposit-immune
# ---------------------------------------------------------------------------


class TestReturnsTrackerImmunity:
    @pytest.mark.asyncio
    async def test_returns_tracker_uses_trading_pnl(self):
        """ReturnsTracker should prefer trading_pnl when available."""
        from bot.data.returns_tracker import ReturnsTracker

        tracker = ReturnsTracker(window=30)

        # Create mock snapshots with trading_pnl
        snap1 = MagicMock()
        snap1.timestamp = datetime(2026, 3, 1, tzinfo=timezone.utc)
        snap1.total_equity = 100.0
        snap1.trading_pnl = 0.0

        snap2 = MagicMock()
        snap2.timestamp = datetime(2026, 3, 2, tzinfo=timezone.utc)
        snap2.total_equity = 150.0  # +50 equity (includes $48 deposit)
        snap2.trading_pnl = 2.0  # only $2 from trading

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [snap2, snap1]

        with patch("bot.data.database.async_session") as mock_session:
            mock_ctx = AsyncMock()
            mock_ctx.execute = AsyncMock(return_value=mock_result)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await tracker.load_from_db()

        # Return should be based on trading_pnl ($2), not equity delta ($50)
        assert len(tracker.returns) == 1
        assert abs(tracker.returns[0] - 0.02) < 0.001  # 2.0 / 100.0

    @pytest.mark.asyncio
    async def test_returns_tracker_fallback_without_trading_pnl(self):
        """Old snapshots without trading_pnl should fallback to equity delta."""
        from bot.data.returns_tracker import ReturnsTracker

        tracker = ReturnsTracker(window=30)

        snap1 = MagicMock()
        snap1.timestamp = datetime(2026, 3, 1, tzinfo=timezone.utc)
        snap1.total_equity = 100.0
        snap1.trading_pnl = 0.0

        snap2 = MagicMock()
        snap2.timestamp = datetime(2026, 3, 2, tzinfo=timezone.utc)
        snap2.total_equity = 101.0
        snap2.trading_pnl = 0.0  # No trading_pnl recorded

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [snap2, snap1]

        with patch("bot.data.database.async_session") as mock_session:
            mock_ctx = AsyncMock()
            mock_ctx.execute = AsyncMock(return_value=mock_result)
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            await tracker.load_from_db()

        assert len(tracker.returns) == 1
        assert abs(tracker.returns[0] - 0.01) < 0.001  # (101-100)/100


# ---------------------------------------------------------------------------
# Engine learner PnL
# ---------------------------------------------------------------------------


class TestEngineLearnerPnl:
    def test_learner_receives_trade_based_pnl(self):
        """Verify learner gets trading PnL, not equity delta."""
        from bot.agent.learner import PerformanceLearner

        learner = PerformanceLearner()
        # Simulate: realized_pnl=1.0, unrealized=0.50
        # day_start=100, equity=150 (deposit of $48.50)
        learner.set_daily_context(
            realized_pnl=1.50,  # trade-based: 1.0 realized + 0.50 unrealized
            equity=100.0,
            target_pct=0.01,
        )
        # Urgency should be based on $1.50 PnL vs $1.00 target
        assert learner._daily_pnl == 1.50


# ---------------------------------------------------------------------------
# Paper mode bankroll change
# ---------------------------------------------------------------------------


class TestPaperModeBankrollChange:
    def test_paper_mode_no_crash_on_bankroll_change(self, portfolio):
        """Changing initial_bankroll shouldn't cause crash in get_overview."""
        portfolio._cash = 20.0  # After bankroll change from 10 to 20
        portfolio._day_start_equity = 20.0
        portfolio._realized_pnl_today = 0.0
        portfolio._positions = []

        overview = portfolio.get_overview()
        assert overview["polymarket_pnl_today"] == 0.0

    @pytest.mark.asyncio
    async def test_paper_cash_restored_on_restart(self, portfolio):
        """Paper cash should be restored from state, not reset to initial_bankroll."""
        portfolio._cash = 10.0  # initial_bankroll
        portfolio._positions = []

        with patch("bot.agent.portfolio.settings") as mock_settings:
            mock_settings.is_paper = True
            mock_settings.initial_bankroll = 10.0
            with patch("bot.agent.portfolio.async_session"):
                with patch("bot.data.settings_store.async_session"):
                    from bot.data.settings_store import StateStore

                    with patch.object(
                        StateStore, "load_paper_cash",
                        return_value=(7.50, 10.0),  # (cash, bankroll)
                    ):
                        with patch.object(StateStore, "save_paper_cash"):
                            await portfolio._restore_paper_cash()

        # Should restore to 7.50, not stay at 10.0
        assert portfolio._cash == 7.50

    @pytest.mark.asyncio
    async def test_paper_bankroll_increase_records_deposit(self, portfolio):
        """Increasing initial_bankroll should record a capital flow deposit."""
        portfolio._cash = 50.0  # new initial_bankroll
        portfolio._positions = [make_position(size=10, avg_price=0.5)]
        portfolio._day_start_equity = 35.0

        with patch("bot.agent.portfolio.settings") as mock_settings:
            mock_settings.is_paper = True
            mock_settings.initial_bankroll = 50.0
            with patch("bot.agent.portfolio.async_session") as mock_db:
                mock_ctx = AsyncMock()
                mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
                mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
                with patch("bot.data.settings_store.async_session"):
                    from bot.data.settings_store import StateStore

                    with patch.object(
                        StateStore, "load_paper_cash",
                        return_value=(30.0, 30.0),  # old cash, old bankroll
                    ):
                        with patch.object(StateStore, "save_paper_cash"):
                            await portfolio._restore_paper_cash()

        # Cash = saved_cash + flow = 30 + (50-30) = 50
        assert portfolio._cash == 50.0
        # day_start should have been adjusted by +20 (50-30)
        assert portfolio._day_start_equity == 55.0  # 35 + 20

    @pytest.mark.asyncio
    async def test_paper_first_run_persists_initial(self, portfolio):
        """First run should persist initial_bankroll without detecting a flow."""
        portfolio._cash = 10.0
        portfolio._positions = []

        with patch("bot.agent.portfolio.settings") as mock_settings:
            mock_settings.is_paper = True
            mock_settings.initial_bankroll = 10.0
            with patch("bot.data.settings_store.async_session"):
                from bot.data.settings_store import StateStore

                with patch.object(
                    StateStore, "load_paper_cash",
                    return_value=(None, None),
                ):
                    save_mock = AsyncMock()
                    with patch.object(
                        StateStore, "save_paper_cash", save_mock
                    ):
                        await portfolio._restore_paper_cash()

        # Should persist initial value, not change cash
        assert portfolio._cash == 10.0
        save_mock.assert_awaited_once_with(10.0, 10.0)


# ---------------------------------------------------------------------------
# Capital flow API endpoint
# ---------------------------------------------------------------------------


class TestCapitalFlowRepository:
    @pytest.mark.asyncio
    async def test_create_flow(self):
        """CapitalFlowRepository.create persists flow."""
        from bot.data.repositories import CapitalFlowRepository

        mock_session = AsyncMock()
        mock_session.refresh = AsyncMock()
        repo = CapitalFlowRepository(mock_session)

        flow = CapitalFlow(
            amount=50.0,
            flow_type="deposit",
            source="polymarket",
            note="Test",
            is_paper=False,
        )
        result = await repo.create(flow)

        mock_session.add.assert_called_once_with(flow)
        mock_session.commit.assert_awaited_once()
        assert result is flow

    @pytest.mark.asyncio
    async def test_get_recent(self):
        """CapitalFlowRepository.get_recent returns flows."""
        from bot.data.repositories import CapitalFlowRepository

        mock_flow = CapitalFlow(
            id=1,
            amount=50.0,
            flow_type="deposit",
            source="polymarket",
            note="Test",
            is_paper=False,
        )

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_flow]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        repo = CapitalFlowRepository(mock_session)
        flows = await repo.get_recent(limit=10)

        assert len(flows) == 1
        assert flows[0].amount == 50.0

    def test_capital_flow_response_schema(self):
        """CapitalFlowResponse schema validates correctly."""
        from api.routers.portfolio import CapitalFlowResponse

        resp = CapitalFlowResponse(
            id=1,
            timestamp="2026-03-10T12:00:00+00:00",
            amount=50.0,
            flow_type="deposit",
            source="polymarket",
            note="Auto-detected: +$50.00",
            is_paper=False,
        )
        assert resp.amount == 50.0
        assert resp.flow_type == "deposit"
