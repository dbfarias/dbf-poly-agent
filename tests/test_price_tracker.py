"""Tests for the shared PriceTracker."""

import time
from unittest.mock import patch

import pytest

from bot.data.price_tracker import PriceTracker


class TestRecord:
    def test_record_and_momentum(self):
        tracker = PriceTracker()
        # Record two prices a tiny interval apart
        tracker.record("m1", 0.50)
        tracker.record("m1", 0.55)
        mom = tracker.momentum("m1", window_minutes=60)
        assert mom is not None
        assert mom == pytest.approx((0.55 - 0.50) / 0.50)

    def test_record_increments_tracked_count(self):
        tracker = PriceTracker()
        assert tracker.tracked_count == 0
        tracker.record("m1", 0.50)
        assert tracker.tracked_count == 1
        tracker.record("m2", 0.60)
        assert tracker.tracked_count == 2


class TestMomentum:
    def test_momentum_empty(self):
        tracker = PriceTracker()
        assert tracker.momentum("unknown") is None

    def test_momentum_single_entry(self):
        tracker = PriceTracker()
        tracker.record("m1", 0.50)
        # Need at least 2 entries
        assert tracker.momentum("m1") is None

    def test_momentum_window(self):
        """Only prices within the window should be considered."""
        tracker = PriceTracker()
        now = time.time()
        # Record an old price (2 hours ago) and a recent one
        with patch("bot.data.price_tracker.time") as mock_time:
            mock_time.time.return_value = now - 7200  # 2h ago
            tracker.record("m1", 0.40)

            mock_time.time.return_value = now - 3600  # 1h ago
            tracker.record("m1", 0.45)

            mock_time.time.return_value = now
            tracker.record("m1", 0.50)

            # 90-min window: should include entry at now-3600 (barely inside) and now
            mom_90 = tracker.momentum("m1", window_minutes=90)
            # Within 90 min: entries at now-3600 (barely inside) and now
            assert mom_90 == pytest.approx((0.50 - 0.45) / 0.45)

    def test_momentum_negative(self):
        tracker = PriceTracker()
        tracker.record("m1", 0.60)
        tracker.record("m1", 0.50)
        mom = tracker.momentum("m1", window_minutes=60)
        assert mom is not None
        assert mom < 0
        assert mom == pytest.approx((0.50 - 0.60) / 0.60)


class TestTrend:
    def test_trend_rising(self):
        tracker = PriceTracker()
        tracker.record("m1", 0.50)
        tracker.record("m1", 0.52)  # +4% > 0.5% threshold
        assert tracker.trend("m1") == "rising"

    def test_trend_falling(self):
        tracker = PriceTracker()
        tracker.record("m1", 0.50)
        tracker.record("m1", 0.48)  # -4% < -0.5% threshold
        assert tracker.trend("m1") == "falling"

    def test_trend_flat(self):
        tracker = PriceTracker()
        tracker.record("m1", 0.500)
        tracker.record("m1", 0.501)  # +0.2% — within flat band
        assert tracker.trend("m1") == "flat"

    def test_trend_unknown_market(self):
        tracker = PriceTracker()
        assert tracker.trend("unknown") == "flat"


class TestEvictStale:
    def test_evict_stale_removes_inactive(self):
        tracker = PriceTracker()
        now = time.time()
        with patch("bot.data.price_tracker.time") as mock_time:
            # Record 20 min ago (stale)
            mock_time.time.return_value = now - 1200
            tracker.record("stale_market", 0.50)

            # Record active market with 2 entries so momentum works
            mock_time.time.return_value = now - 60
            tracker.record("active_market", 0.58)
            mock_time.time.return_value = now
            tracker.record("active_market", 0.60)

            # Evict — stale_market not in active set and last seen 20 min ago
            tracker.evict_stale({"active_market"})

        assert tracker.tracked_count == 1
        assert tracker.momentum("stale_market") is None
        assert tracker.momentum("active_market") is not None

    def test_evict_keeps_active(self):
        tracker = PriceTracker()
        tracker.record("m1", 0.50)
        tracker.record("m2", 0.60)
        # Both in active set — nothing evicted
        tracker.evict_stale({"m1", "m2"})
        assert tracker.tracked_count == 2

    def test_evict_keeps_recent_not_in_active(self):
        """Markets not in active_ids but seen recently should be kept."""
        tracker = PriceTracker()
        tracker.record("recent", 0.50)
        # Not in active set but just recorded (< 15 min ago)
        tracker.evict_stale(set())
        assert tracker.tracked_count == 1


class TestMaxTracked:
    def test_max_tracked_cap(self):
        tracker = PriceTracker(max_tracked=10)
        for i in range(15):
            tracker.record(f"market_{i}", 0.50 + i * 0.01)
        assert tracker.tracked_count == 10

    def test_existing_markets_still_record(self):
        """Markets already tracked should still accept new prices at cap."""
        tracker = PriceTracker(max_tracked=3)
        tracker.record("m1", 0.50)
        tracker.record("m2", 0.60)
        tracker.record("m3", 0.70)
        # At cap — new market rejected
        tracker.record("m4", 0.80)
        assert tracker.tracked_count == 3
        # Existing market still records
        tracker.record("m1", 0.55)
        mom = tracker.momentum("m1", window_minutes=60)
        assert mom == pytest.approx((0.55 - 0.50) / 0.50)


class TestRecordBatch:
    def test_record_batch(self):
        tracker = PriceTracker()
        tracker.record_batch({"m1": 0.50, "m2": 0.60, "m3": 0.70})
        assert tracker.tracked_count == 3
        tracker.record_batch({"m1": 0.55, "m2": 0.65, "m3": 0.75})
        for mid, expected in [("m1", 0.05 / 0.50), ("m2", 0.05 / 0.60), ("m3", 0.05 / 0.70)]:
            mom = tracker.momentum(mid, window_minutes=60)
            assert mom == pytest.approx(expected)
