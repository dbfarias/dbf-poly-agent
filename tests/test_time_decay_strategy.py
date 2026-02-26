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
    hours_to_resolution: float = 6.0,
    price: float = 0.92,
    volume: float = 10000.0,
    liquidity: float = 2000.0,
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
        volume=volume,
        liquidity=liquidity,
    )


# ---------------------------------------------------------------------------
# _estimate_probability
# ---------------------------------------------------------------------------


class TestEstimateProbability:
    def test_high_price_near_resolution(self, strategy):
        # price=0.95, hours_left=2, volume=50000
        prob = strategy._estimate_probability(0.95, 2.0, 50000.0)
        # base=0.95, time_factor ~0.019, volume_factor=0.01 → ~0.979
        assert prob > 0.95
        assert prob <= 0.99

    def test_far_from_resolution(self, strategy):
        # 47 hours → time_factor ≈ (1 - 47/48) * 0.02 ≈ 0.0004
        prob = strategy._estimate_probability(0.90, 47.0, 10000.0)
        # Almost no boost from time
        assert prob < 0.93

    def test_volume_boost(self, strategy):
        low_vol = strategy._estimate_probability(0.90, 24.0, 1000.0)
        high_vol = strategy._estimate_probability(0.90, 24.0, 100000.0)
        assert high_vol > low_vol

    def test_capped_at_099(self, strategy):
        prob = strategy._estimate_probability(0.97, 1.0, 200000.0)
        assert prob == pytest.approx(0.99)


# ---------------------------------------------------------------------------
# _calculate_confidence
# ---------------------------------------------------------------------------


class TestCalculateConfidence:
    def test_base_confidence(self, strategy):
        # Low price (0.80), far resolution (48h), low volume → base only
        conf = strategy._calculate_confidence(0.80, 48.0, 1000.0, 500.0)
        assert conf == pytest.approx(0.80)

    def test_price_ge_095_adds_010(self, strategy):
        conf = strategy._calculate_confidence(0.96, 48.0, 1000.0, 500.0)
        assert conf == pytest.approx(0.90)

    def test_price_ge_090_adds_005(self, strategy):
        conf = strategy._calculate_confidence(0.91, 48.0, 1000.0, 500.0)
        assert conf == pytest.approx(0.85)

    def test_hours_le_12_adds_005(self, strategy):
        conf = strategy._calculate_confidence(0.80, 10.0, 1000.0, 500.0)
        assert conf == pytest.approx(0.85)

    def test_volume_ge_50k_adds_005(self, strategy):
        conf = strategy._calculate_confidence(0.80, 48.0, 60000.0, 500.0)
        assert conf == pytest.approx(0.85)


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
        # price=0.90, hours=3, volume=50k → prob≈0.929 → edge≈0.029 > 0.02
        market = _make_market(hours_to_resolution=3.0, price=0.90, volume=50000.0)
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now)
        assert signal is not None
        assert signal.strategy == "time_decay"
        assert signal.market_id == "mkt1"

    async def test_no_end_date_returns_none(self, strategy):
        market = _make_market()
        market = GammaMarket(
            id="mkt1",
            question="Test?",
            endDateIso="",
            outcomes=["Yes", "No"],
            outcomePrices="[0.92,0.08]",
            clobTokenIds='["t1","t2"]',
            volume=10000.0,
            liquidity=2000.0,
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

    async def test_low_volume_returns_none(self, strategy):
        market = _make_market(volume=100.0)
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now)
        assert signal is None

    async def test_price_below_min_returns_none(self, strategy):
        market = _make_market(price=0.50)
        now = datetime.now(timezone.utc)
        signal = await strategy._evaluate_market(market, now)
        assert signal is None
