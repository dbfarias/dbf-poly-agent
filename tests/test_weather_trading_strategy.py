"""Tests for WeatherTradingStrategy (slug-based + bucket matching)."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from bot.agent.strategies.weather_trading import (
    WeatherTradingStrategy,
    _build_weather_slug,
    _hours_until_resolution,
    parse_temp_range,
)
from bot.data.market_cache import MarketCache
from bot.polymarket.types import GammaMarket
from bot.research.weather_fetcher import TemperaturePeriod


# ── Helpers ──────────────────────────────────────────────────────────────────


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


def _period(
    city: str = "nyc",
    date: str = "2026-03-15",
    temp_f: float = 80.0,
    confidence: float = 0.90,
    period: str = "day",
):
    return TemperaturePeriod(
        city=city, date=date, period=period,
        temp_f=temp_f, temp_low_f=temp_f - 3, temp_high_f=temp_f + 3,
        confidence=confidence,
    )


def _make_event(
    markets: list[dict] | None = None,
    end_date: str | None = None,
) -> dict:
    """Build a fake Gamma API event response."""
    if end_date is None:
        end_date = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    if markets is None:
        markets = [
            {
                "id": "bucket_low",
                "conditionId": "bucket_low",
                "question": "Will the highest temperature be 40°F or below?",
                "outcomePrices": json.dumps([0.10, 0.90]),
                "clobTokenIds": json.dumps(["tok_low_yes", "tok_low_no"]),
            },
            {
                "id": "bucket_41_45",
                "conditionId": "bucket_41_45",
                "question": "Will the highest temperature be between 41-45°F?",
                "outcomePrices": json.dumps([0.08, 0.92]),
                "clobTokenIds": json.dumps(["tok_41_yes", "tok_41_no"]),
            },
            {
                "id": "bucket_46_50",
                "conditionId": "bucket_46_50",
                "question": "Will the highest temperature be between 46-50°F?",
                "outcomePrices": json.dumps([0.12, 0.88]),
                "clobTokenIds": json.dumps(["tok_46_yes", "tok_46_no"]),
            },
            {
                "id": "bucket_high",
                "conditionId": "bucket_high",
                "question": "Will the highest temperature be 51°F or higher?",
                "outcomePrices": json.dumps([0.05, 0.95]),
                "clobTokenIds": json.dumps(["tok_high_yes", "tok_high_no"]),
            },
        ]
    return {"markets": markets, "endDate": end_date}


# ── parse_temp_range ─────────────────────────────────────────────────────────


class TestParseTempRange:
    def test_or_below(self):
        assert parse_temp_range("40°F or below") == (-999.0, 40.0)

    def test_or_lower(self):
        assert parse_temp_range("35°F or lower") == (-999.0, 35.0)

    def test_or_higher(self):
        assert parse_temp_range("48°F or higher") == (48.0, 999.0)

    def test_or_above(self):
        assert parse_temp_range("50°F or above") == (50.0, 999.0)

    def test_between_range(self):
        assert parse_temp_range("between 44-45°F") == (44.0, 45.0)

    def test_between_range_with_spaces(self):
        assert parse_temp_range("between 44 - 45 °F") == (44.0, 45.0)

    def test_full_question_or_below(self):
        q = "Will the highest temperature be 40°F or below on March 11?"
        assert parse_temp_range(q) == (-999.0, 40.0)

    def test_full_question_between(self):
        q = "Will the highest temperature in NYC be between 44-45°F on March 11?"
        assert parse_temp_range(q) == (44.0, 45.0)

    def test_full_question_or_higher(self):
        q = "Will the highest temperature in Chicago be 54°F or higher on March 11?"
        assert parse_temp_range(q) == (54.0, 999.0)

    def test_none_for_non_weather(self):
        assert parse_temp_range("Will BTC reach $100k?") is None

    def test_none_for_empty(self):
        assert parse_temp_range("") is None

    def test_none_for_none_input(self):
        assert parse_temp_range(None) is None


# ── _build_weather_slug ──────────────────────────────────────────────────────


class TestBuildWeatherSlug:
    def test_basic_slug(self):
        dt = datetime(2026, 3, 11, 12, 0, tzinfo=timezone.utc)
        assert _build_weather_slug("nyc", dt) == "highest-temperature-in-nyc-on-march-11-2026"

    def test_december_slug(self):
        dt = datetime(2026, 12, 25, 12, 0, tzinfo=timezone.utc)
        assert _build_weather_slug("chicago", dt) == "highest-temperature-in-chicago-on-december-25-2026"

    def test_single_digit_day(self):
        dt = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
        assert _build_weather_slug("miami", dt) == "highest-temperature-in-miami-on-january-5-2026"


# ── _hours_until_resolution ──────────────────────────────────────────────────


class TestHoursUntilResolution:
    def test_future_event(self):
        end = (datetime.now(timezone.utc) + timedelta(hours=10)).isoformat()
        event = {"endDate": end}
        hours = _hours_until_resolution(event)
        assert 9.0 < hours < 11.0

    def test_past_event(self):
        end = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        event = {"endDate": end}
        assert _hours_until_resolution(event) == 0.0

    def test_missing_date(self):
        assert _hours_until_resolution({}) == 999.0

    def test_z_suffix(self):
        end = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        event = {"endDate": end}
        hours = _hours_until_resolution(event)
        assert 5.0 < hours < 7.0


# ── Strategy fixture ─────────────────────────────────────────────────────────


@pytest.fixture()
def strategy():
    clob = AsyncMock()
    gamma = AsyncMock()
    gamma.get_event_by_slug = AsyncMock(return_value=None)
    cache = MarketCache(default_ttl=60)
    fetcher = AsyncMock()
    return WeatherTradingStrategy(clob, gamma, cache, weather_fetcher=fetcher)


# ── Slug-based scan ──────────────────────────────────────────────────────────


class TestSlugBasedScan:
    @pytest.mark.asyncio()
    async def test_no_fetcher(self):
        clob = AsyncMock()
        gamma = AsyncMock()
        cache = MarketCache(default_ttl=60)
        s = WeatherTradingStrategy(clob, gamma, cache)
        assert await s.scan([]) == []

    @pytest.mark.asyncio()
    async def test_slug_signal_cheap_bucket(self, strategy):
        """Forecast 48°F → match bucket 46-50°F, price $0.12 < threshold $0.15."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        strategy._weather_fetcher.get_forecast.return_value = [
            _period(city="new york", date=today, temp_f=48.0),
        ]
        strategy.gamma.get_event_by_slug.return_value = _make_event()

        signals = await strategy.scan([])
        assert len(signals) >= 1
        signal = signals[0]
        assert signal.outcome == "Yes"
        assert signal.market_id == "bucket_46_50"
        assert signal.edge > 0.03
        assert signal.metadata["source"] == "slug_lookup"

    @pytest.mark.asyncio()
    async def test_slug_no_signal_expensive_bucket(self, strategy):
        """Forecast 48°F → match bucket, but price is above threshold."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        strategy._weather_fetcher.get_forecast.return_value = [
            _period(city="new york", date=today, temp_f=48.0),
        ]
        event = _make_event(markets=[
            {
                "id": "m1",
                "conditionId": "m1",
                "question": "Will the highest temperature be between 46-50°F?",
                "outcomePrices": json.dumps([0.50, 0.50]),  # too expensive
                "clobTokenIds": json.dumps(["tok_yes", "tok_no"]),
            },
        ])
        strategy.gamma.get_event_by_slug.return_value = event

        signals = await strategy.scan([])
        assert len(signals) == 0

    @pytest.mark.asyncio()
    async def test_slug_no_event_found(self, strategy):
        """No event on Polymarket for this slug → no signal."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        strategy._weather_fetcher.get_forecast.return_value = [
            _period(city="new york", date=today, temp_f=48.0),
        ]
        strategy.gamma.get_event_by_slug.return_value = None

        signals = await strategy.scan([])
        assert len(signals) == 0

    @pytest.mark.asyncio()
    async def test_slug_too_close_to_resolution(self, strategy):
        """Event resolves in < 2 hours → skip."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        strategy._weather_fetcher.get_forecast.return_value = [
            _period(city="new york", date=today, temp_f=48.0),
        ]
        # Event resolving in 30 minutes
        soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        event = _make_event(end_date=soon)
        strategy.gamma.get_event_by_slug.return_value = event

        signals = await strategy.scan([])
        assert len(signals) == 0

    @pytest.mark.asyncio()
    async def test_slug_no_bucket_match(self, strategy):
        """Forecast 90°F but highest bucket is '51°F or higher' → should still match."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        strategy._weather_fetcher.get_forecast.return_value = [
            _period(city="new york", date=today, temp_f=90.0),
        ]
        strategy.gamma.get_event_by_slug.return_value = _make_event()

        signals = await strategy.scan([])
        # 90°F falls in "51°F or higher" bucket
        assert len(signals) >= 1
        assert signals[0].market_id == "bucket_high"

    @pytest.mark.asyncio()
    async def test_slug_or_below_bucket(self, strategy):
        """Forecast 35°F → match '40°F or below' bucket."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        strategy._weather_fetcher.get_forecast.return_value = [
            _period(city="new york", date=today, temp_f=35.0),
        ]
        strategy.gamma.get_event_by_slug.return_value = _make_event()

        signals = await strategy.scan([])
        assert len(signals) >= 1
        assert signals[0].market_id == "bucket_low"

    @pytest.mark.asyncio()
    async def test_slug_low_confidence_skipped(self, strategy):
        """Forecast confidence too low → no signal."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        strategy._weather_fetcher.get_forecast.return_value = [
            _period(city="new york", date=today, temp_f=48.0, confidence=0.30),
        ]
        strategy.gamma.get_event_by_slug.return_value = _make_event()

        signals = await strategy.scan([])
        assert len(signals) == 0

    @pytest.mark.asyncio()
    async def test_multiple_cities(self, strategy):
        """Scan generates signals across multiple cities."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        async def mock_forecast(city):
            return [_period(city=city, date=today, temp_f=48.0)]

        strategy._weather_fetcher.get_forecast.side_effect = mock_forecast
        strategy.gamma.get_event_by_slug.return_value = _make_event()

        signals = await strategy.scan([])
        # Multiple cities can produce signals
        assert len(signals) >= 1


# ── Fallback (legacy) scan ───────────────────────────────────────────────────


class TestLegacyFallbackScan:
    @pytest.mark.asyncio()
    async def test_legacy_bucket_match(self, strategy):
        """Fallback scan: weather market with bucket-parseable question."""
        strategy._weather_fetcher.get_forecast.return_value = [
            _period(city="chicago", temp_f=48.0),
        ]
        strategy.gamma.get_event_by_slug.return_value = None

        market = _make_market(
            "Will the highest temperature in Chicago be between 46-50°F on March 15?",
            yes_price=0.10,
        )
        signals = await strategy.scan([market])
        assert len(signals) >= 1
        assert signals[0].metadata["source"] == "legacy_scan"

    @pytest.mark.asyncio()
    async def test_legacy_non_weather_skipped(self, strategy):
        strategy.gamma.get_event_by_slug.return_value = None
        market = _make_market("Will BTC reach $100k?", yes_price=0.50)
        signals = await strategy.scan([market])
        assert len(signals) == 0

    @pytest.mark.asyncio()
    async def test_legacy_price_too_high(self, strategy):
        """Fallback: bucket matches but price above threshold."""
        strategy._weather_fetcher.get_forecast.return_value = [
            _period(city="chicago", temp_f=48.0),
        ]
        strategy.gamma.get_event_by_slug.return_value = None

        market = _make_market(
            "Will the highest temperature in Chicago be between 46-50°F on March 15?",
            yes_price=0.50,  # above ENTRY_THRESHOLD
        )
        signals = await strategy.scan([market])
        assert len(signals) == 0

    @pytest.mark.asyncio()
    async def test_legacy_forecast_outside_bucket(self, strategy):
        """Fallback: forecast doesn't fall in this bucket."""
        strategy._weather_fetcher.get_forecast.return_value = [
            _period(city="chicago", temp_f=60.0),  # outside 46-50
        ]
        strategy.gamma.get_event_by_slug.return_value = None

        market = _make_market(
            "Will the highest temperature in Chicago be between 46-50°F on March 15?",
            yes_price=0.10,
        )
        signals = await strategy.scan([market])
        assert len(signals) == 0


