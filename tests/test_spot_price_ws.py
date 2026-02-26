"""Tests for SpotPriceWS — real-time crypto spot price tracker."""

import time

import pytest

from bot.research.spot_price_ws import SpotPriceWS


@pytest.fixture
def ws():
    return SpotPriceWS()


def test_get_price_returns_none_when_no_data(ws):
    assert ws.get_price("BTC-USD") is None
    assert ws.get_price("UNKNOWN") is None


def test_handle_ticker_updates_prices(ws):
    ws._handle_ticker({
        "type": "ticker",
        "product_id": "BTC-USD",
        "price": "67432.50",
    })
    assert ws.get_price("BTC-USD") == 67432.50


def test_get_prices_returns_all_tracked(ws):
    ws._handle_ticker({
        "type": "ticker",
        "product_id": "BTC-USD",
        "price": "67000.00",
    })
    ws._handle_ticker({
        "type": "ticker",
        "product_id": "ETH-USD",
        "price": "3500.00",
    })
    prices = ws.get_prices()
    assert prices == {"BTC-USD": 67000.0, "ETH-USD": 3500.0}
    # Returned dict is a copy
    prices["SOL-USD"] = 999.0
    assert "SOL-USD" not in ws.get_prices()


def test_get_momentum_with_enough_history(ws):
    base_time = time.monotonic()

    # Simulate price history: oldest at base_time, newest 60s later
    ws._history["BTC-USD"] = [
        (base_time, 100.0),
        (base_time + 30, 105.0),
        (base_time + 60, 110.0),
    ]
    ws._prices["BTC-USD"] = 110.0

    momentum = ws.get_momentum("BTC-USD", window_seconds=120)
    assert momentum is not None
    # (110 - 100) / 100 = 0.10
    assert abs(momentum - 0.10) < 1e-9


def test_get_momentum_insufficient_history(ws):
    assert ws.get_momentum("BTC-USD") is None

    # Single entry is not enough
    ws._history["BTC-USD"] = [(time.monotonic(), 100.0)]
    ws._prices["BTC-USD"] = 100.0
    assert ws.get_momentum("BTC-USD") is None


def test_history_eviction(ws):
    now = time.monotonic()
    # Insert entries: some very old (beyond 2x ROLLING_WINDOW), some recent
    old_cutoff = now - ws.ROLLING_WINDOW * 2 - 10
    ws._history["ETH-USD"] = [
        (old_cutoff - 100, 3000.0),
        (old_cutoff - 50, 3010.0),
        (now - 10, 3050.0),
    ]
    ws._prices["ETH-USD"] = 3050.0

    # Trigger eviction via _handle_ticker
    ws._handle_ticker({
        "type": "ticker",
        "product_id": "ETH-USD",
        "price": "3060.00",
    })

    # Old entries should be evicted, only recent ones remain
    history = ws._history["ETH-USD"]
    for ts, _ in history:
        assert ts >= now - ws.ROLLING_WINDOW * 2


def test_handle_ticker_ignores_non_ticker_messages(ws):
    ws._handle_ticker({"type": "subscriptions", "channels": ["ticker"]})
    ws._handle_ticker({"type": "heartbeat", "product_id": "BTC-USD"})
    ws._handle_ticker({})
    assert ws.get_prices() == {}


def test_handle_ticker_handles_invalid_price(ws):
    ws._handle_ticker({
        "type": "ticker",
        "product_id": "BTC-USD",
        "price": "not-a-number",
    })
    assert ws.get_price("BTC-USD") is None

    ws._handle_ticker({
        "type": "ticker",
        "product_id": "BTC-USD",
        "price": None,
    })
    assert ws.get_price("BTC-USD") is None

    # Missing price key
    ws._handle_ticker({
        "type": "ticker",
        "product_id": "ETH-USD",
    })
    assert ws.get_price("ETH-USD") is None


# --- Tests for new technical indicator methods ---


def test_get_price_history_empty(ws):
    """No data returns empty list."""
    assert ws.get_price_history("BTC-USD") == []


def test_get_price_history_returns_prices(ws):
    """Returns prices from history (without timestamps)."""
    now = time.monotonic()
    ws._history["BTC-USD"] = [
        (now - 3, 100.0),
        (now - 2, 101.0),
        (now - 1, 102.0),
    ]
    prices = ws.get_price_history("BTC-USD")
    assert prices == [100.0, 101.0, 102.0]


def test_get_price_history_respects_window(ws):
    """Window limits the number of returned prices."""
    now = time.monotonic()
    ws._history["BTC-USD"] = [
        (now - i, float(100 + i)) for i in range(50, 0, -1)
    ]
    prices = ws.get_price_history("BTC-USD", window=10)
    assert len(prices) == 10


def test_get_rsi_no_data(ws):
    """No history returns None."""
    assert ws.get_rsi("BTC-USD") is None


def test_get_rsi_with_data(ws):
    """RSI computed from enough price history."""
    now = time.monotonic()
    # 20 prices trending up
    ws._history["BTC-USD"] = [
        (now - 20 + i, 100.0 + i * 0.5) for i in range(20)
    ]
    rsi = ws.get_rsi("BTC-USD")
    assert rsi is not None
    assert 0 <= rsi <= 100
    assert rsi > 50  # Uptrend should have RSI > 50


def test_get_technical_summary_empty(ws):
    """No data returns dict with None values."""
    summary = ws.get_technical_summary("BTC-USD")
    assert summary["symbol"] == "BTC-USD"
    assert summary["price"] is None
    assert summary["rsi"] is None
    assert summary["macd"] is None


def test_get_technical_summary_with_data(ws):
    """Summary returns populated dict with enough history."""
    now = time.monotonic()
    # 50 prices trending up
    ws._history["BTC-USD"] = [
        (now - 50 + i, 100.0 + i * 0.3) for i in range(50)
    ]
    ws._prices["BTC-USD"] = 100.0 + 49 * 0.3

    summary = ws.get_technical_summary("BTC-USD")
    assert summary["symbol"] == "BTC-USD"
    assert summary["price"] is not None
    assert summary["rsi"] is not None
    assert isinstance(summary["rsi"], float)
    assert summary["macd"] is not None
