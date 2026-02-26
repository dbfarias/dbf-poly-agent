"""Tests for TimeDecayStrategy."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from bot.agent.strategies.time_decay import TimeDecayStrategy
from bot.polymarket.types import GammaMarket


@pytest.fixture
def strategy():
    return TimeDecayStrategy(
        clob_client=MagicMock(),
        gamma_client=MagicMock(),
        cache=MagicMock(),
    )


def _make_market(
    hours_to_resolution: float = 100.0,
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
# _estimate_probability
# ---------------------------------------------------------------------------


class TestEstimateProbability:
    def test_high_price_near_resolution(self, strategy):
        # price=0.95, hours_left=24 → base=0.95, time_factor≈0.039, near_certainty=0.03
        prob = strategy._estimate_probability(0.95, 24.0)
        assert prob > 0.95
        assert prob <= 0.99

    def test_far_from_resolution(self, strategy):
        # 700 hours → time_factor ≈ (1 - 700/720) * 0.04 ≈ 0.001
        prob = strategy._estimate_probability(0.90, 700.0)
        assert prob < 0.93

    def test_near_certainty_bonus(self, strategy):
        # price>=0.95 and hours<=72 gives +0.03 bonus
        prob_bonus = strategy._estimate_probability(0.95, 50.0)
        prob_no_bonus = strategy._estimate_probability(0.95, 500.0)
        assert prob_bonus > prob_no_bonus

    def test_capped_at_099(self, strategy):
        prob = strategy._estimate_probability(0.97, 1.0)
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

    def test_hours_le_48_adds_008(self, strategy):
        conf = strategy._calculate_confidence(0.80, 40.0)
        assert conf == pytest.approx(0.83)

    def test_hours_le_168_adds_004(self, strategy):
        conf = strategy._calculate_confidence(0.80, 100.0)
        assert conf == pytest.approx(0.79)


# ---------------------------------------------------------------------------
# should_exit
# ---------------------------------------------------------------------------


class TestShouldExit:
    async def test_below_070_triggers_exit(self, strategy):
        assert await strategy.should_exit("mkt1", 0.65) is True

    async def test_above_070_no_exit(self, strategy):
        assert await strategy.should_exit("mkt1", 0.75) is False


# ---------------------------------------------------------------------------
# _evaluate_market
# ---------------------------------------------------------------------------


class TestEvaluateMarket:
    async def test_valid_market_returns_signal(self, strategy):
        # price=0.92, hours=100 → should find edge
        market = _make_market(hours_to_resolution=100.0, price=0.92)
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now)
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
        signal = await strategy._evaluate_market(market, now)
        assert signal is None

    async def test_expired_market_returns_none(self, strategy):
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        market = _make_market(end_date_iso=past.strftime("%Y-%m-%dT%H:%M:%SZ"))
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now)
        assert signal is None

    async def test_too_far_market_returns_none(self, strategy):
        # 800 hours > 720 limit
        market = _make_market(hours_to_resolution=800.0)
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now)
        assert signal is None

    async def test_price_below_min_returns_none(self, strategy):
        market = _make_market(price=0.50)
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now)
        assert signal is None
