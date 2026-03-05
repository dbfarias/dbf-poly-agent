"""Tests for RiskManager — the most critical bot module."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timezone

import pytest

from bot.agent.risk_manager import RiskCheckResult, RiskManager
from bot.config import CapitalTier, TierConfig, settings, trading_day
from bot.data.models import Position
from bot.polymarket.types import OrderSide, TradeSignal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_signal(
    edge: float = 0.06,
    estimated_prob: float = 0.92,
    market_price: float = 0.86,
    confidence: float = 0.85,
    metadata: dict | None = None,
) -> TradeSignal:
    return TradeSignal(
        strategy="time_decay",
        market_id="mkt1",
        token_id="token1",
        side=OrderSide.BUY,
        estimated_prob=estimated_prob,
        market_price=market_price,
        edge=edge,
        size_usd=0.0,
        confidence=confidence,
        metadata=metadata or {},
    )


def make_position(
    market_id: str = "mkt1",
    category: str = "crypto",
    cost_basis: float = 5.0,
    is_open: bool = True,
) -> Position:
    return Position(
        market_id=market_id,
        token_id="token1",
        side="BUY",
        size=10.0,
        avg_price=0.87,
        current_price=0.90,
        cost_basis=cost_basis,
        unrealized_pnl=0.3,
        is_open=is_open,
        category=category,
    )


@pytest.fixture
def rm():
    """Fresh RiskManager with initial_bankroll patched to 10.0."""
    from datetime import datetime, timezone

    original = settings.initial_bankroll
    settings.initial_bankroll = 10.0
    manager = RiskManager()
    # Set daily date so peak equity doesn't reset on same-day calls
    manager._daily_pnl_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yield manager
    settings.initial_bankroll = original


# ---------------------------------------------------------------------------
# RiskCheckResult
# ---------------------------------------------------------------------------


class TestRiskCheckResult:
    def test_bool_truthy(self):
        assert bool(RiskCheckResult(True)) is True

    def test_bool_falsy(self):
        assert bool(RiskCheckResult(False, "bad")) is False

    def test_repr_pass(self):
        r = RiskCheckResult(True)
        assert "PASS" in repr(r)

    def test_repr_fail(self):
        r = RiskCheckResult(False, "too risky")
        assert "FAIL" in repr(r)
        assert "too risky" in repr(r)


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------


class TestPauseResume:
    def test_initially_not_paused(self, rm):
        assert rm.is_paused is False

    def test_pause_sets_paused(self, rm):
        rm.pause()
        assert rm.is_paused is True

    def test_resume_clears_paused(self, rm):
        rm.pause()
        rm.resume()
        assert rm.is_paused is False

    def test_double_pause_still_paused(self, rm):
        rm.pause()
        rm.pause()
        assert rm.is_paused is True


# ---------------------------------------------------------------------------
# update_peak_equity
# ---------------------------------------------------------------------------


class TestUpdatePeakEquity:
    def test_higher_updates_peak(self, rm):
        rm._daily_pnl_date = trading_day()
        rm.update_peak_equity(20.0)
        assert rm._peak_equity == 20.0

    def test_equal_no_change(self, rm):
        rm._daily_pnl_date = trading_day()
        rm.update_peak_equity(10.0)
        assert rm._peak_equity == 10.0

    def test_lower_no_change(self, rm):
        rm._daily_pnl_date = trading_day()
        rm.update_peak_equity(3.0)
        assert rm._peak_equity == 10.0


# ---------------------------------------------------------------------------
# update_daily_pnl
# ---------------------------------------------------------------------------


class TestPnlDirtyInit:
    def test_pnl_dirty_initialized_false(self, rm):
        """_pnl_dirty should be explicitly initialized (not relying on getattr)."""
        assert rm._pnl_dirty is False

    def test_pnl_dirty_set_after_update(self, rm):
        rm.update_daily_pnl(0.5)
        assert rm._pnl_dirty is True


class TestUpdateDailyPnl:
    def test_accumulates(self, rm):
        rm.update_daily_pnl(0.5)
        rm.update_daily_pnl(0.3)
        assert rm._daily_pnl == pytest.approx(0.8)

    def test_negative_accumulates(self, rm):
        rm.update_daily_pnl(-0.2)
        rm.update_daily_pnl(-0.3)
        assert rm._daily_pnl == pytest.approx(-0.5)

    def test_date_rollover_resets(self, rm):
        rm.update_daily_pnl(1.0)
        # Simulate a date change by manually setting a past date
        rm._daily_pnl_date = "2020-01-01"
        rm.update_daily_pnl(0.5)
        assert rm._daily_pnl == pytest.approx(0.5)

    def test_same_day_no_reset(self, rm):
        rm.update_daily_pnl(1.0)
        assert rm._daily_pnl_date == trading_day()
        rm.update_daily_pnl(0.5)
        assert rm._daily_pnl == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# _check_paused
# ---------------------------------------------------------------------------


class TestCheckPaused:
    def test_pass_when_running(self, rm):
        assert rm._check_paused().passed is True

    def test_fail_when_paused(self, rm):
        rm.pause()
        result = rm._check_paused()
        assert result.passed is False
        assert "paused" in result.reason.lower()


# ---------------------------------------------------------------------------
# _check_duplicate_position
# ---------------------------------------------------------------------------


class TestCheckDuplicatePosition:
    def test_no_positions_passes(self, rm):
        signal = make_signal()
        assert rm._check_duplicate_position(signal, []).passed is True

    def test_different_market_passes(self, rm):
        signal = make_signal()  # market_id="mkt1"
        positions = [make_position(market_id="mkt2")]
        assert rm._check_duplicate_position(signal, positions).passed is True

    def test_same_market_open_fails(self, rm):
        signal = make_signal()  # market_id="mkt1"
        positions = [make_position(market_id="mkt1", is_open=True)]
        result = rm._check_duplicate_position(signal, positions)
        assert result.passed is False
        assert "duplicate" in result.reason.lower()

    def test_same_market_closed_passes(self, rm):
        signal = make_signal()  # market_id="mkt1"
        positions = [make_position(market_id="mkt1", is_open=False)]
        assert rm._check_duplicate_position(signal, positions).passed is True


# ---------------------------------------------------------------------------
# _check_daily_loss
# ---------------------------------------------------------------------------


class TestCheckDailyLoss:
    def test_within_limit(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # daily_loss_limit_pct=0.10
        # Equity-based: bankroll=9.5, day_start=10.0 → PnL=-0.5, limit=-1.0
        rm._day_start_equity = 10.0
        assert rm._check_daily_loss(9.5, config).passed is True

    def test_exceeds_limit(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # daily_loss_limit_pct=0.10
        # Equity-based: bankroll=8.0, day_start=10.0 → PnL=-2.0, limit=-1.0
        rm._day_start_equity = 10.0
        result = rm._check_daily_loss(8.0, config)
        assert result.passed is False

    def test_exact_boundary_passes(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)
        # Equity-based: bankroll=9.0, day_start=10.0 → PnL=-1.0, limit=-1.0
        # -1.0 < -1.0 is False → passes
        rm._day_start_equity = 10.0
        assert rm._check_daily_loss(9.0, config).passed is True

    def test_equity_based_not_accumulated(self, rm):
        """Daily loss check uses equity delta, not accumulated _daily_pnl."""
        config = TierConfig.get(CapitalTier.TIER1)
        # Inflated _daily_pnl says +$5, but equity says -$1.5
        rm._daily_pnl = 5.0  # Inflated (should be ignored)
        rm._day_start_equity = 10.0
        # bankroll=8.5 → equity-based PnL = -1.5 > -1.0 limit → FAIL
        result = rm._check_daily_loss(8.5, config)
        assert result.passed is False


class TestSetDayStartEquity:
    def test_sets_equity(self, rm):
        rm.set_day_start_equity(17.0)
        assert rm._day_start_equity == 17.0

    def test_risk_metrics_uses_equity_pnl(self, rm):
        rm._day_start_equity = 10.0
        rm._daily_pnl = 5.0  # Inflated (should be ignored)
        metrics = rm.get_risk_metrics(9.5)
        assert metrics["daily_pnl"] == -0.5  # Equity-based: 9.5 - 10.0


# ---------------------------------------------------------------------------
# _check_drawdown
# ---------------------------------------------------------------------------


class TestCheckDrawdown:
    def test_within_limit(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # max_drawdown_pct=0.25
        rm._peak_equity = 10.0
        # bankroll 9.0 → dd = 10%. Within 25%.
        assert rm._check_drawdown(9.0, config).passed is True

    def test_exceeds_limit(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # max_drawdown_pct=0.25
        rm._peak_equity = 10.0
        # bankroll 7.0 → dd = 30%. Exceeds 25%.
        result = rm._check_drawdown(7.0, config)
        assert result.passed is False

    def test_zero_peak_passes(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)
        rm._peak_equity = 0.0
        # current_drawdown returns 0.0 when peak is 0
        assert rm._check_drawdown(5.0, config).passed is True


# ---------------------------------------------------------------------------
# _check_max_positions
# ---------------------------------------------------------------------------


class TestCheckMaxPositions:
    def test_under_limit(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # max_positions=6
        assert rm._check_max_positions([], config).passed is True

    def test_at_limit_fails(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # max_positions=6
        positions = [make_position(market_id=f"mkt{i}") for i in range(6)]
        result = rm._check_max_positions(positions, config)
        assert result.passed is False

    def test_under_tier1_limit_passes(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # max_positions=6
        positions = [make_position(market_id=f"mkt{i}") for i in range(5)]
        assert rm._check_max_positions(positions, config).passed is True

    def test_tier3_higher_limit(self, rm):
        config = TierConfig.get(CapitalTier.TIER3)  # max_positions=15
        positions = [make_position(market_id=f"mkt{i}") for i in range(10)]
        assert rm._check_max_positions(positions, config).passed is True

    def test_pending_count_added_to_total(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # max_positions=6
        positions = [make_position(market_id=f"mkt{i}") for i in range(5)]
        # 5 open + 1 pending = 6 → at limit → fails
        result = rm._check_max_positions(positions, config, pending_count=1)
        assert result.passed is False
        assert "pending" in result.reason.lower()

    def test_pending_count_under_limit_passes(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # max_positions=6
        positions = [make_position(market_id=f"mkt{i}") for i in range(4)]
        # 4 open + 1 pending = 5 → under 6 → passes
        result = rm._check_max_positions(positions, config, pending_count=1)
        assert result.passed is True

    def test_stuck_positions_excluded_from_count(self, rm, monkeypatch):
        """Positions with <5 shares (stuck on CLOB) don't count toward limit."""
        from bot.config import settings as _settings

        monkeypatch.setattr(_settings, "trading_mode", "live")
        config = TierConfig.get(CapitalTier.TIER1)  # max_positions=6
        # 5 sellable + 3 stuck (size=2.0 < MIN_SELLABLE=5.0) = 8 total but only 5 count
        sellable = [make_position(market_id=f"sell{i}") for i in range(5)]
        stuck = []
        for i in range(3):
            p = make_position(market_id=f"stuck{i}")
            p.size = 2.0
            stuck.append(p)
        result = rm._check_max_positions(sellable + stuck, config)
        assert result.passed is True  # 5 < 6


