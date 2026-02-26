"""Tests for the in-memory market cache."""

import time

from bot.data.market_cache import CacheEntry, MarketCache
from bot.polymarket.types import GammaMarket


def test_cache_set_get():
    cache = MarketCache(default_ttl=60)
    cache.set("key1", "value1")
    assert cache.get("key1") == "value1"


def test_cache_expiry():
    cache = MarketCache(default_ttl=60)
    # Set with very short TTL and wait for expiry
    cache._misc["key1"] = CacheEntry(data="value1", expires_at=time.monotonic() - 1)
    assert cache.get("key1") is None


def test_cache_market():
    cache = MarketCache()
    market = GammaMarket(id="m1", question="Test?")
    cache.set_market("m1", market)
    assert cache.get_market("m1") is not None
    assert cache.get_market("m1").question == "Test?"


def test_cache_bulk_markets():
    cache = MarketCache()
    markets = [
        GammaMarket(id=f"m{i}", question=f"Question {i}")
        for i in range(5)
    ]
    cache.set_markets_bulk(markets)
    assert len(cache.get_all_markets()) == 5


def test_cache_stats():
    cache = MarketCache()
    cache.set("k", "v")
    cache.set_market("m1", GammaMarket(id="m1"))
    stats = cache.stats
    assert stats["misc"] == 1
    assert stats["markets"] == 1


def test_cache_clear():
    cache = MarketCache()
    cache.set("k", "v")
    cache.set_market("m1", GammaMarket(id="m1"))
    cache.clear()
    assert cache.get("k") is None
    assert cache.get_market("m1") is None
