"""Tests for TimeDecayStrategy."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from bot.agent.strategies.time_decay import (
    HOURS_IMMEDIATE,
    HOURS_MEDIUM,
    HOURS_SHORT,
    URGENCY_CAP_MAX,
    URGENCY_CAP_SHORT,
    TimeDecayStrategy,
    _max_hours_for_urgency,
)
from bot.polymarket.types import GammaMarket


@pytest.fixture
def strategy():
    return TimeDecayStrategy(
        clob_client=MagicMock(),
        gamma_client=MagicMock(),
        cache=MagicMock(),
    )


def _make_market(
    hours_to_resolution: float = 48.0,
    price: float = 0.92,
    end_date_iso: str | None = None,
    outcomes: list[str] | None = None,
    outcome_prices: str | None = None,
    clob_token_ids: str | None = None,
) -> GammaMarket:
    if end_date_iso is None:
        end = datetime.now(timezone.utc) + timedelta(hours=hours_to_resolution)
        end_date_iso = end.strftime("%Y-%m-%dT%H:%M:%SZ")
    if outcomes is None:
        outcomes = ["Yes", "No"]
    if outcome_prices is None:
        no_price = round(1.0 - price, 2)
        outcome_prices = f"[{price},{no_price}]"
    if clob_token_ids is None:
        clob_token_ids = '["token_yes","token_no"]'

    return GammaMarket(
        id="mkt1",
        question="Will X happen?",
        endDateIso=end_date_iso,
        outcomes=outcomes,
        outcomePrices=outcome_prices,
        clobTokenIds=clob_token_ids,
    )


# ---------------------------------------------------------------------------
# _max_hours_for_urgency
# ---------------------------------------------------------------------------


class TestMaxHoursForUrgency:
    def test_ahead_of_target(self):
        assert _max_hours_for_urgency(0.7) == HOURS_IMMEDIATE  # 24h

    def test_on_pace(self):
        assert _max_hours_for_urgency(1.0) == pytest.approx(URGENCY_CAP_SHORT)  # 72h

    def test_behind_target(self):
        assert _max_hours_for_urgency(1.3) == pytest.approx(URGENCY_CAP_MAX)  # 168h

    def test_very_behind(self):
        assert _max_hours_for_urgency(1.5) == URGENCY_CAP_MAX

    def test_very_ahead(self):
        assert _max_hours_for_urgency(0.5) == HOURS_IMMEDIATE

    def test_interpolation_between_breakpoints(self):
        hours = _max_hours_for_urgency(0.85)
        assert HOURS_IMMEDIATE < hours < URGENCY_CAP_SHORT

    def test_monotonically_increasing(self):
        prev = 0.0
        for u in [0.5, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5]:
            h = round(_max_hours_for_urgency(u), 6)
            assert h >= prev, f"urgency={u}: {h} < {prev}"
            prev = h


# ---------------------------------------------------------------------------
# _estimate_probability
# ---------------------------------------------------------------------------


class TestEstimateProbability:
    def test_high_price_near_resolution(self, strategy):
        # price=0.95, hours_left=24 → base + conservative time_factor
        prob = strategy._estimate_probability(0.95, 24.0)
        assert prob > 0.95
        assert prob <= 0.99

    def test_far_from_resolution(self, strategy):
        # 160 hours → time_factor tiny (~0.001)
        prob = strategy._estimate_probability(0.90, 160.0)
        assert prob < 0.92  # No phantom near_certainty bonus anymore

    def test_time_factor_increases_near_resolution(self, strategy):
        # Closer to resolution → higher time_factor → higher estimated prob
        prob_near = strategy._estimate_probability(0.95, 20.0)
        prob_far = strategy._estimate_probability(0.95, 150.0)
        assert prob_near > prob_far

    def test_capped_at_099(self, strategy):
        # 0.98 + time_factor at 1h ≈ 0.98 + 0.0199 > 0.99 → capped
        prob = strategy._estimate_probability(0.98, 1.0)
        assert prob == pytest.approx(0.99)


# ---------------------------------------------------------------------------
# _calculate_confidence
# ---------------------------------------------------------------------------


class TestCalculateConfidence:
    def test_base_confidence(self, strategy):
        # Low price (0.80), far resolution (720h) → base only
        conf = strategy._calculate_confidence(0.80, 720.0)
        assert conf == pytest.approx(0.75)

    def test_price_ge_095_adds_010(self, strategy):
        conf = strategy._calculate_confidence(0.96, 720.0)
        assert conf == pytest.approx(0.85)

    def test_price_ge_090_adds_003(self, strategy):
        conf = strategy._calculate_confidence(0.91, 720.0)
        assert conf == pytest.approx(0.78)

    def test_hours_le_12_adds_012(self, strategy):
        conf = strategy._calculate_confidence(0.80, 10.0)
        assert conf == pytest.approx(0.87)

    def test_hours_le_24_adds_010(self, strategy):
        conf = strategy._calculate_confidence(0.80, 20.0)
        assert conf == pytest.approx(0.85)

    def test_hours_le_48_adds_006(self, strategy):
        conf = strategy._calculate_confidence(0.80, 40.0)
        assert conf == pytest.approx(0.81)

    def test_hours_le_72_adds_003(self, strategy):
        conf = strategy._calculate_confidence(0.80, 60.0)
        assert conf == pytest.approx(0.78)


# ---------------------------------------------------------------------------
# should_exit
# ---------------------------------------------------------------------------


class TestShouldExit:
    async def test_below_070_no_exit_universal_stop_handles(self, strategy):
        """Low price alone doesn't trigger exit — universal stop-loss handles it."""
        assert await strategy.should_exit("mkt1", 0.65) is False

    async def test_above_070_no_exit(self, strategy):
        assert await strategy.should_exit("mkt1", 0.75) is False

    async def test_take_profit_above_threshold_after_hold(self, strategy):
        """1.5%+ profit after 4h+ hold → exit."""
        created = datetime.now(timezone.utc) - timedelta(hours=5)
        result = await strategy.should_exit(
            "mkt1", 0.93, avg_price=0.90, created_at=created,
        )
        assert result is True

    async def test_take_profit_too_fresh(self, strategy):
        """1.5%+ profit but only 1h hold → no exit."""
        created = datetime.now(timezone.utc) - timedelta(hours=1)
        result = await strategy.should_exit(
            "mkt1", 0.93, avg_price=0.90, created_at=created,
        )
        assert result is False

    async def test_take_profit_insufficient_gain(self, strategy):
        """0.5% profit after 5h → no exit (below 1.5% threshold)."""
        created = datetime.now(timezone.utc) - timedelta(hours=5)
        result = await strategy.should_exit(
            "mkt1", 0.9045, avg_price=0.90, created_at=created,
        )
        assert result is False