# ---------------------------------------------------------------------------
# _check_total_deployed
# ---------------------------------------------------------------------------


class TestCheckTotalDeployed:
    def test_with_available_capital_passes(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)
        # 5.0 deployed out of 10.0 = $5.0 available → passes
        positions = [make_position(cost_basis=5.0)]
        result = rm._check_total_deployed(positions, 10.0, config)
        assert result.passed is True

    def test_within_deployed_limit_passes(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)
        # 7.0 deployed out of 10.0 → 70% < 85% max_deployed_pct → passes
        positions = [
            make_position(market_id="mkt1", cost_basis=3.5),
            make_position(market_id="mkt2", cost_basis=3.5),
        ]
        result = rm._check_total_deployed(positions, 10.0, config)
        assert result.passed is True

    def test_over_deployed_limit_fails(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)
        # 9.0 deployed out of 10.0 → 90% > 85% max_deployed_pct → fails
        positions = [make_position(cost_basis=9.0)]
        result = rm._check_total_deployed(positions, 10.0, config)
        assert result.passed is False
        assert "max deployed" in result.reason.lower()

    def test_fully_deployed_fails(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)
        # 10.0 deployed out of 10.0 = $0 available → fails
        positions = [make_position(cost_basis=10.0)]
        result = rm._check_total_deployed(positions, 10.0, config)
        assert result.passed is False

    def test_no_positions_passes(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)
        result = rm._check_total_deployed([], 10.0, config)
        assert result.passed is True

    def test_closed_positions_excluded(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)
        # Closed position should not count toward deployed total
        positions = [make_position(cost_basis=8.0, is_open=False)]
        result = rm._check_total_deployed(positions, 10.0, config)
        assert result.passed is True


