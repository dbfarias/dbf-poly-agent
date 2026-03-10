"""Tests for WeatherTradingStrategy."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from bot.agent.strategies.weather_trading import WeatherTradingStrategy
from bot.data.market_cache import MarketCache
from bot.polymarket.types import GammaMarket
from bot.research.weather_fetcher import TemperaturePeriod


def _make_market(
    question: str,
    yes_price: float = 0.20,
    no_price: float = 0.80,
    market_id: str = "m1",
    token_ids: list[str] | None = None,
) -> GammaMarket:
    if token_ids is None:
        token_ids = ["tok_yes", "tok_no"]
    return GammaMarket.model_validate({
        "id": market_id,
        "conditionId": market_id,
        "question": question,
        "slug": "weather-test",
        "endDateIso": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps([yes_price, no_price]),
        "volume": 5000.0,
        "liquidity": 1000.0,
        "active": True,
        "closed": False,
        "archived": False,
        "groupItemTitle": "Weather",
        "clobTokenIds": json.dumps(token_ids),
        "acceptingOrders": True,
        "negRisk": False,
    })


def _period(temp_f: float = 80.0, confidence: float = 0.90, period: str = "day"):
    return TemperaturePeriod(
        city="nyc", date="2026-03-15", period=period,
        temp_f=temp_f, temp_low_f=temp_f - 3, temp_high_f=temp_f + 3,
        confidence=confidence,
    )


class TestParseWeatherQuestion:
    def test_above_pattern(self):
        q = "Will the high temperature in NYC on March 15 be above 55°F?"
        result = WeatherTradingStrategy._parse_weather_question(q)
        assert result is not None
        assert result["threshold"] == 55.0
        assert result["direction"] == "above"

    def test_below_pattern(self):
        q = "Will the temperature in Chicago on March 20 be below 30°F?"
        result = WeatherTradingStrategy._parse_weather_question(q)
        assert result is not None
        assert result["threshold"] == 30.0
        assert result["direction"] == "below"

    def test_exceed_pattern(self):
        q = "Will the high temperature in Miami on March 12 exceed 85°F?"
        result = WeatherTradingStrategy._parse_weather_question(q)
        assert result is not None
        assert result["threshold"] == 85.0
        assert result["direction"] == "above"

    def test_no_match(self):
        q = "Will BTC go up in the next 5 minutes?"
        result = WeatherTradingStrategy._parse_weather_question(q)
        assert result is None


@pytest.fixture()
def strategy():
    clob = AsyncMock()
    gamma = AsyncMock()
    cache = MarketCache(default_ttl=60)
    fetcher = AsyncMock()
    return WeatherTradingStrategy(clob, gamma, cache, weather_fetcher=fetcher)


class TestWeatherTradingScan:
    @pytest.mark.asyncio()
    async def test_no_fetcher(self):
        clob = AsyncMock()
        gamma = AsyncMock()
        cache = MarketCache(default_ttl=60)
        s = WeatherTradingStrategy(clob, gamma, cache)
        assert await s.scan([]) == []

    @pytest.mark.asyncio()
    async def test_buy_yes_cheap(self, strategy):
        """Forecast 80°F vs threshold 55°F → YES when price cheap."""
        strategy._weather_fetcher.get_forecast.return_value = [_period(80.0)]
        market = _make_market(
            "Will the high temperature in NYC on March 15 be above 55°F?",
            yes_price=0.20,
        )
        signals = await strategy.scan([market])
        assert len(signals) == 1
        assert signals[0].outcome == "Yes"
        assert signals[0].edge > 0.03

    @pytest.mark.asyncio()
    async def test_buy_no_expensive(self, strategy):
        """Forecast 40°F vs threshold 55°F → NO when YES expensive."""
        strategy._weather_fetcher.get_forecast.return_value = [_period(40.0)]
        market = _make_market(
            "Will the high temperature in NYC on March 15 be above 55°F?",
            yes_price=0.85, no_price=0.15,
        )
        signals = await strategy.scan([market])
        assert len(signals) == 1
        assert signals[0].outcome == "No"

    @pytest.mark.asyncio()
    async def test_no_signal_small_margin(self, strategy):
        """Forecast 57°F vs threshold 55°F → margin too small."""
        strategy._weather_fetcher.get_forecast.return_value = [_period(57.0)]
        market = _make_market(
            "Will the high temperature in NYC on March 15 be above 55°F?",
            yes_price=0.20,
        )
        signals = await strategy.scan([market])
        assert len(signals) == 0

    @pytest.mark.asyncio()
    async def test_non_weather_skipped(self, strategy):
        market = _make_market("Will BTC reach $100k?", yes_price=0.50)
        signals = await strategy.scan([market])
        assert len(signals) == 0

    @pytest.mark.asyncio()
    async def test_price_dead_zone(self, strategy):
        """Forecast supports YES but price is too high for YES buy."""
        strategy._weather_fetcher.get_forecast.return_value = [_period(80.0)]
        market = _make_market(
            "Will the high temperature in NYC on March 15 be above 55°F?",
            yes_price=0.50,  # too high for YES buy, too low for NO buy
        )
        signals = await strategy.scan([market])
        assert len(signals) == 0


class TestWeatherTradingExit:
    @pytest.mark.asyncio()
    async def test_stop_loss(self, strategy):
        result = await strategy.should_exit("m1", 0.18, avg_price=0.25)
        assert result is not False
        assert "stop_loss" in str(result)

    @pytest.mark.asyncio()
    async def test_take_profit(self, strategy):
        created = datetime.now(timezone.utc) - timedelta(hours=3)
        result = await strategy.should_exit(
            "m1", 0.30, avg_price=0.20, created_at=created,
        )
        assert result is not False
        assert "take_profit" in str(result)

    @pytest.mark.asyncio()
    async def test_max_age(self, strategy):
        created = datetime.now(timezone.utc) - timedelta(hours=50)
        # Price slightly below entry so take-profit doesn't trigger first
        result = await strategy.should_exit(
            "m1", 0.20, avg_price=0.20, created_at=created,
        )
        assert result is not False
        assert "max_age" in str(result)

    @pytest.mark.asyncio()
    async def test_no_exit(self, strategy):
        created = datetime.now(timezone.utc) - timedelta(hours=1)
        result = await strategy.should_exit(
            "m1", 0.21, avg_price=0.20, created_at=created,
        )
        assert result is False
