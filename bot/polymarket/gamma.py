"""Gamma API client for market discovery and metadata."""

from datetime import datetime, timezone

import httpx
import structlog

from bot.polymarket.types import GammaMarket
from bot.utils.retry import async_retry

logger = structlog.get_logger()

GAMMA_API_URL = "https://gamma-api.polymarket.com"


class GammaClient:
    """Client for Polymarket's Gamma API (market discovery)."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=GAMMA_API_URL,
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
        """Fetch markets from Gamma API."""
        params = {
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "order": order,
            "ascending": str(ascending).lower(),
        }
        resp = await self._client.get("/markets", params=params)
        resp.raise_for_status()
        data = resp.json()
        return [GammaMarket.model_validate(m) for m in data]

    @async_retry(max_attempts=3, min_wait=2, max_wait=30)
    async def get_market(self, market_id: str) -> GammaMarket | None:
        """Fetch a single market by ID."""
        try:
            resp = await self._client.get(f"/markets/{market_id}")
            resp.raise_for_status()
            return GammaMarket.model_validate(resp.json())
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_active_markets(self, limit: int = 200) -> list[GammaMarket]:
        """Get all active, non-closed markets."""
        markets = await self.get_markets(limit=limit, active=True, closed=False)
        return [m for m in markets if m.accepting_orders and not m.archived]

    async def get_near_resolution_markets(
        self, hours: float = 48.0, min_volume: float = 1000.0
    ) -> list[GammaMarket]:
        """Get markets resolving within the given hours window."""
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
            if 0 < hours_left <= hours and m.volume >= min_volume:
                near.append(m)
        return sorted(near, key=lambda m: m.end_date)

    async def get_high_volume_markets(
        self, min_volume: float = 10000.0, limit: int = 50
    ) -> list[GammaMarket]:
        """Get high-volume active markets."""
        markets = await self.get_markets(limit=limit, order="volume", ascending=False)
        return [m for m in markets if m.volume >= min_volume and m.accepting_orders]

    async def search_markets(self, query: str, limit: int = 20) -> list[GammaMarket]:
        """Search markets by question text."""
        markets = await self.get_active_markets(limit=200)
        query_lower = query.lower()
        return [m for m in markets if query_lower in m.question.lower()][:limit]
