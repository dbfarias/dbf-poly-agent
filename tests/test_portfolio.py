"""Tests for Portfolio — state tracker with sync, PnL, and tier logic."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.portfolio import Portfolio
from bot.config import CapitalTier, settings
from bot.data.models import Position

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