# ---------------------------------------------------------------------------
# _check_category_exposure
# ---------------------------------------------------------------------------


class TestCheckCategoryExposure:
    def test_no_category_maps_to_other(self, rm):
        """Empty category should map to 'other' and still run exposure check."""
        config = TierConfig.get(CapitalTier.TIER1)
        signal = make_signal(metadata={})
        result = rm._check_category_exposure(signal, [], 10.0, config)
        assert result.passed is True

    def test_within_limit(self, rm):
        config = TierConfig.get(CapitalTier.TIER2)  # max_per_category_pct=0.35
        signal = make_signal(metadata={"category": "crypto"})
        positions = [make_position(category="crypto", cost_basis=3.0)]
        # 3.0 < 100.0 * 0.35 = 35.0
        result = rm._check_category_exposure(signal, positions, 100.0, config)
        assert result.passed is True

    def test_exceeds_limit(self, rm):
        config = TierConfig.get(CapitalTier.TIER2)  # max_per_category_pct=0.35
        signal = make_signal(metadata={"category": "crypto"})
        positions = [make_position(category="crypto", cost_basis=4.0)]
        # 4.0 >= 10.0 * 0.35 = 3.5
        result = rm._check_category_exposure(signal, positions, 10.0, config)
        assert result.passed is False


