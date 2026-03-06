"""Tests for config API endpoints."""

import pytest

from bot.config import CapitalTier, settings


class TestGetConfig:
    async def test_returns_bot_config(self, client):
        resp = await client.get("/api/config/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trading_mode"] == "paper"
        assert "scan_interval_seconds" in data
        assert "max_daily_loss_pct" in data

    async def test_config_shape(self, client):
        resp = await client.get("/api/config/")
        data = resp.json()
        expected_keys = {
            "trading_mode",
            "scan_interval_seconds",
            "snapshot_interval_seconds",
            "max_daily_loss_pct",
            "max_drawdown_pct",
            "daily_target_pct",
            "current_tier",
            "tier_config",
            "strategy_params",
            "quality_params",
            "disabled_strategies",
            "blocked_market_types",
        }
        assert set(data.keys()) == expected_keys

    async def test_config_engine_runtime_error_fallback(self, client):
        """When engine raises RuntimeError, tier falls back to TIER1."""
        from unittest.mock import patch

        from api.dependencies import get_engine

        def raise_runtime():
            raise RuntimeError("Engine not initialized")

        # Override the fixture override so get_engine raises
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from api.dependencies import get_db
        from api.routers import config as config_router

        import os
        TEST_API_KEY = os.environ["API_SECRET_KEY"]

        test_app = FastAPI()
        test_app.include_router(config_router.router)

        async def override_get_db():
            from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
            from bot.data.models import Base

            eng = create_async_engine("sqlite+aiosqlite:///:memory:")
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                yield session

        test_app.dependency_overrides[get_db] = override_get_db
        test_app.dependency_overrides[get_engine] = raise_runtime

        with patch("bot.main.engine", None):
            transport = ASGITransport(app=test_app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": TEST_API_KEY},
            ) as ac:
                resp = await ac.get("/api/config/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_tier"] == CapitalTier.TIER1.value
        assert data["strategy_params"] == {}
        assert data["quality_params"] == {}


