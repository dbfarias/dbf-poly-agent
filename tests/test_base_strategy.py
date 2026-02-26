"""Tests for BaseStrategy repr, update_param, and order book caching."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.agent.strategies.base import BaseStrategy
from bot.polymarket.types import OrderBook, OrderBookEntry


def _make_concrete_strategy(
    name: str = "test_strat",
    mutable_params: dict | None = None,
):
    """Create a concrete subclass of BaseStrategy for testing."""

    class ConcreteStrategy(BaseStrategy):
        _MUTABLE_PARAMS = mutable_params or {}

        async def scan(self, markets):
            return []

        async def should_exit(self, market_id, current_price):
            return False

    ConcreteStrategy.name = name

    return ConcreteStrategy(
        clob_client=MagicMock(),
        gamma_client=MagicMock(),
        cache=MagicMock(),
    )


class TestBaseStrategy:
    def test_repr_includes_name(self):
        strat = _make_concrete_strategy(name="my_strategy")
        assert "my_strategy" in repr(strat)


# ---------------------------------------------------------------------------
# update_param validation
# ---------------------------------------------------------------------------


class TestUpdateParam:
    def test_accepts_known_param_in_range(self):
        params = {"MIN_EDGE": {"type": float, "min": 0.0, "max": 0.5}}
        strat = _make_concrete_strategy(mutable_params=params)
        strat.MIN_EDGE = 0.01

        assert strat.update_param("MIN_EDGE", 0.05) is True
        assert strat.MIN_EDGE == 0.05

    def test_rejects_unknown_param(self):
        strat = _make_concrete_strategy(mutable_params={})
        assert strat.update_param("HACKED_ATTR", 999) is False
        assert not hasattr(strat, "HACKED_ATTR")

    def test_rejects_value_below_min(self):
        params = {"X": {"type": float, "min": 0.0, "max": 1.0}}
        strat = _make_concrete_strategy(mutable_params=params)
        strat.X = 0.5

        assert strat.update_param("X", -0.1) is False
        assert strat.X == 0.5  # Unchanged

    def test_rejects_value_above_max(self):
        params = {"X": {"type": float, "min": 0.0, "max": 1.0}}
        strat = _make_concrete_strategy(mutable_params=params)
        strat.X = 0.5

        assert strat.update_param("X", 1.5) is False
        assert strat.X == 0.5

    def test_rejects_wrong_type(self):
        params = {"COUNT": {"type": int, "min": 1, "max": 100}}
        strat = _make_concrete_strategy(mutable_params=params)
        strat.COUNT = 5

        assert strat.update_param("COUNT", "not_a_number") is False
        assert strat.COUNT == 5

    def test_coerces_int_from_float(self):
        params = {"COUNT": {"type": int, "min": 1, "max": 100}}
        strat = _make_concrete_strategy(mutable_params=params)
        strat.COUNT = 5

        assert strat.update_param("COUNT", 10.0) is True
        assert strat.COUNT == 10

    def test_boundary_values_accepted(self):
        params = {"X": {"type": float, "min": 0.0, "max": 1.0}}
        strat = _make_concrete_strategy(mutable_params=params)

        assert strat.update_param("X", 0.0) is True
        assert strat.X == 0.0
        assert strat.update_param("X", 1.0) is True
        assert strat.X == 1.0


# ---------------------------------------------------------------------------
# Order Book Caching (H1-H3)
# ---------------------------------------------------------------------------


def _make_order_book(best_bid: float = 0.50, best_ask: float = 0.55) -> OrderBook:
    return OrderBook(
        market="test",
        bids=[OrderBookEntry(price=best_bid, size=100.0)],
        asks=[OrderBookEntry(price=best_ask, size=100.0)],
    )


class TestGetOrderBook:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_api(self):
        """When cache has the order book, should NOT call CLOB API."""
        cached_book = _make_order_book(0.50, 0.55)
        mock_cache = MagicMock()
        mock_cache.get_order_book = MagicMock(return_value=cached_book)

        mock_clob = AsyncMock()

        strat = _make_concrete_strategy()
        strat.cache = mock_cache
        strat.clob = mock_clob

        result = await strat.get_order_book("token_abc")

        assert result is cached_book
        mock_cache.get_order_book.assert_called_once_with("token_abc")
        mock_clob.get_order_book.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_fetches_and_caches(self):
        """On cache miss, should fetch from CLOB and store in cache."""
        fresh_book = _make_order_book(0.48, 0.52)

        mock_cache = MagicMock()
        mock_cache.get_order_book = MagicMock(return_value=None)

        mock_clob = AsyncMock()
        mock_clob.get_order_book = AsyncMock(return_value=fresh_book)

        strat = _make_concrete_strategy()
        strat.cache = mock_cache
        strat.clob = mock_clob

        result = await strat.get_order_book("token_xyz")

        assert result is fresh_book
        mock_clob.get_order_book.assert_called_once_with("token_xyz")
        mock_cache.set_order_book.assert_called_once_with("token_xyz", fresh_book, ttl=10)

    @pytest.mark.asyncio
    async def test_strategies_share_cache(self):
        """Two strategies using the same cache should share order book data."""
        book = _make_order_book()

        # Shared cache — first call misses, second should hit
        mock_cache = MagicMock()
        call_count = 0

        def mock_get(token_id):
            nonlocal call_count
            call_count += 1
            # Simulate: first call returns None (miss), second returns cached
            return None if call_count == 1 else book

        mock_cache.get_order_book = MagicMock(side_effect=mock_get)
        mock_clob = AsyncMock()
        mock_clob.get_order_book = AsyncMock(return_value=book)

        strat1 = _make_concrete_strategy(name="strat1")
        strat1.cache = mock_cache
        strat1.clob = mock_clob

        strat2 = _make_concrete_strategy(name="strat2")
        strat2.cache = mock_cache
        strat2.clob = mock_clob

        # First strategy: cache miss → fetches
        result1 = await strat1.get_order_book("shared_token")
        assert result1 is book
        assert mock_clob.get_order_book.call_count == 1

        # Second strategy: cache hit → no API call
        result2 = await strat2.get_order_book("shared_token")
        assert result2 is book
        assert mock_clob.get_order_book.call_count == 1  # Still 1