# ── Match bucket ─────────────────────────────────────────────────────────────


class TestMatchBucket:
    def test_match_correct_bucket(self, strategy):
        event = _make_event()
        signal = strategy._match_bucket(event, "nyc", "2026-03-15", 48.0, 0.90)
        assert signal is not None
        assert signal.market_id == "bucket_46_50"

    def test_match_or_below(self, strategy):
        event = _make_event()
        signal = strategy._match_bucket(event, "nyc", "2026-03-15", 35.0, 0.90)
        assert signal is not None
        assert signal.market_id == "bucket_low"

    def test_match_or_higher(self, strategy):
        event = _make_event()
        signal = strategy._match_bucket(event, "nyc", "2026-03-15", 55.0, 0.90)
        assert signal is not None
        assert signal.market_id == "bucket_high"

    def test_no_match_at_boundary(self, strategy):
        """41°F falls in bucket_41_45 (between 41-45°F)."""
        event = _make_event()
        signal = strategy._match_bucket(event, "nyc", "2026-03-15", 41.0, 0.90)
        assert signal is not None
        assert signal.market_id == "bucket_41_45"

    def test_signal_has_metadata(self, strategy):
        event = _make_event()
        signal = strategy._match_bucket(event, "nyc", "2026-03-15", 48.0, 0.90)
        assert signal is not None
        assert signal.metadata["city"] == "nyc"
        assert signal.metadata["forecast_temp"] == 48.0
        assert signal.metadata["bucket_low"] == 46.0
        assert signal.metadata["bucket_high"] == 50.0
        assert signal.metadata["source"] == "slug_lookup"

    def test_no_signal_expensive(self, strategy):
        """All buckets priced above ENTRY_THRESHOLD."""
        markets = [
            {
                "id": "m1",
                "conditionId": "m1",
                "question": "between 46-50°F",
                "outcomePrices": json.dumps([0.50, 0.50]),
                "clobTokenIds": json.dumps(["tok_yes", "tok_no"]),
            },
        ]
        event = _make_event(markets=markets)
        signal = strategy._match_bucket(event, "nyc", "2026-03-15", 48.0, 0.90)
        assert signal is None


# ── Extract city ─────────────────────────────────────────────────────────────


class TestExtractCity:
    def test_known_city(self):
        q = "Will the highest temperature in Chicago be 54°F or higher?"
        assert WeatherTradingStrategy._extract_city(q) == "chicago"

    def test_nyc(self):
        q = "Will the highest temperature in NYC be between 44-45°F?"
        assert WeatherTradingStrategy._extract_city(q) == "nyc"

    def test_multi_word_city(self):
        q = "Will the temperature in New York be above 60°F?"
        assert WeatherTradingStrategy._extract_city(q) == "new york"

    def test_no_city(self):
        assert WeatherTradingStrategy._extract_city("Will BTC go up?") is None


# ── Exit logic ───────────────────────────────────────────────────────────────


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
    async def test_exit_threshold(self, strategy):
        result = await strategy.should_exit(
            "m1", 0.70, avg_price=0.10,
        )
        assert result is not False
        assert "exit_threshold" in str(result)

    @pytest.mark.asyncio()
    async def test_max_age(self, strategy):
        created = datetime.now(timezone.utc) - timedelta(hours=50)
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
