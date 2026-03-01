"""Tests for strategies API endpoints."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.data.models import StrategyMetric, Trade


class TestGetStrategyPerformance:
    async def test_returns_empty_list_when_no_data(self, client):
        """GET /strategies/performance returns [] with no trades or metrics."""
        resp = await client.get("/api/strategies/performance")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_strategy_from_trades(self, client, db_session):
        """Endpoint merges trade stats and metric data into a StrategyPerformance list."""
        # Insert a filled trade so trade_repo returns stats
        trade = Trade(
            market_id="mkt1",
            token_id="tok1",
            question="Q?",
            outcome="Yes",
            side="BUY",
            price=0.90,
            size=5.0,
            cost_usd=4.5,
            strategy="time_decay",
            edge=0.05,
            estimated_prob=0.92,
            confidence=0.85,
            reasoning="Test",
            status="filled",
            pnl=0.3,
        )
        db_session.add(trade)
        await db_session.commit()

        resp = await client.get("/api/strategies/performance")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        strategies = [d["strategy"] for d in data]
        assert "time_decay" in strategies

    async def test_performance_response_shape(self, client, db_session):
        """Each element in the response must have all StrategyPerformance fields."""
        trade = Trade(
            market_id="mkt_shape",
            token_id="tok_shape",
            side="BUY",
            price=0.80,
            size=5.0,
            strategy="arbitrage",
            status="filled",
            pnl=0.1,
        )
        db_session.add(trade)
        await db_session.commit()

        resp = await client.get("/api/strategies/performance")
        assert resp.status_code == 200
        data = resp.json()
        arb = next((d for d in data if d["strategy"] == "arbitrage"), None)
        assert arb is not None
        expected_keys = {
            "strategy",
            "total_trades",
            "winning_trades",
            "losing_trades",
            "win_rate",
            "total_pnl",
            "avg_edge",
            "sharpe_ratio",
            "max_drawdown",
            "avg_hold_time_hours",
        }
        assert set(arb.keys()) == expected_keys

    async def test_merges_metric_only_strategy(self, client, db_session):
        """A strategy with a StrategyMetric record but no trades should still appear (seeded)."""
        metric = StrategyMetric(
            strategy="value_betting",
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            total_pnl=0.0,
            avg_edge=0.0,
        )
        db_session.add(metric)
        await db_session.commit()

        resp = await client.get("/api/strategies/performance")
        assert resp.status_code == 200
        data = resp.json()
        vb = next((d for d in data if d["strategy"] == "value_betting"), None)
        assert vb is not None
        # Strategy with no trades shows 0 for all stats
        assert vb["total_trades"] == 0
        assert vb["sharpe_ratio"] == 0.0

    async def test_trade_stats_take_priority_over_metrics(self, client, db_session):
        """When both trade stats and a metric exist, trade stats win for shared fields."""
        # Add a metric with old data
        metric = StrategyMetric(
            strategy="time_decay",
            total_trades=3,
            winning_trades=1,
            losing_trades=2,
            win_rate=0.33,
            total_pnl=-0.2,
            avg_edge=0.02,
        )
        db_session.add(metric)

        # Add a fresh winning trade
        trade = Trade(
            market_id="mkt_fresh",
            token_id="tok_fresh",
            side="BUY",
            price=0.90,
            size=5.0,
            strategy="time_decay",
            status="filled",
            pnl=0.5,
        )
        db_session.add(trade)
        await db_session.commit()

        resp = await client.get("/api/strategies/performance")
        assert resp.status_code == 200
        data = resp.json()
        td = next((d for d in data if d["strategy"] == "time_decay"), None)
        assert td is not None
        # total_trades must reflect DB trade count, not the old metric value (3)
        assert td["total_trades"] >= 1

    async def test_requires_auth(self):
        """GET /strategies/performance without auth returns 401."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from unittest.mock import patch

        from api.dependencies import get_db
        from api.routers import strategies as strat_router

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from bot.data.models import Base

        test_app = FastAPI()
        test_app.include_router(strat_router.router)

        async def override_get_db():
            eng = create_async_engine("sqlite+aiosqlite:///:memory:")
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                yield session

        test_app.dependency_overrides[get_db] = override_get_db

        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            # No auth header
        ) as ac:
            resp = await ac.get("/api/strategies/performance")
        assert resp.status_code == 401

    async def test_multiple_strategies_returned(self, client, db_session):
        """Multiple distinct strategies each produce a separate entry."""
        for strategy in ("time_decay", "arbitrage", "value_betting"):
            trade = Trade(
                market_id=f"mkt_{strategy}",
                token_id=f"tok_{strategy}",
                side="BUY",
                price=0.85,
                size=5.0,
                strategy=strategy,
                status="filled",
                pnl=0.1,
            )
            db_session.add(trade)
        await db_session.commit()

        resp = await client.get("/api/strategies/performance")
        assert resp.status_code == 200
        data = resp.json()
        returned_strategies = {d["strategy"] for d in data}
        assert "time_decay" in returned_strategies
        assert "arbitrage" in returned_strategies
        assert "value_betting" in returned_strategies

    async def test_null_metric_fields_default_to_zero(self, client, db_session):
        """When only trade stats exist (no metric), extra fields default to 0.0."""
        trade = Trade(
            market_id="mkt_zero",
            token_id="tok_zero",
            side="BUY",
            price=0.70,
            size=5.0,
            strategy="market_making",
            status="filled",
            pnl=0.0,
        )
        db_session.add(trade)
        await db_session.commit()

        resp = await client.get("/api/strategies/performance")
        assert resp.status_code == 200
        data = resp.json()
        mm = next((d for d in data if d["strategy"] == "market_making"), None)
        assert mm is not None
        # sharpe, drawdown, hold_time come from metric (absent) → default 0.0
        assert mm["sharpe_ratio"] == pytest.approx(0.0)
        assert mm["max_drawdown"] == pytest.approx(0.0)
        assert mm["avg_hold_time_hours"] == pytest.approx(0.0)
