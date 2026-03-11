"""Weather fetcher — NOAA for US cities, Open-Meteo for worldwide coverage.

Key accuracy features (from gopfan2/AlterEgo research):
- Uses exact airport station coordinates (not city center) — Polymarket resolves
  on Wunderground which sources from NWS airport stations (KLGA, KORD, etc.)
- Combines real observations (past hours) with hourly forecast (upcoming hours)
  to always get the true daily max, even after the peak has passed
- Uses pre-mapped hourly gridpoint endpoints to avoid extra /points round-trip
- Station observations fix the "afternoon blind spot" where forecast-only
  misses the actual high by 3-8°F
"""

import time
from dataclasses import dataclass

import httpx
import structlog

logger = structlog.get_logger()

# Confidence by forecast horizon (hours from now)
_CONFIDENCE_BY_HOURS = [
    (24, 0.90),   # Day 1
    (48, 0.85),   # Day 2
    (72, 0.75),   # Day 3
    (96, 0.65),   # Day 4
]
_DEFAULT_CONFIDENCE = 0.60  # Day 5+

# Confidence by NOAA period index (legacy compat)
_CONFIDENCE_BY_INDEX = {
    0: 0.90, 1: 0.90,
    2: 0.85, 3: 0.85,
    4: 0.75, 5: 0.75,
    6: 0.65, 7: 0.65,
}

# Typical forecast uncertainty range (±°F)
_UNCERTAINTY_F = 3.0


