"""CoinGecko free API fetcher — 30 calls/min, no key required."""

import time

import httpx
import structlog

logger = structlog.get_logger()


class CryptoFetcher:
    """Fetches crypto market data from CoinGecko free tier."""

    BASE_URL = "https://api.coingecko.com/api/v3"
    TIMEOUT = 10.0
    CACHE_TTL = 1800  # 30 minutes internal cache

    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._cached_sentiment: dict[str, float] | None = None
        self._cache_expires_at: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.TIMEOUT,
                follow_redirects=False,
            )
        return self._client

    async def get_market_sentiment(self) -> dict[str, float]:
        """Get crypto market sentiment indicators.

        Returns dict with:
        - btc_24h_change: BTC 24h price change %
        - eth_24h_change: ETH 24h price change %
        - market_trend: normalized trend score [-1, 1]
        """
        # Check internal cache
        if self._cached_sentiment and time.monotonic() < self._cache_expires_at:
            return self._cached_sentiment

        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.BASE_URL}/simple/price",
                params={
                    "ids": "bitcoin,ethereum",
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                },
            )

            if response.status_code == 429:
                logger.warning("coingecko_rate_limited")
                return self._cached_sentiment or self._neutral_result()

            response.raise_for_status()
            data = response.json()

            result = self._parse_response(data)
            self._cached_sentiment = result
            self._cache_expires_at = time.monotonic() + self.CACHE_TTL
            return result

        except httpx.HTTPError as e:
            logger.warning("crypto_fetch_failed", error=str(e))
            return self._cached_sentiment or self._neutral_result()
        except Exception as e:
            logger.warning("crypto_parse_failed", error=str(e))
            return self._cached_sentiment or self._neutral_result()

    def _parse_response(self, data: dict) -> dict[str, float]:
        """Parse CoinGecko response into sentiment dict."""
        btc = data.get("bitcoin", {})
        eth = data.get("ethereum", {})

        btc_change = btc.get("usd_24h_change", 0.0) or 0.0
        eth_change = eth.get("usd_24h_change", 0.0) or 0.0

        # Normalize to [-1, 1] — cap at ±20% daily change
        market_trend = max(-1.0, min(1.0, (btc_change + eth_change) / 2.0 / 20.0))

        return {
            "btc_24h_change": round(btc_change, 2),
            "eth_24h_change": round(eth_change, 2),
            "market_trend": round(market_trend, 4),
        }

    def _neutral_result(self) -> dict[str, float]:
        return {
            "btc_24h_change": 0.0,
            "eth_24h_change": 0.0,
            "market_trend": 0.0,
        }

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