# ---------------------------------------------------------------------------
# _evaluate_market
# ---------------------------------------------------------------------------


class TestEvaluateMarket:
    async def test_valid_market_returns_signal(self, strategy):
        # price=0.92, hours=12, max_hours=72 → edge ~1.86% passes 1.5% MIN_EDGE
        market = _make_market(hours_to_resolution=12.0, price=0.92)
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now, HOURS_SHORT)
        assert signal is not None
        assert signal.strategy == "time_decay"
        assert signal.market_id == "mkt1"

    async def test_no_end_date_returns_none(self, strategy):
        market = GammaMarket(
            id="mkt1",
            question="Test?",
            endDateIso="",
            outcomes=["Yes", "No"],
            outcomePrices="[0.92,0.08]",
            clobTokenIds='["t1","t2"]',
        )
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now, HOURS_MEDIUM)
        assert signal is None

    async def test_expired_market_returns_none(self, strategy):
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        market = _make_market(end_date_iso=past.strftime("%Y-%m-%dT%H:%M:%SZ"))
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now, HOURS_MEDIUM)
        assert signal is None

    async def test_too_far_market_returns_none(self, strategy):
        # 200h > 168h (HOURS_MEDIUM)
        market = _make_market(hours_to_resolution=200.0)
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now, HOURS_MEDIUM)
        assert signal is None

    async def test_beyond_dynamic_max_returns_none(self, strategy):
        # 50h market, but max_hours=24 (urgent=ahead) → rejected
        market = _make_market(hours_to_resolution=50.0, price=0.92)
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now, HOURS_IMMEDIATE)
        assert signal is None

    async def test_within_dynamic_max_returns_signal(self, strategy):
        # 20h market, max_hours=24 (ahead of target) → accepted
        market = _make_market(hours_to_resolution=20.0, price=0.92)
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now, HOURS_IMMEDIATE)
        assert signal is not None

    async def test_price_below_min_returns_none(self, strategy):
        market = _make_market(price=0.50)
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now, HOURS_MEDIUM)
        assert signal is None


