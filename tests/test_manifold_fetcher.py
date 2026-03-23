"""Tests for Manifold Markets cross-platform fetcher."""

import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.research.manifold_fetcher import ManifoldFetcher


def _mock_httpx_client(mock_response):
    """Create a mock httpx.AsyncClient that works as async context manager."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    @asynccontextmanager
    async def fake_client(*args, **kwargs):
        yield mock_client

    return fake_client


def _mock_httpx_client_error(exc):
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=exc)

    @asynccontextmanager
    async def fake_client(*args, **kwargs):
        yield mock_client

    return fake_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_market(question: str, probability: float = 0.65) -> dict:
    return {"question": question, "probability": probability}


# ---------------------------------------------------------------------------
# find_matching_probability
# ---------------------------------------------------------------------------


class TestFindMatchingProbability:
    @pytest.fixture
    def fetcher(self):
        f = ManifoldFetcher()
        f._all_markets = [
            _make_market("Will Trump win the 2026 presidential election?", 0.55),
            _make_market("Will Bitcoin reach $100k before July 2026?", 0.72),
            _make_market("Will SpaceX launch Starship successfully?", 0.80),
            _make_market("Will the Federal Reserve cut interest rates in June?", 0.45),
        ]
        return f

    def test_good_match(self, fetcher):
        prob = fetcher.find_matching_probability(
            "Will Bitcoin reach $100k before July 2026?"
        )
        assert prob == pytest.approx(0.72)

    def test_partial_match_high_overlap(self, fetcher):
        prob = fetcher.find_matching_probability(
            "Bitcoin reach $100k before July 2026 deadline?"
        )
        assert prob is not None
        assert prob == pytest.approx(0.72)

    def test_no_match_different_topic(self, fetcher):
        prob = fetcher.find_matching_probability(
            "Will inflation exceed 5% in Q3?"
        )
        assert prob is None

    def test_empty_markets(self):
        f = ManifoldFetcher()
        prob = f.find_matching_probability("Any question here?")
        assert prob is None

    def test_too_few_keywords(self, fetcher):
        prob = fetcher.find_matching_probability("Will the?")
        assert prob is None

    def test_single_word_question(self, fetcher):
        prob = fetcher.find_matching_probability("Bitcoin")
        assert prob is None

    def test_probability_zero_rejected(self):
        f = ManifoldFetcher()
        f._all_markets = [
            {"question": "Will Bitcoin reach $100k before July 2026?", "probability": 0.0},
        ]
        prob = f.find_matching_probability(
            "Will Bitcoin reach $100k before July 2026?"
        )
        assert prob is None

    def test_probability_one_rejected(self):
        f = ManifoldFetcher()
        f._all_markets = [
            {"question": "Will Bitcoin reach $100k before July 2026?", "probability": 1.0},
        ]
        prob = f.find_matching_probability(
            "Will Bitcoin reach $100k before July 2026?"
        )
        assert prob is None

    def test_missing_probability_key(self):
        f = ManifoldFetcher()
        f._all_markets = [
            {"question": "Will Bitcoin reach $100k before July 2026?"},
        ]
        prob = f.find_matching_probability(
            "Will Bitcoin reach $100k before July 2026?"
        )
        assert prob is None

    def test_stop_words_filtered(self, fetcher):
        prob = fetcher.find_matching_probability("will the be by on in of")
        assert prob is None

    def test_best_match_wins(self):
        f = ManifoldFetcher()
        f._all_markets = [
            _make_market("Will SpaceX launch Starship successfully in 2026?", 0.80),
            _make_market("Will SpaceX launch Starship orbital test successfully 2026?", 0.65),
        ]
        prob = f.find_matching_probability(
            "Will SpaceX launch Starship orbital test successfully 2026?"
        )
        assert prob == pytest.approx(0.65)


# ---------------------------------------------------------------------------
# get_cross_platform_edge
# ---------------------------------------------------------------------------


class TestGetCrossPlatformEdge:
    @pytest.fixture
    def fetcher(self):
        f = ManifoldFetcher()
        f._all_markets = [
            _make_market("Will Bitcoin reach $100k before July 2026?", 0.72),
        ]
        return f

    def test_positive_edge(self, fetcher):
        prob, edge = fetcher.get_cross_platform_edge(
            "Will Bitcoin reach $100k before July 2026?",
            polymarket_price=0.58,
        )
        assert prob == pytest.approx(0.72)
        assert edge == pytest.approx(0.14, abs=0.01)

    def test_negative_edge(self, fetcher):
        prob, edge = fetcher.get_cross_platform_edge(
            "Will Bitcoin reach $100k before July 2026?",
            polymarket_price=0.85,
        )
        assert prob == pytest.approx(0.72)
        assert edge < 0

    def test_no_match(self, fetcher):
        prob, edge = fetcher.get_cross_platform_edge(
            "Some unrelated question about weather?",
            polymarket_price=0.50,
        )
        assert prob == 0.0
        assert edge == 0.0


# ---------------------------------------------------------------------------
# refresh_markets
# ---------------------------------------------------------------------------


class TestRefreshMarkets:
    @pytest.fixture
    def fetcher(self):
        return ManifoldFetcher()

    @pytest.mark.asyncio
    async def test_successful_refresh(self, fetcher):
        mock_response = MagicMock()
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = [
            {"question": "Test market?", "probability": 0.5},
        ]

        with patch("bot.research.manifold_fetcher.httpx.AsyncClient",
                    new=_mock_httpx_client(mock_response)):
            await fetcher.refresh_markets()
            assert len(fetcher._all_markets) == 1
            assert fetcher._cache_expires > time.monotonic()

    @pytest.mark.asyncio
    async def test_cache_prevents_refetch(self, fetcher):
        fetcher._all_markets = [{"question": "Cached", "probability": 0.5}]
        fetcher._cache_expires = time.monotonic() + 600

        await fetcher.refresh_markets()
        assert fetcher._all_markets[0]["question"] == "Cached"

    @pytest.mark.asyncio
    async def test_error_keeps_old_markets(self, fetcher):
        fetcher._all_markets = [{"question": "Old", "probability": 0.3}]

        with patch("bot.research.manifold_fetcher.httpx.AsyncClient",
                    new=_mock_httpx_client_error(httpx.ConnectError("fail"))):
            await fetcher.refresh_markets()
            assert fetcher._all_markets[0]["question"] == "Old"

    @pytest.mark.asyncio
    async def test_refresh_clears_keyword_cache(self, fetcher):
        fetcher._cache["test_key"] = 0.5

        mock_response = MagicMock()
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = []

        with patch("bot.research.manifold_fetcher.httpx.AsyncClient",
                    new=_mock_httpx_client(mock_response)):
            await fetcher.refresh_markets()
            assert len(fetcher._cache) == 0
