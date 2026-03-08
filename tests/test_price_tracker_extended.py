"""Tests for PriceTracker extensions — volatility and price alerts."""

import math
import time

from bot.data.price_tracker import PriceTracker


class TestVolatility:
    """Test the volatility computation method."""

    def test_volatility_insufficient_data(self):
        tracker = PriceTracker()
        tracker.record("m1", 0.50)
        tracker.record("m1", 0.51)
        # Only 2 points — need at least 3
        assert tracker.volatility("m1") is None

    def test_volatility_unknown_market(self):
        tracker = PriceTracker()
        assert tracker.volatility("nonexistent") is None

    def test_volatility_constant_price(self):
        tracker = PriceTracker()
        for _ in range(5):
            tracker.record("m1", 0.50)
        vol = tracker.volatility("m1")
        assert vol is not None
        assert vol == 0.0

    def test_volatility_with_variation(self):
        tracker = PriceTracker()
        prices = [0.50, 0.52, 0.48, 0.53, 0.49]
        for p in prices:
            tracker.record("m1", p)

        vol = tracker.volatility("m1")
        assert vol is not None
        assert vol > 0.0

        # Manually compute expected volatility
        returns = [(prices[i] - prices[i - 1]) / prices[i - 1]
                    for i in range(1, len(prices))]
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        expected = math.sqrt(variance)
        assert abs(vol - expected) < 1e-10

    def test_volatility_respects_window(self):
        tracker = PriceTracker()

        # Manually inject old timestamps into the history deque
        from collections import deque
        old_time = time.time() - 7200  # 2 hours ago
        now = time.time()

        tracker._history["m1"] = deque(maxlen=360)
        # 3 old data points outside 60min window
        tracker._history["m1"].append((0.50, old_time))
        tracker._history["m1"].append((0.55, old_time + 1))
        tracker._history["m1"].append((0.60, old_time + 2))
        # 2 recent points inside window
        tracker._history["m1"].append((0.52, now - 10))
        tracker._history["m1"].append((0.53, now - 5))

        # Only 2 points in window — should return None
        vol = tracker.volatility("m1", window_minutes=60)
        assert vol is None


class TestPriceAlerts:
    """Test price alert set/check/remove."""

    def test_set_and_check_stop_loss(self):
        tracker = PriceTracker()
        tracker.set_alert("m1", stop_loss=0.40, take_profit=0.90)

        assert tracker.check_alerts("m1", 0.35) == "stop_loss"
        assert tracker.check_alerts("m1", 0.40) == "stop_loss"

    def test_set_and_check_take_profit(self):
        tracker = PriceTracker()
        tracker.set_alert("m1", stop_loss=0.40, take_profit=0.90)

        assert tracker.check_alerts("m1", 0.95) == "take_profit"
        assert tracker.check_alerts("m1", 0.90) == "take_profit"

    def test_no_alert_in_range(self):
        tracker = PriceTracker()
        tracker.set_alert("m1", stop_loss=0.40, take_profit=0.90)

        assert tracker.check_alerts("m1", 0.50) is None
        assert tracker.check_alerts("m1", 0.89) is None

    def test_no_alert_for_unset_market(self):
        tracker = PriceTracker()
        assert tracker.check_alerts("m1", 0.50) is None

    def test_remove_alert(self):
        tracker = PriceTracker()
        tracker.set_alert("m1", stop_loss=0.40, take_profit=0.90)
        tracker.remove_alert("m1")
        assert tracker.check_alerts("m1", 0.35) is None

    def test_remove_nonexistent_alert(self):
        tracker = PriceTracker()
        # Should not raise
        tracker.remove_alert("nonexistent")

    def test_overwrite_alert(self):
        tracker = PriceTracker()
        tracker.set_alert("m1", stop_loss=0.40, take_profit=0.90)
        tracker.set_alert("m1", stop_loss=0.30, take_profit=0.95)

        # Old thresholds should not trigger
        assert tracker.check_alerts("m1", 0.35) is None
        # New stop_loss triggers at 0.30
        assert tracker.check_alerts("m1", 0.25) == "stop_loss"
        # New take_profit at 0.95
        assert tracker.check_alerts("m1", 0.95) == "take_profit"

    def test_multiple_markets(self):
        tracker = PriceTracker()
        tracker.set_alert("m1", stop_loss=0.40, take_profit=0.90)
        tracker.set_alert("m2", stop_loss=0.20, take_profit=0.80)

        assert tracker.check_alerts("m1", 0.35) == "stop_loss"
        assert tracker.check_alerts("m2", 0.85) == "take_profit"
        assert tracker.check_alerts("m1", 0.50) is None

    def test_on_alert_callback_registered(self):
        tracker = PriceTracker()
        callbacks_called = []

        async def my_callback(market_id, alert_type, price):
            callbacks_called.append((market_id, alert_type, price))

        tracker.on_alert(my_callback)
        assert len(tracker._alert_callbacks) == 1


class TestExistingFunctionality:
    """Verify existing methods still work after changes."""

    def test_momentum(self):
        tracker = PriceTracker()
        tracker.record("m1", 1.0)
        tracker.record("m1", 1.05)
        mom = tracker.momentum("m1")
        assert mom is not None
        assert abs(mom - 0.05) < 1e-10

    def test_trend(self):
        tracker = PriceTracker()
        tracker.record("m1", 1.0)
        tracker.record("m1", 1.05)
        assert tracker.trend("m1") == "rising"

    def test_tracked_count(self):
        tracker = PriceTracker()
        tracker.record("m1", 1.0)
        tracker.record("m2", 2.0)
        assert tracker.tracked_count == 2
