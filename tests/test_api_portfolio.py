"""Tests for portfolio API endpoints."""

from datetime import datetime

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
            timestamp=datetime.utcnow(),
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
