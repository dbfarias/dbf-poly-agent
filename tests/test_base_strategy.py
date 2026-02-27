"""Tests for BaseStrategy tier gating, repr, and order book caching."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.agent.strategies.base import BaseStrategy
from bot.config import CapitalTier
from bot.polymarket.types import OrderBook, OrderBookEntry


def _make_concrete_strategy(min_tier: CapitalTier, name: str = "test_strat"):
    """Create a concrete subclass of BaseStrategy for testing."""

    class ConcreteStrategy(BaseStrategy):
        async def scan(self, markets):
            return []

        async def should_exit(self, market_id, current_price):
            return False

    ConcreteStrategy.name = name
    ConcreteStrategy.min_tier = min_tier

    return ConcreteStrategy(
        clob_client=MagicMock(),
        gamma_client=MagicMock(),
        cache=MagicMock(),
    )


class TestBaseStrategy:
    def test_tier1_strategy_enabled_for_all_tiers(self):
        strat = _make_concrete_strategy(CapitalTier.TIER1)
        assert strat.is_enabled_for_tier(CapitalTier.TIER1) is True
        assert strat.is_enabled_for_tier(CapitalTier.TIER2) is True
        assert strat.is_enabled_for_tier(CapitalTier.TIER3) is True

    def test_tier2_strategy_disabled_for_tier1(self):
        strat = _make_concrete_strategy(CapitalTier.TIER2)
        assert strat.is_enabled_for_tier(CapitalTier.TIER1) is False

    def test_tier2_strategy_enabled_for_tier2_and_above(self):
        strat = _make_concrete_strategy(CapitalTier.TIER2)
        assert strat.is_enabled_for_tier(CapitalTier.TIER2) is True
        assert strat.is_enabled_for_tier(CapitalTier.TIER3) is True

    def test_tier3_strategy_disabled_for_tier1_and_tier2(self):
        strat = _make_concrete_strategy(CapitalTier.TIER3)
        assert strat.is_enabled_for_tier(CapitalTier.TIER1) is False
        assert strat.is_enabled_for_tier(CapitalTier.TIER2) is False

    def test_tier3_strategy_enabled_for_tier3(self):
        strat = _make_concrete_strategy(CapitalTier.TIER3)
        assert strat.is_enabled_for_tier(CapitalTier.TIER3) is True

    def test_repr_includes_name(self):
        strat = _make_concrete_strategy(CapitalTier.TIER1, name="my_strategy")
        assert "my_strategy" in repr(strat)


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

        strat = _make_concrete_strategy(CapitalTier.TIER1)
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

        strat = _make_concrete_strategy(CapitalTier.TIER1)
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

        strat1 = _make_concrete_strategy(CapitalTier.TIER1, name="strat1")
        strat1.cache = mock_cache
        strat1.clob = mock_clob

        strat2 = _make_concrete_strategy(CapitalTier.TIER1, name="strat2")
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
