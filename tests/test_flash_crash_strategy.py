"""Tests for FlashCrashStrategy."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from bot.agent.strategies.flash_crash import FlashCrashStrategy
from bot.polymarket.orderbook_tracker import PricePoint
from bot.polymarket.types import GammaMarket


def _make_market(
    *,
    market_id: str = "m1",
    volume: float = 10_000.0,
    liquidity: float = 5_000.0,
    active: bool = True,
    accepting_orders: bool = True,
    token_ids: list[str] | None = None,
    question: str = "Will X happen?",
) -> GammaMarket:
    token_ids = token_ids or ["tok_yes", "tok_no"]
    return GammaMarket(
        id=market_id,
        question=question,
        volume=volume,
        liquidity=liquidity,
        active=active,
        acceptingOrders=accepting_orders,
        clobTokenIds=json.dumps(token_ids),
        outcomePrices=json.dumps(["0.50", "0.50"]),
    )


def _make_strategy(
    tracker: MagicMock | None = None,
) -> FlashCrashStrategy:
    clob = MagicMock()
    gamma = MagicMock()
    cache = MagicMock()
    return FlashCrashStrategy(
        clob_client=clob,
        gamma_client=gamma,
        cache=cache,
        orderbook_tracker=tracker,
    )


def _make_tracker(
    *,
    crashed: bool = False,
    drop_magnitude: float = 0.0,
    mid_price: float = 0.35,
    history: list[PricePoint] | None = None,
) -> MagicMock:
    tracker = MagicMock()
    tracker.detect_flash_crash.return_value = (crashed, drop_magnitude)
    tracker.get_mid_price.return_value = mid_price
    if history is None:
        history = [
            PricePoint(timestamp=1000.0, mid_price=0.50),
            PricePoint(timestamp=1025.0, mid_price=mid_price),
        ]
    tracker.mid_price_history.return_value = history
    return tracker


# ---- scan tests ----


@pytest.mark.asyncio
async def test_scan_no_crash_no_signal():
    """No flash crash detected -> no signals."""
    tracker = _make_tracker(crashed=False)
    strategy = _make_strategy(tracker=tracker)
    market = _make_market()

    signals = await strategy.scan([market])

    assert signals == []


@pytest.mark.asyncio
async def test_scan_crash_detected_generates_signal():
    """Flash crash detected -> generates a BUY signal."""
    tracker = _make_tracker(crashed=True, drop_magnitude=0.35, mid_price=0.35)
    strategy = _make_strategy(tracker=tracker)
    market = _make_market()

    signals = await strategy.scan([market])

    assert len(signals) == 1
    sig = signals[0]
    assert sig.strategy == "flash_crash"
    assert sig.side.value == "BUY"
    assert sig.market_id == "m1"
    assert sig.estimated_prob == 0.50  # pre-crash max
    assert sig.market_price == 0.35
    assert sig.edge > 0
    assert sig.confidence >= 0.50
    assert "Flash crash" in sig.reasoning


@pytest.mark.asyncio
async def test_scan_filters_low_volume():
    """Low-volume markets should be skipped."""
    tracker = _make_tracker(crashed=True, drop_magnitude=0.35, mid_price=0.35)
    strategy = _make_strategy(tracker=tracker)
    market = _make_market(volume=100.0)

    signals = await strategy.scan([market])

    assert signals == []
    tracker.detect_flash_crash.assert_not_called()


@pytest.mark.asyncio
async def test_scan_filters_high_price_before_crash():
    """Pre-crash price above MAX_PRICE_BEFORE_DROP should be skipped."""
    # Pre-crash max = 0.90, which exceeds default MAX_PRICE_BEFORE_DROP (0.85)
    history = [
        PricePoint(timestamp=1000.0, mid_price=0.90),
        PricePoint(timestamp=1025.0, mid_price=0.35),
    ]
    tracker = _make_tracker(crashed=True, drop_magnitude=0.60, mid_price=0.35, history=history)
    strategy = _make_strategy(tracker=tracker)
    market = _make_market()

    signals = await strategy.scan([market])

    assert signals == []


@pytest.mark.asyncio
async def test_scan_no_tracker_returns_empty():
    """Strategy with no tracker returns empty signals."""
    strategy = _make_strategy(tracker=None)
    market = _make_market()

    signals = await strategy.scan([market])

    assert signals == []


@pytest.mark.asyncio
async def test_scan_inactive_market_skipped():
    """Inactive markets are filtered out."""
    tracker = _make_tracker(crashed=True, drop_magnitude=0.35, mid_price=0.35)
    strategy = _make_strategy(tracker=tracker)
    market = _make_market(active=False)

    signals = await strategy.scan([market])

    assert signals == []


# ---- should_exit tests ----


@pytest.mark.asyncio
async def test_should_exit_stop_loss():
    """Price dropped below stop-loss threshold -> exit."""
    strategy = _make_strategy()
    created = datetime.now(timezone.utc) - timedelta(seconds=60)

    result = await strategy.should_exit(
        "m1", current_price=0.30, avg_price=0.40, created_at=created
    )

    assert result == "stop_loss"


@pytest.mark.asyncio
async def test_should_exit_take_profit():
    """Price rose above take-profit threshold -> exit."""
    strategy = _make_strategy()
    created = datetime.now(timezone.utc) - timedelta(seconds=60)

    result = await strategy.should_exit(
        "m1", current_price=0.50, avg_price=0.40, created_at=created
    )

    assert result == "take_profit"


@pytest.mark.asyncio
async def test_should_exit_time_expiry():
    """Position held beyond MAX_HOLD_SECONDS -> exit."""
    strategy = _make_strategy()
    created = datetime.now(timezone.utc) - timedelta(seconds=400)

    result = await strategy.should_exit(
        "m1", current_price=0.41, avg_price=0.40, created_at=created
    )

    assert result == "time_expiry"


@pytest.mark.asyncio
async def test_should_exit_not_yet():
    """Within hold period, no TP/SL triggered -> no exit."""
    strategy = _make_strategy()
    created = datetime.now(timezone.utc) - timedelta(seconds=60)

    result = await strategy.should_exit(
        "m1", current_price=0.41, avg_price=0.40, created_at=created
    )

    assert result is False


@pytest.mark.asyncio
async def test_should_exit_respects_min_hold():
    """Even if stop-loss hit, MIN_HOLD_SECONDS not met -> no exit."""
    strategy = _make_strategy()
    created = datetime.now(timezone.utc) - timedelta(seconds=5)

    result = await strategy.should_exit(
        "m1", current_price=0.30, avg_price=0.40, created_at=created
    )

    assert result is False


# ---- mutable params ----


def test_mutable_params_update():
    """Mutable params can be updated within valid range."""
    strategy = _make_strategy()

    assert strategy.update_param("DROP_THRESHOLD_PCT", 0.50)
    assert strategy.DROP_THRESHOLD_PCT == 0.50

    assert strategy.update_param("MAX_HOLD_SECONDS", 600)
    assert strategy.MAX_HOLD_SECONDS == 600

    # Out of range -> rejected
    assert not strategy.update_param("DROP_THRESHOLD_PCT", 0.05)
    assert strategy.DROP_THRESHOLD_PCT == 0.50  # unchanged

    # Unknown param -> rejected
    assert not strategy.update_param("NONEXISTENT", 1.0)
