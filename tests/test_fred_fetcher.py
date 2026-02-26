"""Tests for FRED economic data fetcher."""

import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.research.fred_fetcher import SERIES, FredFetcher


def _mock_httpx_client(mock_response):
    """Create a mock httpx.AsyncClient that works as async context manager."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    @asynccontextmanager
    async def fake_client(*args, **kwargs):
        yield mock_client

    return fake_client


def _mock_httpx_client_error(exc):
    """Create a mock httpx.AsyncClient that raises on get()."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=exc)

    @asynccontextmanager
    async def fake_client(*args, **kwargs):
        yield mock_client

    return fake_client


# ---------------------------------------------------------------------------
# is_relevant_to_market
# ---------------------------------------------------------------------------


class TestIsRelevantToMarket:
    @pytest.fixture
    def fetcher(self):
        return FredFetcher()

    def test_fed_rate(self, fetcher):
        assert fetcher.is_relevant_to_market("Will the Fed cut rates?") == "fed_funds_rate"

    def test_interest_rate(self, fetcher):
        assert fetcher.is_relevant_to_market("Interest rate hike in June?") == "fed_funds_rate"

    def test_fomc(self, fetcher):
        assert fetcher.is_relevant_to_market("FOMC meeting decision") == "fed_funds_rate"

    def test_federal_funds(self, fetcher):
        assert fetcher.is_relevant_to_market("Federal funds rate above 5%?") == "fed_funds_rate"

    def test_cpi(self, fetcher):
        assert fetcher.is_relevant_to_market("Will CPI exceed 3%?") == "cpi_yoy"

    def test_inflation(self, fetcher):
        assert fetcher.is_relevant_to_market("Inflation above target?") == "cpi_yoy"

    def test_consumer_price(self, fetcher):
        assert fetcher.is_relevant_to_market("Consumer price index rising") == "cpi_yoy"

    def test_unemployment(self, fetcher):
        assert fetcher.is_relevant_to_market("Unemployment below 4%?") == "unemployment"

    def test_jobless(self, fetcher):
        assert fetcher.is_relevant_to_market("Jobless claims surging") == "unemployment"

    def test_jobs_report(self, fetcher):
        assert fetcher.is_relevant_to_market("Jobs report beat expectations?") == "unemployment"

    def test_treasury(self, fetcher):
        assert fetcher.is_relevant_to_market("10-year treasury yield above 5%?") == "treasury_10y"

    def test_bond_yield(self, fetcher):
        assert fetcher.is_relevant_to_market("Bond yield inversion?") == "treasury_10y"

    def test_gdp(self, fetcher):
        assert fetcher.is_relevant_to_market("GDP growth above 2%?") == "gdp_growth"

    def test_gross_domestic(self, fetcher):
        assert fetcher.is_relevant_to_market("Gross domestic product Q1") == "gdp_growth"

    def test_non_economic(self, fetcher):
        assert fetcher.is_relevant_to_market("Will Bitcoin reach $100k?") is None

    def test_empty(self, fetcher):
        assert fetcher.is_relevant_to_market("") is None

    def test_sports(self, fetcher):
        assert fetcher.is_relevant_to_market("Will the Lakers win?") is None


# ---------------------------------------------------------------------------
# get_latest
# ---------------------------------------------------------------------------


