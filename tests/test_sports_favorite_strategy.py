"""Tests for SportsFavoriteStrategy."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from bot.agent.strategies.sports_favorite import SportsFavoriteStrategy
from bot.polymarket.types import GammaMarket


def _make_market(
    *,
    market_id: str = "m1",
    question: str = "Will Palmeiras win on 2026-04-05?",
    yes_price: float = 0.20,
    no_price: float = 0.80,
    volume: float = 10_000.0,
    active: bool = True,
    accepting_orders: bool = True,
    end_date_iso: str | None = None,
    token_ids: list[str] | None = None,
) -> GammaMarket:
    token_ids = token_ids or ["tok_yes", "tok_no"]
    if end_date_iso is None:
        # Default: 6 hours from now
        end = datetime.now(timezone.utc) + timedelta(hours=6)
        end_date_iso = end.isoformat()
    return GammaMarket(
        id=market_id,
        question=question,
        volume=volume,
        active=active,
        acceptingOrders=accepting_orders,
        clobTokenIds=json.dumps(token_ids),
        outcomePrices=json.dumps([str(yes_price), str(no_price)]),
        endDateIso=end_date_iso,
    )


def _make_strategy() -> SportsFavoriteStrategy:
    clob = MagicMock()
    gamma = MagicMock()
    cache = MagicMock()
    return SportsFavoriteStrategy(
        clob_client=clob, gamma_client=gamma, cache=cache,
    )


# ---- scan tests ----


@pytest.mark.asyncio
async def test_scan_finds_weak_team_no():
    """Market with Yes=0.20, No=0.80 -> signal to buy No."""
    strategy = _make_strategy()
    market = _make_market(yes_price=0.20, no_price=0.80)
    signals = await strategy.scan([market])

    assert len(signals) == 1
    sig = signals[0]
    assert sig.strategy == "sports_favorite"
    assert sig.outcome == "No"
    assert sig.token_id == "tok_no"
    assert sig.market_price == 0.80
    assert sig.confidence == 0.80


@pytest.mark.asyncio
async def test_scan_skips_strong_team():
    """Market with Yes=0.70, No=0.30 -> No too cheap, skip."""
    strategy = _make_strategy()
    market = _make_market(yes_price=0.70, no_price=0.30)
    signals = await strategy.scan([market])

    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_skips_no_too_expensive():
    """No=0.95 -> above MAX_NO_PRICE, skip."""
    strategy = _make_strategy()
    market = _make_market(yes_price=0.05, no_price=0.95)
    signals = await strategy.scan([market])

    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_skips_no_too_cheap():
    """No=0.60 -> below MIN_NO_PRICE, skip."""
    strategy = _make_strategy()
    market = _make_market(yes_price=0.40, no_price=0.60)
    signals = await strategy.scan([market])

    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_skips_wrong_pattern():
    """Question doesn't match 'Will X win on YYYY-MM-DD' -> skip."""
    strategy = _make_strategy()
    market = _make_market(question="Will Bitcoin hit $100k?")
    signals = await strategy.scan([market])

    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_skips_low_volume():
    """Volume below MIN_VOLUME -> skip."""
    strategy = _make_strategy()
    market = _make_market(volume=1000.0)
    signals = await strategy.scan([market])

    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_skips_too_far_from_resolution():
    """Market resolves in 24h (> MAX_HOURS_TO_RESOLUTION=12) -> skip."""
    strategy = _make_strategy()
    end = datetime.now(timezone.utc) + timedelta(hours=24)
    market = _make_market(end_date_iso=end.isoformat())
    signals = await strategy.scan([market])

    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_skips_too_close_to_resolution():
    """Market resolves in 30 min (< MIN_HOURS_TO_RESOLUTION=1) -> skip."""
    strategy = _make_strategy()
    end = datetime.now(timezone.utc) + timedelta(minutes=30)
    market = _make_market(end_date_iso=end.isoformat())
    signals = await strategy.scan([market])

    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_skips_inactive_market():
    """Inactive market -> skip."""
    strategy = _make_strategy()
    market = _make_market(active=False)
    signals = await strategy.scan([market])

    assert len(signals) == 0


