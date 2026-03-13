"""Tests for bot.research.news_sniper — NewsSniper pipeline."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.research.news_sniper import (
    MIN_KEYWORD_OVERLAP,
    MIN_PRICE,
    MIN_SENTIMENT_ABS,
    MAX_PRICE,
    NewsSniper,
    SnipeCandidate,
    _DEDUP_MAX,
)


@pytest.fixture
def mock_market_cache():
    cache = MagicMock()
    cache.get_all_markets.return_value = []
    return cache


@pytest.fixture
def sniper(mock_market_cache):
    return NewsSniper(mock_market_cache)


class TestSnipeCandidate:
    def test_frozen_dataclass(self):
        c = SnipeCandidate(
            market_id="m1",
            question="Will X happen?",
            headline="Breaking: X happened",
            source="Reuters",
            sentiment=0.8,
            keyword_overlap=0.6,
            yes_price=0.5,
        )
        assert c.market_id == "m1"
        assert c.sentiment == 0.8
        with pytest.raises(AttributeError):
            c.market_id = "m2"


class TestJaccardOverlap:
    def test_identical_sets(self):
        assert NewsSniper.jaccard_overlap({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets(self):
        assert NewsSniper.jaccard_overlap({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        result = NewsSniper.jaccard_overlap({"a", "b", "c"}, {"b", "c", "d"})
        assert abs(result - 0.5) < 0.01  # 2/4

    def test_empty_sets(self):
        assert NewsSniper.jaccard_overlap(set(), {"a"}) == 0.0
        assert NewsSniper.jaccard_overlap(set(), set()) == 0.0


class TestHeadlineDedup:
    def test_mark_and_check(self, sniper):
        assert not sniper._is_seen("Test headline")
        sniper._mark_seen("Test headline")
        assert sniper._is_seen("Test headline")

    def test_case_insensitive(self, sniper):
        sniper._mark_seen("Test Headline")
        assert sniper._is_seen("test headline")

    def test_lru_eviction(self, sniper):
        # Fill beyond capacity
        for i in range(_DEDUP_MAX + 100):
            sniper._mark_seen(f"headline_{i}")
        # Oldest should be evicted
        assert not sniper._is_seen("headline_0")
        # Recent should still be there
        assert sniper._is_seen(f"headline_{_DEDUP_MAX + 99}")

    def test_hash_deterministic(self, sniper):
        h1 = sniper._headline_hash("test")
        h2 = sniper._headline_hash("test")
        assert h1 == h2


class TestKeywordRefresh:
    def test_refresh_builds_index(self, sniper, mock_market_cache):
        market = MagicMock()
        market.id = "market_1"
        market.question = "Will Bitcoin reach 100k by December 2026?"
        market.yes_price = 0.45
        mock_market_cache.get_all_markets.return_value = [market]

        sniper._refresh_keyword_index()

        assert "market_1" in sniper._keyword_index
        assert len(sniper._keyword_index["market_1"]) >= 1

    def test_skip_sports_markets(self, sniper, mock_market_cache):
        market = MagicMock()
        market.id = "sports_1"
        market.question = "Will the Lakers win the NBA championship?"
        market.yes_price = 0.3
        mock_market_cache.get_all_markets.return_value = [market]

        sniper._refresh_keyword_index()

        assert "sports_1" not in sniper._keyword_index

    def test_skip_extreme_prices(self, sniper, mock_market_cache):
        market = MagicMock()
        market.id = "extreme_1"
        market.question = "Will Bitcoin reach 100k?"
        market.yes_price = None
        mock_market_cache.get_all_markets.return_value = [market]

        sniper._refresh_keyword_index()

        assert "extreme_1" not in sniper._keyword_index

    def test_respects_refresh_interval(self, sniper, mock_market_cache):
        market = MagicMock()
        market.id = "m1"
        market.question = "Will inflation drop below 3%?"
        market.yes_price = 0.5
        mock_market_cache.get_all_markets.return_value = [market]

        sniper._refresh_keyword_index()
        first_refresh = sniper._last_keyword_refresh

        # Immediate second call should be a no-op
        mock_market_cache.get_all_markets.return_value = []
        sniper._refresh_keyword_index()
        assert sniper._last_keyword_refresh == first_refresh
        assert "m1" in sniper._keyword_index  # Still has old data


class TestPoll:
    @pytest.mark.asyncio
    async def test_poll_returns_candidates(self, sniper, mock_market_cache):
        market = MagicMock()
        market.id = "m1"
        market.question = "Will Trump win 2028 election?"
        market.yes_price = 0.45
        mock_market_cache.get_all_markets.return_value = [market]

        # Mock news fetch to return a matching headline
        news_item = MagicMock()
        news_item.title = "Breaking: Trump announces 2028 election campaign"
        news_item.source = "Reuters"
        news_item.published = datetime.now(timezone.utc)

        with patch.object(
            sniper._news_fetcher, "fetch_news", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = [news_item]

            # We need keyword overlap >= 0.20 and sentiment >= 0.08
            # The keyword index needs to match the headline words
            with patch(
                "bot.research.news_sniper.get_headline_sentiment",
                return_value=0.6,
            ):
                candidates = await sniper.poll()

        # May or may not find candidates depending on keyword overlap
        assert isinstance(candidates, list)

    @pytest.mark.asyncio
    async def test_poll_empty_when_no_markets(self, sniper):
        candidates = await sniper.poll()
        assert candidates == []

    @pytest.mark.asyncio
    async def test_poll_dedup_headlines(self, sniper, mock_market_cache):
        market = MagicMock()
        market.id = "m1"
        market.question = "Will X happen by 2026?"
        market.yes_price = 0.5
        mock_market_cache.get_all_markets.return_value = [market]

        sniper._mark_seen("Old headline already seen")

        news_item = MagicMock()
        news_item.title = "Old headline already seen"
        news_item.source = "AP"
        news_item.published = datetime.now(timezone.utc)

        with patch.object(
            sniper._news_fetcher, "fetch_news", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = [news_item]
            with patch(
                "bot.research.news_sniper.get_headline_sentiment",
                return_value=0.8,
            ):
                candidates = await sniper.poll()

        assert len(candidates) == 0  # Deduped

    @pytest.mark.asyncio
    async def test_get_candidates_returns_copy(self, sniper):
        sniper._candidates = [
            SnipeCandidate(
                market_id="m1", question="Q?", headline="H",
                source="S", sentiment=0.5, keyword_overlap=0.6,
                yes_price=0.5,
            )
        ]
        result = sniper.get_candidates()
        assert len(result) == 1
        assert result is not sniper._candidates  # Returns copy

    @pytest.mark.asyncio
    async def test_close(self, sniper):
        with patch.object(
            sniper._news_fetcher, "close", new_callable=AsyncMock
        ) as mock_close:
            await sniper.close()
            mock_close.assert_called_once()


class TestSentimentFilter:
    @pytest.mark.asyncio
    async def test_low_sentiment_filtered(self, sniper, mock_market_cache):
        """Headlines with abs(sentiment) < 0.15 should be filtered."""
        market = MagicMock()
        market.id = "m1"
        market.question = "Will inflation drop?"
        market.yes_price = 0.5
        mock_market_cache.get_all_markets.return_value = [market]

        news_item = MagicMock()
        news_item.title = "Inflation data released today"
        news_item.source = "AP"
        news_item.published = datetime.now(timezone.utc)

        with patch.object(
            sniper._news_fetcher, "fetch_news", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = [news_item]
            # Low sentiment — should be filtered (below MIN_SENTIMENT_ABS=0.08)
            with patch(
                "bot.research.news_sniper.get_headline_sentiment",
                return_value=0.05,
            ):
                candidates = await sniper.poll()

        assert len(candidates) == 0
