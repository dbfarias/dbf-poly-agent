"""Crypto Fear & Greed Index — free, no API key, strong sentiment signal.

Source: Alternative.me. Updated daily.
Values: 0-100 (0=Extreme Fear, 100=Extreme Greed)
Signal: Extreme fear (<20) = contrarian buy, Extreme greed (>80) = contrarian sell.
"""

import time

import httpx
import structlog

logger = structlog.get_logger()

_API_URL = "https://api.alternative.me/fng/"


class FearGreedFetcher:
    """Fetches crypto Fear & Greed Index from Alternative.me."""

    CACHE_TTL = 3600  # 1 hour (updates daily)

    def __init__(self) -> None:
        self._value: int | None = None
        self._classification: str = ""
        self._cache_expires: float = 0.0

    async def get_index(self) -> tuple[int, str]:
        """Returns (value 0-100, classification string) or (50, 'Neutral') on failure."""
        if self._value is not None and time.monotonic() < self._cache_expires:
            return self._value, self._classification

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(_API_URL, params={"limit": 1})
                response.raise_for_status()
                data = response.json()

                entry = data.get("data", [{}])[0]
                self._value = int(entry.get("value", 50))
                self._classification = entry.get("value_classification", "Neutral")
                self._cache_expires = time.monotonic() + self.CACHE_TTL

                logger.info(
                    "fear_greed_fetched",
                    value=self._value,
                    classification=self._classification,
                )
                return self._value, self._classification

        except Exception as e:
            logger.warning("fear_greed_fetch_failed", error=str(e))
            return self._value or 50, self._classification or "Neutral"

    def get_edge_multiplier(self) -> float:
        """Convert Fear & Greed into an edge multiplier for crypto markets.

        Extreme fear (<25): 1.15 (boost — contrarian buy signal)
        Fear (25-40): 1.05
        Neutral (40-60): 1.0
        Greed (60-75): 0.95
        Extreme greed (>75): 0.85 (penalize — overbought)
        """
        if self._value is None:
            return 1.0
        v = self._value
        if v < 25:
            return 1.15
        if v < 40:
            return 1.05
        if v <= 60:
            return 1.0
        if v <= 75:
            return 0.95
        return 0.85
