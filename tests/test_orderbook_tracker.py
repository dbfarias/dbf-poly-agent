"""Tests for OrderbookTracker."""

from time import time

from bot.polymarket.orderbook_tracker import OrderbookTracker, PricePoint
from bot.polymarket.types import OrderBook, OrderBookEntry


def _make_book(bid: float, ask: float, asset_id: str = "token1") -> OrderBook:
    """Create an OrderBook with a single bid/ask level."""
    return OrderBook(
        asset_id=asset_id,
        bids=[OrderBookEntry(price=bid, size=10.0)],
        asks=[OrderBookEntry(price=ask, size=10.0)],
    )


def _make_empty_book(asset_id: str = "token1") -> OrderBook:
    """Create an OrderBook with no bids/asks (no mid_price)."""
    return OrderBook(asset_id=asset_id, bids=[], asks=[])


class TestUpdateAndGetBook:
    def test_update_and_get_book(self):
        tracker = OrderbookTracker()
        book = _make_book(0.40, 0.50)

        tracker.update("token1", book)

        result = tracker.get_book("token1")
        assert result is book

    def test_get_book_returns_none_for_unknown(self):
        tracker = OrderbookTracker()
        assert tracker.get_book("unknown") is None

    def test_update_replaces_previous_book(self):
        tracker = OrderbookTracker()
        book1 = _make_book(0.40, 0.50)
        book2 = _make_book(0.45, 0.55)

        tracker.update("token1", book1)
        tracker.update("token1", book2)

        assert tracker.get_book("token1") is book2


class TestGetMidPrice:
    def test_get_mid_price(self):
        tracker = OrderbookTracker()
        tracker.update("token1", _make_book(0.40, 0.50))

        assert tracker.get_mid_price("token1") == 0.45

    def test_get_mid_price_returns_none_for_unknown(self):
        tracker = OrderbookTracker()
        assert tracker.get_mid_price("unknown") is None


class TestGetSpread:
    def test_get_spread(self):
        tracker = OrderbookTracker()
        tracker.update("token1", _make_book(0.40, 0.50))

        spread = tracker.get_spread("token1")
        assert spread is not None
        assert abs(spread - 0.10) < 1e-9

    def test_get_spread_returns_none_for_unknown(self):
        tracker = OrderbookTracker()
        assert tracker.get_spread("unknown") is None


class TestBookAgeSeconds:
    def test_book_age_seconds(self):
        tracker = OrderbookTracker()
        tracker.update("token1", _make_book(0.40, 0.50))

        age = tracker.book_age_seconds("token1")
        assert age is not None
        assert age < 1.0  # Should be near-zero

    def test_book_age_seconds_returns_none_for_unknown(self):
        tracker = OrderbookTracker()
        assert tracker.book_age_seconds("unknown") is None


class TestMidPriceHistory:
    def test_mid_price_history_window(self):
        tracker = OrderbookTracker()
        now = time()

        # Inject price history directly to control timestamps
        tracker._price_history["token1"] = __import__("collections").deque(
            maxlen=OrderbookTracker.MAX_HISTORY_POINTS
        )
        tracker._price_history["token1"].extend([
            PricePoint(now - 120, 0.40),  # 2 min ago — outside 60s window
            PricePoint(now - 30, 0.45),   # 30s ago — inside window
            PricePoint(now - 10, 0.48),   # 10s ago — inside window
            PricePoint(now - 1, 0.50),    # 1s ago — inside window
        ])

        history = tracker.mid_price_history("token1", window_seconds=60)
        assert len(history) == 3
        assert history[0].mid_price == 0.45
        assert history[-1].mid_price == 0.50

    def test_mid_price_history_empty_for_unknown(self):
        tracker = OrderbookTracker()
        assert tracker.mid_price_history("unknown") == []


class TestDetectFlashCrash:
    def test_detect_flash_crash_triggered(self):
        tracker = OrderbookTracker()
        now = time()

        tracker._price_history["token1"] = __import__("collections").deque(
            maxlen=OrderbookTracker.MAX_HISTORY_POINTS
        )
        tracker._price_history["token1"].extend([
            PricePoint(now - 20, 0.80),  # peak
            PricePoint(now - 10, 0.60),  # declining
            PricePoint(now - 1, 0.50),   # 37.5% drop from peak
        ])

        triggered, magnitude = tracker.detect_flash_crash(
            "token1", drop_pct=0.30, window_seconds=30
        )
        assert triggered is True
        assert abs(magnitude - 0.375) < 1e-9

    def test_detect_flash_crash_not_triggered(self):
        tracker = OrderbookTracker()
        now = time()

        tracker._price_history["token1"] = __import__("collections").deque(
            maxlen=OrderbookTracker.MAX_HISTORY_POINTS
        )
        tracker._price_history["token1"].extend([
            PricePoint(now - 20, 0.80),
            PricePoint(now - 10, 0.78),
            PricePoint(now - 1, 0.75),  # only 6.25% drop
        ])

        triggered, magnitude = tracker.detect_flash_crash(
            "token1", drop_pct=0.30, window_seconds=30
        )
        assert triggered is False
        assert abs(magnitude - 0.0625) < 1e-9

    def test_detect_flash_crash_insufficient_data(self):
        tracker = OrderbookTracker()
        now = time()

        # Only one data point
        tracker._price_history["token1"] = __import__("collections").deque(
            maxlen=OrderbookTracker.MAX_HISTORY_POINTS
        )
        tracker._price_history["token1"].append(PricePoint(now - 1, 0.80))

        triggered, magnitude = tracker.detect_flash_crash("token1")
        assert triggered is False
        assert magnitude == 0.0

    def test_detect_flash_crash_no_data(self):
        tracker = OrderbookTracker()
        triggered, magnitude = tracker.detect_flash_crash("unknown")
        assert triggered is False
        assert magnitude == 0.0


class TestPruneOldData:
    def test_prune_old_data(self):
        tracker = OrderbookTracker()
        now = time()

        tracker._price_history["token1"] = __import__("collections").deque(
            maxlen=OrderbookTracker.MAX_HISTORY_POINTS
        )
        # Add points: some older than MAX_HISTORY_SECONDS, some recent
        tracker._price_history["token1"].extend([
            PricePoint(now - 700, 0.30),  # older than 600s — should be pruned
            PricePoint(now - 650, 0.35),  # older than 600s — should be pruned
            PricePoint(now - 500, 0.40),  # within window
            PricePoint(now - 100, 0.45),  # within window
        ])

        tracker._prune_old("token1", now)

        history = list(tracker._price_history["token1"])
        assert len(history) == 2
        assert history[0].mid_price == 0.40
        assert history[1].mid_price == 0.45

    def test_update_skips_history_for_empty_book(self):
        """Books with no mid_price should not add to price history."""
        tracker = OrderbookTracker()
        tracker.update("token1", _make_empty_book())

        assert "token1" not in tracker._price_history
        # But book snapshot is still stored
        assert tracker.get_book("token1") is not None
