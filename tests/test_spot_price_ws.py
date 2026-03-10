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
