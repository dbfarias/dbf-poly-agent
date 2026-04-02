"""Determine if a market qualifies for a Trade Watcher."""

from datetime import datetime, timezone

from bot.research.market_classifier import MarketType

# Watcher-eligible market types (evolving information, multi-day horizons)
_ELIGIBLE_TYPES = frozenset({MarketType.LONG_TERM, MarketType.ECONOMIC, MarketType.UNKNOWN})


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
