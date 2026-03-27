"""Real-time orderbook state tracker with flash crash detection."""

from __future__ import annotations

from collections import deque
from time import time
from typing import NamedTuple

import structlog

from bot.polymarket.types import OrderBook

logger = structlog.get_logger()


class PricePoint(NamedTuple):
    timestamp: float
    mid_price: float


class OrderbookTracker:
    """Maintains per-token orderbook state and price history.

    Fed by WebSocketManager on each book update. Provides:
    - Latest orderbook snapshot per token
    - Mid-price history with configurable window
    - Flash crash detection (significant drop within time window)
    """

    MAX_HISTORY_SECONDS = 600  # 10 minutes of history
    MAX_HISTORY_POINTS = 3600  # ~1 point per ~0.17s at max

    def __init__(self) -> None:
        self._books: dict[str, OrderBook] = {}
        self._book_timestamps: dict[str, float] = {}
        self._price_history: dict[str, deque[PricePoint]] = {}

    def update(self, token_id: str, book: OrderBook) -> None:
        """Update orderbook state for a token. Called on each WS message."""
        self._books[token_id] = book
        self._book_timestamps[token_id] = time()

        mid = book.mid_price
        if mid is None or mid <= 0:
            return

        if token_id not in self._price_history:
            self._price_history[token_id] = deque(maxlen=self.MAX_HISTORY_POINTS)

        now = time()
        self._price_history[token_id].append(PricePoint(now, mid))
        self._prune_old(token_id, now)

    def get_book(self, token_id: str) -> OrderBook | None:
        """Get latest orderbook snapshot for a token."""
        return self._books.get(token_id)

    def get_mid_price(self, token_id: str) -> float | None:
        """Get latest mid price for a token."""
        book = self._books.get(token_id)
        return book.mid_price if book else None

    def get_spread(self, token_id: str) -> float | None:
        """Get latest spread for a token."""
        book = self._books.get(token_id)
        return book.spread if book else None

    def book_age_seconds(self, token_id: str) -> float | None:
        """How many seconds since the last book update. None if no data."""
        ts = self._book_timestamps.get(token_id)
        return time() - ts if ts is not None else None

    def mid_price_history(
        self, token_id: str, window_seconds: int = 60
    ) -> list[PricePoint]:
        """Get mid-price history within the given time window."""
        history = self._price_history.get(token_id)
        if not history:
            return []
        cutoff = time() - window_seconds
        return [p for p in history if p.timestamp >= cutoff]

    def detect_flash_crash(
        self,
        token_id: str,
        drop_pct: float = 0.30,
        window_seconds: int = 30,
    ) -> tuple[bool, float]:
        """Check if mid-price dropped by drop_pct within window_seconds.

        Returns (triggered, drop_magnitude). drop_magnitude is the actual
        percentage drop from the window max to current price.
        """
        history = self.mid_price_history(token_id, window_seconds)
        if len(history) < 2:
            return False, 0.0

        max_price = max(p.mid_price for p in history)
        current_price = history[-1].mid_price

        if max_price <= 0:
            return False, 0.0

        drop = (max_price - current_price) / max_price
        return drop >= drop_pct, drop

    def _prune_old(self, token_id: str, now: float) -> None:
        """Remove price points older than MAX_HISTORY_SECONDS."""
        history = self._price_history.get(token_id)
        if not history:
            return
        cutoff = now - self.MAX_HISTORY_SECONDS
        while history and history[0].timestamp < cutoff:
            history.popleft()
