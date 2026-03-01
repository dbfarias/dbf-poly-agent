"""CoinGecko free API fetcher — 30 calls/min, no key required."""

import time

import httpx
import structlog

logger = structlog.get_logger()


class CryptoFetcher:
    """Fetches crypto market data from CoinGecko free tier."""

    BASE_URL = "https://api.coingecko.com/api/v3"
    TIMEOUT = 10.0
    CACHE_TTL = 1800  # 30 minutes internal cache (sentiment)
    PRICE_CACHE_TTL = 300  # 5 minutes for prices (faster refresh)

    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._cached_sentiment: dict[str, float] | None = None
        self._cache_expires_at: float = 0.0
        self._cached_prices: dict[str, float] | None = None
        self._prices_expires_at: float = 0.0

        from bot.utils.circuit_breaker import CircuitBreaker

        self._breaker = CircuitBreaker("coingecko", failure_threshold=3, recovery_seconds=300)

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

        # Circuit breaker — return cached data when CoinGecko is down
        if not self._breaker.allow_request():
            return self._cached_sentiment or self._neutral_result()

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
                self._breaker.record_failure()
                return self._cached_sentiment or self._neutral_result()

            response.raise_for_status()
            data = response.json()

            result = self._parse_response(data)
            self._cached_sentiment = result
            self._cache_expires_at = time.monotonic() + self.CACHE_TTL
            self._breaker.record_success()
            return result

        except httpx.HTTPError as e:
            self._breaker.record_failure()
            logger.warning("crypto_fetch_failed", error=str(e))
            return self._cached_sentiment or self._neutral_result()
        except Exception as e:
            self._breaker.record_failure()
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

    async def get_prices(self) -> dict[str, float]:
        """Get actual USD prices for BTC and ETH.

        Returns dict like {"bitcoin": 102000.0, "ethereum": 3400.0}.
        Uses the same CoinGecko /simple/price endpoint, with its own cache.
        """
        if self._cached_prices and time.monotonic() < self._prices_expires_at:
            return self._cached_prices

        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.BASE_URL}/simple/price",
                params={
                    "ids": "bitcoin,ethereum",
                    "vs_currencies": "usd",
                },
            )

            if response.status_code == 429:
                logger.warning("coingecko_prices_rate_limited")
                return self._cached_prices or {}

            response.raise_for_status()
            data = response.json()

            result = {
                coin: info.get("usd", 0.0)
                for coin, info in data.items()
                if isinstance(info, dict)
            }
            self._cached_prices = result
            self._prices_expires_at = time.monotonic() + self.PRICE_CACHE_TTL
            return result

        except httpx.HTTPError as e:
            logger.warning("crypto_prices_fetch_failed", error=str(e))
            return self._cached_prices or {}
        except Exception as e:
            logger.warning("crypto_prices_parse_failed", error=str(e))
            return self._cached_prices or {}

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
