"""Tests for Portfolio — state tracker with sync, PnL, and tier logic."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.portfolio import Portfolio
from bot.config import CapitalTier, settings
from bot.data.models import Position
from bot.polymarket.types import PositionInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_position(
    market_id: str = "mkt1",
    token_id: str = "token1",
    size: float = 10.0,
    avg_price: float = 0.50,
    current_price: float = 0.55,
    cost_basis: float = 5.0,
    is_open: bool = True,
    strategy: str = "time_decay",
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
        cost_basis=cost_basis,
        unrealized_pnl=(current_price - avg_price) * size,
        is_open=is_open,
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
    """Fresh portfolio with mocked clients and initial_bankroll=10."""
    original = settings.initial_bankroll
    settings.initial_bankroll = 10.0
    p = Portfolio(mock_clob, mock_data_api, mock_gamma)
    yield p
    settings.initial_bankroll = original


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_initial_cash(self, portfolio):
        assert portfolio.cash == 10.0

    def test_initial_equity(self, portfolio):
        assert portfolio.total_equity == 10.0

    def test_initial_positions_empty(self, portfolio):
        assert portfolio.positions == []
        assert portfolio.open_position_count == 0

    def test_positions_value_empty(self, portfolio):
        assert portfolio.positions_value == 0.0

    def test_unrealized_pnl_empty(self, portfolio):
        assert portfolio.unrealized_pnl == 0.0

    def test_tier_from_equity(self, portfolio):
        assert portfolio.tier == CapitalTier.TIER1  # $10 = Tier1

    def test_positions_filters_open(self, portfolio):
        """Only open positions should be returned."""
        portfolio._positions = [
            make_position(market_id="mkt1", is_open=True),
            make_position(market_id="mkt2", is_open=False),
        ]
        assert len(portfolio.positions) == 1
        assert portfolio.positions[0].market_id == "mkt1"

    def test_positions_value_sums_correctly(self, portfolio):
        portfolio._positions = [
            make_position(market_id="mkt1", size=10.0, current_price=0.60, is_open=True),
            make_position(market_id="mkt2", size=5.0, current_price=0.80, is_open=True),
        ]
        # 10*0.60 + 5*0.80 = 6.0 + 4.0 = 10.0
        assert portfolio.positions_value == pytest.approx(10.0)

    def test_unrealized_pnl_sums_correctly(self, portfolio):
        portfolio._positions = [
            make_position(
                market_id="mkt1",
                size=10.0,
                avg_price=0.50,
                current_price=0.60,
                is_open=True,
            ),
        ]
        # (0.60 - 0.50) * 10 = 1.0
        assert portfolio.unrealized_pnl == pytest.approx(1.0)

    def test_total_equity_with_positions(self, portfolio):
        portfolio._cash = 5.0
        portfolio._positions = [
            make_position(market_id="mkt1", size=10.0, current_price=0.60, is_open=True),
        ]
        # cash=5 + positions_value=6 = 11
        assert portfolio.total_equity == pytest.approx(11.0)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


class TestSync:
    @pytest.mark.asyncio
    async def test_sync_loads_positions_from_db(self, portfolio):
        """sync() should load positions from the DB."""
        positions = [make_position(market_id="mkt1")]

        mock_session = AsyncMock()
        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=positions)

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            mock_as.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)
            await portfolio.sync()

        assert len(portfolio._positions) == 1

    @pytest.mark.asyncio
    async def test_sync_updates_peak_equity(self, portfolio):
        """Peak equity should increase when equity grows."""
        portfolio._peak_equity = 10.0
        portfolio._cash = 15.0  # equity = 15

        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            mock_as.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)
            await portfolio.sync()

        assert portfolio._peak_equity == 15.0

    @pytest.mark.asyncio
    async def test_sync_resets_daily_pnl_on_new_day(self, portfolio):
        """PnL should reset when the UTC date changes."""
        portfolio._realized_pnl_today = 1.5
        portfolio._pnl_date = "2020-01-01"  # Old date forces reset

        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            mock_as.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)
            await portfolio.sync()

        assert portfolio._realized_pnl_today == 0.0
        assert portfolio._pnl_date == datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @pytest.mark.asyncio
    async def test_sync_captures_day_start_equity_on_reset(self, portfolio):
        """Day start equity should be set when daily PnL resets."""
        portfolio._pnl_date = "2020-01-01"
        portfolio._cash = 12.0

        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            mock_as.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)
            await portfolio.sync()

        assert portfolio._day_start_equity == pytest.approx(12.0)

    @pytest.mark.asyncio
    async def test_sync_fetches_balance_when_connected(self, portfolio, mock_clob):
        """When connected, should fetch real Polymarket balance."""
        mock_clob.is_connected = True
        mock_clob.get_balance = AsyncMock(return_value=25.5)

        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            mock_as.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)
            await portfolio.sync()

        assert portfolio._polymarket_balance == 25.5


# ---------------------------------------------------------------------------
# Record Trade Open / Close
# ---------------------------------------------------------------------------


class TestRecordTrade:
    @pytest.mark.asyncio
    async def test_record_trade_open_reduces_cash(self, portfolio):
        """Opening a trade should reduce cash by cost."""
        initial_cash = portfolio._cash

        mock_repo = AsyncMock()
        mock_repo.upsert = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            mock_as.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio.record_trade_open(
                market_id="mkt1",
                token_id="token1",
                question="Will X?",
                outcome="Yes",
                category="crypto",
                strategy="time_decay",
                side="BUY",
                size=10.0,
                price=0.50,
            )

        # Cost = 10 * 0.50 = 5.0
        assert portfolio._cash == pytest.approx(initial_cash - 5.0)

    @pytest.mark.asyncio
    async def test_record_trade_close_returns_pnl(self, portfolio):
        """Closing a trade should return the realized PnL."""
        pos = make_position(
            market_id="mkt1",
            size=10.0,
            avg_price=0.50,
            current_price=0.60,
        )
        portfolio._positions = [pos]

        mock_repo = AsyncMock()
        mock_repo.close = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            mock_as.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)

            pnl = await portfolio.record_trade_close("mkt1", close_price=0.70)

        # PnL = (0.70 - 0.50) * 10 = 2.0
        assert pnl == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_record_trade_close_not_found(self, portfolio):
        """Closing a non-existent position should return 0."""
        portfolio._positions = []

        pnl = await portfolio.record_trade_close("nonexistent", close_price=0.50)
        assert pnl == 0.0

    @pytest.mark.asyncio
    async def test_record_trade_close_updates_realized_pnl(self, portfolio):
        """Realized PnL should accumulate."""
        pos = make_position(
            market_id="mkt1",
            size=10.0,
            avg_price=0.50,
        )
        portfolio._positions = [pos]
        portfolio._realized_pnl_today = 1.0
        # Set pnl_date to today so sync() doesn't reset it
        portfolio._pnl_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        mock_repo = AsyncMock()
        mock_repo.close = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            mock_as.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio.record_trade_close("mkt1", close_price=0.70)

        # Previous 1.0 + new 2.0 = 3.0
        assert portfolio._realized_pnl_today == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Settlement Price
# ---------------------------------------------------------------------------


class TestGetSettlementPrice:
    def test_winning_outcome(self):
        """Settlement should be 1.0 for a winning outcome (price >= 0.95)."""
        market = MagicMock()
        market.outcome_price_list = [0.99, 0.01]
        market.outcomes = ["Yes", "No"]

        pos = make_position(market_id="mkt1")
        pos.outcome = "Yes"

        result = Portfolio._get_settlement_price(market, pos)
        assert result == 1.0

    def test_losing_outcome(self):
        """Settlement should be 0.0 for a losing outcome (price <= 0.05)."""
        market = MagicMock()
        market.outcome_price_list = [0.01, 0.99]
        market.outcomes = ["Yes", "No"]

        pos = make_position(market_id="mkt1")
        pos.outcome = "Yes"

        result = Portfolio._get_settlement_price(market, pos)
        assert result == 0.0

    def test_no_prices_returns_current(self):
        """Without prices, should fall back to current_price."""
        market = MagicMock()
        market.outcome_price_list = []
        market.outcomes = []

        pos = make_position(current_price=0.75)
        result = Portfolio._get_settlement_price(market, pos)
        assert result == 0.75

    def test_outcome_not_found_returns_current(self):
        """If outcome not in list, return current_price."""
        market = MagicMock()
        market.outcome_price_list = [0.50, 0.50]
        market.outcomes = ["Yes", "No"]

        pos = make_position(current_price=0.65)
        pos.outcome = "Maybe"  # Not in outcomes list

        result = Portfolio._get_settlement_price(market, pos)
        assert result == 0.65

    def test_intermediate_price_returned(self):
        """Price between 0.05 and 0.95 should be returned as-is."""
        market = MagicMock()
        market.outcome_price_list = [0.50, 0.50]
        market.outcomes = ["Yes", "No"]

        pos = make_position()
        pos.outcome = "Yes"

        result = Portfolio._get_settlement_price(market, pos)
        assert result == 0.50


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


class TestGetOverview:
    def test_overview_keys(self, portfolio):
        overview = portfolio.get_overview()
        expected_keys = {
            "total_equity",
            "cash_balance",
            "polymarket_balance",
            "positions_value",
            "unrealized_pnl",
            "realized_pnl_today",
            "polymarket_pnl_today",
            "open_positions",
            "peak_equity",
            "day_start_equity",
            "tier",
            "is_paper",
            "wallet_address",
            "daily_target_pct",
            "daily_target_usd",
            "daily_progress_pct",
        }
        assert set(overview.keys()) == expected_keys

    def test_overview_equity_reflects_state(self, portfolio):
        portfolio._cash = 8.0
        portfolio._positions = [
            make_position(size=10.0, current_price=0.50, is_open=True),
        ]
        overview = portfolio.get_overview()
        # cash=8 + 10*0.50=5 = 13
        assert overview["total_equity"] == pytest.approx(13.0)

    def test_overview_daily_progress(self, portfolio):
        """Progress should be based on polymarket PnL vs target."""
        portfolio._day_start_equity = 10.0
        portfolio._cash = 10.2  # equity went up by 0.2

        overview = portfolio.get_overview()
        # target = 10 * 0.01 = 0.1 (1% daily)
        # progress = 0.2 / 0.1 = 2.0 (200% of target)
        assert overview["daily_progress_pct"] == pytest.approx(2.0)

    def test_overview_polymarket_pnl(self, portfolio):
        """polymarket_pnl_today = current equity - day start equity."""
        portfolio._day_start_equity = 10.0
        portfolio._cash = 10.5

        overview = portfolio.get_overview()
        assert overview["polymarket_pnl_today"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Update Position Prices
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Paper Mode Price Updates (H7)
# ---------------------------------------------------------------------------


class TestUpdatePaperPrices:
    @pytest.mark.asyncio
    async def test_updates_prices_via_gamma(self, portfolio, mock_gamma):
        """Paper mode should fetch current prices from Gamma API."""
        pos = make_position(
            market_id="mkt1",
            token_id="token1",
            avg_price=0.50,
            current_price=0.50,
            size=10.0,
        )
        portfolio._positions = [pos]

        # Gamma returns updated price
        gamma_market = MagicMock()
        gamma_market.token_ids = ["token1", "token2"]
        gamma_market.outcome_price_list = [0.65, 0.35]
        mock_gamma.get_market = AsyncMock(return_value=gamma_market)

        mock_repo = AsyncMock()
        mock_repo.upsert = AsyncMock()

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            mock_as.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio._update_paper_prices()

        assert pos.current_price == 0.65
        assert pos.unrealized_pnl == pytest.approx(1.5)  # (0.65 - 0.50) * 10

    @pytest.mark.asyncio
    async def test_does_not_create_positions(self, portfolio, mock_gamma):
        """Paper price update should never create new positions."""
        portfolio._positions = []

        await portfolio._update_paper_prices()

        mock_gamma.get_market.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_error_does_not_crash(self, portfolio, mock_gamma):
        """Gamma API errors should be caught gracefully."""
        pos = make_position(
            market_id="mkt1",
            token_id="token1",
            current_price=0.50,
        )
        portfolio._positions = [pos]

        mock_gamma.get_market = AsyncMock(side_effect=Exception("API timeout"))

        # Should not raise
        await portfolio._update_paper_prices()

        # Price unchanged
        assert pos.current_price == 0.50

    @pytest.mark.asyncio
    async def test_skips_closed_positions(self, portfolio, mock_gamma):
        """Positions with is_open=False should be skipped — no Gamma call made."""
        closed_pos = make_position(
            market_id="mkt1",
            token_id="token1",
            is_open=False,
            current_price=0.50,
        )
        portfolio._positions = [closed_pos]

        mock_gamma.get_market = AsyncMock()

        await portfolio._update_paper_prices()

        # is_open=False → skipped before Gamma is called
        mock_gamma.get_market.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_position_when_market_returns_none(self, portfolio, mock_gamma):
        """When Gamma returns None for a market, that position should be skipped."""
        pos = make_position(
            market_id="mkt1",
            token_id="token1",
            current_price=0.50,
            is_open=True,
        )
        portfolio._positions = [pos]
        mock_gamma.get_market = AsyncMock(return_value=None)

        await portfolio._update_paper_prices()

        # Price should be unchanged since market was None
        assert pos.current_price == 0.50


class TestUpdatePositionPrices:
    @pytest.mark.asyncio
    async def test_updates_matching_positions(self, portfolio):
        """Should update current_price and unrealized_pnl for matching tokens."""
        pos = make_position(
            token_id="token1",
            avg_price=0.50,
            current_price=0.50,
            size=10.0,
        )
        portfolio._positions = [pos]

        mock_repo = AsyncMock()
        mock_repo.upsert = AsyncMock()

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            mock_as.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio.update_position_prices({"token1": 0.65})

        assert pos.current_price == 0.65
        # unrealized = (0.65 - 0.50) * 10 = 1.5
        assert pos.unrealized_pnl == pytest.approx(1.5)

    @pytest.mark.asyncio
    async def test_ignores_non_matching_tokens(self, portfolio):
        """Positions with unmatched token_ids should not be updated."""
        pos = make_position(token_id="token1", current_price=0.50)
        portfolio._positions = [pos]

        mock_repo = AsyncMock()
        mock_repo.upsert = AsyncMock()

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            mock_as.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio.update_position_prices({"token_other": 0.90})

        assert pos.current_price == 0.50  # Unchanged


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


class TestSnapshot:
    @pytest.mark.asyncio
    async def test_take_snapshot_creates_record(self, portfolio):
        """take_snapshot should create a PortfolioSnapshot and persist it."""
        mock_snap_repo = AsyncMock()
        mock_snap_repo.get_latest = AsyncMock(return_value=None)
        mock_snap_repo.create = AsyncMock()

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch(
                "bot.agent.portfolio.PortfolioSnapshotRepository",
                return_value=mock_snap_repo,
            ),
        ):
            mock_as.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)

            snapshot = await portfolio.take_snapshot()

        assert snapshot.total_equity == pytest.approx(10.0)
        assert snapshot.cash_balance == pytest.approx(10.0)
        assert snapshot.open_positions == 0
        mock_snap_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_take_snapshot_computes_daily_return_from_latest(self, portfolio):
        """daily_return_pct should be computed relative to the latest snapshot."""
        portfolio._cash = 11.0  # equity = 11

        latest_snap = MagicMock()
        latest_snap.total_equity = 10.0  # previous equity

        mock_snap_repo = AsyncMock()
        mock_snap_repo.get_latest = AsyncMock(return_value=latest_snap)
        mock_snap_repo.create = AsyncMock()

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch(
                "bot.agent.portfolio.PortfolioSnapshotRepository",
                return_value=mock_snap_repo,
            ),
        ):
            mock_as.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)

            snapshot = await portfolio.take_snapshot()

        # (11 - 10) / 10 = 0.1
        assert snapshot.daily_return_pct == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# sync() — connected live mode path (calls _sync_from_polymarket)
# ---------------------------------------------------------------------------


def _make_session_ctx(repo):
    """Build a minimal async context manager that yields a mock session."""
    mock_as = MagicMock()
    mock_as.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_as.__aexit__ = AsyncMock(return_value=False)
    return mock_as


class TestSyncLiveMode:
    @pytest.mark.asyncio
    async def test_sync_calls_sync_from_polymarket_when_connected_live(
        self, portfolio, mock_clob
    ):
        """sync() must call _sync_from_polymarket when connected and not paper."""
        mock_clob.is_connected = True

        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.settings") as mock_settings,
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
            patch.object(
                portfolio, "_sync_from_polymarket", new=AsyncMock()
            ) as mock_sync,
        ):
            mock_settings.is_paper = False
            mock_settings.initial_bankroll = 10.0
            mock_settings.daily_target_pct = 0.01
            mock_as.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio.sync()

        mock_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_updates_cash_to_real_balance_in_live_mode(
        self, portfolio, mock_clob
    ):
        """In live mode, real Polymarket balance should override _cash."""
        mock_clob.is_connected = True
        mock_clob.get_balance = AsyncMock(return_value=42.0)

        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.settings") as mock_settings,
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
            patch.object(portfolio, "_sync_from_polymarket", new=AsyncMock()),
        ):
            mock_settings.is_paper = False
            mock_settings.initial_bankroll = 10.0
            mock_settings.daily_target_pct = 0.01
            mock_as.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio.sync()

        assert portfolio._cash == pytest.approx(42.0)
        assert portfolio._polymarket_balance == pytest.approx(42.0)

    @pytest.mark.asyncio
    async def test_sync_handles_balance_fetch_exception(self, portfolio, mock_clob):
        """Balance fetch exceptions should be caught; cash should remain unchanged."""
        mock_clob.is_connected = True
        mock_clob.get_balance = AsyncMock(side_effect=Exception("network error"))
        initial_cash = portfolio._cash

        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.settings") as mock_settings,
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
            patch.object(portfolio, "_sync_from_polymarket", new=AsyncMock()),
        ):
            mock_settings.is_paper = False
            mock_settings.initial_bankroll = 10.0
            mock_settings.daily_target_pct = 0.01
            mock_as.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_as.return_value.__aexit__ = AsyncMock(return_value=False)

            await portfolio.sync()

        assert portfolio._cash == pytest.approx(initial_cash)


# ---------------------------------------------------------------------------
# _sync_from_polymarket
# ---------------------------------------------------------------------------


def _make_position_info(
    market_id: str = "mkt1",
    token_id: str = "tok1",
    size: float = 10.0,
    avg_price: float = 0.50,
    current_price: float = 0.60,
    outcome: str = "Yes",
    question: str = "Will X happen?",
    unrealized_pnl: float = 1.0,
) -> PositionInfo:
    return PositionInfo(
        market_id=market_id,
        token_id=token_id,
        outcome=outcome,
        question=question,
        size=size,
        avg_price=avg_price,
        current_price=current_price,
        unrealized_pnl=unrealized_pnl,
    )


def _setup_session_mock(mock_as, mock_repo):
    """Wire async_session context manager to yield a mock that owns mock_repo."""
    mock_session = MagicMock()
    mock_as.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_as.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_session


class TestSyncFromPolymarket:
    @pytest.mark.asyncio
    async def test_returns_early_when_no_address(self, portfolio, mock_clob):
        """_sync_from_polymarket must exit immediately when address is None."""
        mock_clob.get_address.return_value = None

        mock_repo = AsyncMock()
        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            _setup_session_mock(mock_as, mock_repo)
            await portfolio._sync_from_polymarket()

        mock_repo.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_data_api_exception(self, portfolio, mock_clob, mock_data_api):
        """data_api exception should be caught and return early without crash."""
        mock_clob.get_address.return_value = "0xwallet"
        mock_data_api.get_positions = AsyncMock(side_effect=Exception("API down"))

        mock_repo = AsyncMock()
        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            _setup_session_mock(mock_as, mock_repo)
            await portfolio._sync_from_polymarket()

        mock_repo.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_upserts_valid_remote_positions(self, portfolio, mock_clob, mock_data_api):
        """Valid remote positions should be upserted into the local DB."""
        mock_clob.get_address.return_value = "0xwallet"
        rp = _make_position_info(market_id="mkt1", size=10.0, current_price=0.60)
        mock_data_api.get_positions = AsyncMock(return_value=[rp])

        mock_repo = AsyncMock()
        mock_repo.upsert = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            _setup_session_mock(mock_as, mock_repo)
            await portfolio._sync_from_polymarket()

        mock_repo.upsert.assert_awaited_once()
        upserted = mock_repo.upsert.call_args[0][0]
        assert upserted.market_id == "mkt1"
        assert upserted.is_paper is False
        assert upserted.strategy == "external"

    @pytest.mark.asyncio
    async def test_skips_zero_size_remote_positions(
        self, portfolio, mock_clob, mock_data_api
    ):
        """Remote positions with size <= 0 should be skipped."""
        mock_clob.get_address.return_value = "0xwallet"
        rp = _make_position_info(market_id="mkt1", size=0.0, current_price=0.60)
        mock_data_api.get_positions = AsyncMock(return_value=[rp])

        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            _setup_session_mock(mock_as, mock_repo)
            await portfolio._sync_from_polymarket()

        mock_repo.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_zero_price_remote_positions(
        self, portfolio, mock_clob, mock_data_api
    ):
        """Remote positions with current_price <= 0 should be skipped."""
        mock_clob.get_address.return_value = "0xwallet"
        rp = _make_position_info(market_id="mkt1", size=10.0, current_price=0.0)
        mock_data_api.get_positions = AsyncMock(return_value=[rp])

        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            _setup_session_mock(mock_as, mock_repo)
            await portfolio._sync_from_polymarket()

        mock_repo.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_closes_external_local_position_not_on_chain(
        self, portfolio, mock_clob, mock_data_api
    ):
        """External local positions missing from remote should be closed."""
        mock_clob.get_address.return_value = "0xwallet"
        mock_data_api.get_positions = AsyncMock(return_value=[])

        local_pos = make_position(
            market_id="local_mkt",
            strategy="external",
            is_open=True,
        )
        local_pos.created_at = datetime.now(timezone.utc) - timedelta(minutes=20)

        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[local_pos])
        mock_repo.close = AsyncMock()

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            _setup_session_mock(mock_as, mock_repo)
            await portfolio._sync_from_polymarket()

        mock_repo.close.assert_awaited_once_with("local_mkt")

    @pytest.mark.asyncio
    async def test_skips_recently_created_local_position(
        self, portfolio, mock_clob, mock_data_api
    ):
        """Local positions created within 10 min should not be closed (grace period)."""
        mock_clob.get_address.return_value = "0xwallet"
        mock_data_api.get_positions = AsyncMock(return_value=[])

        local_pos = make_position(
            market_id="recent_mkt",
            strategy="time_decay",
            is_open=True,
        )
        # Created 2 minutes ago — within the 10-minute grace period
        local_pos.created_at = datetime.now(timezone.utc) - timedelta(minutes=2)

        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[local_pos])
        mock_repo.close = AsyncMock()

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            _setup_session_mock(mock_as, mock_repo)
            await portfolio._sync_from_polymarket()

        mock_repo.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_local_position_with_no_created_at_treated_as_old(
        self, portfolio, mock_clob, mock_data_api
    ):
        """Positions with created_at=None should have age_seconds=0, but since 0<600
        they are still in the grace period and should be skipped."""
        mock_clob.get_address.return_value = "0xwallet"
        mock_data_api.get_positions = AsyncMock(return_value=[])

        local_pos = make_position(
            market_id="no_date_mkt",
            strategy="external",
            is_open=True,
        )
        local_pos.created_at = None

        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[local_pos])
        mock_repo.close = AsyncMock()

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            _setup_session_mock(mock_as, mock_repo)
            await portfolio._sync_from_polymarket()

        # age_seconds = 0 < 600 → grace period → NOT closed
        mock_repo.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_position_missing_from_remote_calls_close_if_resolved(
        self, portfolio, mock_clob, mock_data_api
    ):
        """Bot-opened positions missing from remote trigger _close_if_resolved."""
        mock_clob.get_address.return_value = "0xwallet"
        mock_data_api.get_positions = AsyncMock(return_value=[])

        local_pos = make_position(
            market_id="bot_mkt",
            strategy="time_decay",
            is_open=True,
        )
        local_pos.created_at = datetime.now(timezone.utc) - timedelta(minutes=20)

        mock_repo = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[local_pos])

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
            patch.object(
                portfolio, "_close_if_resolved", new=AsyncMock()
            ) as mock_close_resolved,
        ):
            _setup_session_mock(mock_as, mock_repo)
            await portfolio._sync_from_polymarket()

        mock_close_resolved.assert_awaited_once()
        called_pos = mock_close_resolved.call_args[0][0]
        assert called_pos.market_id == "bot_mkt"

    @pytest.mark.asyncio
    async def test_does_not_close_position_still_on_chain(
        self, portfolio, mock_clob, mock_data_api
    ):
        """Local positions whose market_id is in remote_market_ids should stay open."""
        mock_clob.get_address.return_value = "0xwallet"
        rp = _make_position_info(market_id="mkt_on_chain", size=5.0, current_price=0.8)
        mock_data_api.get_positions = AsyncMock(return_value=[rp])

        local_pos = make_position(market_id="mkt_on_chain", is_open=True)
        local_pos.created_at = datetime.now(timezone.utc) - timedelta(hours=1)

        mock_repo = AsyncMock()
        mock_repo.upsert = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[local_pos])
        mock_repo.close = AsyncMock()

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            _setup_session_mock(mock_as, mock_repo)
            await portfolio._sync_from_polymarket()

        mock_repo.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_question_truncated_to_200_chars(
        self, portfolio, mock_clob, mock_data_api
    ):
        """Remote position question should be truncated to 200 characters when upserted."""
        mock_clob.get_address.return_value = "0xwallet"
        long_question = "A" * 300
        rp = _make_position_info(
            market_id="mkt1", size=5.0, current_price=0.7, question=long_question
        )
        mock_data_api.get_positions = AsyncMock(return_value=[rp])

        mock_repo = AsyncMock()
        mock_repo.upsert = AsyncMock()
        mock_repo.get_open = AsyncMock(return_value=[])

        with (
            patch("bot.agent.portfolio.async_session") as mock_as,
            patch("bot.agent.portfolio.PositionRepository", return_value=mock_repo),
        ):
            _setup_session_mock(mock_as, mock_repo)
            await portfolio._sync_from_polymarket()

        upserted = mock_repo.upsert.call_args[0][0]
        assert len(upserted.question) == 200


# ---------------------------------------------------------------------------
# _close_if_resolved
# ---------------------------------------------------------------------------


class TestCloseIfResolved:
    @pytest.mark.asyncio
    async def test_closes_at_last_known_price_without_gamma(self, portfolio):
        """Without gamma client, should close at last known price (pnl=0)."""
        portfolio.gamma = None
        pos = make_position(
            market_id="mkt1",
            avg_price=0.50,
            current_price=0.70,
            size=10.0,
            strategy="time_decay",
        )

        mock_repo = AsyncMock()
        mock_repo.close = AsyncMock()

        await portfolio._close_if_resolved(pos, mock_repo)

        mock_repo.close.assert_awaited_once_with("mkt1")
        # pnl = 0.0 when no gamma
        assert portfolio._realized_pnl_today == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_closes_at_settlement_when_market_resolved(
        self, portfolio, mock_gamma
    ):
        """When market is confirmed resolved (closed=True), should compute pnl properly."""
        pos = make_position(
            market_id="mkt1",
            avg_price=0.50,
            current_price=0.70,
            size=10.0,
            strategy="time_decay",
        )
        pos.outcome = "Yes"

        gamma_market = MagicMock()
        gamma_market.closed = True
        gamma_market.archived = False
        gamma_market.active = False
        gamma_market.outcome_price_list = [1.0, 0.0]
        gamma_market.outcomes = ["Yes", "No"]
        mock_gamma.get_market = AsyncMock(return_value=gamma_market)

        mock_repo = AsyncMock()
        mock_repo.close = AsyncMock()

        await portfolio._close_if_resolved(pos, mock_repo)

        mock_repo.close.assert_awaited_once_with("mkt1")
        # settlement=1.0, avg=0.50, size=10 → pnl = 5.0
        assert portfolio._realized_pnl_today == pytest.approx(5.0)

    @pytest.mark.asyncio
    async def test_closes_at_current_price_when_market_not_found(
        self, portfolio, mock_gamma
    ):
        """When gamma returns None (market removed), settle at current_price."""
        pos = make_position(
            market_id="mkt1",
            avg_price=0.50,
            current_price=0.80,
            size=10.0,
            strategy="time_decay",
        )
        mock_gamma.get_market = AsyncMock(return_value=None)

        mock_repo = AsyncMock()
        mock_repo.close = AsyncMock()

        await portfolio._close_if_resolved(pos, mock_repo)

        mock_repo.close.assert_awaited_once_with("mkt1")
        # pnl = (0.80 - 0.50) * 10 = 3.0
        assert portfolio._realized_pnl_today == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_closes_with_external_sale_when_market_still_active(
        self, portfolio, mock_gamma
    ):
        """When market is still active but position gone → sold externally."""
        pos = make_position(
            market_id="mkt1",
            avg_price=0.50,
            current_price=0.65,
            size=10.0,
            strategy="time_decay",
        )
        pos.outcome = "Yes"

        gamma_market = MagicMock()
        gamma_market.closed = False
        gamma_market.archived = False
        gamma_market.active = True
        mock_gamma.get_market = AsyncMock(return_value=gamma_market)

        mock_repo = AsyncMock()
        mock_repo.close = AsyncMock()

        await portfolio._close_if_resolved(pos, mock_repo)

        mock_repo.close.assert_awaited_once_with("mkt1")
        # settlement = current_price = 0.65, pnl = (0.65-0.50)*10 = 1.5
        assert portfolio._realized_pnl_today == pytest.approx(1.5)

    @pytest.mark.asyncio
    async def test_does_not_close_when_gamma_raises(self, portfolio, mock_gamma):
        """When gamma.get_market raises, should NOT close position."""
        pos = make_position(
            market_id="mkt1",
            strategy="time_decay",
        )
        mock_gamma.get_market = AsyncMock(side_effect=Exception("network error"))

        mock_repo = AsyncMock()
        mock_repo.close = AsyncMock()

        await portfolio._close_if_resolved(pos, mock_repo)

        mock_repo.close.assert_not_called()
        assert portfolio._realized_pnl_today == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_closes_when_market_is_archived(self, portfolio, mock_gamma):
        """Archived market should be treated as resolved."""
        pos = make_position(
            market_id="mkt1",
            avg_price=0.50,
            current_price=0.70,
            size=5.0,
            strategy="time_decay",
        )
        pos.outcome = "No"

        gamma_market = MagicMock()
        gamma_market.closed = False
        gamma_market.archived = True
        gamma_market.active = True
        gamma_market.outcome_price_list = [1.0, 0.0]
        gamma_market.outcomes = ["Yes", "No"]
        mock_gamma.get_market = AsyncMock(return_value=gamma_market)

        mock_repo = AsyncMock()
        mock_repo.close = AsyncMock()

        await portfolio._close_if_resolved(pos, mock_repo)

        mock_repo.close.assert_awaited_once_with("mkt1")
        # "No" price=0.0 → settlement=0.0, pnl=(0.0-0.50)*5=-2.5
        assert portfolio._realized_pnl_today == pytest.approx(-2.5)

    @pytest.mark.asyncio
    async def test_close_if_resolved_notifies_risk_manager(
        self, portfolio, mock_gamma
    ):
        """When risk_manager is set, _close_if_resolved should call update_daily_pnl."""
        pos = make_position(
            market_id="mkt1",
            avg_price=0.50,
            current_price=0.80,
            size=10.0,
            strategy="time_decay",
        )
        mock_gamma.get_market = AsyncMock(return_value=None)

        mock_rm = MagicMock()
        mock_rm.update_daily_pnl = MagicMock()
        portfolio._risk_manager = mock_rm

        mock_repo = AsyncMock()
        mock_repo.close = AsyncMock()

        await portfolio._close_if_resolved(pos, mock_repo)

        # pnl = (0.80 - 0.50) * 10 = 3.0
        mock_rm.update_daily_pnl.assert_called_once()
        actual_pnl = mock_rm.update_daily_pnl.call_args[0][0]
        assert actual_pnl == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_close_if_resolved_no_risk_notify_for_zero_pnl(
        self, portfolio
    ):
        """Zero PnL should not notify risk_manager (avoid spurious updates)."""
        portfolio.gamma = None
        pos = make_position(
            market_id="mkt1",
            avg_price=0.50,
            current_price=0.50,
            size=10.0,
        )

        mock_rm = MagicMock()
        mock_rm.update_daily_pnl = MagicMock()
        portfolio._risk_manager = mock_rm

        mock_repo = AsyncMock()
        mock_repo.close = AsyncMock()

        await portfolio._close_if_resolved(pos, mock_repo)

        mock_rm.update_daily_pnl.assert_not_called()
