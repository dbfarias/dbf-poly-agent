"""Tests for RiskManager — the most critical bot module."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timezone

import pytest

from bot.agent.risk_manager import RiskCheckResult, RiskManager
from bot.config import CapitalTier, TierConfig, settings
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
        rm.update_peak_equity(20.0)
        assert rm._peak_equity == 20.0

    def test_equal_no_change(self, rm):
        rm.update_peak_equity(10.0)
        assert rm._peak_equity == 10.0

    def test_lower_no_change(self, rm):
        rm.update_peak_equity(3.0)
        assert rm._peak_equity == 10.0


# ---------------------------------------------------------------------------
# update_daily_pnl
# ---------------------------------------------------------------------------


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
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert rm._daily_pnl_date == today
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
        rm._daily_pnl = -0.5  # -0.5 vs limit -1.0 (10% of 10)
        assert rm._check_daily_loss(10.0, config).passed is True

    def test_exceeds_limit(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # daily_loss_limit_pct=0.10
        rm._daily_pnl = -2.0  # exceeds -1.0 (10% of 10)
        result = rm._check_daily_loss(10.0, config)
        assert result.passed is False

    def test_exact_boundary_passes(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)
        # Limit is -1.0 (10% of 10). At exactly -1.0: -1.0 < -1.0 is False → passes
        rm._daily_pnl = -1.0
        assert rm._check_daily_loss(10.0, config).passed is True


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
        config = TierConfig.get(CapitalTier.TIER1)  # max_positions=8
        assert rm._check_max_positions([], config).passed is True

    def test_at_limit_fails(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # max_positions=3
        positions = [make_position(market_id=f"mkt{i}") for i in range(3)]
        result = rm._check_max_positions(positions, config)
        assert result.passed is False

    def test_under_tier1_limit_passes(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # max_positions=3
        positions = [make_position(market_id=f"mkt{i}") for i in range(2)]
        assert rm._check_max_positions(positions, config).passed is True

    def test_tier3_higher_limit(self, rm):
        config = TierConfig.get(CapitalTier.TIER3)  # max_positions=15
        positions = [make_position(market_id=f"mkt{i}") for i in range(10)]
        assert rm._check_max_positions(positions, config).passed is True

    def test_pending_count_added_to_total(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # max_positions=3
        positions = [make_position(market_id=f"mkt{i}") for i in range(2)]
        # 2 open + 1 pending = 3 → at limit → fails
        result = rm._check_max_positions(positions, config, pending_count=1)
        assert result.passed is False
        assert "pending" in result.reason.lower()

    def test_pending_count_under_limit_passes(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)  # max_positions=3
        positions = [make_position(market_id=f"mkt{i}") for i in range(1)]
        # 1 open + 1 pending = 2 → under 3 → passes
        result = rm._check_max_positions(positions, config, pending_count=1)
        assert result.passed is True


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
        # 7.0 deployed out of 10.0 → 70% < 80% max_deployed_pct → passes
        positions = [
            make_position(market_id="mkt1", cost_basis=3.5),
            make_position(market_id="mkt2", cost_basis=3.5),
        ]
        result = rm._check_total_deployed(positions, 10.0, config)
        assert result.passed is True

    def test_over_deployed_limit_fails(self, rm):
        config = TierConfig.get(CapitalTier.TIER1)
        # 9.0 deployed out of 10.0 → 90% > 80% max_deployed_pct → fails
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
    def test_no_category_passes(self, rm):
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
        config = TierConfig.get(CapitalTier.TIER1)  # min_win_prob=0.65
        signal = make_signal(estimated_prob=0.60)
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
        # bankroll=1.0, kelly=$0.08, 1-share min=$0.87 exceeds
        # max_per_position (55% of $1 = $0.55) → returns 0
        assert size == 0.0

    def test_kelly_produces_varied_sizes(self, rm):
        """Kelly naturally produces $1-$5 range without 5-share bump."""
        config = TierConfig.get(CapitalTier.TIER2)
        # Strong signal: higher Kelly → bigger trade
        strong = make_signal(estimated_prob=0.95, market_price=0.86)
        big_size = rm._calculate_size(strong, 30.0, config)
        # Weaker signal: lower Kelly → smaller trade
        weak = make_signal(estimated_prob=0.80, market_price=0.72)
        small_size = rm._calculate_size(weak, 30.0, config)
        # Sizes should differ meaningfully
        assert big_size > small_size
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
        # Tier 1 max_positions=8: 6 open + 2 pending = 8 → rejected
        positions = [make_position(market_id=f"mkt{i}", cost_basis=1.0) for i in range(6)]
        approved, size, reason = await rm.evaluate_signal(
            signal, bankroll=100.0, open_positions=positions,
            tier=CapitalTier.TIER1, pending_count=2,
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
