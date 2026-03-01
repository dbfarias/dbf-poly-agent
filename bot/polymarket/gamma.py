"""Market discovery client using Polymarket Gamma API + CLOB fallback.

Primary source: Gamma API (gamma-api.polymarket.com) — provides volume,
liquidity, spread data and supports server-side end_date filtering.
Fallback: CLOB /sampling-markets endpoint for when Gamma API is unavailable.
"""

import json
from datetime import datetime, timedelta, timezone

import httpx
import structlog

from bot.polymarket.types import GammaMarket
from bot.utils.retry import async_retry

logger = structlog.get_logger()

CLOB_API_URL = "https://clob.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"


_GENERIC_TAGS = frozenset({
    "Politics", "Elections", "Primaries", "primary elections",
    "US Election", "Midterms", "Global Elections",
})


def _best_category(tags: list[str]) -> str:
    """Pick the most descriptive tag, skipping generic ones like 'Politics'.

    Returns empty string when only generic tags exist — prevents
    artificially labeling markets as 'Politics' when no specific
    sub-category is available.
    """
    for tag in tags:
        if tag not in _GENERIC_TAGS:
            return tag
    return ""


def _transform_clob_market(raw: dict) -> dict:
    """Transform CLOB API market format to GammaMarket-compatible format."""
    tokens = raw.get("tokens", [])
    outcomes = [t.get("outcome", "") for t in tokens]
    prices = [t.get("price", 0) for t in tokens]
    token_ids = [t.get("token_id", "") for t in tokens]
    tags = raw.get("tags", [])

    return {
        "id": raw.get("condition_id", ""),
        "conditionId": raw.get("condition_id", ""),
        "question": raw.get("question", ""),
        "slug": raw.get("market_slug", ""),
        "endDateIso": raw.get("end_date_iso", ""),
        "gameStartTime": raw.get("game_start_time"),
        "description": raw.get("description", ""),
        "outcomes": json.dumps(outcomes),
        "outcomePrices": json.dumps(prices),
        "volume": 0.0,
        "liquidity": 0.0,
        "active": raw.get("active", True),
        "closed": raw.get("closed", False),
        "archived": raw.get("archived", False),
        "groupItemTitle": _best_category(tags),
        "clobTokenIds": json.dumps(token_ids),
        "acceptingOrders": raw.get("accepting_orders", True),
        "negRisk": raw.get("neg_risk", False),
    }


def _transform_gamma_api_market(raw: dict) -> dict:
    """Transform Gamma API response to GammaMarket-compatible dict.

    Gamma API has richer data than CLOB: volume, liquidity, spread,
    bestBid/bestAsk, and negRisk fields.
    """
    # Use endDate (full ISO) if available, fall back to endDateIso (date-only)
    end_date = raw.get("endDate", "") or raw.get("endDateIso", "")

    return {
        "id": raw.get("conditionId", ""),
        "conditionId": raw.get("conditionId", ""),
        "question": raw.get("question", ""),
        "slug": raw.get("slug", ""),
        "endDateIso": end_date,
        "gameStartTime": raw.get("game_start_time"),
        "description": raw.get("description", ""),
        "outcomes": raw.get("outcomes", "[]"),
        "outcomePrices": raw.get("outcomePrices", "[]"),
        "volume": float(raw.get("volume", 0) or 0),
        "liquidity": float(raw.get("liquidity", 0) or 0),
        "active": raw.get("active", True),
        "closed": raw.get("closed", False),
        "archived": raw.get("archived", False),
        "groupItemTitle": raw.get("groupItemTitle", ""),
        "clobTokenIds": raw.get("clobTokenIds", "[]"),
        "acceptingOrders": raw.get("acceptingOrders", True),
        "negRisk": raw.get("negRisk", False),
        "bestBid": raw.get("bestBid"),
        "bestAsk": raw.get("bestAsk"),
        "volume24hr": float(raw.get("volume24hr", 0) or 0),
    }


