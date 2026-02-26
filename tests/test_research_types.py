"""Tests for research types and cache."""

import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from bot.research.cache import ResearchCache
from bot.research.types import NewsItem, ResearchResult


def _make_news_item(**overrides) -> NewsItem:
    defaults = {
        "title": "Test headline",
        "source": "Test Source",
        "published": datetime(2026, 3, 1, tzinfo=timezone.utc),
        "url": "https://example.com/article",
        "sentiment": 0.5,
    }
    return NewsItem(**(defaults | overrides))


def _make_research_result(**overrides) -> ResearchResult:
    defaults = {
        "market_id": "market_123",
        "keywords": ("bitcoin", "price"),
        "news_items": (),
        "sentiment_score": 0.3,
        "confidence": 0.8,
        "research_multiplier": 0.95,
        "updated_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    }
    return ResearchResult(**(defaults | overrides))


class TestNewsItem:
    def test_creation(self):
        item = _make_news_item()
        assert item.title == "Test headline"
        assert item.sentiment == 0.5

    def test_frozen(self):
        item = _make_news_item()
        with pytest.raises(AttributeError):
            item.title = "changed"


class TestResearchResult:
    def test_creation(self):
        result = _make_research_result()
        assert result.market_id == "market_123"
        assert result.research_multiplier == 0.95
        assert result.confidence == 0.8

    def test_frozen(self):
        result = _make_research_result()
        with pytest.raises(AttributeError):
            result.sentiment_score = 0.9

    def test_with_news_items(self):
        items = (
            _make_news_item(title="Headline 1", sentiment=0.5),
            _make_news_item(title="Headline 2", sentiment=-0.3),
        )
        result = _make_research_result(news_items=items)
        assert len(result.news_items) == 2


class TestResearchCache:
    def test_set_and_get(self):
        cache = ResearchCache(default_ttl=3600)
        result = _make_research_result()
        cache.set("market_123", result)
        assert cache.get("market_123") is result

    def test_get_missing_returns_none(self):
        cache = ResearchCache()
        assert cache.get("nonexistent") is None

    def test_expiry(self):
        cache = ResearchCache(default_ttl=3600)
        result = _make_research_result()
        cache.set("market_123", result, ttl=1)

        # Not expired yet
        assert cache.get("market_123") is not None

        # Simulate expiry by patching time.monotonic
        with patch("bot.research.cache.time.monotonic", return_value=time.monotonic() + 2):
            assert cache.get("market_123") is None

    def test_get_all(self):
        cache = ResearchCache()
        r1 = _make_research_result(market_id="m1")
        r2 = _make_research_result(market_id="m2")
        cache.set("m1", r1)
        cache.set("m2", r2)

        results = cache.get_all()
        assert len(results) == 2
        market_ids = {r.market_id for r in results}
        assert market_ids == {"m1", "m2"}

    def test_get_all_filters_expired(self):
        cache = ResearchCache(default_ttl=3600)
        r1 = _make_research_result(market_id="m1")
        r2 = _make_research_result(market_id="m2")
        cache.set("m1", r1, ttl=1)
        cache.set("m2", r2, ttl=9999)

        with patch("bot.research.cache.time.monotonic", return_value=time.monotonic() + 2):
            results = cache.get_all()
            assert len(results) == 1
            assert results[0].market_id == "m2"

    def test_clear(self):
        cache = ResearchCache()
        cache.set("m1", _make_research_result())
        cache.clear()
        assert cache.get("m1") is None
        assert cache.get_all() == []

    def test_stats(self):
        cache = ResearchCache()
        assert cache.stats["cached_markets"] == 0
        assert cache.stats["last_scan"] is None

        cache.set("m1", _make_research_result())
        cache.record_scan(5)

        stats = cache.stats
        assert stats["cached_markets"] == 1
        assert stats["markets_scanned"] == 5
        assert stats["last_scan"] is not None
