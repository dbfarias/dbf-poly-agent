"""Tests for NOAA Weather API fetcher."""

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from bot.research.weather_fetcher import TemperaturePeriod, WeatherFetcher

# Sample NOAA /points response
SAMPLE_POINTS_RESPONSE = {
    "properties": {
        "forecast": "https://api.weather.gov/gridpoints/OKX/33,37/forecast"
    }
}

# Sample NOAA forecast response
SAMPLE_FORECAST_RESPONSE = {
    "properties": {
        "periods": [
            {
                "name": "Today",
                "startTime": "2026-03-10T06:00:00-05:00",
                "isDaytime": True,
                "temperature": 55,
                "temperatureUnit": "F",
            },
            {
                "name": "Tonight",
                "startTime": "2026-03-10T18:00:00-05:00",
                "isDaytime": False,
                "temperature": 42,
                "temperatureUnit": "F",
            },
            {
                "name": "Wednesday",
                "startTime": "2026-03-11T06:00:00-05:00",
                "isDaytime": True,
                "temperature": 60,
                "temperatureUnit": "F",
            },
            {
                "name": "Wednesday Night",
                "startTime": "2026-03-11T18:00:00-05:00",
                "isDaytime": False,
                "temperature": 45,
                "temperatureUnit": "F",
            },
            {
                "name": "Thursday",
                "startTime": "2026-03-12T06:00:00-05:00",
                "isDaytime": True,
                "temperature": 58,
                "temperatureUnit": "F",
            },
        ]
    }
}


def _make_response(json_data: dict, url: str = "https://api.weather.gov/test") -> httpx.Response:
    return httpx.Response(
        status_code=200,
        json=json_data,
        request=httpx.Request("GET", url),
    )


class TestWeatherFetcherCityLookup:
    @pytest.fixture
    def fetcher(self):
        return WeatherFetcher()

    def test_known_city(self, fetcher):
        assert "new york" in fetcher.CITIES
        assert fetcher.CITIES["new york"] == (40.7128, -74.0060)

    def test_alias_city(self, fetcher):
        assert fetcher.CITIES["nyc"] == fetcher.CITIES["new york"]
        assert fetcher.CITIES["dc"] == fetcher.CITIES["washington"]
        assert fetcher.CITIES["la"] == fetcher.CITIES["los angeles"]

    @pytest.mark.asyncio
    async def test_unknown_city_returns_none(self, fetcher):
        result = await fetcher.get_forecast("atlantis")
        assert result is None

    @pytest.mark.asyncio
    async def test_case_insensitive_lookup(self, fetcher):
        """City lookup is case-insensitive via lowering."""
        # Patch to avoid real HTTP calls
        with patch.object(fetcher, "_fetch_gridpoint_url", new_callable=AsyncMock) as mock_gp:
            mock_gp.return_value = None
            result = await fetcher.get_forecast("New York")
            mock_gp.assert_called_once_with(40.7128, -74.0060)


class TestWeatherFetcherParsing:
    @pytest.fixture
    def fetcher(self):
        return WeatherFetcher()

    @pytest.mark.asyncio
    async def test_parses_forecast_response(self, fetcher):
        points_resp = _make_response(SAMPLE_POINTS_RESPONSE)
        forecast_resp = _make_response(SAMPLE_FORECAST_RESPONSE)

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_get = AsyncMock(side_effect=[points_resp, forecast_resp])
            mock_client.return_value.get = mock_get
            result = await fetcher.get_forecast("new york")

        assert result is not None
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_period_fields(self, fetcher):
        points_resp = _make_response(SAMPLE_POINTS_RESPONSE)
        forecast_resp = _make_response(SAMPLE_FORECAST_RESPONSE)

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_get = AsyncMock(side_effect=[points_resp, forecast_resp])
            mock_client.return_value.get = mock_get
            result = await fetcher.get_forecast("new york")

        first = result[0]
        assert first.city == "new york"
        assert first.date == "2026-03-10"
        assert first.period == "day"
        assert first.temp_f == 55.0
        assert first.temp_low_f == 52.0  # 55 - 3
        assert first.temp_high_f == 58.0  # 55 + 3

    @pytest.mark.asyncio
    async def test_night_period(self, fetcher):
        points_resp = _make_response(SAMPLE_POINTS_RESPONSE)
        forecast_resp = _make_response(SAMPLE_FORECAST_RESPONSE)

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_get = AsyncMock(side_effect=[points_resp, forecast_resp])
            mock_client.return_value.get = mock_get
            result = await fetcher.get_forecast("new york")

        second = result[1]
        assert second.period == "night"
        assert second.temp_f == 42.0

    def test_parse_periods_directly(self, fetcher):
        periods = SAMPLE_FORECAST_RESPONSE["properties"]["periods"]
        result = fetcher._parse_periods("chicago", periods)

        assert len(result) == 5
        assert all(p.city == "chicago" for p in result)


