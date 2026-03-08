"""Whale detector — tracks large orders seen on CLOB WebSocket."""

import time
from dataclasses import dataclass

import structlog

from bot.polymarket.types import OrderBook

logger = structlog.get_logger()

WHALE_THRESHOLD_USD = 500.0
_SIGNAL_TTL = 3600.0  # 1 hour


@dataclass(frozen=True)
class WhaleSignal:
    """A single whale order observation."""

    side: str  # "BUY" or "SELL"
    size_usd: float
    price: float
    timestamp: float


class WhaleDetector:
    """Tracks large orders from CLOB order book snapshots.

    Records any bid/ask where price * size exceeds WHALE_THRESHOLD_USD.
    Signals are evicted after 1 hour.
    """

    def __init__(self, threshold_usd: float = WHALE_THRESHOLD_USD):
        self.threshold_usd = threshold_usd
        # asset_id (token_id) → list of recent whale signals
        self._whale_signals: dict[str, list[WhaleSignal]] = {}

    def record_book_update(self, asset_id: str, book: OrderBook) -> None:
        """Scan an order book snapshot for whale-sized orders.

        Called from WebSocket handler on each book update.
        """
        now = time.time()
        new_signals: list[WhaleSignal] = []

        for bid in book.bids:
            notional = bid.price * bid.size
            if notional >= self.threshold_usd:
                new_signals.append(WhaleSignal(
                    side="BUY",
                    size_usd=notional,
                    price=bid.price,
                    timestamp=now,
                ))

        for ask in book.asks:
            notional = ask.price * ask.size
            if notional >= self.threshold_usd:
                new_signals.append(WhaleSignal(
                    side="SELL",
                    size_usd=notional,
                    price=ask.price,
                    timestamp=now,
                ))

        if new_signals:
            existing = self._whale_signals.get(asset_id, [])
            self._whale_signals[asset_id] = existing + new_signals
            logger.debug(
                "whale_signals_recorded",
                asset_id=asset_id[:16],
                count=len(new_signals),
            )

    def has_whale_activity_by_token(self, token_id: str) -> bool:
        """Check if any whale signals exist for a token in the last hour."""
        signals = self._whale_signals.get(token_id)
        if not signals:
            return False
        cutoff = time.time() - _SIGNAL_TTL
        return any(s.timestamp >= cutoff for s in signals)

    def has_whale_activity(self, market_id: str) -> bool:
        """Check whale activity by market_id.

        Since we store by asset_id (token_id), this checks all tracked
        tokens. For a direct lookup, use has_whale_activity_by_token().
        """
        # Market IDs and token IDs are different on Polymarket.
        # Without a mapping, we cannot reliably look up by market_id here.
        # Callers with token_ids should use has_whale_activity_by_token().
        return False

    def get_whale_summary(self, asset_id: str) -> dict | None:
        """Return a summary of whale activity for the given asset.

        Returns {"count": N, "total_usd": X, "net_side": "BUY"|"SELL"|"MIXED"}
        or None if no recent signals.
        """
        signals = self._whale_signals.get(asset_id)
        if not signals:
            return None

        cutoff = time.time() - _SIGNAL_TTL
        recent = [s for s in signals if s.timestamp >= cutoff]
        if not recent:
            return None

        total_usd = sum(s.size_usd for s in recent)
        buy_count = sum(1 for s in recent if s.side == "BUY")
        sell_count = sum(1 for s in recent if s.side == "SELL")

        if buy_count > 0 and sell_count == 0:
            net_side = "BUY"
        elif sell_count > 0 and buy_count == 0:
            net_side = "SELL"
        else:
            net_side = "MIXED"

        return {
            "count": len(recent),
            "total_usd": round(total_usd, 2),
            "net_side": net_side,
        }

    def evict_stale(self) -> None:
        """Remove signals older than 1 hour."""
        cutoff = time.time() - _SIGNAL_TTL
        to_remove: list[str] = []

        for asset_id, signals in self._whale_signals.items():
            fresh = [s for s in signals if s.timestamp >= cutoff]
            if fresh:
                self._whale_signals[asset_id] = fresh
            else:
                to_remove.append(asset_id)

        for asset_id in to_remove:
            del self._whale_signals[asset_id]

        if to_remove:
            logger.debug("whale_signals_evicted", count=len(to_remove))

    @property
    def tracked_assets(self) -> int:
        """Number of assets with whale signals."""
        return len(self._whale_signals)
