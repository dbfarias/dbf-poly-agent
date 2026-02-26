"""In-memory cache for research results with TTL."""

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from bot.research.types import ResearchResult


@dataclass
class CacheEntry:
    result: ResearchResult
    expires_at: float


class ResearchCache:
    """Thread-safe in-memory cache for research results, following MarketCache pattern."""

    def __init__(self, default_ttl: int = 3600):
        self.default_ttl = default_ttl
        self._entries: dict[str, CacheEntry] = {}
        self._last_scan: datetime | None = None
        self._markets_scanned: int = 0

    def set(self, market_id: str, result: ResearchResult, ttl: int | None = None) -> None:
        """Cache a research result with TTL."""
        self._entries[market_id] = CacheEntry(
            result=result,
            expires_at=time.monotonic() + (ttl or self.default_ttl),
        )

    def get(self, market_id: str) -> ResearchResult | None:
        """Get a cached result, returning None if expired or missing."""
        entry = self._entries.get(market_id)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._entries[market_id]
            return None
        return entry.result

    def get_all(self) -> list[ResearchResult]:
        """Get all non-expired results."""
        now = time.monotonic()
        expired_keys = []
        results = []
        for key, entry in self._entries.items():
            if now > entry.expires_at:
                expired_keys.append(key)
            else:
                results.append(entry.result)
        for key in expired_keys:
            del self._entries[key]
        return results

    def clear(self) -> None:
        """Clear all cached entries."""
        self._entries.clear()

    def record_scan(self, markets_scanned: int) -> None:
        """Record that a scan completed."""
        self._last_scan = datetime.now(timezone.utc)
        self._markets_scanned = markets_scanned

    @property
    def stats(self) -> dict:
        """Cache statistics for API/dashboard."""
        return {
            "cached_markets": len(self._entries),
            "last_scan": self._last_scan.isoformat() if self._last_scan else None,
            "markets_scanned": self._markets_scanned,
        }
