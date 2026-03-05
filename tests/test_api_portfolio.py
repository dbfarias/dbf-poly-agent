"""Tests for portfolio API endpoints."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.data.models import PortfolioSnapshot, Position


class TestGetOverview:
    async def test_returns_overview(self, client, mock_engine):
        resp = await client.get("/api/portfolio/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_equity"] == 10.0
        assert data["is_paper"] is True

    async def test_overview_keys(self, client):
        resp = await client.get("/api/portfolio/overview")
        data = resp.json()
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
            "daily_target_pct",
            "daily_target_usd",
            "daily_progress_pct",
            "stuck_positions",
        }
        assert set(data.keys()) == expected_keys


class TestGetPositions:
    async def test_empty_positions(self, client):
        resp = await client.get("/api/portfolio/positions")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_with_open_position(self, client, db_session):
        pos = Position(
            market_id="mkt_test",
            token_id="tok1",
            question="Test?",
            outcome="Yes",
            category="crypto",
            strategy="time_decay",
            side="BUY",
            size=10.0,
            avg_price=0.90,
            current_price=0.95,
            cost_basis=9.0,
            unrealized_pnl=0.5,
            is_open=True,
        )
        db_session.add(pos)
        await db_session.commit()

        resp = await client.get("/api/portfolio/positions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["market_id"] == "mkt_test"
        assert data[0]["is_open"] is True

    async def test_excludes_closed_positions(self, client, db_session):
        closed = Position(
            market_id="mkt_closed",
            token_id="tok2",
            question="Closed?",
            outcome="No",
            category="sports",
            strategy="arb",
            side="BUY",
            size=5.0,
            avg_price=0.80,
            current_price=0.85,
            cost_basis=4.0,
            unrealized_pnl=0.25,
            is_open=False,
        )
        db_session.add(closed)
        await db_session.commit()

        resp = await client.get("/api/portfolio/positions")
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetEquityCurve:
    async def test_empty_curve(self, client):
        resp = await client.get("/api/portfolio/equity-curve")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_with_snapshot(self, client, db_session):
        snap = PortfolioSnapshot(
            timestamp=datetime.now(timezone.utc),
            total_equity=10.0,
            cash_balance=8.0,
            positions_value=2.0,
            unrealized_pnl=0.5,
            realized_pnl_today=0.1,
            daily_return_pct=1.0,
        )
        db_session.add(snap)
        await db_session.commit()

        resp = await client.get("/api/portfolio/equity-curve")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["total_equity"] == 10.0


class TestGetAllocation:
    async def test_empty_allocation(self, client):
        resp = await client.get("/api/portfolio/allocation")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_calculates_percentages(self, client, db_session):
        pos1 = Position(
            market_id="mkt_a",
            token_id="ta",
            side="BUY",
            category="crypto",
            cost_basis=6.0,
            is_open=True,
        )
        pos2 = Position(
            market_id="mkt_b",
            token_id="tb",
            side="BUY",
            category="sports",
            cost_basis=4.0,
            is_open=True,
        )
        db_session.add_all([pos1, pos2])
        await db_session.commit()

        resp = await client.get("/api/portfolio/allocation")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        total_pct = sum(item["percentage"] for item in data)
        assert total_pct == pytest.approx(1.0)

    async def test_allocation_zero_cost_basis_does_not_divide_by_zero(self, client, db_session):
        """When all positions have cost_basis=0, total defaults to 1.0 to avoid ZeroDivisionError."""
        pos = Position(
            market_id="mkt_zero",
            token_id="tz",
            side="BUY",
            category="crypto",
            cost_basis=0.0,  # Zero cost — triggers the `or 1.0` guard
            is_open=True,
        )
        db_session.add(pos)
        await db_session.commit()

        resp = await client.get("/api/portfolio/allocation")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        # percentage = 0.0 / 1.0 = 0.0 (not a division error)
        assert data[0]["percentage"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Force Close Position
# ---------------------------------------------------------------------------


class TestForceClosePosition:
    def _make_open_position(self, position_id: int = 1, size: float = 10.0):
        pos = MagicMock()
        pos.id = position_id
        pos.market_id = f"mkt_{position_id}"
        pos.token_id = f"tok_{position_id}"
        pos.question = "Will X happen?"
        pos.outcome = "Yes"
        pos.category = "crypto"
        pos.strategy = "time_decay"
        pos.size = size
        pos.current_price = 0.75
        pos.is_open = True
        return pos

    async def test_force_close_success(self, client, mock_engine):
        """POST /portfolio/positions/close closes position and returns PnL."""
        pos = self._make_open_position(position_id=42, size=10.0)
        mock_engine.portfolio.positions = [pos]

        trade = MagicMock()
        trade.status = "filled"
        mock_engine.order_manager = AsyncMock()
        mock_engine.order_manager.close_position = AsyncMock(return_value=trade)
        mock_engine.portfolio.record_trade_close = AsyncMock(return_value=0.75)
        mock_engine.risk_manager.update_daily_pnl = MagicMock()

        resp = await client.post(
            "/api/portfolio/positions/close",
            json={"position_id": 42, "reason": "manual"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["position_id"] == 42
        assert data["pnl"] == pytest.approx(0.75)
        assert "PnL" in data["message"]

    async def test_force_close_position_not_found(self, client, mock_engine):
        """POST /portfolio/positions/close returns 404 when position_id missing."""
        mock_engine.portfolio.positions = []

        resp = await client.post(
            "/api/portfolio/positions/close",
            json={"position_id": 999, "reason": "manual"},
        )
        assert resp.status_code == 404
        assert "999" in resp.json()["detail"]

    async def test_force_close_position_too_small(self, client, mock_engine):
        """POST /portfolio/positions/close returns 400 when size < 5 and trade is None."""
        pos = self._make_open_position(position_id=7, size=2.0)
        mock_engine.portfolio.positions = [pos]

        mock_engine.order_manager = AsyncMock()
        mock_engine.order_manager.close_position = AsyncMock(return_value=None)

        resp = await client.post(
            "/api/portfolio/positions/close",
            json={"position_id": 7, "reason": "test"},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "too small" in detail.lower() or "min" in detail.lower()

    async def test_force_close_sell_rejected_by_exchange(self, client, mock_engine):
        """POST /portfolio/positions/close returns 500 when order_manager returns None for size >= 5."""
        pos = self._make_open_position(position_id=8, size=10.0)
        mock_engine.portfolio.positions = [pos]

        mock_engine.order_manager = AsyncMock()
        mock_engine.order_manager.close_position = AsyncMock(return_value=None)

        resp = await client.post(
            "/api/portfolio/positions/close",
            json={"position_id": 8, "reason": "test"},
        )
        assert resp.status_code == 500
        assert "exchange" in resp.json()["detail"].lower() or "rejected" in resp.json()["detail"].lower()

    async def test_force_close_records_pnl_and_updates_risk(self, client, mock_engine):
        """Successful close calls record_trade_close and update_daily_pnl."""
        pos = self._make_open_position(position_id=10, size=8.0)
        mock_engine.portfolio.positions = [pos]

        trade = MagicMock()
        trade.status = "filled"
        mock_engine.order_manager = AsyncMock()
        mock_engine.order_manager.close_position = AsyncMock(return_value=trade)
        mock_engine.portfolio.record_trade_close = AsyncMock(return_value=1.25)
        mock_engine.risk_manager.update_daily_pnl = MagicMock()

        await client.post(
            "/api/portfolio/positions/close",
            json={"position_id": 10, "reason": "test"},
        )

        mock_engine.portfolio.record_trade_close.assert_called_once_with(
            pos.market_id, pos.current_price
        )
        mock_engine.risk_manager.update_daily_pnl.assert_called_once_with(1.25)

    async def test_force_close_default_reason(self, client, mock_engine):
        """POST /portfolio/positions/close uses 'manual_close' as default reason."""
        pos = self._make_open_position(position_id=11, size=6.0)
        mock_engine.portfolio.positions = [pos]

        trade = MagicMock()
        trade.status = "filled"
        mock_engine.order_manager = AsyncMock()
        mock_engine.order_manager.close_position = AsyncMock(return_value=trade)
        mock_engine.portfolio.record_trade_close = AsyncMock(return_value=0.0)
        mock_engine.risk_manager.update_daily_pnl = MagicMock()

        resp = await client.post(
            "/api/portfolio/positions/close",
            json={"position_id": 11},  # No reason provided
        )
        assert resp.status_code == 200

    async def test_force_close_only_targets_open_positions(self, client, mock_engine):
        """POST /portfolio/positions/close returns 404 if position is closed."""
        pos = self._make_open_position(position_id=20, size=10.0)
        pos.is_open = False  # Closed position
        mock_engine.portfolio.positions = [pos]

        resp = await client.post(
            "/api/portfolio/positions/close",
            json={"position_id": 20, "reason": "test"},
        )
        assert resp.status_code == 404

    async def test_force_close_response_market_id(self, client, mock_engine):
        """Response includes correct market_id from the closed position."""
        pos = self._make_open_position(position_id=30, size=5.0)
        pos.market_id = "market_abc_123"
        mock_engine.portfolio.positions = [pos]

        trade = MagicMock()
        trade.status = "filled"
        mock_engine.order_manager = AsyncMock()
        mock_engine.order_manager.close_position = AsyncMock(return_value=trade)
        mock_engine.portfolio.record_trade_close = AsyncMock(return_value=0.1)
        mock_engine.risk_manager.update_daily_pnl = MagicMock()

        resp = await client.post(
            "/api/portfolio/positions/close",
            json={"position_id": 30, "reason": "test"},
        )
        assert resp.status_code == 200
        assert resp.json()["market_id"] == "market_abc_123"


# ---------------------------------------------------------------------------
# GET /api/portfolio/daily-pnl
# ---------------------------------------------------------------------------


class TestGetDailyPnl:
    async def test_empty_snapshots(self, client):
        """Returns empty list when no snapshots exist."""
        resp = await client.get("/api/portfolio/daily-pnl")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_single_day(self, client, db_session):
        """Single day with two snapshots computes PnL correctly."""
        now = datetime.now(timezone.utc)
        # Use hours that fall on the same local trading day (offset -3)
        # 06:00 UTC = 03:00 local, 20:00 UTC = 17:00 local → same day
        snap1 = PortfolioSnapshot(
            timestamp=now.replace(hour=6, minute=0, second=0),
            total_equity=18.00,
            cash_balance=5.0,
            positions_value=13.0,
        )
        snap2 = PortfolioSnapshot(
            timestamp=now.replace(hour=20, minute=59),
            total_equity=18.50,
            cash_balance=6.0,
            positions_value=12.5,
        )
        db_session.add_all([snap1, snap2])
        await db_session.commit()

        resp = await client.get("/api/portfolio/daily-pnl")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["start_equity"] == 18.0
        assert data[0]["end_equity"] == 18.5
        assert data[0]["pnl"] == pytest.approx(0.5, abs=0.01)
        assert data[0]["pnl_pct"] == pytest.approx(2.78, abs=0.1)

    async def test_multi_day_ordering(self, client, db_session):
        """Multiple days are sorted chronologically."""
        day1 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
        day2 = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
        snap1 = PortfolioSnapshot(
            timestamp=day1,
            total_equity=17.50,
            cash_balance=5.0,
            positions_value=12.5,
        )
        snap2 = PortfolioSnapshot(
            timestamp=day2,
            total_equity=18.00,
            cash_balance=5.5,
            positions_value=12.5,
        )
        db_session.add_all([snap1, snap2])
        await db_session.commit()

        resp = await client.get("/api/portfolio/daily-pnl")
        data = resp.json()
        assert len(data) == 2
        assert data[0]["date"] == "2026-03-01"
        assert data[1]["date"] == "2026-03-02"

    async def test_hit_target_true(self, client, db_session):
        """Day that exceeds 1% target marks hit_target=True."""
        day = datetime(2026, 3, 1, tzinfo=timezone.utc)
        snap_am = PortfolioSnapshot(
            timestamp=day.replace(hour=6),
            total_equity=20.0,
            cash_balance=10.0,
            positions_value=10.0,
        )
        snap_pm = PortfolioSnapshot(
            timestamp=day.replace(hour=20),
            total_equity=20.50,  # +2.5% > 1% target
            cash_balance=10.0,
            positions_value=10.5,
        )
        db_session.add_all([snap_am, snap_pm])
        await db_session.commit()

        resp = await client.get("/api/portfolio/daily-pnl")
        data = resp.json()
        assert data[0]["hit_target"] is True

    async def test_hit_target_false(self, client, db_session):
        """Day below 1% target marks hit_target=False."""
        day = datetime(2026, 3, 1, tzinfo=timezone.utc)
        snap_am = PortfolioSnapshot(
            timestamp=day.replace(hour=6),
            total_equity=20.0,
            cash_balance=10.0,
            positions_value=10.0,
        )
        snap_pm = PortfolioSnapshot(
            timestamp=day.replace(hour=20),
            total_equity=20.05,  # +0.25% < 1% target
            cash_balance=10.0,
            positions_value=10.05,
        )
        db_session.add_all([snap_am, snap_pm])
        await db_session.commit()

        resp = await client.get("/api/portfolio/daily-pnl")
        data = resp.json()
        assert data[0]["hit_target"] is False

    async def test_negative_pnl_day(self, client, db_session):
        """Negative PnL day has negative values and hit_target=False."""
        day = datetime(2026, 3, 1, tzinfo=timezone.utc)
        snap_am = PortfolioSnapshot(
            timestamp=day.replace(hour=6),
            total_equity=20.0,
            cash_balance=10.0,
            positions_value=10.0,
        )
        snap_pm = PortfolioSnapshot(
            timestamp=day.replace(hour=20),
            total_equity=19.50,
            cash_balance=10.0,
            positions_value=9.5,
        )
        db_session.add_all([snap_am, snap_pm])
        await db_session.commit()

        resp = await client.get("/api/portfolio/daily-pnl")
        data = resp.json()
        assert data[0]["pnl"] < 0
        assert data[0]["hit_target"] is False
