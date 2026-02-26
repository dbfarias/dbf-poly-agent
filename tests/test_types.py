"""Tests for Polymarket types."""

from bot.polymarket.types import GammaMarket, OrderBook, OrderBookEntry


def test_gamma_market_prices():
    m = GammaMarket(
        id="1",
        question="Test?",
        outcomes=["Yes", "No"],
        outcomePrices="[0.85,0.15]",
    )
    assert m.yes_price == 0.85
    assert m.no_price == 0.15


def test_gamma_market_token_ids():
    m = GammaMarket(
        id="1",
        clobTokenIds='["token1","token2"]',
    )
    assert m.token_ids == ["token1", "token2"]


def test_gamma_market_end_date():
    m = GammaMarket(id="1", endDateIso="2025-03-01T00:00:00Z")
    assert m.end_date is not None
    assert m.end_date.year == 2025


def test_order_book_properties():
    book = OrderBook(
        bids=[OrderBookEntry(price=0.85, size=100), OrderBookEntry(price=0.84, size=50)],
        asks=[OrderBookEntry(price=0.87, size=80), OrderBookEntry(price=0.88, size=60)],
    )
    assert book.best_bid == 0.85
    assert book.best_ask == 0.87
    assert abs(book.spread - 0.02) < 1e-10
    assert abs(book.mid_price - 0.86) < 1e-10


def test_order_book_empty():
    book = OrderBook()
    assert book.best_bid is None
    assert book.best_ask is None
    assert book.spread is None