# ---------------------------------------------------------------------------
# _check_min_edge
# ---------------------------------------------------------------------------


class TestCheckMinEdge:
    def test_above_threshold(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # min_edge_pct=0.01
        signal = make_signal(edge=0.06)
        assert rm._check_min_edge(signal, config).passed is True

    def test_below_threshold(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # min_edge_pct=0.01
        signal = make_signal(edge=0.005)
        result = rm._check_min_edge(signal, config)
        assert result.passed is False

    def test_edge_multiplier_tightens_threshold(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # min_edge_pct=0.01
        # Edge of 0.014 normally passes (> 0.01), but with 1.5x multiplier
        # required edge = 0.015, so 0.014 should fail
        signal = make_signal(edge=0.014)
        result = rm._check_min_edge(signal, config, edge_multiplier=1.5)
        assert result.passed is False

    def test_edge_multiplier_relaxes_threshold(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # min_edge_pct=0.01
        # With 0.8x multiplier, required edge = 0.008
        signal = make_signal(edge=0.009)
        result = rm._check_min_edge(signal, config, edge_multiplier=0.8)
        assert result.passed is True

    def test_edge_multiplier_default_is_one(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # min_edge_pct=0.01
        signal = make_signal(edge=0.011)
        # Without multiplier (default 1.0), should pass
        result = rm._check_min_edge(signal, config)
        assert result.passed is True

    def test_edge_multiplier_in_reason_message(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # min_edge_pct=0.01
        signal = make_signal(edge=0.005)
        result = rm._check_min_edge(signal, config, edge_multiplier=1.5)
        assert result.passed is False
        assert "1.5" in result.reason


# ---------------------------------------------------------------------------
# _check_min_win_prob
# ---------------------------------------------------------------------------


class TestCheckMinWinProb:
    def test_above_threshold(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # min_win_prob=0.65
        signal = make_signal(estimated_prob=0.92)
        assert rm._check_min_win_prob(signal, config).passed is True

    def test_below_threshold(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # min_win_prob=0.55
        signal = make_signal(estimated_prob=0.50)
        result = rm._check_min_win_prob(signal, config)
        assert result.passed is False


# ---------------------------------------------------------------------------
# _calculate_size
# ---------------------------------------------------------------------------


class TestCalculateSize:
    def test_valid_signal_positive_size(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)
        signal = make_signal(estimated_prob=0.92, market_price=0.87)
        size = rm._calculate_size(signal, 100.0, config)
        assert size > 0

    def test_tiny_bankroll_returns_zero(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)
        signal = make_signal(estimated_prob=0.92, market_price=0.87)
        size = rm._calculate_size(signal, 1.0, config)
        # bankroll=1.0, 5-share min=$4.35 exceeds
        # max_per_position (40% of $1 = $0.40) → returns 0
        assert size == 0.0

    def test_kelly_produces_varied_sizes(self, rm):
        """Kelly naturally produces varied sizes with 5-share minimum floor."""
        config = TierConfig.get(CapitalTier.TIER2)
        # Strong signal: higher Kelly → bigger trade
        strong = make_signal(estimated_prob=0.95, market_price=0.86)
        big_size = rm._calculate_size(strong, 30.0, config)
        # Weaker signal: lower Kelly → smaller trade (may hit 5-share floor)
        weak = make_signal(estimated_prob=0.80, market_price=0.72)
        small_size = rm._calculate_size(weak, 30.0, config)
        # Both produce positive sizes; strong signal gets bigger
        assert big_size >= small_size
        assert big_size > 1.0  # at least $1
        assert small_size > 0  # still trades


# ---------------------------------------------------------------------------
# evaluate_signal (async)
# ---------------------------------------------------------------------------


class TestEvaluateSignal:
    async def test_all_checks_pass(self, rm):
        signal = make_signal(edge=0.06, estimated_prob=0.92, market_price=0.86)
        approved, size, reason = await rm.evaluate_signal(
            signal, bankroll=100.0, open_positions=[], tier=CapitalTier.TIER1
        )
        assert approved is True
        assert size > 0
        assert reason == "approved"

    async def test_paused_blocks(self, rm):
        rm.pause()
        signal = make_signal()
        approved, size, reason = await rm.evaluate_signal(
            signal, bankroll=100.0, open_positions=[], tier=CapitalTier.TIER1
        )
        assert approved is False
        assert size == 0.0
        assert "paused" in reason.lower()

    async def test_low_edge_blocks(self, rm):
        signal = make_signal(edge=0.005, estimated_prob=0.88, market_price=0.875)
        approved, size, reason = await rm.evaluate_signal(
            signal, bankroll=100.0, open_positions=[], tier=CapitalTier.TIER1
        )
        assert approved is False
        assert "edge" in reason.lower()

    async def test_duplicate_position_blocks(self, rm):
        signal = make_signal()  # market_id="mkt1"
        # mkt1 in positions matches signal → duplicate check fires
        positions = [make_position(market_id=f"mkt{i}", cost_basis=1.0) for i in range(3)]
        approved, size, reason = await rm.evaluate_signal(
            signal, bankroll=100.0, open_positions=positions, tier=CapitalTier.TIER1
        )
        assert approved is False
        assert "position" in reason.lower()

    async def test_max_positions_with_pending_blocks(self, rm):
        signal = TradeSignal(
            strategy="time_decay",
            market_id="mkt_new",
            token_id="token_new",
            side=OrderSide.BUY,
            estimated_prob=0.92,
            market_price=0.86,
            edge=0.06,
            size_usd=0.0,
            confidence=0.85,
            metadata={},
        )
        # Tier 1 max_positions=6: 5 open + 1 pending = 6 → rejected
        positions = [make_position(market_id=f"mkt{i}", cost_basis=1.0) for i in range(5)]
        approved, size, reason = await rm.evaluate_signal(
            signal, bankroll=100.0, open_positions=positions,
            tier=CapitalTier.TIER1, pending_count=1,
        )
        assert approved is False
        assert "position" in reason.lower()

    async def test_edge_multiplier_applied(self, rm):
        """Edge multiplier from learner should tighten/relax edge threshold."""
        signal = make_signal(edge=0.015, estimated_prob=0.92, market_price=0.86)
        # With edge_multiplier=1.5, required edge = 0.01*1.5 = 0.015
        # edge of 0.015 == adjusted threshold → should just pass (not strictly <)
        approved, size, reason = await rm.evaluate_signal(
            signal, bankroll=100.0, open_positions=[], tier=CapitalTier.TIER1,
            edge_multiplier=1.5,
        )
        assert approved is True


# ---------------------------------------------------------------------------
# get_risk_metrics
# ---------------------------------------------------------------------------


class TestGetRiskMetrics:
    def test_correct_keys(self, rm):
        metrics = rm.get_risk_metrics(10.0)
        expected_keys = {
            "tier",
            "bankroll",
            "peak_equity",
            "current_drawdown_pct",
            "max_drawdown_limit_pct",
            "daily_pnl",
            "daily_loss_limit_pct",
            "max_positions",
            "is_paused",
        }
        assert set(metrics.keys()) == expected_keys

    def test_bankroll_reflected(self, rm):
        metrics = rm.get_risk_metrics(50.0)
        assert metrics["bankroll"] == 50.0
        assert metrics["tier"] == "tier2"  # 50 → TIER2
