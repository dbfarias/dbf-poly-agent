"""Determine if a market qualifies for a Trade Watcher."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from bot.research.market_classifier import MarketType

# Watcher-eligible market types (evolving information, multi-day horizons)
_ELIGIBLE_TYPES = frozenset({MarketType.LONG_TERM, MarketType.ECONOMIC, MarketType.UNKNOWN})

# Patterns that indicate price-level markets within a scalable event
_PRICE_HIT_RE = re.compile(
    r"hit\s+\((HIGH|LOW)\)\s+\$[\d,]+", re.IGNORECASE
)
_PRICE_ABOVE_BELOW_RE = re.compile(
    r"(above|below|over|under)\s+\$[\d,]+", re.IGNORECASE
)
_PRICE_BETWEEN_RE = re.compile(
    r"between\s+\$[\d,]+\s+and\s+\$[\d,]+", re.IGNORECASE
)
# Extract the numeric price level from the question
_PRICE_LEVEL_RE = re.compile(r"\$[\d,]+(?:\.\d+)?")


def is_watcher_eligible(
    market_type: MarketType,
    end_date: datetime | None,
    price: float,
    volume: float,
    min_hours: float = 48.0,
    min_price: float = 0.10,
    max_price: float = 0.85,
    min_volume: float = 5000.0,
) -> bool:
    """Check if a market qualifies for a Trade Watcher.

    Eligible markets have evolving information over multi-day horizons
    where price scales with news/events. Sports, weather, and short-term
    markets do NOT qualify.
    """
    if market_type not in _ELIGIBLE_TYPES:
        return False
    if end_date is not None:
        now = datetime.now(timezone.utc)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        hours_left = (end_date - now).total_seconds() / 3600
        if hours_left < min_hours:
            return False
    if not (min_price <= price <= max_price):
        return False
    if volume < min_volume:
        return False
    return True


def detect_scalable_event(question: str) -> bool:
    """Detect if a market question indicates a scalable price-level event.

    Scalable events have multiple price targets in the same event, e.g.:
    - "Will WTI Crude Oil (WTI) hit (HIGH) $120 in April?"
    - "Will Bitcoin be above $100,000 on April 30?"
    - "Will ETH be between $2,000 and $3,000?"

    Returns True if the question matches a price-level pattern.
    """
    if _PRICE_HIT_RE.search(question):
        return True
    if _PRICE_ABOVE_BELOW_RE.search(question):
        return True
    if _PRICE_BETWEEN_RE.search(question):
        return True
    return False


def extract_price_level(question: str) -> float | None:
    """Extract the primary price level from a market question.

    Examples:
        "hit (HIGH) $120" -> 120.0
        "above $100,000" -> 100000.0

    Returns None if no price found.
    """
    match = _PRICE_LEVEL_RE.search(question)
    if not match:
        return None
    raw = match.group().replace("$", "").replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None
