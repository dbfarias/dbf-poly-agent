"""Event-level price scaling for Trade Watcher agents.

When a watcher monitors a price-level market (e.g. "WTI $120 in April"),
this module fetches ALL sibling price levels in the same Polymarket event
and evaluates whether to scale up/down to a different level.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# Cache event levels for 5 minutes (300s) to avoid hammering Gamma API
_LEVEL_CACHE_TTL_SEC = 300


@dataclass(frozen=True)
class PriceLevel:
    """A single price-level market within an event."""

    price_target: float
    market_id: str  # conditionId
    token_id: str  # Yes token ID
    yes_price: float
    question: str


@dataclass(frozen=True)
class ScaleLevelRequest:
    """Immutable request to scale from one price level to another."""

    watcher_id: int
    direction: str  # "up" or "down"
    sell_market_id: str
    sell_token_id: str
    buy_market_id: str
    buy_token_id: str
    buy_price: float
    buy_question: str
    buy_outcome: str
    from_level: float
    to_level: float
    reasoning: str


@dataclass(frozen=True)
class CachedLevels:
    """Time-bounded cache for event price levels."""

    levels: tuple[PriceLevel, ...]
    fetched_at: float


def parse_levels_from_event(event: dict) -> list[PriceLevel]:
    """Extract sorted price levels from a Gamma API event response.

    The event dict contains a 'markets' list, each with conditionId,
    question, outcomePrices, outcomes, and clobTokenIds.
    """
    from bot.agent.watcher_eligibility import extract_price_level

    markets = event.get("markets", [])
    levels: list[PriceLevel] = []

    for m in markets:
        question = m.get("question", "")
        price_target = extract_price_level(question)
        if price_target is None:
            continue

        condition_id = m.get("conditionId", "") or m.get("id", "")
        if not condition_id:
            continue

        # Parse prices and token IDs
        yes_price = _extract_yes_price(m)
        token_id = _extract_yes_token_id(m)
        if yes_price is None or not token_id:
            continue

        levels.append(PriceLevel(
            price_target=price_target,
            market_id=condition_id,
            token_id=token_id,
            yes_price=yes_price,
            question=question,
        ))

    return sorted(levels, key=lambda lv: lv.price_target)


def _extract_yes_price(market: dict) -> float | None:
    """Extract the Yes outcome price from a Gamma API market dict."""
    prices = market.get("outcomePrices", "[]")
    outcomes = market.get("outcomes", "[]")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(outcomes, str):
        try:
            outcomes = json.loads(outcomes)
        except (json.JSONDecodeError, TypeError):
            return None
    if not prices or not outcomes:
        return None
    try:
        idx = outcomes.index("Yes")
        return float(prices[idx])
    except (ValueError, IndexError):
        return float(prices[0]) if prices else None


def _extract_yes_token_id(market: dict) -> str:
    """Extract the Yes token ID from a Gamma API market dict."""
    token_ids = market.get("clobTokenIds", "[]")
    if isinstance(token_ids, str):
        try:
            token_ids = json.loads(token_ids)
        except (json.JSONDecodeError, TypeError):
            return ""
    # First token is typically Yes
    return str(token_ids[0]) if token_ids else ""


def find_our_level(
    levels: list[PriceLevel], market_id: str
) -> PriceLevel | None:
    """Find the price level matching our current position."""
    for lv in levels:
        if lv.market_id == market_id:
            return lv
    return None


def find_adjacent_level(
    levels: list[PriceLevel],
    current: PriceLevel,
    direction: str,
) -> PriceLevel | None:
    """Find the next level up or down from our current level.

    Args:
        levels: Sorted list of price levels.
        current: Our current price level.
        direction: "up" or "down".

    Returns next active level or None.
    """
    idx = _index_of_level(levels, current)
    if idx is None:
        return None

    if direction == "up" and idx < len(levels) - 1:
        candidate = levels[idx + 1]
        # Skip resolved levels (price near 1.0 or 0.0)
        if 0.02 < candidate.yes_price < 0.98:
            return candidate
    elif direction == "down" and idx > 0:
        candidate = levels[idx - 1]
        if 0.02 < candidate.yes_price < 0.98:
            return candidate
    return None


def _index_of_level(
    levels: list[PriceLevel], target: PriceLevel
) -> int | None:
    """Find index of target level in sorted list."""
    for i, lv in enumerate(levels):
        if lv.market_id == target.market_id:
            return i
    return None


def evaluate_scale_up(
    current_price: float,
    our_level: PriceLevel,
    next_up: PriceLevel | None,
) -> bool:
    """Determine if scaling up is warranted.

    Scale up when our position is expensive (>= 0.80) and the next
    level offers better risk/reward (<= 0.50).
    """
    if next_up is None:
        return False
    if current_price < 0.80:
        return False
    if next_up.yes_price > 0.50:
        return False
    return True


def evaluate_scale_down(
    current_price: float,
    avg_entry: float,
    our_level: PriceLevel,
    next_down: PriceLevel | None,
) -> bool:
    """Determine if scaling down is warranted.

    Scale down when price has dropped significantly (< 85% of entry)
    and the lower level is safer (>= 0.70).
    """
    if next_down is None:
        return False
    if avg_entry <= 0:
        return False
    if current_price >= avg_entry * 0.85:
        return False
    if next_down.yes_price < 0.70:
        return False
    return True


def is_cache_valid(cached: CachedLevels | None) -> bool:
    """Check if cached levels are still within TTL."""
    if cached is None:
        return False
    return (time.time() - cached.fetched_at) < _LEVEL_CACHE_TTL_SEC
