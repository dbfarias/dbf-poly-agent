"""Tests for WhaleDetector — large order tracking from CLOB WebSocket."""

import time

import pytest

from bot.polymarket.types import OrderBook, OrderBookEntry
from bot.research.whale_detector import WhaleDetector, WhaleSignal


class TestWhaleDetector:
    """Test whale detection, eviction, and summaries."""

    def _make_book(
        self,
        asset_id: str = "token-abc",
        bids: list[tuple[float, float]] | None = None,
        asks: list[tuple[float, float]] | None = None,
    ) -> OrderBook:
        return OrderBook(
            asset_id=asset_id,
            bids=[OrderBookEntry(price=p, size=s) for p, s in (bids or [])],
            asks=[OrderBookEntry(price=p, size=s) for p, s in (asks or [])],
        )

    def test_no_whale_on_small_orders(self):
        detector = WhaleDetector(threshold_usd=500.0)
        book = self._make_book(bids=[(0.50, 100)], asks=[(0.55, 100)])
        # 0.50 * 100 = $50, below $500
        detector.record_book_update("token-abc", book)
        assert not detector.has_whale_activity_by_token("token-abc")
        assert detector.get_whale_summary("token-abc") is None

    def test_whale_detected_on_large_bid(self):
        detector = WhaleDetector(threshold_usd=500.0)
        book = self._make_book(bids=[(0.60, 1000)], asks=[(0.65, 10)])
        # 0.60 * 1000 = $600 >= $500
        detector.record_book_update("token-abc", book)
        assert detector.has_whale_activity_by_token("token-abc")

    def test_whale_detected_on_large_ask(self):
        detector = WhaleDetector(threshold_usd=500.0)
        book = self._make_book(bids=[(0.50, 10)], asks=[(0.55, 1000)])
        # 0.55 * 1000 = $550 >= $500
        detector.record_book_update("token-abc", book)
        assert detector.has_whale_activity_by_token("token-abc")

    def test_whale_summary_buy_only(self):
        detector = WhaleDetector(threshold_usd=500.0)
        book = self._make_book(bids=[(0.60, 1000)], asks=[(0.65, 10)])
        detector.record_book_update("token-abc", book)
        summary = detector.get_whale_summary("token-abc")
        assert summary is not None
        assert summary["count"] == 1
        assert summary["total_usd"] == 600.0
        assert summary["net_side"] == "BUY"

    def test_whale_summary_sell_only(self):
        detector = WhaleDetector(threshold_usd=500.0)
        book = self._make_book(bids=[(0.50, 10)], asks=[(0.55, 1000)])
        detector.record_book_update("token-abc", book)
        summary = detector.get_whale_summary("token-abc")
        assert summary is not None
        assert summary["net_side"] == "SELL"

    def test_whale_summary_mixed(self):
        detector = WhaleDetector(threshold_usd=500.0)
        book = self._make_book(
            bids=[(0.60, 1000)],
            asks=[(0.55, 1000)],
        )
        detector.record_book_update("token-abc", book)
        summary = detector.get_whale_summary("token-abc")
        assert summary is not None
        assert summary["count"] == 2
        assert summary["net_side"] == "MIXED"

    def test_custom_threshold(self):
        detector = WhaleDetector(threshold_usd=100.0)
        book = self._make_book(bids=[(0.50, 250)])
        # 0.50 * 250 = $125 >= $100
        detector.record_book_update("token-abc", book)
        assert detector.has_whale_activity_by_token("token-abc")

    def test_evict_stale_signals(self, monkeypatch):
        detector = WhaleDetector(threshold_usd=500.0)
        book = self._make_book(bids=[(0.60, 1000)])
        detector.record_book_update("token-abc", book)
        assert detector.has_whale_activity_by_token("token-abc")

        # Advance time past 1h TTL
        future = time.time() + 3700
        monkeypatch.setattr(time, "time", lambda: future)
        detector.evict_stale()

        assert not detector.has_whale_activity_by_token("token-abc")
        assert detector.tracked_assets == 0

    def test_stale_signals_not_counted(self, monkeypatch):
        detector = WhaleDetector(threshold_usd=500.0)
        book = self._make_book(bids=[(0.60, 1000)])
        detector.record_book_update("token-abc", book)

        # Advance time past TTL — has_whale_activity should return False
        future = time.time() + 3700
        monkeypatch.setattr(time, "time", lambda: future)
        assert not detector.has_whale_activity_by_token("token-abc")

    def test_multiple_assets_tracked(self):
        detector = WhaleDetector(threshold_usd=500.0)
        book1 = self._make_book(asset_id="token-a", bids=[(0.60, 1000)])
        book2 = self._make_book(asset_id="token-b", asks=[(0.55, 1000)])
        detector.record_book_update("token-a", book1)
        detector.record_book_update("token-b", book2)
        assert detector.tracked_assets == 2
        assert detector.has_whale_activity_by_token("token-a")
        assert detector.has_whale_activity_by_token("token-b")

    def test_has_whale_activity_market_id_returns_false(self):
        """has_whale_activity(market_id) always returns False (no mapping)."""
        detector = WhaleDetector(threshold_usd=500.0)
        book = self._make_book(bids=[(0.60, 1000)])
        detector.record_book_update("token-abc", book)
        assert not detector.has_whale_activity("some-market-id")

    def test_whale_signal_frozen(self):
        signal = WhaleSignal(side="BUY", size_usd=600.0, price=0.60, timestamp=1.0)
        with pytest.raises(AttributeError):
            signal.side = "SELL"  # type: ignore[misc]

    def test_evict_keeps_fresh_signals(self, monkeypatch):
        detector = WhaleDetector(threshold_usd=500.0)
        book = self._make_book(bids=[(0.60, 1000)])
        detector.record_book_update("token-abc", book)

        # Only 30 min later — should still be active
        future = time.time() + 1800
        monkeypatch.setattr(time, "time", lambda: future)
        detector.evict_stale()
        assert detector.tracked_assets == 1
