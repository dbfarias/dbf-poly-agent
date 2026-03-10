"""NOAA Weather API fetcher — free, no API key, 85-90% accuracy."""

import time
from dataclasses import dataclass

import httpx
import structlog

logger = structlog.get_logger()

# Confidence by forecast period index (0-based, each index = ~12h)
_CONFIDENCE_BY_INDEX = {
    0: 0.90, 1: 0.90,  # Day 1
    2: 0.85, 3: 0.85,  # Day 2
    4: 0.75, 5: 0.75,  # Day 3
    6: 0.65, 7: 0.65,  # Day 4
}
_DEFAULT_CONFIDENCE = 0.60  # Day 5+

# Typical NOAA uncertainty range (±°F)
_UNCERTAINTY_F = 3.0


@dataclass(frozen=True)
class TemperaturePeriod:
    """Forecast temperature for a specific period."""

    city: str
    date: str  # ISO date string
    period: str  # "day" or "night"
    temp_f: float  # forecast temperature
    temp_low_f: float
    temp_high_f: float
    confidence: float  # 0-1 based on forecast horizon


class WeatherFetcher:
    """Fetches weather forecasts from the NOAA Weather API (free, no key)."""

    BASE_URL = "https://api.weather.gov"
    CACHE_TTL = 1800  # 30 min
    TIMEOUT = 15.0

    # City -> (lat, lon) for common Polymarket weather cities
    CITIES: dict[str, tuple[float, float]] = {
        "new york": (40.7128, -74.0060),
        "nyc": (40.7128, -74.0060),
        "los angeles": (34.0522, -118.2437),
        "la": (34.0522, -118.2437),
        "chicago": (41.8781, -87.6298),
        "miami": (25.7617, -80.1918),
        "houston": (29.7604, -95.3698),
        "phoenix": (33.4484, -112.0740),
        "philadelphia": (39.9526, -75.1652),
        "san antonio": (29.4241, -98.4936),
        "san diego": (32.7157, -117.1611),
        "dallas": (32.7767, -96.7970),
        "denver": (39.7392, -104.9903),
        "seattle": (47.6062, -122.3321),
        "boston": (42.3601, -71.0589),
        "atlanta": (33.7490, -84.3880),
        "san francisco": (37.7749, -122.4194),
        "minneapolis": (44.9778, -93.2650),
        "detroit": (42.3314, -83.0458),
        "washington": (38.9072, -77.0369),
        "dc": (38.9072, -77.0369),
        "washington dc": (38.9072, -77.0369),
    }

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._forecast_cache: dict[str, list[TemperaturePeriod]] = {}
        self._cache_expires: dict[str, float] = {}
        self._gridpoint_cache: dict[str, str] = {}  # permanent cache

        from bot.utils.circuit_breaker import CircuitBreaker

        self._breaker = CircuitBreaker(
            "noaa_weather", failure_threshold=3, recovery_seconds=300
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "(polybot, contact@polybot.dev)"},
            )
        return self._client

    async def get_forecast(
        self, city: str
    ) -> list[TemperaturePeriod] | None:
        """Get temperature forecast for a city.

        Returns list of TemperaturePeriod or None on failure.
        """
        city_key = city.strip().lower()

        coords = self.CITIES.get(city_key)
        if coords is None:
            logger.warning("weather_unknown_city", city=city)
            return None

        # Check cache
        cached = self._forecast_cache.get(city_key)
        expires = self._cache_expires.get(city_key, 0.0)
        if cached is not None and time.monotonic() < expires:
            return cached

        if not self._breaker.allow_request():
            return cached

        try:
            forecast_url = await self._fetch_gridpoint_url(
                coords[0], coords[1]
            )
            if forecast_url is None:
                return cached

            periods = await self._fetch_forecast(forecast_url)
            if periods is None:
                return cached

            result = self._parse_periods(city_key, periods)
            self._forecast_cache[city_key] = result
            self._cache_expires[city_key] = time.monotonic() + self.CACHE_TTL
            self._breaker.record_success()
            return result

        except httpx.HTTPError as e:
            self._breaker.record_failure()
            logger.warning("weather_fetch_failed", city=city, error=str(e))
            return cached
        except Exception as e:
            self._breaker.record_failure()
            logger.warning("weather_parse_failed", city=city, error=str(e))
            return cached

    async def _fetch_gridpoint_url(
        self, lat: float, lon: float
    ) -> str | None:
        """Get the forecast URL for a lat/lon from NOAA /points endpoint.

        Gridpoint URLs are cached permanently (they never change).
        """
        cache_key = f"{lat},{lon}"
        cached_url = self._gridpoint_cache.get(cache_key)
        if cached_url is not None:
            return cached_url

        client = await self._get_client()
        response = await client.get(f"{self.BASE_URL}/points/{lat},{lon}")

        if response.status_code == 429:
            logger.warning("noaa_rate_limited", endpoint="points")
            self._breaker.record_failure()
            return None

        response.raise_for_status()
        data = response.json()

        forecast_url = data.get("properties", {}).get("forecast")
        if not forecast_url:
            logger.warning("noaa_no_forecast_url", lat=lat, lon=lon)
            return None

        self._gridpoint_cache[cache_key] = forecast_url
        return forecast_url

    async def _fetch_forecast(self, forecast_url: str) -> list[dict] | None:
        """Fetch the forecast periods from a gridpoint forecast URL."""
        client = await self._get_client()
        response = await client.get(forecast_url)

        if response.status_code == 429:
            logger.warning("noaa_rate_limited", endpoint="forecast")
            self._breaker.record_failure()
            return None

        response.raise_for_status()
        data = response.json()

        periods = data.get("properties", {}).get("periods")
        if not periods:
            logger.warning("noaa_no_periods", url=forecast_url)
            return None

        return periods

    def _parse_periods(
        self, city: str, periods: list[dict]
    ) -> list[TemperaturePeriod]:
        """Parse NOAA forecast periods into TemperaturePeriod objects."""
        result: list[TemperaturePeriod] = []

        for i, p in enumerate(periods):
            temp = p.get("temperature")
            if temp is None:
                continue

            temp_f = float(temp)
            start_time = p.get("startTime", "")
            # Extract ISO date from startTime like "2024-03-10T18:00:00-05:00"
            date_str = start_time[:10] if len(start_time) >= 10 else ""
            is_day = p.get("isDaytime", True)

            confidence = _CONFIDENCE_BY_INDEX.get(i, _DEFAULT_CONFIDENCE)

            result.append(
                TemperaturePeriod(
                    city=city,
                    date=date_str,
                    period="day" if is_day else "night",
                    temp_f=temp_f,
                    temp_low_f=temp_f - _UNCERTAINTY_F,
                    temp_high_f=temp_f + _UNCERTAINTY_F,
                    confidence=confidence,
                )
            )

        return result

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
