"""Volume anomaly detection — flags markets with sudden volume/price spikes."""

from collections import deque
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger()

# Detection thresholds
_VOLUME_SPIKE_FACTOR = 3.0  # Current > 3x rolling mean
_PRICE_MOVE_THRESHOLD = 0.10  # 10% price deviation from rolling mean
_MIN_SAMPLES = 5  # Need at least 5 samples for meaningful comparison
_MAX_HISTORY = 96  # ~24h at 15min intervals
_STALE_SECONDS = 7200  # Remove markets not seen in 2 hours


class VolumeAnomalyDetector:
    """Detects sudden volume spikes and price moves across markets.

    Maintains rolling 24h history (sampled every scan cycle).
    Markets with current metrics > thresholds are flagged as anomalies.
    """

    def __init__(self) -> None:
        self._volume_history: dict[str, deque[float]] = {}
        self._price_history: dict[str, deque[float]] = {}
        self._last_seen: dict[str, datetime] = {}
        self._anomaly_ids: set[str] = set()

    def update(self, markets: list) -> list[str]:
        """Update histories from market data. Returns list of anomaly market IDs.

        Each market must have: id, volume_24h, best_bid_price (or None).
        """
        now = datetime.now(timezone.utc)
        new_anomalies: list[str] = []
        self._anomaly_ids = set()

        for market in markets:
            market_id = market.id
            self._last_seen[market_id] = now

            # Update volume history
            volume = getattr(market, "volume_24h", 0.0) or 0.0
            if market_id not in self._volume_history:
                self._volume_history[market_id] = deque(maxlen=_MAX_HISTORY)
            self._volume_history[market_id].append(volume)

            # Update price history
            price = getattr(market, "best_bid_price", None)
            if price is not None and price > 0:
                if market_id not in self._price_history:
                    self._price_history[market_id] = deque(maxlen=_MAX_HISTORY)
                self._price_history[market_id].append(price)

            # Check for anomalies
            if self._is_volume_spike(market_id) or self._is_price_move(market_id):
                self._anomaly_ids.add(market_id)
                new_anomalies.append(market_id)

        # Evict stale markets
        self._evict_stale(now)

        if new_anomalies:
            logger.info(
                "volume_anomalies_detected",
                count=len(new_anomalies),
                market_ids=[mid[:16] for mid in new_anomalies[:5]],
            )

        return new_anomalies

    def is_anomaly(self, market_id: str) -> bool:
        """Check if a market is currently flagged as anomalous."""
        return market_id in self._anomaly_ids

    def get_anomalies(self) -> list[str]:
        """Return currently flagged anomaly market IDs."""
        return list(self._anomaly_ids)

    def _is_volume_spike(self, market_id: str) -> bool:
        """Check if current volume is > 3x rolling mean of last 10 samples."""
        history = self._volume_history.get(market_id)
        if not history or len(history) < _MIN_SAMPLES:
            return False

        current = history[-1]
        # Use last 10 samples (excluding current) for comparison
        lookback = list(history)[-11:-1]
        if not lookback:
            return False

        mean_vol = sum(lookback) / len(lookback)
        if mean_vol <= 0:
            return False

        return current > mean_vol * _VOLUME_SPIKE_FACTOR

    def _is_price_move(self, market_id: str) -> bool:
        """Check if current price deviated > 10% from rolling mean of last 5."""
        history = self._price_history.get(market_id)
        if not history or len(history) < _MIN_SAMPLES:
            return False

        current = history[-1]
        lookback = list(history)[-6:-1]
        if not lookback:
            return False

        mean_price = sum(lookback) / len(lookback)
        if mean_price <= 0:
            return False

        deviation = abs(current - mean_price) / mean_price
        return deviation > _PRICE_MOVE_THRESHOLD

    def _evict_stale(self, now: datetime) -> None:
        """Remove markets not seen in _STALE_SECONDS."""
        stale_ids = [
            mid
            for mid, last in self._last_seen.items()
            if (now - last).total_seconds() > _STALE_SECONDS
        ]
        for mid in stale_ids:
            self._volume_history.pop(mid, None)
            self._price_history.pop(mid, None)
            self._last_seen.pop(mid, None)
            self._anomaly_ids.discard(mid)