def _c_to_f(celsius: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return celsius * 9.0 / 5.0 + 32.0


@dataclass(frozen=True)
class TemperaturePeriod:
    """Forecast temperature for a specific period."""

    city: str
    date: str  # ISO date string
    period: str  # "day" or "night"
    temp_f: float  # forecast temperature in Fahrenheit
    temp_low_f: float
    temp_high_f: float
    confidence: float  # 0-1 based on forecast horizon


# ---------------------------------------------------------------------------
# US cities: use EXACT AIRPORT coordinates where Polymarket resolves.
#
# Polymarket weather markets resolve via Wunderground, which sources from
# NWS airport weather stations. Using city-center coordinates introduces
# 3-8°F error — fatal when buckets are 1-2°F wide.
# ---------------------------------------------------------------------------
_US_CITIES: dict[str, tuple[float, float]] = {
    # Primary Polymarket cities (airport coordinates)
    "new york": (40.7772, -73.8726),     # KLGA LaGuardia
    "nyc": (40.7772, -73.8726),          # KLGA LaGuardia
    "chicago": (41.9742, -87.9073),      # KORD O'Hare
    "miami": (25.7959, -80.2870),        # KMIA Miami Intl
    "dallas": (32.8471, -96.8518),       # KDAL Love Field
    "seattle": (47.4502, -122.3088),     # KSEA Sea-Tac
    "atlanta": (33.6407, -84.4277),      # KATL Hartsfield
    # Secondary cities (nearest major airport)
    "los angeles": (33.9425, -118.4081), # KLAX
    "la": (33.9425, -118.4081),          # KLAX
    "houston": (29.9844, -95.3414),      # KIAH George Bush
    "phoenix": (33.4373, -112.0078),     # KPHX Sky Harbor
    "philadelphia": (39.8721, -75.2411), # KPHL
    "san antonio": (29.5337, -98.4698),  # KSAT
    "san diego": (32.7336, -117.1897),   # KSAN
    "denver": (39.8561, -104.6737),      # KDEN
    "boston": (42.3656, -71.0096),        # KBOS Logan
    "san francisco": (37.6213, -122.3790), # KSFO
    "minneapolis": (44.8848, -93.2223),  # KMSP
    "detroit": (42.2124, -83.3534),      # KDTW
    "washington": (38.8512, -77.0402),   # KDCA Reagan
    "dc": (38.8512, -77.0402),           # KDCA Reagan
    "washington dc": (38.8512, -77.0402), # KDCA Reagan
}

# NWS station IDs — for fetching real observations (what already happened today)
_STATION_IDS: dict[str, str] = {
    "new york": "KLGA", "nyc": "KLGA",
    "chicago": "KORD",
    "miami": "KMIA",
    "dallas": "KDAL",
    "seattle": "KSEA",
    "atlanta": "KATL",
    "los angeles": "KLAX", "la": "KLAX",
    "houston": "KIAH",
    "phoenix": "KPHX",
    "philadelphia": "KPHL",
    "san antonio": "KSAT",
    "san diego": "KSAN",
    "denver": "KDEN",
    "boston": "KBOS",
    "san francisco": "KSFO",
    "minneapolis": "KMSP",
    "detroit": "KDTW",
    "washington": "KDCA", "dc": "KDCA", "washington dc": "KDCA",
}

# Pre-mapped NWS hourly forecast endpoints — avoids extra /points round-trip.
# Format: https://api.weather.gov/gridpoints/{WFO}/{gridX},{gridY}/forecast/hourly
_NWS_HOURLY_ENDPOINTS: dict[str, str] = {
    "new york": "https://api.weather.gov/gridpoints/OKX/37,39/forecast/hourly",
    "nyc": "https://api.weather.gov/gridpoints/OKX/37,39/forecast/hourly",
    "chicago": "https://api.weather.gov/gridpoints/LOT/66,77/forecast/hourly",
    "miami": "https://api.weather.gov/gridpoints/MFL/106,51/forecast/hourly",
    "dallas": "https://api.weather.gov/gridpoints/FWD/87,107/forecast/hourly",
    "seattle": "https://api.weather.gov/gridpoints/SEW/124,61/forecast/hourly",
    "atlanta": "https://api.weather.gov/gridpoints/FFC/50,82/forecast/hourly",
}

# International cities for Open-Meteo (free, worldwide, no API key)
_INTL_CITIES: dict[str, tuple[float, float]] = {
    "london": (51.5074, -0.1278),
    "paris": (48.8566, 2.3522),
    "berlin": (52.5200, 13.4050),
    "munich": (48.1351, 11.5820),
    "madrid": (40.4168, -3.7038),
    "rome": (41.9028, 12.4964),
    "amsterdam": (52.3676, 4.9041),
    "ankara": (39.9334, 32.8597),
    "istanbul": (41.0082, 28.9784),
    "tokyo": (35.6762, 139.6503),
    "seoul": (37.5665, 126.9780),
    "beijing": (39.9042, 116.4074),
    "shanghai": (31.2304, 121.4737),
    "mumbai": (19.0760, 72.8777),
    "lucknow": (26.8467, 80.9462),
    "delhi": (28.7041, 77.1025),
    "sydney": (-33.8688, 151.2093),
    "melbourne": (-37.8136, 144.9631),
    "wellington": (-41.2865, 174.7762),
    "auckland": (-36.8485, 174.7633),
    "toronto": (43.6532, -79.3832),
    "montreal": (45.5017, -73.5673),
    "vancouver": (49.2827, -123.1207),
    "mexico city": (19.4326, -99.1332),
    "buenos aires": (-34.6037, -58.3816),
    "sao paulo": (-23.5505, -46.6333),
    "são paulo": (-23.5505, -46.6333),
    "rio de janeiro": (-22.9068, -43.1729),
    "santiago": (-33.4489, -70.6693),
    "bogota": (4.7110, -74.0721),
    "lima": (-12.0464, -77.0428),
    "cairo": (30.0444, 31.2357),
    "johannesburg": (-26.2041, 28.0473),
    "nairobi": (-1.2921, 36.8219),
    "tel aviv": (32.0853, 34.7818),
    "dubai": (25.2048, 55.2708),
    "singapore": (1.3521, 103.8198),
    "bangkok": (13.7563, 100.5018),
    "jakarta": (-6.2088, 106.8456),
    "moscow": (55.7558, 37.6173),
    "stockholm": (59.3293, 18.0686),
    "oslo": (59.9139, 10.7522),
    "copenhagen": (55.6761, 12.5683),
    "helsinki": (60.1699, 24.9384),
    "lisbon": (38.7223, -9.1393),
    "athens": (37.9838, 23.7275),
    "zurich": (47.3769, 8.5417),
    "vienna": (48.2082, 16.3738),
    "prague": (50.0755, 14.4378),
    "warsaw": (52.2297, 21.0122),
    "taipei": (25.0330, 121.5654),
    "manila": (14.5995, 120.9842),
    "kuala lumpur": (3.1390, 101.6869),
    "lagos": (6.5244, 3.3792),
}


class WeatherFetcher:
    """Fetches forecasts from NOAA (US) and Open-Meteo (worldwide).

    For US cities, combines station observations (what already happened today)
    with hourly forecasts (what's coming) to produce accurate daily max temps.
    """

    BASE_URL = "https://api.weather.gov"
    OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
    CACHE_TTL = 1800  # 30 min
    TIMEOUT = 15.0

    # Combined city dict — all supported cities
    CITIES: dict[str, tuple[float, float]] = {**_US_CITIES, **_INTL_CITIES}

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

        Routes to NOAA for US cities, Open-Meteo for international.
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

        # Route: US cities → NOAA, international → Open-Meteo
        is_us = city_key in _US_CITIES
        try:
            if is_us:
                result = await self._fetch_noaa(city_key, coords)
            else:
                result = await self._fetch_open_meteo(city_key, coords)

            if result is None:
                return cached

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

    async def _fetch_noaa(
        self, city_key: str, coords: tuple[float, float],
    ) -> list[TemperaturePeriod] | None:
        """Fetch forecast from NOAA — combines observations + hourly forecast.

        Two-phase approach (from AlterEgo/gopfan2 research):
        1. Station observations: actual temps from past hours today
        2. Hourly forecast: predicted temps for upcoming hours
        Combined → accurate daily max regardless of time-of-day.
        """
        client = await self._get_client()

        # Phase 1: Real observations from airport station
        daily_max: dict[str, float] = {}
        station_id = _STATION_IDS.get(city_key)
        if station_id:
            daily_max = await self._fetch_station_observations(
                client, station_id,
            )

        # Phase 2: Hourly forecast (use pre-mapped endpoint if available)
        hourly_url = _NWS_HOURLY_ENDPOINTS.get(city_key)
        if hourly_url:
            await self._fetch_hourly_forecast(client, hourly_url, daily_max)
        else:
            # Fallback: use /points → forecast URL (extra round-trip)
            forecast_url = await self._fetch_gridpoint_url(
                coords[0], coords[1],
            )
            if forecast_url:
                # Convert half-day forecast URL to hourly
                hourly_url = forecast_url + "/hourly"
                if not hourly_url.endswith("/hourly"):
                    hourly_url = forecast_url.replace(
                        "/forecast", "/forecast/hourly",
                    )
                await self._fetch_hourly_forecast(
                    client, hourly_url, daily_max,
                )

        if not daily_max:
            # Complete fallback: use the half-day forecast endpoint
            forecast_url = await self._fetch_gridpoint_url(
                coords[0], coords[1],
            )
            if forecast_url is None:
                return None
            periods = await self._fetch_forecast(forecast_url)
            if periods is None:
                return None
            return self._parse_periods(city_key, periods)

        # Convert daily_max dict → TemperaturePeriod list
        return self._daily_max_to_periods(city_key, daily_max)

    async def _fetch_station_observations(
        self, client: httpx.AsyncClient, station_id: str,
    ) -> dict[str, float]:
        """Fetch real temperature observations from an NWS airport station.

        Returns dict mapping ISO date → max observed temperature (°F).
        This captures the actual high even if it occurred hours ago.
        """
        daily_max: dict[str, float] = {}
        try:
            url = (
                f"{self.BASE_URL}/stations/{station_id}"
                f"/observations?limit=48"
            )
            response = await client.get(url)
            if response.status_code == 429:
                logger.warning("noaa_rate_limited", endpoint="observations")
                return daily_max

            response.raise_for_status()
            data = response.json()

            for obs in data.get("features", []):
                props = obs.get("properties", {})
                time_str = props.get("timestamp", "")[:10]
                if not time_str:
                    continue
                temp_val = props.get("temperature", {})
                if isinstance(temp_val, dict):
                    temp_c = temp_val.get("value")
                else:
                    continue
                if temp_c is None:
                    continue
                temp_f = round(_c_to_f(temp_c))
                if time_str not in daily_max or temp_f > daily_max[time_str]:
                    daily_max[time_str] = temp_f

            logger.info(
                "station_observations_fetched",
                station=station_id,
                days=len(daily_max),
            )
        except Exception as e:
            logger.debug("station_observations_error", station=station_id, error=str(e))

        return daily_max

    async def _fetch_hourly_forecast(
        self,
        client: httpx.AsyncClient,
        hourly_url: str,
        daily_max: dict[str, float],
    ) -> None:
        """Fetch hourly forecast and merge into daily_max dict (in-place).

        Each hourly period's temperature is compared against the running
        daily max — keeping the highest value from observations + forecast.
        """
        try:
            response = await client.get(hourly_url)
            if response.status_code == 429:
                logger.warning("noaa_rate_limited", endpoint="hourly")
                return

            response.raise_for_status()
            data = response.json()

            for period in data.get("properties", {}).get("periods", []):
                date = period.get("startTime", "")[:10]
                if not date:
                    continue
                temp = period.get("temperature")
                if temp is None:
                    continue
                temp_f = float(temp)
                if period.get("temperatureUnit") == "C":
                    temp_f = round(_c_to_f(temp_f))
                if date not in daily_max or temp_f > daily_max[date]:
                    daily_max[date] = temp_f

            logger.info(
                "hourly_forecast_fetched",
                url=hourly_url[:60],
                days=len(daily_max),
            )
        except Exception as e:
            logger.debug("hourly_forecast_error", error=str(e))

    def _daily_max_to_periods(
        self, city_key: str, daily_max: dict[str, float],
    ) -> list[TemperaturePeriod]:
        """Convert daily_max dict to TemperaturePeriod list with confidence."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        result: list[TemperaturePeriod] = []

        for date_str in sorted(daily_max):
            temp_f = daily_max[date_str]

            # Compute confidence from forecast horizon
            try:
                forecast_date = datetime.strptime(date_str, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc,
                )
                hours_ahead = (forecast_date - now).total_seconds() / 3600
            except ValueError:
                hours_ahead = 999

            confidence = _DEFAULT_CONFIDENCE
            for hours_limit, conf in _CONFIDENCE_BY_HOURS:
                if hours_ahead <= hours_limit:
                    confidence = conf
                    break

            result.append(
                TemperaturePeriod(
                    city=city_key,
                    date=date_str,
                    period="day",
                    temp_f=temp_f,
                    temp_low_f=temp_f - _UNCERTAINTY_F,
                    temp_high_f=temp_f + _UNCERTAINTY_F,
                    confidence=confidence,
                )
            )

        return result

    async def _fetch_open_meteo(
        self, city_key: str, coords: tuple[float, float],
    ) -> list[TemperaturePeriod] | None:
        """Fetch forecast from Open-Meteo API (worldwide, free, no key)."""
        client = await self._get_client()
        response = await client.get(
            self.OPEN_METEO_URL,
            params={
                "latitude": coords[0],
                "longitude": coords[1],
                "daily": "temperature_2m_max,temperature_2m_min",
                "timezone": "auto",
                "forecast_days": 7,
            },
        )
        if response.status_code == 429:
            logger.warning("open_meteo_rate_limited")
            self._breaker.record_failure()
            return None

        response.raise_for_status()
        data = response.json()

        daily = data.get("daily")
        if not daily:
            logger.warning("open_meteo_no_daily", city=city_key)
            return None

        dates = daily.get("time", [])
        highs_c = daily.get("temperature_2m_max", [])
        lows_c = daily.get("temperature_2m_min", [])

        result: list[TemperaturePeriod] = []
        for i, date_str in enumerate(dates):
            if i >= len(highs_c) or i >= len(lows_c):
                break
            high_c = highs_c[i]
            low_c = lows_c[i]
            if high_c is None or low_c is None:
                continue

            high_f = _c_to_f(high_c)
            low_f = _c_to_f(low_c)

            # Confidence by forecast day
            confidence = _DEFAULT_CONFIDENCE
            for hours_limit, conf in _CONFIDENCE_BY_HOURS:
                if (i + 1) * 24 <= hours_limit:
                    confidence = conf
                    break

            # Daytime period (high)
            result.append(
                TemperaturePeriod(
                    city=city_key,
                    date=date_str,
                    period="day",
                    temp_f=high_f,
                    temp_low_f=high_f - _UNCERTAINTY_F,
                    temp_high_f=high_f + _UNCERTAINTY_F,
                    confidence=confidence,
                )
            )
            # Nighttime period (low)
            result.append(
                TemperaturePeriod(
                    city=city_key,
                    date=date_str,
                    period="night",
                    temp_f=low_f,
                    temp_low_f=low_f - _UNCERTAINTY_F,
                    temp_high_f=low_f + _UNCERTAINTY_F,
                    confidence=confidence,
                )
            )

        logger.info(
            "open_meteo_forecast_fetched",
            city=city_key,
            periods=len(result),
        )
        return result

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