class TestUpdateConfig:
    async def test_partial_update(self, client, monkeypatch):
        monkeypatch.setattr(settings, "scan_interval_seconds", settings.scan_interval_seconds)
        resp = await client.put(
            "/api/config/", json={"scan_interval_seconds": 120}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        get_resp = await client.get("/api/config/")
        assert get_resp.json()["scan_interval_seconds"] == 120

    async def test_update_risk_params(self, client, monkeypatch):
        monkeypatch.setattr(settings, "max_daily_loss_pct", settings.max_daily_loss_pct)
        monkeypatch.setattr(settings, "max_drawdown_pct", settings.max_drawdown_pct)
        resp = await client.put(
            "/api/config/",
            json={"max_daily_loss_pct": 0.05, "max_drawdown_pct": 0.15},
        )
        assert resp.status_code == 200
        get_resp = await client.get("/api/config/")
        assert get_resp.json()["max_daily_loss_pct"] == pytest.approx(0.05)
        assert get_resp.json()["max_drawdown_pct"] == pytest.approx(0.15)

    async def test_update_returns_changes(self, client, monkeypatch):
        monkeypatch.setattr(settings, "scan_interval_seconds", settings.scan_interval_seconds)
        resp = await client.put(
            "/api/config/", json={"scan_interval_seconds": 90}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert "changes" in data
        assert len(data["changes"]) == 1

    async def test_update_daily_target_pct(self, client, monkeypatch):
        monkeypatch.setattr(settings, "daily_target_pct", settings.daily_target_pct)
        resp = await client.put(
            "/api/config/", json={"daily_target_pct": 0.02}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert any("daily_target" in c for c in data["changes"])
        get_resp = await client.get("/api/config/")
        assert get_resp.json()["daily_target_pct"] == pytest.approx(0.02)

    async def test_empty_update_noop(self, client):
        before = await client.get("/api/config/")
        resp = await client.put("/api/config/", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["changes"] == []
        after = await client.get("/api/config/")
        assert before.json() == after.json()

    async def test_update_tier_config_with_engine(self, client, mock_engine):
        """PUT /config/ with tier_config applies update to live engine tier."""
        resp = await client.put(
            "/api/config/",
            json={"tier_config": {"max_positions": 5}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        # The change should be listed
        assert any("tier_config" in c for c in data["changes"])

    async def test_update_tier_config_engine_not_available(self, client, monkeypatch):
        """PUT /config/ tier_config survives when engine raises RuntimeError."""
        from unittest.mock import patch
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        import os

        from api.dependencies import get_db, get_engine
        from api.routers import config as config_router
        from bot.data.settings_store import SettingsStore

        TEST_API_KEY = os.environ["API_SECRET_KEY"]

        def raise_runtime():
            raise RuntimeError("No engine")

        test_app = FastAPI()
        test_app.include_router(config_router.router)

        async def override_get_db():
            from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
            from bot.data.models import Base
            eng = create_async_engine("sqlite+aiosqlite:///:memory:")
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                yield session

        test_app.dependency_overrides[get_db] = override_get_db
        test_app.dependency_overrides[get_engine] = raise_runtime

        with patch("bot.main.engine", None):
            transport = ASGITransport(app=test_app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": TEST_API_KEY},
            ) as ac:
                resp = await ac.put(
                    "/api/config/",
                    json={"tier_config": {"max_positions": 4}},
                )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    async def test_update_strategy_params_with_engine(self, client, mock_engine):
        """PUT /config/ with strategy_params patches live strategy attributes."""
        resp = await client.put(
            "/api/config/",
            json={"strategy_params": {"time_decay": {"MIN_EDGE": 0.05}}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        # Confirm attribute was set on the mock strategy
        strategy = mock_engine.analyzer.strategies[0]
        assert strategy.MIN_EDGE == 0.05

    async def test_update_strategy_params_engine_not_available(self, client):
        """PUT /config/ strategy_params survives when engine raises RuntimeError."""
        from unittest.mock import patch
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        import os

        from api.dependencies import get_db, get_engine
        from api.routers import config as config_router

        TEST_API_KEY = os.environ["API_SECRET_KEY"]

        def raise_runtime():
            raise RuntimeError("No engine")

        test_app = FastAPI()
        test_app.include_router(config_router.router)

        async def override_get_db():
            from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
            from bot.data.models import Base
            eng = create_async_engine("sqlite+aiosqlite:///:memory:")
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                yield session

        test_app.dependency_overrides[get_db] = override_get_db
        test_app.dependency_overrides[get_engine] = raise_runtime

        with patch("bot.main.engine", None):
            transport = ASGITransport(app=test_app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": TEST_API_KEY},
            ) as ac:
                resp = await ac.put(
                    "/api/config/",
                    json={"strategy_params": {"time_decay": {"MIN_EDGE": 0.07}}},
                )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    async def test_update_quality_params_with_engine(self, client, mock_engine):
        """PUT /config/ with quality_params patches live analyzer attributes."""
        resp = await client.put(
            "/api/config/",
            json={"quality_params": {"max_spread": 0.03}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert any("quality.max_spread" in c for c in data["changes"])

    async def test_update_quality_params_engine_not_available(self, client):
        """PUT /config/ quality_params survives when engine raises RuntimeError."""
        from unittest.mock import patch
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        import os

        from api.dependencies import get_db, get_engine
        from api.routers import config as config_router

        TEST_API_KEY = os.environ["API_SECRET_KEY"]

        def raise_runtime():
            raise RuntimeError("No engine")

        test_app = FastAPI()
        test_app.include_router(config_router.router)

        async def override_get_db():
            from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
            from bot.data.models import Base
            eng = create_async_engine("sqlite+aiosqlite:///:memory:")
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                yield session

        test_app.dependency_overrides[get_db] = override_get_db
        test_app.dependency_overrides[get_engine] = raise_runtime

        with patch("bot.main.engine", None):
            transport = ASGITransport(app=test_app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": TEST_API_KEY},
            ) as ac:
                resp = await ac.put(
                    "/api/config/",
                    json={"quality_params": {"stop_loss_pct": 0.35}},
                )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    async def test_update_quality_params_unknown_key_ignored(self, client, mock_engine):
        """Unknown quality_param keys should be silently ignored."""
        resp = await client.put(
            "/api/config/",
            json={"quality_params": {"nonexistent_key": 99}},
        )
        assert resp.status_code == 200
        data = resp.json()
        # No changes recorded for unknown key
        assert not any("quality.nonexistent_key" in c for c in data["changes"])

    async def test_update_engine_tier_fallback_for_persist(self, client):
        """When engine raises during tier detection for persist, uses TIER1."""
        from unittest.mock import patch
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        import os

        from api.dependencies import get_db, get_engine
        from api.routers import config as config_router

        TEST_API_KEY = os.environ["API_SECRET_KEY"]

        def raise_runtime():
            raise RuntimeError("No engine")

        test_app = FastAPI()
        test_app.include_router(config_router.router)

        async def override_get_db():
            from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
            from bot.data.models import Base
            eng = create_async_engine("sqlite+aiosqlite:///:memory:")
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                yield session

        test_app.dependency_overrides[get_db] = override_get_db
        test_app.dependency_overrides[get_engine] = raise_runtime

        with patch("bot.main.engine", None):
            transport = ASGITransport(app=test_app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": TEST_API_KEY},
            ) as ac:
                resp = await ac.put(
                    "/api/config/",
                    json={"scan_interval_seconds": 60},
                )
        assert resp.status_code == 200


class TestDisabledStrategies:
    async def test_get_config_returns_disabled_strategies(self, client, mock_engine):
        mock_engine.disabled_strategies = {"market_making"}
        resp = await client.get("/api/config/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["disabled_strategies"] == ["market_making"]

    async def test_disable_strategy(self, client, mock_engine):
        resp = await client.put(
            "/api/config/",
            json={"disabled_strategies": ["time_decay"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert any("disabled_strategies" in c for c in data["changes"])
        assert "time_decay" in mock_engine.disabled_strategies
        assert "time_decay" in mock_engine.analyzer.disabled_strategies

    async def test_enable_all_strategies(self, client, mock_engine):
        mock_engine.disabled_strategies = {"time_decay"}
        mock_engine.analyzer.disabled_strategies = {"time_decay"}
        resp = await client.put(
            "/api/config/",
            json={"disabled_strategies": []},
        )
        assert resp.status_code == 200
        assert mock_engine.disabled_strategies == set()
        assert mock_engine.analyzer.disabled_strategies == set()

    async def test_disable_invalid_strategy_filtered(self, client, mock_engine):
        """Only valid strategy names are accepted."""
        resp = await client.put(
            "/api/config/",
            json={"disabled_strategies": ["nonexistent", "time_decay"]},
        )
        assert resp.status_code == 200
        # Only time_decay is a valid strategy in mock
        assert mock_engine.disabled_strategies == {"time_decay"}

    async def test_disabled_strategies_empty_by_default(self, client):
        resp = await client.get("/api/config/")
        data = resp.json()
        assert data["disabled_strategies"] == []


class TestPauseResume:
    async def test_pause_calls_engine(self, client, mock_engine):
        resp = await client.post("/api/config/trading/pause")
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"
        mock_engine.risk_manager.pause.assert_called_once()

    async def test_resume_calls_engine(self, client, mock_engine):
        resp = await client.post("/api/config/trading/resume")
        assert resp.status_code == 200
        assert resp.json()["status"] == "resumed"
        mock_engine.risk_manager.resume.assert_called_once()

    async def test_pause_then_resume(self, client, mock_engine):
        await client.post("/api/config/trading/pause")
        await client.post("/api/config/trading/resume")
        mock_engine.risk_manager.pause.assert_called_once()
        mock_engine.risk_manager.resume.assert_called_once()


class TestResetRiskState:
    async def test_reset_returns_expected_keys(self, client, mock_engine):
        """POST /risk/reset returns status, equity, daily_pnl, peak_equity."""
        mock_engine.portfolio.total_equity = 12.5
        resp = await client.post("/api/config/risk/reset")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "reset"
        assert data["equity"] == pytest.approx(12.5)
        assert data["daily_pnl"] == pytest.approx(0.0)
        assert data["peak_equity"] == pytest.approx(12.5)

    async def test_reset_calls_risk_manager_reset(self, client, mock_engine):
        """POST /risk/reset calls risk_manager.reset_daily_state(equity)."""
        mock_engine.portfolio.total_equity = 9.0
        await client.post("/api/config/risk/reset")
        mock_engine.risk_manager.reset_daily_state.assert_called_once_with(9.0)

    async def test_reset_calls_portfolio_reset(self, client, mock_engine):
        """POST /risk/reset calls portfolio.reset_daily_state(equity)."""
        mock_engine.portfolio.total_equity = 15.0
        await client.post("/api/config/risk/reset")
        mock_engine.portfolio.reset_daily_state.assert_called_once_with(15.0)

    async def test_reset_requires_auth(self):
        """POST /risk/reset without auth header returns 401."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from unittest.mock import MagicMock, patch

        from api.dependencies import get_db, get_engine
        from api.routers import config as config_router
        from bot.config import CapitalTier

        engine = MagicMock()
        engine.portfolio.total_equity = 10.0
        engine.portfolio.tier = CapitalTier.TIER1

        test_app = FastAPI()
        test_app.include_router(config_router.router)
        test_app.dependency_overrides[get_engine] = lambda: engine

        with patch("bot.main.engine", engine):
            transport = ASGITransport(app=test_app)
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                # No X-API-Key header
            ) as ac:
                resp = await ac.post("/api/config/risk/reset")
        assert resp.status_code == 401
