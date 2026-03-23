"""Tests for TavilyNewsFetcher — general news via Tavily search API."""

import time
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.research.tavily_news_fetcher import TavilyNewsFetcher


def _make_tavily_response(results: list[dict] | None = None) -> MagicMock:
    """Build a mock httpx.Response with Tavily-shaped JSON."""
    if results is None:
        results = [
            {
                "title": "Federal Reserve holds rates steady",
                "url": "https://reuters.com/article/123",
                "published_date": "2026-03-20T12:00:00Z",
                "content": "The Fed held interest rates.",
            },
            {
                "title": "Oil prices surge amid tensions",
                "url": "https://bloomberg.com/article/456",
                "published_date": "2026-03-20T10:00:00Z",
                "content": "Crude oil prices jumped 3%.",
            },
        ]
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"results": results}
    return resp


class TestTavilyNewsFetcher:
    @pytest.mark.asyncio
    @patch("bot.research.tavily_news_fetcher.settings")
    async def test_search_news_happy_path(self, mock_settings: MagicMock) -> None:
        mock_settings.tavily_api_key = "test-key"

        fetcher = TavilyNewsFetcher()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_make_tavily_response())
        mock_client.is_closed = False
        fetcher._client = mock_client

        items = await fetcher.search_news(["federal reserve", "rates"])
        assert len(items) == 2
        assert items[0].source == "Tavily"
        assert items[0].title == "Federal Reserve holds rates steady"
        assert isinstance(items[0].sentiment, float)

        await fetcher.close()

    @pytest.mark.asyncio
    @patch("bot.research.tavily_news_fetcher.settings")
    async def test_empty_keywords_returns_empty(self, mock_settings: MagicMock) -> None:
        mock_settings.tavily_api_key = "test-key"
        fetcher = TavilyNewsFetcher()
        items = await fetcher.search_news([])
        assert items == []

    @pytest.mark.asyncio
    @patch("bot.research.tavily_news_fetcher.settings")
    async def test_no_api_key_returns_empty(self, mock_settings: MagicMock) -> None:
        mock_settings.tavily_api_key = ""
        fetcher = TavilyNewsFetcher()
        items = await fetcher.search_news(["bitcoin"])
        assert items == []

    @pytest.mark.asyncio
    @patch("bot.research.tavily_news_fetcher.settings")
    async def test_circuit_breaker_opens_after_failures(
        self, mock_settings: MagicMock,
    ) -> None:
        mock_settings.tavily_api_key = "test-key"
        fetcher = TavilyNewsFetcher()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("API error"))
        mock_client.is_closed = False
        fetcher._client = mock_client

        # Three failures should open circuit breaker
        for _ in range(3):
            await fetcher.search_news(["test"])
        assert fetcher._failure_count >= 3
        assert fetcher._is_circuit_open()

        # Should return empty while circuit is open
        items = await fetcher.search_news(["test"])
        assert items == []

    @pytest.mark.asyncio
    @patch("bot.research.tavily_news_fetcher.settings")
    async def test_daily_budget_limits_calls(
        self, mock_settings: MagicMock,
    ) -> None:
        mock_settings.tavily_api_key = "test-key"
        fetcher = TavilyNewsFetcher(daily_budget=2)
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_make_tavily_response())
        mock_client.is_closed = False
        fetcher._client = mock_client

        await fetcher.search_news(["test1"])
        await fetcher.search_news(["test2"])
        # Third call should be blocked
        items = await fetcher.search_news(["test3"])
        assert items == []

    @pytest.mark.asyncio
    @patch("bot.research.tavily_news_fetcher.settings")
    async def test_deduplicates_results(self, mock_settings: MagicMock) -> None:
        mock_settings.tavily_api_key = "test-key"
        fetcher = TavilyNewsFetcher()
        results = [
            {"title": "Same headline", "url": "https://a.com/1", "published_date": ""},
            {"title": "Same headline", "url": "https://b.com/2", "published_date": ""},
            {"title": "Different headline", "url": "https://c.com/3", "published_date": ""},
        ]
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_make_tavily_response(results))
        mock_client.is_closed = False
        fetcher._client = mock_client

        items = await fetcher.search_news(["test"])
        assert len(items) == 2

    @pytest.mark.asyncio
    @patch("bot.research.tavily_news_fetcher.settings")
    async def test_no_domain_restriction_in_payload(
        self, mock_settings: MagicMock,
    ) -> None:
        """Verify Tavily news fetcher does NOT restrict to Twitter domains."""
        mock_settings.tavily_api_key = "test-key"
        fetcher = TavilyNewsFetcher()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=_make_tavily_response())
        mock_client.is_closed = False
        fetcher._client = mock_client

        await fetcher.search_news(["bitcoin"])
        # Check the payload sent
        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json", call_args[1].get("json", {}))
        assert "include_domains" not in payload
        assert payload.get("topic") == "news"

    def test_circuit_breaker_resets_after_cooldown(self) -> None:
        fetcher = TavilyNewsFetcher()
        fetcher._failure_count = 3
        fetcher._circuit_open_until = time.monotonic() - 1  # Past cooldown
        assert not fetcher._is_circuit_open()
        assert fetcher._failure_count == 0

    def test_daily_budget_resets_on_new_day(self) -> None:
        fetcher = TavilyNewsFetcher(daily_budget=5)
        fetcher._today_calls = 5
        # Simulate a day change
        fetcher._today_date = date(2020, 1, 1)
        assert fetcher._check_daily_budget()
        assert fetcher._today_calls == 1