class TestWeatherFetcherConfidence:
    @pytest.fixture
    def fetcher(self):
        return WeatherFetcher()

    def test_confidence_decreases_with_horizon(self, fetcher):
        periods = SAMPLE_FORECAST_RESPONSE["properties"]["periods"]
        result = fetcher._parse_periods("test", periods)

        # Index 0,1 (day 1) = 0.90
        assert result[0].confidence == 0.90
        assert result[1].confidence == 0.90
        # Index 2,3 (day 2) = 0.85
        assert result[2].confidence == 0.85
        assert result[3].confidence == 0.85
        # Index 4 (day 3) = 0.75
        assert result[4].confidence == 0.75

    def test_far_future_confidence(self, fetcher):
        """Periods beyond index 7 get default 0.60 confidence."""
        periods = [
            {
                "name": f"Day {i}",
                "startTime": f"2026-03-{15 + i}T06:00:00-05:00",
                "isDaytime": True,
                "temperature": 50 + i,
                "temperatureUnit": "F",
            }
            for i in range(10)
        ]
        result = fetcher._parse_periods("test", periods)
        # Index 8 and 9 should get 0.60
        assert result[8].confidence == 0.60
        assert result[9].confidence == 0.60


class TestWeatherFetcherCache:
    @pytest.fixture
    def fetcher(self):
        return WeatherFetcher()

    @pytest.mark.asyncio
    async def test_caches_forecast(self, fetcher):
        points_resp = _make_response(SAMPLE_POINTS_RESPONSE)
        forecast_resp = _make_response(SAMPLE_FORECAST_RESPONSE)

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_get = AsyncMock(side_effect=[points_resp, forecast_resp])
            mock_client.return_value.get = mock_get

            result1 = await fetcher.get_forecast("new york")
            result2 = await fetcher.get_forecast("new york")

        # Only 2 HTTP calls (points + forecast), not 4
        assert mock_get.call_count == 2
        assert result1 == result2

    @pytest.mark.asyncio
    async def test_gridpoint_url_cached_permanently(self, fetcher):
        points_resp = _make_response(SAMPLE_POINTS_RESPONSE)
        forecast_resp = _make_response(SAMPLE_FORECAST_RESPONSE)

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_get = AsyncMock(side_effect=[points_resp, forecast_resp])
            mock_client.return_value.get = mock_get
            await fetcher.get_forecast("new york")

        # Gridpoint URL should be cached
        assert "40.7128,-74.006" in fetcher._gridpoint_cache

    @pytest.mark.asyncio
    async def test_expired_cache_refetches(self, fetcher):
        points_resp = _make_response(SAMPLE_POINTS_RESPONSE)
        forecast_resp = _make_response(SAMPLE_FORECAST_RESPONSE)

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_get = AsyncMock(side_effect=[
                points_resp, forecast_resp,
                forecast_resp,  # only forecast re-fetched (gridpoint cached)
            ])
            mock_client.return_value.get = mock_get

            await fetcher.get_forecast("new york")

            # Force cache expiry
            fetcher._cache_expires["new york"] = time.monotonic() - 1

            await fetcher.get_forecast("new york")

        # 3 calls: points + forecast + forecast (re-fetch)
        assert mock_get.call_count == 3


