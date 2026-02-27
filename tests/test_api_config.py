"""Tests for config API endpoints."""

import pytest

from bot.config import settings


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
            "current_tier",
            "tier_config",
            "strategy_params",
            "quality_params",
        }
        assert set(data.keys()) == expected_keys


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

    async def test_empty_update_noop(self, client):
        before = await client.get("/api/config/")
        resp = await client.put("/api/config/", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["changes"] == []
        after = await client.get("/api/config/")
        assert before.json() == after.json()


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
