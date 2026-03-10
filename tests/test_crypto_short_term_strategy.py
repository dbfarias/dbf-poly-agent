"""Tests for CryptoShortTermStrategy."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from bot.agent.strategies.crypto_short_term import CryptoShortTermStrategy
from bot.data.market_cache import MarketCache
from bot.polymarket.types import GammaMarket, OrderBook, OrderBookEntry


def _make_crypto_market(
    question: str = "Will BTC go up in the next 5 minutes?",
    slug: str = "btc-5min-up",
    yes_price: float = 0.50,
    minutes_to_resolve: float = 4.0,
    market_id: str = "cm1",
) -> GammaMarket:
    end_date = datetime.now(timezone.utc) + timedelta(minutes=minutes_to_resolve)
    return GammaMarket.model_validate({
        "id": market_id,
        "conditionId": market_id,
        "question": question,
        "slug": slug,
        "endDateIso": end_date.isoformat(),
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps([yes_price, 1.0 - yes_price]),
        "volume": 10000.0,
        "liquidity": 5000.0,
        "active": True,
        "closed": False,
        "archived": False,
        "groupItemTitle": "",
        "clobTokenIds": json.dumps(["tok_yes", "tok_no"]),
        "acceptingOrders": True,
        "negRisk": False,
    })


class MockSpotWS:
    def __init__(self, prices=None, momentum=None):
        self._prices = prices or {}
        self._momentum = momentum or {}

    def get_price(self, symbol):
        return self._prices.get(symbol)

    def get_momentum(self, symbol, window_seconds=300):
        return self._momentum.get(symbol)


class TestExtractSymbol:
    def test_bitcoin(self):
        assert CryptoShortTermStrategy._extract_symbol("Will Bitcoin go up?") == "BTC-USD"

    def test_btc(self):
        assert CryptoShortTermStrategy._extract_symbol("Will BTC price increase?") == "BTC-USD"

    def test_eth(self):
        assert CryptoShortTermStrategy._extract_symbol("Will ETH go up in 5 min?") == "ETH-USD"

    def test_sol(self):
        assert CryptoShortTermStrategy._extract_symbol("Will SOL price drop?") == "SOL-USD"

    def test_no_crypto(self):
        assert CryptoShortTermStrategy._extract_symbol("Will it rain?") is None


class TestIsCryptoShortTerm:
    @pytest.fixture()
    def _strat(self):
        clob = AsyncMock()
        gamma = AsyncMock()
        cache = MarketCache(default_ttl=60)
        return CryptoShortTermStrategy(clob, gamma, cache)

    def test_btc_5min(self, _strat):
        m = _make_crypto_market("Will BTC go up in the next 5 minutes?")
        assert _strat._is_crypto_short_term(m) is True

    def test_eth_15min(self, _strat):
        m = _make_crypto_market("Will ethereum price increase in 15 min?")
        assert _strat._is_crypto_short_term(m) is True

    def test_slug_match(self, _strat):
        m = _make_crypto_market("Will BTC go up?", slug="btc-5min-up")
        assert _strat._is_crypto_short_term(m) is True

    def test_non_crypto(self, _strat):
        m = _make_crypto_market("Will it rain in NYC?", slug="weather")
        assert _strat._is_crypto_short_term(m) is False

    def test_expired(self, _strat):
        m = _make_crypto_market(minutes_to_resolve=-1.0)
        assert _strat._is_crypto_short_term(m) is False

    def test_bitcoin_up_or_down_format(self, _strat):
        """Polymarket's actual format for 5-min crypto markets."""
        m = _make_crypto_market(
            "Bitcoin Up or Down - March 10, 4:30PM-4:35PM ET",
            slug="bitcoin-up-or-down-march-10-430pm-435pm-et",
        )
        assert _strat._is_crypto_short_term(m) is True

    def test_ethereum_up_or_down_format(self, _strat):
        m = _make_crypto_market(
            "Ethereum Up or Down - March 10, 12:05PM-12:10PM ET",
            slug="ethereum-up-or-down",
        )
        assert _strat._is_crypto_short_term(m) is True

    def test_solana_up_or_down_format(self, _strat):
        m = _make_crypto_market(
            "Solana Up or Down - March 11, 9:00AM-9:05AM ET",
            slug="solana-up-or-down",
        )
        assert _strat._is_crypto_short_term(m) is True


@pytest.fixture()
def strategy():
    clob = AsyncMock()
    gamma = AsyncMock()
    cache = MarketCache(default_ttl=60)
    book = OrderBook(
        asset_id="tok_yes",
        bids=[OrderBookEntry(price=0.50, size=200.0)],
        asks=[OrderBookEntry(price=0.52, size=100.0)],
    )
    cache.set_order_book("tok_yes", book, ttl=60)
    spot_ws = MockSpotWS(
        prices={"BTC-USD": 95000.0},
        momentum={"BTC-USD": 0.03},
    )
    return CryptoShortTermStrategy(clob, gamma, cache, spot_ws=spot_ws)


class TestCryptoShortTermScan:
    @pytest.mark.asyncio()
    async def test_no_spot_ws(self):
        clob = AsyncMock()
        gamma = AsyncMock()
        cache = MarketCache(default_ttl=60)
        s = CryptoShortTermStrategy(clob, gamma, cache)
        assert await s.scan([]) == []

    @pytest.mark.asyncio()
    async def test_btc_5min_signal(self, strategy):
        market = _make_crypto_market()
        signals = await strategy.scan([market])
        assert len(signals) == 1
        assert signals[0].strategy == "crypto_short_term"
        assert signals[0].outcome == "Yes"  # positive momentum + bid-heavy

    @pytest.mark.asyncio()
    async def test_no_momentum_data(self):
        clob = AsyncMock()
        gamma = AsyncMock()
        cache = MarketCache(default_ttl=60)
        spot_ws = MockSpotWS()
        s = CryptoShortTermStrategy(clob, gamma, cache, spot_ws=spot_ws)
        market = _make_crypto_market()
        signals = await s.scan([market])
        assert len(signals) == 0

    @pytest.mark.asyncio()
    async def test_non_crypto_skipped(self, strategy):
        market = _make_crypto_market("Will it rain in NYC?", slug="weather")
        signals = await strategy.scan([market])
        assert len(signals) == 0


class TestCryptoShortTermExit:
    @pytest.mark.asyncio()
    async def test_stop_loss(self, strategy):
        result = await strategy.should_exit("cm1", 0.45, avg_price=0.50)
        assert result == "stop_loss"

    @pytest.mark.asyncio()
    async def test_take_profit(self, strategy):
        result = await strategy.should_exit("cm1", 0.55, avg_price=0.50)
        assert result == "take_profit"

    @pytest.mark.asyncio()
    async def test_time_expiry(self, strategy):
        created = datetime.now(timezone.utc) - timedelta(minutes=25)
        result = await strategy.should_exit(
            "cm1", 0.505, avg_price=0.50, created_at=created,
        )
        assert result == "time_expiry"

    @pytest.mark.asyncio()
    async def test_no_exit(self, strategy):
        created = datetime.now(timezone.utc) - timedelta(minutes=2)
        result = await strategy.should_exit(
            "cm1", 0.505, avg_price=0.50, created_at=created,
        )
        assert result is False
