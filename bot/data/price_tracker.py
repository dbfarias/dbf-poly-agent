"""Shared in-memory price tracker for momentum detection across strategies."""

import math
import time
from collections import deque
from collections.abc import Callable

import structlog

logger = structlog.get_logger()

# Defaults
_MAX_HISTORY = 360  # ~6h at 1-min cycles
_MAX_TRACKED = 500  # Memory cap on tracked markets
_STALE_SECONDS = 900  # 15 min before eviction of inactive markets
_MOMENTUM_RISING = 0.005
_MOMENTUM_FALLING = -0.005


class PriceTracker:
    """Track price history for multiple markets in memory.

    Designed to be shared across strategies and the market analyzer
    so all components see the same momentum data.
    """

    def __init__(
        self,
        max_history: int = _MAX_HISTORY,
        max_tracked: int = _MAX_TRACKED,
    ):
        self._max_history = max_history
        self._max_tracked = max_tracked
        # market_id → deque of (price, timestamp)
        self._history: dict[str, deque[tuple[float, float]]] = {}
        # Price alerts: market_id → (stop_loss_price, take_profit_price)
        self._alerts: dict[str, tuple[float, float]] = {}
        # Registered alert callbacks (async callables: market_id, alert_type, price)
        self._alert_callbacks: list[Callable] = []

    def record(self, market_id: str, price: float) -> None:
        """Append a price observation for a market."""
        if market_id not in self._history:
            if len(self._history) >= self._max_tracked:
                logger.warning(
                    "price_tracker_cap_reached",
                    tracked=len(self._history),
                    cap=self._max_tracked,
                )
                return
            self._history[market_id] = deque(maxlen=self._max_history)
        self._history[market_id].append((price, time.time()))

    def record_batch(self, prices: dict[str, float]) -> None:
        """Bulk-record prices for multiple markets."""
        for market_id, price in prices.items():
            self.record(market_id, price)

    def momentum(
        self, market_id: str, window_minutes: int = 60
    ) -> float | None:
        """Compute % price change over the given window.

        Returns None if insufficient data. Otherwise (latest - oldest) / oldest
        where oldest is the first entry within the window.
        """
        history = self._history.get(market_id)
        if not history or len(history) < 2:
            return None

        now = time.time()
        cutoff = now - window_minutes * 60
        latest_price, _ = history[-1]

        # Find oldest entry within the window
        oldest_price: float | None = None
        for price, ts in history:
            if ts >= cutoff:
                oldest_price = price
                break

        if oldest_price is None or oldest_price <= 0:
            return None

        return (latest_price - oldest_price) / oldest_price

    def trend(
        self, market_id: str, window_minutes: int = 60
    ) -> str:
        """Classify trend as 'rising', 'falling', or 'flat'."""
        mom = self.momentum(market_id, window_minutes)
        if mom is None:
            return "flat"
        if mom > _MOMENTUM_RISING:
            return "rising"
        if mom < _MOMENTUM_FALLING:
            return "falling"
        return "flat"

    def volatility(
        self, market_id: str, window_minutes: int = 60
    ) -> float | None:
        """Compute price return volatility (std dev) over the given window.

        Returns None if fewer than 3 data points in the window.
        """
        history = self._history.get(market_id)
        if not history or len(history) < 3:
            return None

        now = time.time()
        cutoff = now - window_minutes * 60

        # Collect prices within the window
        prices_in_window = [p for p, ts in history if ts >= cutoff]
        if len(prices_in_window) < 3:
            return None

        # Compute returns: (p[i] - p[i-1]) / p[i-1]
        returns: list[float] = []
        for i in range(1, len(prices_in_window)):
            prev = prices_in_window[i - 1]
            if prev <= 0:
                continue
            ret = (prices_in_window[i] - prev) / prev
            returns.append(ret)

        if len(returns) < 2:
            return None

        # Standard deviation of returns
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return math.sqrt(variance)

    def set_alert(
        self, market_id: str, stop_loss: float, take_profit: float
    ) -> None:
        """Set price alert thresholds for a market."""
        self._alerts[market_id] = (stop_loss, take_profit)

    def remove_alert(self, market_id: str) -> None:
        """Remove price alert for a market."""
        self._alerts.pop(market_id, None)

    def check_alerts(self, market_id: str, price: float) -> str | None:
        """Check if a price triggers an alert.

        Returns "stop_loss", "take_profit", or None.
        """
        thresholds = self._alerts.get(market_id)
        if thresholds is None:
            return None

        stop_loss, take_profit = thresholds
        if price <= stop_loss:
            return "stop_loss"
        if price >= take_profit:
            return "take_profit"
        return None

    def on_alert(self, callback: Callable) -> None:
        """Register an async callback for price alerts.

        Callback signature: async (market_id: str, alert_type: str, price: float)
        """
        self._alert_callbacks.append(callback)

    def evict_stale(self, active_ids: set[str]) -> None:
        """Remove markets not in active_ids and not seen in 15+ minutes."""
        now = time.time()
        to_remove: list[str] = []
        for market_id, history in self._history.items():
            if market_id in active_ids:
                continue
            if not history:
                to_remove.append(market_id)
                continue
            _, last_ts = history[-1]
            if now - last_ts > _STALE_SECONDS:
                to_remove.append(market_id)
        for market_id in to_remove:
            del self._history[market_id]
        if to_remove:
            logger.debug("price_tracker_evicted", count=len(to_remove))

    @property
    def tracked_count(self) -> int:
        """Number of markets currently tracked."""
        return len(self._history)
