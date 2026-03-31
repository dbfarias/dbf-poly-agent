"""Load historical Polymarket data from the Data API.

Uses Polymarket's trade history API for price data. No heavy dependencies
(pyarrow, duckdb) required -- just httpx which is already in the project.
"""

from datetime import datetime, timezone
from typing import NamedTuple

import httpx
import structlog

logger = structlog.get_logger()

DATA_API_URL = "https://data-api.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"

# Reasonable limits to avoid pulling enormous datasets
_MAX_TRADES_PER_REQUEST = 1000
_MAX_PAGES = 10


class PriceTick(NamedTuple):
    """A single trade from historical data."""

    timestamp: datetime
    price: float
    size: float
    side: str  # "BUY" or "SELL"


class MarketHistory(NamedTuple):
    """Historical data for a market."""

    slug: str
    condition_id: str
    token_id: str
    question: str
    ticks: list[PriceTick]
    start_time: datetime
    end_time: datetime
    resolution: float | None  # 1.0 for Yes, 0.0 for No, None if unresolved


async def resolve_market_slug(slug: str) -> dict | None:
    """Resolve a market slug to condition_id, token_ids, question via Gamma API.

    Returns:
        Dict with keys: condition_id, token_ids, question, end_date, closed.
        None if market not found.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(
                f"{GAMMA_API_URL}/markets",
                params={"slug": slug, "limit": 1},
            )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return None

            market = data[0]
            return {
                "condition_id": market.get("conditionId", ""),
                "token_ids": market.get("clobTokenIds", "[]"),
                "question": market.get("question", ""),
                "end_date": market.get("endDate", ""),
                "closed": market.get("closed", False),
            }
        except Exception:
            logger.warning("resolve_slug_failed", slug=slug)
            return None


async def _fetch_trades_page(
    client: httpx.AsyncClient,
    condition_id: str,
    cursor: str | None = None,
) -> tuple[list[dict], str | None]:
    """Fetch a single page of trades from the Data API.

    Returns:
        Tuple of (trades list, next_cursor or None).
    """
    params: dict = {
        "market": condition_id,
        "limit": _MAX_TRADES_PER_REQUEST,
    }
    if cursor:
        params["cursor"] = cursor

    resp = await client.get(f"{DATA_API_URL}/trades", params=params)
    resp.raise_for_status()
    data = resp.json()

    trades = data if isinstance(data, list) else data.get("data", [])
    next_cursor = data.get("next_cursor") if isinstance(data, dict) else None
    return trades, next_cursor


def _parse_trade(raw: dict) -> PriceTick | None:
    """Parse a raw trade dict into a PriceTick."""
    try:
        ts_str = raw.get("timestamp") or raw.get("created_at", "")
        if not ts_str:
            return None
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return PriceTick(
            timestamp=ts,
            price=float(raw.get("price", 0)),
            size=float(raw.get("size", 0)),
            side=raw.get("side", "BUY").upper(),
        )
    except (ValueError, TypeError):
        return None


async def load_market_history(
    slug: str,
    token_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> MarketHistory:
    """Load historical data for a Polymarket market.

    Fetches trade history from the Polymarket Data API. Resolves
    the market slug to condition_id via Gamma API if needed.

    Args:
        slug: Market URL slug (e.g. "will-team-x-win-2026-03-30").
        token_id: Optional specific token ID. If None, uses first (Yes) token.
        start: Optional start time filter.
        end: Optional end time filter.

    Returns:
        MarketHistory with sorted price ticks.

    Raises:
        ValueError: If market not found or no trade data available.
    """
    # Resolve slug to market metadata
    info = await resolve_market_slug(slug)
    if info is None:
        raise ValueError(f"Market not found: {slug}")

    condition_id = info["condition_id"]
    question = info["question"]
    is_closed = info.get("closed", False)

    # Parse token IDs
    import json

    raw_tokens = info.get("token_ids", "[]")
    try:
        token_ids = json.loads(raw_tokens) if isinstance(raw_tokens, str) else raw_tokens
    except (json.JSONDecodeError, TypeError):
        token_ids = []

    if token_id is None and token_ids:
        token_id = str(token_ids[0])

    if not token_id:
        raise ValueError(f"No token ID available for market: {slug}")

    # Fetch trades
    ticks: list[PriceTick] = []
    async with httpx.AsyncClient(timeout=30) as client:
        cursor = None
        for _ in range(_MAX_PAGES):
            trades_raw, next_cursor = await _fetch_trades_page(
                client, condition_id, cursor,
            )
            if not trades_raw:
                break
            for raw in trades_raw:
                tick = _parse_trade(raw)
                if tick is not None:
                    ticks.append(tick)
            cursor = next_cursor
            if not cursor:
                break

    # Sort chronologically
    ticks.sort(key=lambda t: t.timestamp)

    # Apply time filters
    if start:
        start_utc = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
        ticks = [t for t in ticks if t.timestamp >= start_utc]
    if end:
        end_utc = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end
        ticks = [t for t in ticks if t.timestamp <= end_utc]

    start_time = ticks[0].timestamp if ticks else datetime.now(timezone.utc)
    end_time = ticks[-1].timestamp if ticks else datetime.now(timezone.utc)

    # Determine resolution for closed markets
    resolution: float | None = None
    if is_closed and ticks:
        last_price = ticks[-1].price
        resolution = 1.0 if last_price > 0.9 else 0.0

    logger.info(
        "market_history_loaded",
        slug=slug,
        condition_id=condition_id,
        ticks=len(ticks),
        start=str(start_time),
        end=str(end_time),
    )

    return MarketHistory(
        slug=slug,
        condition_id=condition_id,
        token_id=token_id or "",
        question=question,
        ticks=ticks,
        start_time=start_time,
        end_time=end_time,
        resolution=resolution,
    )
