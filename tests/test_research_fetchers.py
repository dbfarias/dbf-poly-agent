"""Tests for news and crypto fetchers."""

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from bot.research.crypto_fetcher import CryptoFetcher
from bot.research.news_fetcher import NewsFetcher

# Sample Google News RSS XML
SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>bitcoin - Google News</title>
<item>
  <title>Bitcoin rallies past $60k - Reuters</title>
  <link>https://news.google.com/articles/123</link>
  <pubDate>Sat, 01 Mar 2026 10:00:00 GMT</pubDate>
  <source url="https://reuters.com">Reuters</source>
</item>
<item>
  <title>Crypto market shows mixed signals - Bloomberg</title>
  <link>https://news.google.com/articles/456</link>
  <pubDate>Sat, 01 Mar 2026 09:00:00 GMT</pubDate>
  <source url="https://bloomberg.com">Bloomberg</source>
</item>
<item>
  <title>Regulation fears hit crypto prices - CNN</title>
  <link>https://news.google.com/articles/789</link>
  <pubDate>Sat, 01 Mar 2026 08:00:00 GMT</pubDate>
  <source url="https://cnn.com">CNN</source>
</item>
</channel>
</rss>"""

# Sample CoinGecko response
SAMPLE_COINGECKO = {
    "bitcoin": {"usd": 62000, "usd_24h_change": 3.5},
    "ethereum": {"usd": 3400, "usd_24h_change": -1.2},
}


class TestNewsFetcher:
    @pytest.fixture
    def fetcher(self):
        return NewsFetcher()

    @pytest.mark.asyncio
    async def test_parses_rss_xml(self, fetcher):
        mock_response = AsyncMock()
        mock_response.text = SAMPLE_RSS
        mock_response.raise_for_status = lambda: None

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(return_value=mock_response)
            items = await fetcher.fetch_news(["bitcoin"])

        assert len(items) == 3
        assert items[0].source in ("Reuters", "Bloomberg", "CNN")
        assert items[0].url.startswith("https://")

    @pytest.mark.asyncio
    async def test_handles_network_error(self, fetcher):
        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            items = await fetcher.fetch_news(["bitcoin"])

        assert items == []

    @pytest.mark.asyncio
    async def test_respects_max_results(self, fetcher):
        mock_response = AsyncMock()
        mock_response.text = SAMPLE_RSS
        mock_response.raise_for_status = lambda: None

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(return_value=mock_response)
            items = await fetcher.fetch_news(["bitcoin"], max_results=2)

        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_applies_vader_sentiment(self, fetcher):
        mock_response = AsyncMock()
        mock_response.text = SAMPLE_RSS
        mock_response.raise_for_status = lambda: None

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(return_value=mock_response)
            items = await fetcher.fetch_news(["bitcoin"])

        # Each item should have a VADER sentiment score
        for item in items:
            assert -1.0 <= item.sentiment <= 1.0

    @pytest.mark.asyncio
    async def test_empty_keywords(self, fetcher):
        items = await fetcher.fetch_news([])
        assert items == []


class TestCryptoFetcher:
    @pytest.fixture
    def fetcher(self):
        return CryptoFetcher()

    @pytest.mark.asyncio
    async def test_parses_coingecko_response(self, fetcher):
        mock_response = httpx.Response(
            status_code=200,
            json=SAMPLE_COINGECKO,
            request=httpx.Request("GET", "https://api.coingecko.com/api/v3/simple/price"),
        )

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(return_value=mock_response)
            result = await fetcher.get_market_sentiment()

        assert result["btc_24h_change"] == 3.5
        assert result["eth_24h_change"] == -1.2
        assert -1.0 <= result["market_trend"] <= 1.0

    @pytest.mark.asyncio
    async def test_handles_rate_limit(self, fetcher):
        mock_response = AsyncMock()
        mock_response.status_code = 429

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(return_value=mock_response)
            result = await fetcher.get_market_sentiment()

        # Should return neutral result when rate limited
        assert result["market_trend"] == 0.0

    @pytest.mark.asyncio
    async def test_handles_network_error(self, fetcher):
        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            result = await fetcher.get_market_sentiment()

        assert result["market_trend"] == 0.0
        assert result["btc_24h_change"] == 0.0

    @pytest.mark.asyncio
    async def test_caches_results(self, fetcher):
        mock_response = httpx.Response(
            status_code=200,
            json=SAMPLE_COINGECKO,
            request=httpx.Request("GET", "https://api.coingecko.com/api/v3/simple/price"),
        )

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_get = AsyncMock(return_value=mock_response)
            mock_client.return_value.get = mock_get

            # First call should hit API
            await fetcher.get_market_sentiment()
            # Second call should use cache
            await fetcher.get_market_sentiment()

        # Only one HTTP call should have been made
        assert mock_get.call_count == 1


class TestCryptoFetcherGetPrices:
    """Tests for CryptoFetcher.get_prices() method."""

    @pytest.fixture
    def fetcher(self):
        return CryptoFetcher()

    @pytest.mark.asyncio
    async def test_returns_usd_prices(self, fetcher):
        mock_response = httpx.Response(
            status_code=200,
            json=SAMPLE_COINGECKO,
            request=httpx.Request("GET", "https://api.coingecko.com/api/v3/simple/price"),
        )

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(return_value=mock_response)
            result = await fetcher.get_prices()

        assert result["bitcoin"] == 62000
        assert result["ethereum"] == 3400

    @pytest.mark.asyncio
    async def test_caches_prices(self, fetcher):
        mock_response = httpx.Response(
            status_code=200,
            json=SAMPLE_COINGECKO,
            request=httpx.Request("GET", "https://api.coingecko.com/api/v3/simple/price"),
        )

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_get = AsyncMock(return_value=mock_response)
            mock_client.return_value.get = mock_get

            await fetcher.get_prices()
            await fetcher.get_prices()

        # Only 1 HTTP call for 2 calls
        assert mock_get.call_count == 1

    @pytest.mark.asyncio
    async def test_handles_rate_limit(self, fetcher):
        mock_response = AsyncMock()
        mock_response.status_code = 429

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(return_value=mock_response)
            result = await fetcher.get_prices()

        # No cached data → empty dict
        assert result == {}