class TestGetLatest:
    @pytest.fixture
    def fetcher(self):
        return FredFetcher()

    @pytest.mark.asyncio
    async def test_no_api_key(self, fetcher):
        with patch("bot.research.fred_fetcher._API_KEY", ""):
            result = await fetcher.get_latest("fed_funds_rate")
            assert result is None

    @pytest.mark.asyncio
    async def test_invalid_series_name(self, fetcher):
        with patch("bot.research.fred_fetcher._API_KEY", "test-key"):
            result = await fetcher.get_latest("nonexistent_series")
            assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit(self, fetcher):
        fetcher._cache["fed_funds_rate"] = 5.33
        fetcher._cache_expires = time.monotonic() + 3600
        with patch("bot.research.fred_fetcher._API_KEY", "test-key"):
            result = await fetcher.get_latest("fed_funds_rate")
            assert result == 5.33

    @pytest.mark.asyncio
    async def test_successful_fetch(self, fetcher):
        mock_response = MagicMock()
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {
            "observations": [{"value": "5.33"}],
        }

        with patch("bot.research.fred_fetcher._API_KEY", "test-key"), \
             patch(
                "bot.research.fred_fetcher.httpx.AsyncClient",
                new=_mock_httpx_client(mock_response),
            ):
            result = await fetcher.get_latest("fed_funds_rate")
            assert result == 5.33
            assert fetcher._cache["fed_funds_rate"] == 5.33

    @pytest.mark.asyncio
    async def test_empty_observations(self, fetcher):
        mock_response = MagicMock()
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {"observations": []}

        with patch("bot.research.fred_fetcher._API_KEY", "test-key"), \
             patch(
                "bot.research.fred_fetcher.httpx.AsyncClient",
                new=_mock_httpx_client(mock_response),
            ):
            result = await fetcher.get_latest("fed_funds_rate")
            assert result is None

    @pytest.mark.asyncio
    async def test_dot_value_returns_none(self, fetcher):
        """FRED uses '.' for missing values."""
        mock_response = MagicMock()
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {
            "observations": [{"value": "."}],
        }

        with patch("bot.research.fred_fetcher._API_KEY", "test-key"), \
             patch(
                "bot.research.fred_fetcher.httpx.AsyncClient",
                new=_mock_httpx_client(mock_response),
            ):
            result = await fetcher.get_latest("fed_funds_rate")
            assert result is None

    @pytest.mark.asyncio
    async def test_empty_value_string(self, fetcher):
        mock_response = MagicMock()
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {
            "observations": [{"value": ""}],
        }

        with patch("bot.research.fred_fetcher._API_KEY", "test-key"), \
             patch(
                "bot.research.fred_fetcher.httpx.AsyncClient",
                new=_mock_httpx_client(mock_response),
            ):
            result = await fetcher.get_latest("fed_funds_rate")
            assert result is None

    @pytest.mark.asyncio
    async def test_http_error_returns_cached(self, fetcher):
        fetcher._cache["fed_funds_rate"] = 4.50

        with patch("bot.research.fred_fetcher._API_KEY", "test-key"), \
             patch("bot.research.fred_fetcher.httpx.AsyncClient",
                   new=_mock_httpx_client_error(httpx.ConnectError("fail"))):
            result = await fetcher.get_latest("fed_funds_rate")
            assert result == 4.50

    @pytest.mark.asyncio
    async def test_http_error_no_cache_returns_none(self, fetcher):
        with patch("bot.research.fred_fetcher._API_KEY", "test-key"), \
             patch("bot.research.fred_fetcher.httpx.AsyncClient",
                   new=_mock_httpx_client_error(httpx.ConnectError("fail"))):
            result = await fetcher.get_latest("fed_funds_rate")
            assert result is None


# ---------------------------------------------------------------------------
# get_all
# ---------------------------------------------------------------------------


class TestGetAll:
    @pytest.fixture
    def fetcher(self):
        return FredFetcher()

    @pytest.mark.asyncio
    async def test_returns_dict(self, fetcher):
        async def mock_get_latest(name):
            return {"fed_funds_rate": 5.33, "cpi_yoy": 3.1}.get(name)

        with patch.object(fetcher, "get_latest", side_effect=mock_get_latest):
            result = await fetcher.get_all()
            assert "fed_funds_rate" in result
            assert "cpi_yoy" in result
            assert result["fed_funds_rate"] == 5.33

    @pytest.mark.asyncio
    async def test_skips_none_values(self, fetcher):
        async def mock_get_latest(name):
            if name == "fed_funds_rate":
                return 5.33
            return None

        with patch.object(fetcher, "get_latest", side_effect=mock_get_latest):
            result = await fetcher.get_all()
            assert "fed_funds_rate" in result
            assert "cpi_yoy" not in result


# ---------------------------------------------------------------------------
# SERIES constant
# ---------------------------------------------------------------------------


class TestSeriesConstant:
    def test_known_series_exist(self):
        assert "fed_funds_rate" in SERIES
        assert "cpi_yoy" in SERIES
        assert "unemployment" in SERIES
        assert "treasury_10y" in SERIES
        assert "gdp_growth" in SERIES
        assert "pce_inflation" in SERIES

    def test_series_ids_are_strings(self):
        for name, sid in SERIES.items():
            assert isinstance(sid, str)
            assert len(sid) > 0
