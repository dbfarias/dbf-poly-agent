"""Tests for ArbitrageStrategy."""

from unittest.mock import MagicMock

import pytest

from bot.agent.strategies.arbitrage import ArbitrageStrategy
from bot.polymarket.types import GammaMarket


@pytest.fixture
def strategy():
    return ArbitrageStrategy(
        clob_client=MagicMock(),
        gamma_client=MagicMock(),
        cache=MagicMock(),
    )


def _make_arb_market(
    yes_price: float = 0.45,
    no_price: float = 0.45,
    volume: float = 5000.0,
    token_ids: str = '["ty","tn"]',
) -> GammaMarket:
    return GammaMarket(
        id="arb_mkt",
        question="Arb test?",
        outcomePrices=f"[{yes_price},{no_price}]",
        clobTokenIds=token_ids,
        outcomes=["Yes", "No"],
        volume=volume,
        liquidity=2000.0,
    )


# ---------------------------------------------------------------------------
# _check_yes_no_arb
# ---------------------------------------------------------------------------


class TestCheckYesNoArb:
    def test_detects_arb_opportunity(self, strategy):
        # total = 0.45 + 0.45 = 0.90 < 0.99 → arb!
        market = _make_arb_market(yes_price=0.45, no_price=0.45)
        signal = strategy._check_yes_no_arb(market)
        assert signal is not None
        assert signal.edge == pytest.approx(0.10)

    def test_no_gap_returns_none(self, strategy):
        # total = 0.50 + 0.50 = 1.00 → no arb
        market = _make_arb_market(yes_price=0.50, no_price=0.50)
        signal = strategy._check_yes_no_arb(market)
        assert signal is None

    def test_no_token_ids_returns_none(self, strategy):
        market = _make_arb_market(token_ids="[]")
        signal = strategy._check_yes_no_arb(market)
        assert signal is None

    def test_buys_cheaper_side_yes(self, strategy):
        market = _make_arb_market(yes_price=0.40, no_price=0.50)
        signal = strategy._check_yes_no_arb(market)
        assert signal is not None
        assert signal.outcome == "Yes"
        assert signal.token_id == "ty"

    def test_buys_cheaper_side_no(self, strategy):
        market = _make_arb_market(yes_price=0.50, no_price=0.40)
        signal = strategy._check_yes_no_arb(market)
        assert signal is not None
        assert signal.outcome == "No"
        assert signal.token_id == "tn"

    def test_edge_calculation(self, strategy):
        market = _make_arb_market(yes_price=0.45, no_price=0.45)
        signal = strategy._check_yes_no_arb(market)
        # edge = 1.0 - 0.90 = 0.10
        assert signal.edge == pytest.approx(0.10)

    def test_estimated_prob_formula(self, strategy):
        market = _make_arb_market(yes_price=0.40, no_price=0.50)
        signal = strategy._check_yes_no_arb(market)
        # Buys YES at 0.40. edge = 1.0 - 0.90 = 0.10
        # estimated_prob = 1.0 - 0.40 + 0.10/2 = 0.65
        assert signal.estimated_prob == pytest.approx(0.65)

    def test_confidence_is_095(self, strategy):
        market = _make_arb_market(yes_price=0.45, no_price=0.45)
        signal = strategy._check_yes_no_arb(market)
        assert signal.confidence == pytest.approx(0.95)

    def test_metadata_has_arb_type(self, strategy):
        market = _make_arb_market(yes_price=0.45, no_price=0.45)
        signal = strategy._check_yes_no_arb(market)
        assert signal.metadata["arb_type"] == "yes_no"
        assert "total_price" in signal.metadata


# ---------------------------------------------------------------------------
# should_exit
# ---------------------------------------------------------------------------


class TestShouldExit:
    async def test_always_returns_false(self, strategy):
        assert await strategy.should_exit("mkt1", 0.10) is False
        assert await strategy.should_exit("mkt1", 0.99) is False
