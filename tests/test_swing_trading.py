"""Tests for SwingTradingStrategy."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from collections import deque
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.agent.strategies.swing_trading import (
    PRICE_HISTORY_MAXLEN,
    SwingTradingStrategy,
)
from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy() -> SwingTradingStrategy:
    clob = AsyncMock()
    gamma = AsyncMock()
    cache = MagicMock()
    return SwingTradingStrategy(clob, gamma, cache)


def _make_market(
    market_id: str = "mkt1",
    yes_price: float = 0.50,
    no_price: float = 0.50,
    best_bid: float = 0.49,
    best_ask: float = 0.51,
    volume_24h: float = 500.0,
    hours_ahead: float = 24.0,
    category: str = "Sports",
    neg_risk: bool = False,
) -> GammaMarket:
    end_dt = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
    return GammaMarket(
        id=market_id,
        question=f"Test market {market_id}?",
        endDateIso=end_dt.isoformat(),
        outcomes=["Yes", "No"],
        outcomePrices=f'["{yes_price}", "{no_price}"]',
        clobTokenIds='["token_yes", "token_no"]',
        volume24hr=volume_24h,
        bestBid=best_bid,
        bestAsk=best_ask,
        groupItemTitle=category,
        negRisk=neg_risk,
    )


def _seed_momentum(
    strategy: SwingTradingStrategy,
    market_id: str = "mkt1",
    prices: list[float] | None = None,
) -> None:
    """Seed price history with rising prices to trigger momentum."""
    if prices is None:
        prices = [0.48, 0.485, 0.49, 0.495, 0.50]
    strategy._price_history[market_id] = deque(prices, maxlen=PRICE_HISTORY_MAXLEN)


def _seed_falling(
    strategy: SwingTradingStrategy,
    market_id: str = "mkt1",
    prices: list[float] | None = None,
) -> None:
    """Seed price history with falling prices."""
    if prices is None:
        prices = [0.52, 0.515, 0.51, 0.505, 0.50]
    strategy._price_history[market_id] = deque(prices, maxlen=PRICE_HISTORY_MAXLEN)


# ---------------------------------------------------------------------------
# Class metadata
# ---------------------------------------------------------------------------


class TestSwingTradingMeta:
    def test_name(self):
        s = _make_strategy()
        assert s.name == "swing_trading"

    def test_min_tier(self):
        s = _make_strategy()
        assert s.min_tier == CapitalTier.TIER1

    def test_tier1_enabled(self):
        s = _make_strategy()
        assert s.is_enabled_for_tier(CapitalTier.TIER1)

    def test_tier2_enabled(self):
        s = _make_strategy()
        assert s.is_enabled_for_tier(CapitalTier.TIER2)

    def test_tier3_enabled(self):
        s = _make_strategy()
        assert s.is_enabled_for_tier(CapitalTier.TIER3)

    def test_tighter_take_profit(self):
        s = _make_strategy()
        assert s.TAKE_PROFIT_PCT == 0.012

    def test_tighter_stop_loss(self):
        s = _make_strategy()
        assert s.STOP_LOSS_PCT == 0.012

    def test_higher_min_volume(self):
        s = _make_strategy()
        assert s.MIN_VOLUME_24H == 250.0


# ---------------------------------------------------------------------------
# Price history tracking
# ---------------------------------------------------------------------------


class TestPriceHistory:
    def test_update_adds_snapshots(self):
        s = _make_strategy()
        m = _make_market(best_bid=0.50)
        s._update_price_history([m])
        assert len(s._price_history["mkt1"]) == 1
        assert s._price_history["mkt1"][-1] == 0.50

    def test_update_appends_multiple(self):
        s = _make_strategy()
        m1 = _make_market(best_bid=0.50)
        m2 = _make_market(best_bid=0.51)
        s._update_price_history([m1])
        s._update_price_history([m2])
        assert len(s._price_history["mkt1"]) == 2

    def test_update_respects_maxlen(self):
        s = _make_strategy()
        for i in range(25):
            m = _make_market(best_bid=0.40 + i * 0.01)
            s._update_price_history([m])
        assert len(s._price_history["mkt1"]) == PRICE_HISTORY_MAXLEN

    def test_skips_none_bid(self):
        s = _make_strategy()
        m = _make_market(best_bid=None)
        s._update_price_history([m])
        assert "mkt1" not in s._price_history

    def test_skips_zero_bid(self):
        s = _make_strategy()
        m = _make_market(best_bid=0.0)
        s._update_price_history([m])
        assert "mkt1" not in s._price_history


# ---------------------------------------------------------------------------
# Momentum detection
# ---------------------------------------------------------------------------


class TestMomentumDetection:
    def test_upward_momentum_detected(self):
        s = _make_strategy()
        _seed_momentum(s)
        has_mom, pct = s._detect_momentum("mkt1")
        assert has_mom is True
        assert pct > 0

    def test_no_momentum_flat_prices(self):
        s = _make_strategy()
        s._price_history["mkt1"] = deque([0.50, 0.50, 0.50, 0.50], maxlen=20)
        has_mom, pct = s._detect_momentum("mkt1")
        assert has_mom is False

    def test_no_momentum_falling_prices(self):
        s = _make_strategy()
        _seed_falling(s)
        has_mom, pct = s._detect_momentum("mkt1")
        assert has_mom is False

    def test_no_momentum_insufficient_ticks(self):
        s = _make_strategy()
        s._price_history["mkt1"] = deque([0.50, 0.51], maxlen=20)
        has_mom, pct = s._detect_momentum("mkt1")
        assert has_mom is False

    def test_no_momentum_unknown_market(self):
        s = _make_strategy()
        has_mom, pct = s._detect_momentum("unknown")
        assert has_mom is False
        assert pct == 0.0

    def test_momentum_below_threshold(self):
        s = _make_strategy()
        # Very tiny moves: 0.001% per tick < 0.5% threshold
        s._price_history["mkt1"] = deque(
            [0.5000, 0.5001, 0.5002, 0.5003], maxlen=20
        )
        has_mom, pct = s._detect_momentum("mkt1")
        assert has_mom is False

    def test_downward_momentum_detected(self):
        s = _make_strategy()
        _seed_falling(s)
        assert s._detect_downward_momentum("mkt1") is True

    def test_no_downward_when_rising(self):
        s = _make_strategy()
        _seed_momentum(s)
        assert s._detect_downward_momentum("mkt1") is False


# ---------------------------------------------------------------------------
# Scan — entry signals
# ---------------------------------------------------------------------------


class TestScan:
    @pytest.mark.asyncio
    async def test_signal_on_momentum(self):
        s = _make_strategy()
        m = _make_market(best_bid=0.50)
        # Seed with prices that stay rising after scan appends best_bid=0.50
        _seed_momentum(s, prices=[0.46, 0.47, 0.48, 0.49])
        signals = await s.scan([m])
        assert len(signals) == 1
        sig = signals[0]
        assert sig.strategy == "swing_trading"
        assert sig.market_id == "mkt1"
        assert sig.side.value == "BUY"
        assert sig.metadata["momentum_pct"] > 0
        assert sig.metadata["hours_to_resolution"] == s.MAX_HOLD_HOURS

    @pytest.mark.asyncio
    async def test_no_signal_without_momentum(self):
        s = _make_strategy()
        m = _make_market()
        # No price history seeded
        signals = await s.scan([m])
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_price_too_high(self):
        s = _make_strategy()
        # Both outcomes above MAX_PRICE (0.85) — no valid entry
        m = _make_market(yes_price=0.90, no_price=0.90, best_bid=0.89, best_ask=0.91)
        _seed_momentum(s, prices=[0.86, 0.87, 0.88, 0.89])
        signals = await s.scan([m])
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_price_too_low(self):
        s = _make_strategy()
        m = _make_market(yes_price=0.10, no_price=0.10, best_bid=0.09, best_ask=0.11)
        _seed_momentum(s, prices=[0.06, 0.07, 0.08, 0.09])
        signals = await s.scan([m])
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_wide_spread(self):
        s = _make_strategy()
        m = _make_market(best_bid=0.45, best_ask=0.55)
        _seed_momentum(s, prices=[0.42, 0.43, 0.44, 0.45])
        signals = await s.scan([m])
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_low_volume(self):
        s = _make_strategy()
        m = _make_market(volume_24h=100.0, best_bid=0.50)
        _seed_momentum(s, prices=[0.46, 0.47, 0.48, 0.49])
        signals = await s.scan([m])
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_expiring_soon(self):
        s = _make_strategy()
        m = _make_market(hours_ahead=2.0, best_bid=0.50)
        _seed_momentum(s, prices=[0.46, 0.47, 0.48, 0.49])
        signals = await s.scan([m])
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_no_signal_no_bid_ask(self):
        s = _make_strategy()
        m = _make_market(best_bid=None, best_ask=None)
        _seed_momentum(s, prices=[0.46, 0.47, 0.48, 0.49])
        signals = await s.scan([m])
        assert len(signals) == 0

    @pytest.mark.asyncio
    async def test_scan_updates_price_history(self):
        """scan() should call _update_price_history even if no signal."""
        s = _make_strategy()
        m = _make_market()
        assert "mkt1" not in s._price_history
        await s.scan([m])
        # Price history should now have 1 entry from scan's update
        assert "mkt1" in s._price_history

    @pytest.mark.asyncio
    async def test_signals_sorted_by_momentum(self):
        s = _make_strategy()
        m1 = _make_market(market_id="mkt1")
        m2 = _make_market(market_id="mkt2")
        # Stronger momentum for mkt2
        _seed_momentum(s, "mkt1", [0.48, 0.485, 0.49])
        _seed_momentum(s, "mkt2", [0.40, 0.44, 0.48])
        signals = await s.scan([m1, m2])
        if len(signals) == 2:
            assert signals[0].market_id == "mkt2"  # Higher momentum first


# ---------------------------------------------------------------------------
# should_exit — exit conditions
# ---------------------------------------------------------------------------


class TestShouldExit:
    @pytest.mark.asyncio
    async def test_take_profit(self):
        s = _make_strategy()
        result = await s.should_exit(
            "mkt1", 0.52, avg_price=0.50, created_at=datetime.now(timezone.utc)
        )
        # 4% profit > 1.2% threshold
        assert result is True

    @pytest.mark.asyncio
    async def test_stop_loss(self):
        s = _make_strategy()
        result = await s.should_exit(
            "mkt1", 0.48, avg_price=0.50, created_at=datetime.now(timezone.utc)
        )
        # -4% loss > 1.2% threshold
        assert result is True

    @pytest.mark.asyncio
    async def test_time_expiry(self):
        s = _make_strategy()
        old_time = datetime.now(timezone.utc) - timedelta(hours=5)
        result = await s.should_exit(
            "mkt1", 0.505, avg_price=0.50, created_at=old_time
        )
        # 5h > 4h max hold
        assert result is True

    @pytest.mark.asyncio
    async def test_momentum_reversal(self):
        s = _make_strategy()
        _seed_falling(s)
        result = await s.should_exit(
            "mkt1", 0.505, avg_price=0.50, created_at=datetime.now(timezone.utc)
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_no_exit_normal(self):
        s = _make_strategy()
        _seed_momentum(s)  # Rising momentum, no reversal
        result = await s.should_exit(
            "mkt1", 0.505, avg_price=0.50, created_at=datetime.now(timezone.utc)
        )
        # 1% profit < 1.5% threshold, recent creation, upward momentum
        assert result is False

    @pytest.mark.asyncio
    async def test_no_exit_without_kwargs(self):
        """should_exit is graceful when avg_price/created_at not provided."""
        s = _make_strategy()
        result = await s.should_exit("mkt1", 0.505)
        assert result is False

    @pytest.mark.asyncio
    async def test_take_profit_just_above_threshold(self):
        s = _make_strategy()
        avg = 0.50
        # Just above 1.5% profit (avoids float precision edge)
        price = avg * (1 + s.TAKE_PROFIT_PCT + 0.001)
        result = await s.should_exit(
            "mkt1", price, avg_price=avg, created_at=datetime.now(timezone.utc)
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_stop_loss_exact_threshold(self):
        s = _make_strategy()
        avg = 0.50
        # Exactly 1.5% loss
        price = avg * (1 - s.STOP_LOSS_PCT)
        result = await s.should_exit(
            "mkt1", price, avg_price=avg, created_at=datetime.now(timezone.utc)
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_naive_created_at_handled(self):
        """created_at without tzinfo should be treated as UTC."""
        s = _make_strategy()
        old_time = datetime.now(timezone.utc) - timedelta(hours=5)  # Naive datetime
        result = await s.should_exit(
            "mkt1", 0.505, avg_price=0.50, created_at=old_time
        )
        assert result is True  # 5h > 4h max hold
