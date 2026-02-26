"""Tests for TwitterFetcher — Tavily-based Twitter/X search."""

import time
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.research.twitter_fetcher import TwitterFetcher


def _make_tavily_response(results: list[dict] | None = None) -> MagicMock:
    """Build a mock httpx.Response with Tavily-shaped JSON."""
    if results is None:
        results = [
            {
                "title": "Bitcoin surges past $100k",
                "url": "https://x.com/user/status/123",
                "published_date": "2026-03-07T12:00:00Z",
                "content": "BTC breaks through the $100k barrier.",
            },
            {
                "title": "ETH staking rewards increase",
                "url": "https://twitter.com/user/status/456",
                "published_date": "2026-03-07T10:00:00Z",
                "content": "Ethereum staking yields hit 5%.",
            },
        ]
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"results": results}
    return resp


class TestTwitterFetcher:
    @pytest.mark.asyncio
    @patch("bot.research.twitter_fetcher.settings")
    async def test_fetch_tweets_happy_path(self, mock_settings: MagicMock) -> None:
        mock_settings.tavily_api_key = "test-key"
        mock_settings.twitter_daily_budget = 30

        fetcher = TwitterFetcher()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_make_tavily_response())
        mock_client.is_closed = False
        fetcher._client = mock_client

        items = await fetcher.fetch_tweets(["bitcoin", "price"], category="crypto")
        assert len(items) == 2
        assert items[0].source == "Twitter/X"
        assert items[0].title == "Bitcoin surges past $100k"
        assert isinstance(items[0].sentiment, float)

        await fetcher.close()

    @pytest.mark.asyncio
    @patch("bot.research.twitter_fetcher.settings")
    async def test_empty_keywords_returns_empty(self, mock_settings: MagicMock) -> None:
        mock_settings.tavily_api_key = "test-key"
        mock_settings.twitter_daily_budget = 30

        fetcher = TwitterFetcher()
        items = await fetcher.fetch_tweets([])
        assert items == []
        await fetcher.close()

    @pytest.mark.asyncio
    @patch("bot.research.twitter_fetcher.settings")
    async def test_no_api_key_returns_empty(self, mock_settings: MagicMock) -> None:
        mock_settings.tavily_api_key = ""

        fetcher = TwitterFetcher()
        items = await fetcher.fetch_tweets(["bitcoin"])
        assert items == []
        await fetcher.close()

    @pytest.mark.asyncio
    @patch("bot.research.twitter_fetcher.settings")
    async def test_api_error_records_failure(self, mock_settings: MagicMock) -> None:
        mock_settings.tavily_api_key = "test-key"
        mock_settings.twitter_daily_budget = 30

        fetcher = TwitterFetcher()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("API timeout"))
        mock_client.is_closed = False
        fetcher._client = mock_client

        items = await fetcher.fetch_tweets(["bitcoin"])
        assert items == []
        assert fetcher._failure_count == 1

        await fetcher.close()

    @pytest.mark.asyncio
    @patch("bot.research.twitter_fetcher.settings")
    async def test_circuit_breaker_opens_after_3_failures(
        self, mock_settings: MagicMock
    ) -> None:
        mock_settings.tavily_api_key = "test-key"
        mock_settings.twitter_daily_budget = 30

        fetcher = TwitterFetcher()
        for _ in range(3):
            fetcher._record_failure()

        assert fetcher._is_circuit_open()
        items = await fetcher.fetch_tweets(["bitcoin"])
        assert items == []

        await fetcher.close()

    @pytest.mark.asyncio
    @patch("bot.research.twitter_fetcher.settings")
    async def test_circuit_breaker_resets_after_cooldown(
        self, mock_settings: MagicMock
    ) -> None:
        mock_settings.tavily_api_key = "test-key"
        mock_settings.twitter_daily_budget = 30

        fetcher = TwitterFetcher()
        for _ in range(3):
            fetcher._record_failure()

        assert fetcher._is_circuit_open()

        # Simulate cooldown elapsed
        fetcher._circuit_open_until = time.monotonic() - 1
        assert not fetcher._is_circuit_open()
        assert fetcher._failure_count == 0

        await fetcher.close()

    @pytest.mark.asyncio
    @patch("bot.research.twitter_fetcher.settings")
    async def test_daily_budget_exceeded_returns_empty(
        self, mock_settings: MagicMock
    ) -> None:
        mock_settings.tavily_api_key = "test-key"
        mock_settings.twitter_daily_budget = 2

        fetcher = TwitterFetcher()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_make_tavily_response())
        mock_client.is_closed = False
        fetcher._client = mock_client

        # Use up the budget
        await fetcher.fetch_tweets(["bitcoin"])
        await fetcher.fetch_tweets(["ethereum"])

        # Third call should be blocked
        items = await fetcher.fetch_tweets(["solana"])
        assert items == []

        await fetcher.close()

    @pytest.mark.asyncio
    @patch("bot.research.twitter_fetcher.settings")
    async def test_daily_budget_resets_at_midnight(
        self, mock_settings: MagicMock
    ) -> None:
        mock_settings.tavily_api_key = "test-key"
        mock_settings.twitter_daily_budget = 1

        fetcher = TwitterFetcher()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_make_tavily_response())
        mock_client.is_closed = False
        fetcher._client = mock_client

        # Exhaust budget
        await fetcher.fetch_tweets(["bitcoin"])
        items = await fetcher.fetch_tweets(["ethereum"])
        assert items == []

        # Simulate date change (next day)
        fetcher._today_date = date(2020, 1, 1)
        items = await fetcher.fetch_tweets(["bitcoin"])
        assert len(items) > 0

        await fetcher.close()

    @pytest.mark.asyncio
    @patch("bot.research.twitter_fetcher.settings")
    async def test_sentiment_computed_per_item(
        self, mock_settings: MagicMock
    ) -> None:
        mock_settings.tavily_api_key = "test-key"
        mock_settings.twitter_daily_budget = 30

        fetcher = TwitterFetcher()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_make_tavily_response())
        mock_client.is_closed = False
        fetcher._client = mock_client

        items = await fetcher.fetch_tweets(["bitcoin"])
        for item in items:
            assert isinstance(item.sentiment, float)
            assert -1.0 <= item.sentiment <= 1.0

        await fetcher.close()

    @pytest.mark.asyncio
    @patch("bot.research.twitter_fetcher.settings")
    async def test_deduplication_by_title(
        self, mock_settings: MagicMock
    ) -> None:
        mock_settings.tavily_api_key = "test-key"
        mock_settings.twitter_daily_budget = 30

        dup_results = [
            {
                "title": "Same headline here",
                "url": "https://x.com/a/1",
                "published_date": "2026-03-07T12:00:00Z",
                "content": "Content A",
            },
            {
                "title": "Same headline here",
                "url": "https://x.com/b/2",
                "published_date": "2026-03-07T11:00:00Z",
                "content": "Content B",
            },
        ]

        fetcher = TwitterFetcher()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            return_value=_make_tavily_response(dup_results)
        )
        mock_client.is_closed = False
        fetcher._client = mock_client

        items = await fetcher.fetch_tweets(["test"])
        assert len(items) == 1

        await fetcher.close()