# ---------------------------------------------------------------------------
# _score_signal
# ---------------------------------------------------------------------------


class TestScoreSignal:
    def test_shorter_market_scores_higher(self, strategy):
        short = MagicMock(edge=0.02, metadata={"hours_to_resolution": 12.0})
        long = MagicMock(edge=0.02, metadata={"hours_to_resolution": 120.0})
        assert strategy._score_signal(short) > strategy._score_signal(long)

    def test_same_time_higher_edge_wins(self, strategy):
        high_edge = MagicMock(edge=0.04, metadata={"hours_to_resolution": 48.0})
        low_edge = MagicMock(edge=0.01, metadata={"hours_to_resolution": 48.0})
        assert strategy._score_signal(high_edge) > strategy._score_signal(low_edge)

    def test_very_short_beats_high_edge_long(self, strategy):
        # 6h market with 2% edge should beat 5-day market with 3% edge
        short = MagicMock(edge=0.02, metadata={"hours_to_resolution": 6.0})
        long = MagicMock(edge=0.03, metadata={"hours_to_resolution": 120.0})
        assert strategy._score_signal(short) > strategy._score_signal(long)


# ---------------------------------------------------------------------------
# adjust_params with urgency
# ---------------------------------------------------------------------------


class TestAdjustParams:
    def test_urgency_stored(self, strategy):
        strategy.adjust_params({"urgency_multiplier": 1.3})
        assert strategy._urgency == 1.3

    def test_default_urgency(self, strategy):
        strategy.adjust_params({})
        assert strategy._urgency == 1.0

    def test_calibration_adjusts_max_price(self, strategy):
        strategy.adjust_params({"calibration": {"95-99": 0.5}})
        assert strategy._max_price == 0.96

    def test_good_calibration_keeps_max_price(self, strategy):
        strategy.adjust_params({"calibration": {"95-99": 0.9}})
        assert strategy._max_price == 0.97


# ---------------------------------------------------------------------------
# _dynamic_max_price
# ---------------------------------------------------------------------------


class TestDynamicMaxPrice:
    def test_12h_allows_099(self, strategy):
        assert strategy._dynamic_max_price(10.0) == 0.99

    def test_24h_allows_098(self, strategy):
        assert strategy._dynamic_max_price(20.0) == 0.98

    def test_48h_allows_097(self, strategy):
        assert strategy._dynamic_max_price(40.0) == 0.97

    def test_72h_allows_096(self, strategy):
        assert strategy._dynamic_max_price(60.0) == 0.96

    def test_120h_keeps_096(self, strategy):
        assert strategy._dynamic_max_price(120.0) == 0.96

    async def test_high_price_near_resolution_accepted(self, strategy):
        """Market at $0.95 resolving in 20h should be accepted."""
        market = _make_market(hours_to_resolution=20.0, price=0.95)
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now, HOURS_SHORT)
        assert signal is not None
        assert signal.market_price == 0.95
