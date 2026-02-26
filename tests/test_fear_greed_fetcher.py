"""Tests for crypto Fear & Greed Index fetcher."""

import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.research.fear_greed_fetcher import FearGreedFetcher


def _mock_httpx_client(mock_response):
    """Create a mock httpx.AsyncClient that works as async context manager."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    @asynccontextmanager
    async def fake_client(*args, **kwargs):
        yield mock_client

    return fake_client


def _mock_httpx_client_error(exc):
    """Create a mock that raises on get()."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=exc)

    @asynccontextmanager
    async def fake_client(*args, **kwargs):
        yield mock_client

    return fake_client


# ---------------------------------------------------------------------------
# get_index
# ---------------------------------------------------------------------------


class TestGetIndex:
    @pytest.fixture
    def fetcher(self):
        return FearGreedFetcher()

    @pytest.mark.asyncio
    async def test_successful_fetch(self, fetcher):
        mock_response = MagicMock()
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {
            "data": [{"value": "25", "value_classification": "Extreme Fear"}],
        }

        with patch("bot.research.fear_greed_fetcher.httpx.AsyncClient",
                    new=_mock_httpx_client(mock_response)):
            value, classification = await fetcher.get_index()
            assert value == 25
            assert classification == "Extreme Fear"

    @pytest.mark.asyncio
    async def test_cache_hit(self, fetcher):
        fetcher._value = 72
        fetcher._classification = "Greed"
        fetcher._cache_expires = time.monotonic() + 3600

        value, classification = await fetcher.get_index()
        assert value == 72
        assert classification == "Greed"

    @pytest.mark.asyncio
    async def test_cache_expired(self, fetcher):
        fetcher._value = 72
        fetcher._classification = "Greed"
        fetcher._cache_expires = time.monotonic() - 1  # expired

        mock_response = MagicMock()
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {
            "data": [{"value": "45", "value_classification": "Fear"}],
        }

        with patch("bot.research.fear_greed_fetcher.httpx.AsyncClient",
                    new=_mock_httpx_client(mock_response)):
            value, classification = await fetcher.get_index()
            assert value == 45
            assert classification == "Fear"

    @pytest.mark.asyncio
    async def test_error_returns_cached(self, fetcher):
        fetcher._value = 60
        fetcher._classification = "Greed"

        with patch("bot.research.fear_greed_fetcher.httpx.AsyncClient",
                    new=_mock_httpx_client_error(httpx.ConnectError("fail"))):
            value, classification = await fetcher.get_index()
            assert value == 60
            assert classification == "Greed"

    @pytest.mark.asyncio
    async def test_error_no_cache_returns_defaults(self, fetcher):
        with patch("bot.research.fear_greed_fetcher.httpx.AsyncClient",
                    new=_mock_httpx_client_error(httpx.ConnectError("fail"))):
            value, classification = await fetcher.get_index()
            assert value == 50
            assert classification == "Neutral"

    @pytest.mark.asyncio
    async def test_missing_data_uses_defaults(self, fetcher):
        mock_response = MagicMock()
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {"data": [{}]}

        with patch("bot.research.fear_greed_fetcher.httpx.AsyncClient",
                    new=_mock_httpx_client(mock_response)):
            value, classification = await fetcher.get_index()
            assert value == 50
            assert classification == "Neutral"

    @pytest.mark.asyncio
    async def test_empty_data_list(self, fetcher):
        mock_response = MagicMock()
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {"data": []}

        with patch("bot.research.fear_greed_fetcher.httpx.AsyncClient",
                    new=_mock_httpx_client(mock_response)):
            value, classification = await fetcher.get_index()
            # data[0] on empty list → IndexError caught → defaults
            assert value == 50
            assert classification == "Neutral"


# ---------------------------------------------------------------------------
# get_edge_multiplier — all 5 ranges
# ---------------------------------------------------------------------------


class TestGetEdgeMultiplier:
    @pytest.fixture
    def fetcher(self):
        return FearGreedFetcher()

    def test_none_value(self, fetcher):
        fetcher._value = None
        assert fetcher.get_edge_multiplier() == 1.0

    def test_extreme_fear_0(self, fetcher):
        fetcher._value = 0
        assert fetcher.get_edge_multiplier() == 1.15

    def test_extreme_fear_10(self, fetcher):
        fetcher._value = 10
        assert fetcher.get_edge_multiplier() == 1.15

    def test_extreme_fear_24(self, fetcher):
        fetcher._value = 24
        assert fetcher.get_edge_multiplier() == 1.15

    def test_fear_25(self, fetcher):
        fetcher._value = 25
        assert fetcher.get_edge_multiplier() == 1.05

    def test_fear_35(self, fetcher):
        fetcher._value = 35
        assert fetcher.get_edge_multiplier() == 1.05

    def test_fear_39(self, fetcher):
        fetcher._value = 39
        assert fetcher.get_edge_multiplier() == 1.05

    def test_neutral_40(self, fetcher):
        fetcher._value = 40
        assert fetcher.get_edge_multiplier() == 1.0

    def test_neutral_50(self, fetcher):
        fetcher._value = 50
        assert fetcher.get_edge_multiplier() == 1.0

    def test_neutral_60(self, fetcher):
        fetcher._value = 60
        assert fetcher.get_edge_multiplier() == 1.0

    def test_greed_61(self, fetcher):
        fetcher._value = 61
        assert fetcher.get_edge_multiplier() == 0.95

    def test_greed_70(self, fetcher):
        fetcher._value = 70
        assert fetcher.get_edge_multiplier() == 0.95

    def test_greed_75(self, fetcher):
        fetcher._value = 75
        assert fetcher.get_edge_multiplier() == 0.95

    def test_extreme_greed_76(self, fetcher):
        fetcher._value = 76
        assert fetcher.get_edge_multiplier() == 0.85

    def test_extreme_greed_90(self, fetcher):
        fetcher._value = 90
        assert fetcher.get_edge_multiplier() == 0.85

    def test_extreme_greed_100(self, fetcher):
        fetcher._value = 100
        assert fetcher.get_edge_multiplier() == 0.85