class GammaClient:
    """Client for Polymarket market discovery via Gamma API + CLOB fallback."""

    def __init__(self):
        self._clob_client: httpx.AsyncClient | None = None
        self._gamma_client: httpx.AsyncClient | None = None

        from bot.utils.circuit_breaker import CircuitBreaker

        self._gamma_breaker = CircuitBreaker("gamma_api", failure_threshold=5, recovery_seconds=120)
        self._clob_breaker = CircuitBreaker("clob_api", failure_threshold=5, recovery_seconds=120)

    async def initialize(self) -> None:
        self._clob_client = httpx.AsyncClient(
            base_url=CLOB_API_URL,
            timeout=30,
            headers={"Accept": "application/json"},
        )
        self._gamma_client = httpx.AsyncClient(
            base_url=GAMMA_API_URL,
            timeout=30,
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        if self._clob_client:
            await self._clob_client.aclose()
        if self._gamma_client:
            await self._gamma_client.aclose()

    @async_retry(max_attempts=3, min_wait=2, max_wait=30)
    async def _fetch_gamma_markets(
        self,
        params: dict,
    ) -> list[GammaMarket]:
        """Fetch markets from Gamma API with given params."""
        if not self._gamma_breaker.allow_request():
            return []

        try:
            resp = await self._gamma_client.get("/markets", params=params)
            resp.raise_for_status()
            raw_markets = resp.json()

            markets = []
            for raw in raw_markets:
                if not raw.get("active", True) or raw.get("closed", False):
                    continue
                try:
                    transformed = _transform_gamma_api_market(raw)
                    markets.append(GammaMarket.model_validate(transformed))
                except Exception as e:
                    logger.debug("gamma_market_parse_skipped", error=str(e))

            self._gamma_breaker.record_success()
            return markets
        except Exception:
            self._gamma_breaker.record_failure()
            raise

    @async_retry(max_attempts=3, min_wait=2, max_wait=30)
    async def _fetch_clob_markets(self, limit: int = 200) -> list[GammaMarket]:
        """Fetch markets from CLOB /sampling-markets (fallback)."""
        if not self._clob_breaker.allow_request():
            return []

        try:
            resp = await self._clob_client.get(
                "/sampling-markets", params={"next_cursor": "MA=="}
            )
            resp.raise_for_status()
            data = resp.json()

            raw_markets = data.get("data", [])
            markets = []
            for raw in raw_markets:
                if not raw.get("active", True) or raw.get("closed", False):
                    continue
                try:
                    transformed = _transform_clob_market(raw)
                    markets.append(GammaMarket.model_validate(transformed))
                except Exception as e:
                    logger.debug("clob_market_parse_skipped", error=str(e))

            self._clob_breaker.record_success()
            return markets[:limit]
        except Exception:
            self._clob_breaker.record_failure()
            raise

    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
        order: str = "volume",
        _ascending: bool = False,
    ) -> list[GammaMarket]:
        """Fetch active markets. Gamma API primary, CLOB fallback."""
        try:
            params = {
                "active": str(active).lower(),
                "closed": str(closed).lower(),
                "limit": limit,
                "offset": offset,
            }
            markets = await self._fetch_gamma_markets(params)
            if markets:
                logger.debug("markets_from_gamma", count=len(markets))
                return markets
        except Exception as e:
            logger.warning("gamma_api_failed_using_clob", error=str(e))

        return await self._fetch_clob_markets(limit)

    @async_retry(max_attempts=3, min_wait=2, max_wait=30)
    async def get_market(self, market_id: str) -> GammaMarket | None:
        """Fetch a single market by condition_id."""
        try:
            resp = await self._clob_client.get(f"/markets/{market_id}")
            resp.raise_for_status()
            raw = resp.json()
            transformed = _transform_clob_market(raw)
            return GammaMarket.model_validate(transformed)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_active_markets(self, limit: int = 200) -> list[GammaMarket]:
        """Get all active, non-closed markets from Gamma API.

        Uses Gamma API as primary source for richer data (volume, liquidity,
        spread). Falls back to CLOB /sampling-markets if Gamma is unavailable.
        """
        try:
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit,
            }
            markets = await self._fetch_gamma_markets(params)
            if markets:
                return [m for m in markets if m.accepting_orders and not m.archived]
        except Exception as e:
            logger.warning("gamma_active_markets_failed", error=str(e))

        # CLOB fallback
        markets = await self._fetch_clob_markets(limit)
        return [m for m in markets if m.accepting_orders and not m.archived]

    async def get_short_term_markets(
        self,
        max_hours: float = 48.0,
        min_volume_24h: float = 50.0,
    ) -> list[GammaMarket]:
        """Fetch markets resolving within max_hours using Gamma API.

        Uses server-side end_date filtering for efficient short-term
        market discovery. Filters by 24h volume to exclude dead markets.
        """
        now = datetime.now(timezone.utc)
        end_max = now + timedelta(hours=max_hours)

        params = {
            "active": "true",
            "closed": "false",
            "limit": 100,
            "end_date_min": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date_max": end_max.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        try:
            markets = await self._fetch_gamma_markets(params)
        except Exception as e:
            logger.warning("gamma_short_term_failed", error=str(e))
            return []

        # Filter by 24h volume and accepting orders
        result = [
            m for m in markets
            if m.accepting_orders
            and not m.archived
            and m.volume_24h >= min_volume_24h
        ]

        return sorted(result, key=lambda m: m.end_date or datetime.max.replace(tzinfo=timezone.utc))

    async def get_near_resolution_markets(
        self, hours: float = 48.0, _min_volume: float = 0.0
    ) -> list[GammaMarket]:
        """Get markets resolving within the given hours window.

        Uses get_short_term_markets (Gamma API) for efficient server-side filtering.
        Falls back to client-side filtering of active markets.
        """
        # Try Gamma API first (server-side filtering)
        markets = await self.get_short_term_markets(max_hours=hours)
        if markets:
            return markets

        # Fallback: client-side filtering
        all_markets = await self.get_active_markets(limit=200)
        now = datetime.now(timezone.utc)
        near = []
        for m in all_markets:
            end = m.end_date
            if end is None:
                continue
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            hours_left = (end - now).total_seconds() / 3600
            if 0 < hours_left <= hours:
                near.append(m)
        return sorted(near, key=lambda m: m.end_date)

    async def get_high_volume_markets(
        self, _min_volume: float = 0.0, limit: int = 50
    ) -> list[GammaMarket]:
        """Get active markets accepting orders."""
        markets = await self.get_markets(limit=limit)
        return [m for m in markets if m.accepting_orders]

    async def search_markets(self, query: str, limit: int = 20) -> list[GammaMarket]:
        """Search markets by question text."""
        markets = await self.get_active_markets(limit=200)
        query_lower = query.lower()
        return [m for m in markets if query_lower in m.question.lower()][:limit]
