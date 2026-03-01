"""Tests for research API endpoints."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middleware import verify_api_key
from api.routers import research
from bot.research.cache import ResearchCache
from bot.research.engine import ResearchEngine
from bot.research.types import NewsItem, ResearchResult


def _make_mock_engine():
    """Create a mock engine with research components."""
    engine = MagicMock()
    engine.research_cache = ResearchCache(default_ttl=3600)
    engine.research_engine = MagicMock(spec=ResearchEngine)
    engine.research_engine._running = True
    engine.research_engine.SCAN_INTERVAL = 1800
    engine.research_engine.MAX_MARKETS = 30
    return engine


def _make_result(market_id: str = "m1", sentiment: float = 0.5) -> ResearchResult:
    return ResearchResult(
        market_id=market_id,
        keywords=("bitcoin", "price"),
        news_items=(
            NewsItem(
                title="Bitcoin rallies",
                source="Reuters",
                published=datetime(2026, 3, 1, tzinfo=timezone.utc),
                url="https://example.com/1",
                sentiment=0.6,
            ),
        ),
        sentiment_score=sentiment,
        confidence=0.8,
        research_multiplier=0.9,
        updated_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def client():
    mock_engine = _make_mock_engine()

    app = FastAPI()
    app.include_router(research.router)
    app.dependency_overrides[verify_api_key] = lambda: "test"

    with patch("bot.main.engine", mock_engine):
        yield TestClient(app), mock_engine


class TestResearchApi:
    def test_status_returns_valid(self, client):
        test_client, _ = client
        resp = test_client.get("/api/research/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "cached_markets" in data
        assert "running" in data
        assert data["running"] is True

    def test_markets_returns_list(self, client):
        test_client, engine = client
        engine.research_cache.set("m1", _make_result())

        resp = test_client.get("/api/research/markets")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["market_id"] == "m1"
        assert "sentiment_score" in data[0]
        assert "top_headlines" in data[0]

    def test_market_detail_found(self, client):
        test_client, engine = client
        engine.research_cache.set("m1", _make_result())

        resp = test_client.get("/api/research/markets/m1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["market_id"] == "m1"
        assert "headlines" in data
        assert len(data["headlines"]) == 1

    def test_market_detail_not_found(self, client):
        test_client, _ = client
        resp = test_client.get("/api/research/markets/nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
