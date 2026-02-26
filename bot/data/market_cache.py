"""In-memory cache for market data with TTL."""

import time
from dataclasses import dataclass

from bot.polymarket.types import GammaMarket, OrderBook


@dataclass
class CacheEntry:
    data: object
    expires_at: float


class MarketCache:
    """Thread-safe in-memory cache with TTL for market data."""

    def __init__(self, default_ttl: int = 60):
        self.default_ttl = default_ttl
        self._markets: dict[str, CacheEntry] = {}
        self._order_books: dict[str, CacheEntry] = {}
        self._misc: dict[str, CacheEntry] = {}

    def _is_expired(self, entry: CacheEntry) -> bool:
        return time.monotonic() > entry.expires_at

    # Market cache
    def set_market(self, market_id: str, market: GammaMarket, ttl: int | None = None) -> None:
        self._markets[market_id] = CacheEntry(
            data=market, expires_at=time.monotonic() + (ttl or self.default_ttl)
        )

    def get_market(self, market_id: str) -> GammaMarket | None:
        entry = self._markets.get(market_id)
        if entry and not self._is_expired(entry):
            return entry.data
        if entry:
            del self._markets[market_id]
        return None

    def set_markets_bulk(self, markets: list[GammaMarket], ttl: int | None = None) -> None:
        for m in markets:
            self.set_market(m.id, m, ttl)

    def get_all_markets(self) -> list[GammaMarket]:
        now = time.monotonic()
        valid = []
        expired_keys = []
        for key, entry in self._markets.items():
            if now > entry.expires_at:
                expired_keys.append(key)
            else:
                valid.append(entry.data)
        for key in expired_keys:
            del self._markets[key]
        return valid

    # Order book cache
    def set_order_book(self, token_id: str, book: OrderBook, ttl: int = 10) -> None:
        self._order_books[token_id] = CacheEntry(
            data=book, expires_at=time.monotonic() + ttl
        )

    def get_order_book(self, token_id: str) -> OrderBook | None:
        entry = self._order_books.get(token_id)
        if entry and not self._is_expired(entry):
            return entry.data
        if entry:
            del self._order_books[token_id]
        return None

    # Generic cache
    def set(self, key: str, value: object, ttl: int | None = None) -> None:
        self._misc[key] = CacheEntry(
            data=value, expires_at=time.monotonic() + (ttl or self.default_ttl)
        )

    def get(self, key: str) -> object | None:
        entry = self._misc.get(key)
        if entry and not self._is_expired(entry):
            return entry.data
        if entry:
            del self._misc[key]
        return None

    def clear(self) -> None:
        self._markets.clear()
        self._order_books.clear()
        self._misc.clear()

    @property
    def stats(self) -> dict:
        return {
            "markets": len(self._markets),
            "order_books": len(self._order_books),
            "misc": len(self._misc),
        }
