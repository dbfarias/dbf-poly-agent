"""Market discovery client using Polymarket CLOB API.

The original Gamma API (gamma-api.polymarket.com) is no longer available.
This client uses the CLOB API's /sampling-markets endpoint instead, which
returns active markets with prices, and transforms the response into
GammaMarket models for backward compatibility.
"""

import json
from datetime import datetime, timezone

import httpx
import structlog

from bot.polymarket.types import GammaMarket
from bot.utils.retry import async_retry

logger = structlog.get_logger()

CLOB_API_URL = "https://clob.polymarket.com"


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
        "groupItemTitle": tags[0] if tags else "",
        "clobTokenIds": json.dumps(token_ids),
        "acceptingOrders": raw.get("accepting_orders", True),
    }


class GammaClient:
    """Client for Polymarket market discovery via CLOB API."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=CLOB_API_URL,
            timeout=30,
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    @async_retry(max_attempts=3, min_wait=2, max_wait=30)
    async def get_markets(
        self,
        limit: int = 100,
        offset: int = 0,
        active: bool = True,
        closed: bool = False,
        order: str = "volume",
        ascending: bool = False,
    ) -> list[GammaMarket]:
        """Fetch active markets from CLOB sampling endpoint."""
        resp = await self._client.get(
            "/sampling-markets", params={"next_cursor": "MA=="}
        )
        resp.raise_for_status()
        data = resp.json()

        raw_markets = data.get("data", [])
        markets = []
        for raw in raw_markets:
            if active and not raw.get("active", True):
                continue
            if not closed and raw.get("closed", False):
                continue
            try:
                transformed = _transform_clob_market(raw)
                markets.append(GammaMarket.model_validate(transformed))
            except Exception as e:
                logger.debug("market_parse_skipped", error=str(e))
                continue

        return markets[:limit]

    @async_retry(max_attempts=3, min_wait=2, max_wait=30)
    async def get_market(self, market_id: str) -> GammaMarket | None:
        """Fetch a single market by condition_id."""
        try:
            resp = await self._client.get(f"/markets/{market_id}")
            resp.raise_for_status()
            raw = resp.json()
            transformed = _transform_clob_market(raw)
            return GammaMarket.model_validate(transformed)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_active_markets(self, limit: int = 200) -> list[GammaMarket]:
        """Get all active, non-closed markets."""
        markets = await self.get_markets(limit=limit, active=True, closed=False)
        return [m for m in markets if m.accepting_orders and not m.archived]

    async def get_near_resolution_markets(
        self, hours: float = 48.0, min_volume: float = 0.0
    ) -> list[GammaMarket]:
        """Get markets resolving within the given hours window.

        Note: min_volume is kept for API compatibility but defaults to 0
        since the CLOB API does not provide volume data.
        """
        markets = await self.get_active_markets(limit=200)
        now = datetime.now(timezone.utc)
        near = []
        for m in markets:
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
        self, min_volume: float = 0.0, limit: int = 50
    ) -> list[GammaMarket]:
        """Get active markets accepting orders.

        Note: Volume filtering is not available from the CLOB API.
        Returns all active markets up to the limit.
        """
        markets = await self.get_markets(limit=limit)
        return [m for m in markets if m.accepting_orders]

    async def search_markets(self, query: str, limit: int = 20) -> list[GammaMarket]:
        """Search markets by question text."""
        markets = await self.get_active_markets(limit=200)
        query_lower = query.lower()
        return [m for m in markets if query_lower in m.question.lower()][:limit]