class TestWeatherFetcherCircuitBreaker:
    @pytest.fixture
    def fetcher(self):
        return WeatherFetcher()

    @pytest.mark.asyncio
    async def test_circuit_breaker_trips_after_failures(self, fetcher):
        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            # Trip the breaker (threshold=3)
            for _ in range(3):
                await fetcher.get_forecast("new york")

        assert fetcher._breaker.is_open

    @pytest.mark.asyncio
    async def test_returns_cached_when_breaker_open(self, fetcher):
        # Pre-populate cache
        cached = [
            TemperaturePeriod(
                city="new york", date="2026-03-10", period="day",
                temp_f=55.0, temp_low_f=52.0, temp_high_f=58.0,
                confidence=0.90,
            )
        ]
        fetcher._forecast_cache["new york"] = cached
        fetcher._cache_expires["new york"] = 0.0  # expired

        # Trip breaker
        fetcher._breaker._failures = 3
        fetcher._breaker._state = "open"
        fetcher._breaker._last_failure_time = time.monotonic()

        result = await fetcher.get_forecast("new york")
        assert result == cached


class TestWeatherFetcherErrors:
    @pytest.fixture
    def fetcher(self):
        return WeatherFetcher()

    @pytest.mark.asyncio
    async def test_handles_network_error(self, fetcher):
        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            result = await fetcher.get_forecast("new york")

        # No cache, returns None-like (cached is None initially)
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_rate_limit_on_points(self, fetcher):
        rate_limit_resp = AsyncMock()
        rate_limit_resp.status_code = 429

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(return_value=rate_limit_resp)
            result = await fetcher.get_forecast("new york")

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_rate_limit_on_forecast(self, fetcher):
        points_resp = _make_response(SAMPLE_POINTS_RESPONSE)
        rate_limit_resp = AsyncMock()
        rate_limit_resp.status_code = 429

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(
                side_effect=[points_resp, rate_limit_resp]
            )
            result = await fetcher.get_forecast("new york")

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_missing_forecast_url(self, fetcher):
        bad_points = _make_response({"properties": {}})

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(return_value=bad_points)
            result = await fetcher.get_forecast("new york")

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_empty_periods(self, fetcher):
        points_resp = _make_response(SAMPLE_POINTS_RESPONSE)
        empty_forecast = _make_response({"properties": {"periods": []}})

        with patch.object(fetcher, "_get_client") as mock_client:
            mock_client.return_value.get = AsyncMock(
                side_effect=[points_resp, empty_forecast]
            )
            result = await fetcher.get_forecast("new york")

        assert result is None

    @pytest.mark.asyncio
    async def test_skips_periods_without_temperature(self, fetcher):
        periods = [
            {
                "name": "Today",
                "startTime": "2026-03-10T06:00:00-05:00",
                "isDaytime": True,
                "temperature": 55,
                "temperatureUnit": "F",
            },
            {
                "name": "Bad Period",
                "startTime": "2026-03-10T18:00:00-05:00",
                "isDaytime": False,
                # no temperature field
                "temperatureUnit": "F",
            },
        ]
        result = fetcher._parse_periods("test", periods)
        assert len(result) == 1


class TestWeatherFetcherClose:
    @pytest.mark.asyncio
    async def test_close_client(self):
        fetcher = WeatherFetcher()
        # Create a client
        client = await fetcher._get_client()
        assert not client.is_closed

        await fetcher.close()
        assert client.is_closed

    @pytest.mark.asyncio
    async def test_close_without_client(self):
        fetcher = WeatherFetcher()
        # Should not raise
        await fetcher.close()


class TestTemperaturePeriodFrozen:
    def test_immutable(self):
        tp = TemperaturePeriod(
            city="new york", date="2026-03-10", period="day",
            temp_f=55.0, temp_low_f=52.0, temp_high_f=58.0,
            confidence=0.90,
        )
        with pytest.raises(AttributeError):
            tp.temp_f = 60.0  # type: ignore[misc]
