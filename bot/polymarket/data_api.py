"""Polymarket Data API client for positions, trades, and PnL."""

import httpx
import structlog

from bot.config import settings
from bot.polymarket.types import PositionInfo
from bot.utils.retry import async_retry

logger = structlog.get_logger()

DATA_API_URL = "https://data-api.polymarket.com"


class DataApiClient:
    """Client for Polymarket's Data API (positions, PnL, history)."""

    def __init__(self):
        self._client: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=DATA_API_URL,
            timeout=30,
            headers={"Accept": "application/json"},
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    @async_retry(max_attempts=3, min_wait=2, max_wait=30)
    async def get_positions(self, address: str | None = None) -> list[PositionInfo]:
        """Fetch current positions for the wallet."""
        if settings.is_paper:
            return []

        addr = address or self._get_wallet_address()
        if not addr:
            return []

        resp = await self._client.get("/positions", params={"user": addr})
        resp.raise_for_status()
        data = resp.json()
        positions = []
        for p in data:
            try:
                outcome = p.get("outcome", "")
                # curPrice is the current market price of the token held in this
                # position — use it directly regardless of outcome side.
                token_price = float(p.get("curPrice", 0))
                positions.append(
                    PositionInfo(
                        market_id=p.get("conditionId", ""),
                        token_id=p.get("asset", ""),
                        outcome=outcome,
                        question=p.get("title", ""),
                        size=float(p.get("size", 0)),
                        avg_price=float(p.get("avgPrice") or p.get("curPrice", 0)),
                        current_price=token_price,
                        unrealized_pnl=float(p.get("cashPnl", 0)),
                    )
                )
            except (ValueError, KeyError) as e:
                logger.warning("position_parse_error", error=str(e))
        return positions

    @async_retry(max_attempts=3, min_wait=2, max_wait=30)
    async def get_trade_history(
        self, address: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Fetch trade history for the wallet."""
        if settings.is_paper:
            return []

        addr = address or self._get_wallet_address()
        if not addr:
            return []

        resp = await self._client.get(
            "/trades", params={"user": addr, "limit": limit}
        )
        resp.raise_for_status()
        return resp.json()

    @async_retry(max_attempts=3, min_wait=2, max_wait=30)
    async def get_balance(self, address: str | None = None) -> float:
        """Fetch USDC balance for the wallet."""
        if settings.is_paper:
            return settings.initial_bankroll

        addr = address or self._get_wallet_address()
        if not addr:
            return 0.0

        resp = await self._client.get("/balance", params={"user": addr})
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("balance", 0))

    @async_retry(max_attempts=3, min_wait=2, max_wait=30)
    async def get_leaderboard(
        self, window: str = "7d", limit: int = 100,
    ) -> list[dict]:
        """Fetch top traders from the leaderboard.

        Returns list of dicts with: proxyAddress, username, pnl, volume,
        numTrades, winRate, etc.
        """
        resp = await self._client.get(
            "/v1/leaderboard",
            params={"window": window, "limit": limit},
        )
        resp.raise_for_status()
        data = resp.json()
        # Normalize: API may return list or {"leaderboard": [...]}
        if isinstance(data, dict):
            return data.get("leaderboard", data.get("data", []))
        return data if isinstance(data, list) else []

    @async_retry(max_attempts=2, min_wait=1, max_wait=10)
    async def get_user_activity(self, proxy_address: str) -> list[dict]:
        """Fetch recent activity for a specific user/wallet.

        Returns list of activity items (trades, positions opened/closed).
        """
        resp = await self._client.get(
            "/activity",
            params={"user": proxy_address},
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            return data.get("activity", data.get("data", []))
        return data if isinstance(data, list) else []

    @async_retry(max_attempts=2, min_wait=1, max_wait=10)
    async def get_user_trades(
        self, proxy_address: str, limit: int = 20,
    ) -> list[dict]:
        """Fetch trade history for a specific user/wallet."""
        resp = await self._client.get(
            "/trades",
            params={"user": proxy_address, "limit": limit},
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        return data.get("trades", data.get("data", []))

    def _get_wallet_address(self) -> str | None:
        """Derive wallet address from private key."""
        if not settings.poly_private_key:
            return None
        try:
            from eth_account import Account

            account = Account.from_key(settings.poly_private_key)
            return account.address
        except Exception as e:
            logger.warning("wallet_address_derivation_failed", error=str(e))
            return None
