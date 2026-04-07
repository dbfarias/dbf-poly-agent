"""Tests for NewsFetcher — Google News + Financial Times RSS integration."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from bot.research.news_fetcher import (
    _FT_FEEDS,
    NewsFetcher,
    _filter_ft_items,
    _FTFeedCache,
    _jaccard,
    _match_ft_feeds,
)
from bot.research.types import NewsItem

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)

_SAMPLE_GOOGLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Oil prices surge as OPEC cuts output</title>
      <link>https://example.com/oil-surge</link>
      <source url="https://reuters.com">Reuters</source>
      <pubDate>Mon, 07 Apr 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Gas prices expected to rise this summer</title>
      <link>https://example.com/gas-rise</link>
      <source url="https://bbc.com">BBC</source>
      <pubDate>Mon, 07 Apr 2026 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""

_SAMPLE_FT_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Brent crude hits $90 amid OPEC supply cuts</title>
      <link>https://www.ft.com/content/abc123</link>
      <pubDate>Mon, 07 Apr 2026 11:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Gold rallies on safe-haven demand</title>
      <link>https://www.ft.com/content/def456</link>
      <pubDate>Mon, 07 Apr 2026 08:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Copper futures unchanged in quiet session</title>
      <link>https://www.ft.com/content/ghi789</link>
      <pubDate>Mon, 07 Apr 2026 07:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""


def _make_item(title: str, source: str = "Test", minutes_ago: int = 0) -> NewsItem:
    return NewsItem(
        title=title,
        source=source,
        published=_NOW - timedelta(minutes=minutes_ago),
        url="https://example.com",
        sentiment=0.0,
    )


# ---------------------------------------------------------------------------
# FT feed keyword matching
# ---------------------------------------------------------------------------


class TestFTFeedKeywordMatching:
    """Test _match_ft_feeds selects correct feeds for given keywords."""

    def test_oil_matches_commodities(self) -> None:
        result = _match_ft_feeds({"oil", "prices"})
        assert "commodities" in result
        assert "world" not in result
        assert "markets" not in result

    def test_iran_matches_world(self) -> None:
        result = _match_ft_feeds({"iran", "nuclear"})
        assert "world" in result
        assert "commodities" not in result

    def test_fed_matches_markets(self) -> None:
        result = _match_ft_feeds({"fed", "rate", "hike"})
        assert "markets" in result

    def test_multiple_feeds_matched(self) -> None:
        result = _match_ft_feeds({"oil", "iran", "sanctions"})
        assert "commodities" in result
        assert "world" in result

    def test_no_match_returns_empty(self) -> None:
        result = _match_ft_feeds({"basketball", "score"})
        assert result == []

    def test_case_insensitive_via_lower(self) -> None:
        # The caller lowercases; verify the feed keywords are lowercase
        for config in _FT_FEEDS.values():
            kws = config["keywords"]
            assert isinstance(kws, set)
            for kw in kws:
                assert kw == kw.lower()

    def test_all_feed_configs_have_required_keys(self) -> None:
        for name, config in _FT_FEEDS.items():
            assert "url" in config, f"{name} missing url"
            assert "keywords" in config, f"{name} missing keywords"
            assert isinstance(config["keywords"], set)


# ---------------------------------------------------------------------------
# FT feed caching
# ---------------------------------------------------------------------------


class TestFTFeedCaching:
    """Test _FTFeedCache expiry logic."""

    def test_fresh_cache_not_expired(self) -> None:
        cache = _FTFeedCache(items=[], fetched_at=datetime.now(timezone.utc))
        assert not cache.is_expired()

    def test_old_cache_is_expired(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(minutes=20)
        cache = _FTFeedCache(items=[], fetched_at=old)
        assert cache.is_expired()

    def test_boundary_not_expired(self) -> None:
        # Just under 15 minutes
        almost = datetime.now(timezone.utc) - timedelta(seconds=899)
        cache = _FTFeedCache(items=[], fetched_at=almost)
        assert not cache.is_expired()

    @pytest.mark.asyncio
    async def test_cached_feed_not_refetched(self) -> None:
        """When cache is fresh, no HTTP request is made."""
        fetcher = NewsFetcher()
        item = _make_item("Cached oil article")
        fetcher._ft_cache["commodities"] = _FTFeedCache(
            items=[item], fetched_at=datetime.now(timezone.utc),
        )

        mock_client = AsyncMock()
        fetcher._client = mock_client

        result = await fetcher._fetch_single_ft_feed("commodities")
        assert len(result) == 1
        assert result[0].title == "Cached oil article"
        mock_client.get.assert_not_called()
        await fetcher.close()

    @pytest.mark.asyncio
    async def test_expired_cache_triggers_refetch(self) -> None:
        """When cache is expired, a new HTTP request is made."""
        fetcher = NewsFetcher()
        old = datetime.now(timezone.utc) - timedelta(minutes=20)
        fetcher._ft_cache["commodities"] = _FTFeedCache(items=[], fetched_at=old)

        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.text = _SAMPLE_FT_RSS
        mock_resp.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.is_closed = False
        fetcher._client = mock_client

        result = await fetcher._fetch_single_ft_feed("commodities")
        mock_client.get.assert_called_once()
        assert len(result) == 3  # All items from FT RSS
        await fetcher.close()


# ---------------------------------------------------------------------------
# FT items merged with Google News
# ---------------------------------------------------------------------------


class TestFTMergedWithGoogleNews:
    """Test that FT items are merged and deduplicated with Google News."""

    @pytest.mark.asyncio
    async def test_ft_items_merged_with_google_news(self) -> None:
        """Full integration: Google News + FT merged, deduped, sorted."""
        fetcher = NewsFetcher()

        # Mock HTTP client to return different RSS for different URLs
        async def mock_get(url: str) -> AsyncMock:
            resp = AsyncMock()
            resp.raise_for_status = lambda: None
            if "news.google.com" in url:
                resp.text = _SAMPLE_GOOGLE_RSS
            elif "ft.com/commodities" in url:
                resp.text = _SAMPLE_FT_RSS
            else:
                resp.text = "<rss><channel></channel></rss>"
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.is_closed = False
        fetcher._client = mock_client

        result = await fetcher.fetch_news(["oil", "crude"], max_results=10)

        titles = [item.title for item in result]
        # Should have Google News items
        assert any("Oil prices surge" in t for t in titles)
        # Should have FT items (crude matches "Brent crude" title)
        assert any("Brent crude" in t for t in titles)
        # Should be sorted by date (newest first)
        for i in range(len(result) - 1):
            assert result[i].published >= result[i + 1].published

        await fetcher.close()

    @pytest.mark.asyncio
    async def test_duplicate_headlines_removed(self) -> None:
        """Headlines with Jaccard > 0.6 are deduplicated."""
        fetcher = NewsFetcher()

        google = [_make_item("Oil prices surge as OPEC cuts output", minutes_ago=5)]
        ft = [_make_item("Oil prices surge as OPEC cuts output today", minutes_ago=3)]

        merged = fetcher._merge_and_deduplicate(google, ft, max_results=10)
        # Near-duplicate should be removed
        assert len(merged) == 1

    @pytest.mark.asyncio
    async def test_unique_headlines_kept(self) -> None:
        """Different headlines from Google and FT are both kept."""
        fetcher = NewsFetcher()

        google = [_make_item("Bitcoin hits new all-time high", minutes_ago=5)]
        ft = [_make_item("Brent crude drops on weak demand", minutes_ago=3)]

        merged = fetcher._merge_and_deduplicate(google, ft, max_results=10)
        assert len(merged) == 2
        await fetcher.close()

    @pytest.mark.asyncio
    async def test_no_ft_match_returns_only_google(self) -> None:
        """Keywords with no FT feed match return only Google results."""
        fetcher = NewsFetcher()

        async def mock_get(url: str) -> AsyncMock:
            resp = AsyncMock()
            resp.raise_for_status = lambda: None
            resp.text = _SAMPLE_GOOGLE_RSS
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.is_closed = False
        fetcher._client = mock_client

        result = await fetcher.fetch_news(["basketball", "score"], max_results=10)
        # Only Google News items, no FT
        assert all(item.source != "FT" for item in result)
        await fetcher.close()

    def test_ft_source_tag(self) -> None:
        """FT items should have source='FT'."""
        fetcher = NewsFetcher()
        items = fetcher._parse_rss(_SAMPLE_FT_RSS, max_results=10, source="FT")
        assert all(item.source == "FT" for item in items)

    @pytest.mark.asyncio
    async def test_ft_http_error_returns_empty(self) -> None:
        """If FT feed fails, Google News results are still returned."""
        fetcher = NewsFetcher()

        call_count = 0

        async def mock_get(url: str) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            if "ft.com" in url:
                raise httpx.HTTPStatusError(
                    "503", request=httpx.Request("GET", url),
                    response=httpx.Response(503),
                )
            resp = AsyncMock()
            resp.raise_for_status = lambda: None
            resp.text = _SAMPLE_GOOGLE_RSS
            return resp

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.is_closed = False
        fetcher._client = mock_client

        result = await fetcher.fetch_news(["oil"], max_results=10)
        # Should still have Google News results
        assert len(result) > 0
        await fetcher.close()


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_jaccard_identical(self) -> None:
        assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_jaccard_disjoint(self) -> None:
        assert _jaccard({"a"}, {"b"}) == 0.0

    def test_jaccard_empty(self) -> None:
        assert _jaccard(set(), {"a"}) == 0.0

    def test_filter_ft_items_by_keyword(self) -> None:
        items = [
            _make_item("Oil prices soar"),
            _make_item("Tech stocks rally"),
            _make_item("Crude oil drops"),
        ]
        filtered = _filter_ft_items(items, {"oil"})
        assert len(filtered) == 2
        assert all("oil" in i.title.lower() for i in filtered)

    def test_filter_ft_items_no_match(self) -> None:
        items = [_make_item("Gold rallies")]
        filtered = _filter_ft_items(items, {"oil"})
        assert filtered == []
