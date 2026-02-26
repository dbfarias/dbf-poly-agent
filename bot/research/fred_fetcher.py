"""FRED (Federal Reserve Economic Data) — free API for economic indicators.

Provides CPI, unemployment, fed funds rate, treasury yields.
Directly relevant to Polymarket markets like "Will the Fed cut rates?"
or "Will CPI be above X%?"
"""

import os
import time

import httpx
import structlog

logger = structlog.get_logger()

_API_URL = "https://api.stlouisfed.org/fred/series/observations"
_API_KEY = os.environ.get("FRED_API_KEY", "")

# Key series IDs for Polymarket-relevant economic data
SERIES = {
    "fed_funds_rate": "FEDFUNDS",  # Federal Funds Effective Rate
    "cpi_yoy": "CPIAUCSL",  # Consumer Price Index (All Urban)
    "unemployment": "UNRATE",  # Unemployment Rate
    "treasury_10y": "DGS10",  # 10-Year Treasury Yield
    "gdp_growth": "A191RL1Q225SBEA",  # Real GDP Growth Rate
    "pce_inflation": "PCEPI",  # PCE Price Index (Fed's preferred)
}


class FredFetcher:
    """Fetch economic indicators from FRED API."""

    CACHE_TTL = 3600  # 1 hour (data changes daily at most)
    TIMEOUT = 15.0

    def __init__(self) -> None:
        self._cache: dict[str, float] = {}
        self._cache_expires: float = 0.0

    async def get_latest(self, series_name: str) -> float | None:
        """Get latest value for a named series.

        series_name: one of 'fed_funds_rate', 'cpi_yoy', 'unemployment', etc.
        Returns the latest value as float, or None on failure.
        """
        if not _API_KEY:
            return None

        series_id = SERIES.get(series_name)
        if series_id is None:
            return None

        # Check cache
        cached = self._cache.get(series_name)
        if cached is not None and time.monotonic() < self._cache_expires:
            return cached

        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
                response = await client.get(
                    _API_URL,
                    params={
                        "series_id": series_id,
                        "api_key": _API_KEY,
                        "file_type": "json",
                        "sort_order": "desc",
                        "limit": 1,
                    },
                )
                response.raise_for_status()
                data = response.json()

                observations = data.get("observations", [])
                if not observations:
                    return None

                value_str = observations[0].get("value", "")
                if value_str == "." or not value_str:
                    return None

                value = float(value_str)
                self._cache[series_name] = value
                self._cache_expires = time.monotonic() + self.CACHE_TTL

                logger.info(
                    "fred_data_fetched",
                    series=series_name,
                    value=value,
                )
                return value

        except Exception as e:
            logger.warning("fred_fetch_failed", series=series_name, error=str(e))
            return cached

    async def get_all(self) -> dict[str, float]:
        """Fetch all key economic indicators. Returns name → value dict."""
        result = {}
        for name in SERIES:
            value = await self.get_latest(name)
            if value is not None:
                result[name] = value
        return result

    def is_relevant_to_market(self, question: str) -> str | None:
        """Check if a market question is about economic data we track.

        Returns the relevant series name or None.
        """
        q = question.lower()

        if any(w in q for w in ["fed", "interest rate", "federal funds", "fomc"]):
            return "fed_funds_rate"
        if any(w in q for w in ["cpi", "inflation", "consumer price"]):
            return "cpi_yoy"
        if any(w in q for w in ["unemployment", "jobless", "jobs report"]):
            return "unemployment"
        if any(w in q for w in ["treasury", "10-year", "bond yield"]):
            return "treasury_10y"
        if any(w in q for w in ["gdp", "gross domestic"]):
            return "gdp_growth"

        return None