@pytest.mark.asyncio
async def test_scan_skips_not_accepting_orders():
    """Market not accepting orders -> skip."""
    strategy = _make_strategy()
    market = _make_market(accepting_orders=False)
    signals = await strategy.scan([market])

    assert len(signals) == 0


# ---- should_exit tests ----


@pytest.mark.asyncio
async def test_should_exit_take_profit():
    """Current price >= entry * 1.15 -> take profit."""
    strategy = _make_strategy()
    result = await strategy.should_exit("m1", 0.92, avg_price=0.80)
    assert result is True


@pytest.mark.asyncio
async def test_should_exit_stop_loss():
    """Current price <= entry * 0.75 -> stop loss."""
    strategy = _make_strategy()
    result = await strategy.should_exit("m1", 0.59, avg_price=0.80)
    assert result is True


@pytest.mark.asyncio
async def test_should_exit_not_yet():
    """Price between SL and TP -> hold."""
    strategy = _make_strategy()
    result = await strategy.should_exit("m1", 0.82, avg_price=0.80)
    assert result is False


# ---- signal correctness ----


@pytest.mark.asyncio
async def test_signal_has_correct_outcome_no():
    """Signal must buy the No token (index 1)."""
    strategy = _make_strategy()
    market = _make_market(
        token_ids=["yes_id_123", "no_id_456"],
        yes_price=0.15,
        no_price=0.85,
    )
    signals = await strategy.scan([market])

    assert len(signals) == 1
    assert signals[0].token_id == "no_id_456"
    assert signals[0].outcome == "No"
    assert signals[0].side.value == "BUY"


@pytest.mark.asyncio
async def test_signal_edge_calculation():
    """Edge = (estimated_prob - market_price) / market_price."""
    strategy = _make_strategy()
    market = _make_market(yes_price=0.20, no_price=0.80)
    signals = await strategy.scan([market])

    sig = signals[0]
    expected_prob = 0.90  # 0.80 + 0.10
    expected_edge = (expected_prob - 0.80) / 0.80
    assert abs(sig.edge - expected_edge) < 1e-6
    assert sig.estimated_prob == expected_prob


@pytest.mark.asyncio
async def test_signal_reasoning_contains_team():
    """Reasoning should mention the team name."""
    strategy = _make_strategy()
    market = _make_market(question="Will Palmeiras win on 2026-04-05?")
    signals = await strategy.scan([market])

    assert "Palmeiras" in signals[0].reasoning


# ---- mutable params ----


def test_mutable_params():
    """All mutable params should be accepted within range."""
    strategy = _make_strategy()

    assert strategy.update_param("MIN_NO_PRICE", 0.75)
    assert strategy.MIN_NO_PRICE == 0.75

    assert strategy.update_param("MAX_NO_PRICE", 0.92)
    assert strategy.MAX_NO_PRICE == 0.92

    assert strategy.update_param("MIN_VOLUME", 8000.0)
    assert strategy.MIN_VOLUME == 8000.0

    assert strategy.update_param("TAKE_PROFIT_PCT", 0.20)
    assert strategy.TAKE_PROFIT_PCT == 0.20

    # Out of range -> rejected
    assert not strategy.update_param("MIN_NO_PRICE", 0.30)
    assert not strategy.update_param("STOP_LOSS_PCT", 0.80)

    # Unknown param -> rejected
    assert not strategy.update_param("NONEXISTENT", 1.0)


@pytest.mark.asyncio
async def test_scan_sorts_by_edge_descending():
    """Multiple signals should be sorted by edge, highest first."""
    strategy = _make_strategy()
    m1 = _make_market(market_id="m1", yes_price=0.20, no_price=0.80)
    m2 = _make_market(
        market_id="m2",
        question="Will Flamengo win on 2026-04-05?",
        yes_price=0.28,
        no_price=0.72,
    )
    signals = await strategy.scan([m1, m2])

    assert len(signals) == 2
    # m2 has No=0.72, estimated=0.82, edge=0.139
    # m1 has No=0.80, estimated=0.90, edge=0.125
    assert signals[0].market_id == "m2"
    assert signals[1].market_id == "m1"


@pytest.mark.asyncio
async def test_scan_no_end_date():
    """Market with no end date -> skip (can't check time filter)."""
    strategy = _make_strategy()
    market = _make_market(end_date_iso="")
    signals = await strategy.scan([market])

    assert len(signals) == 0
