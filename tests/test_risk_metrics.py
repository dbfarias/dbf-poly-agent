"""Tests for bot/utils/risk_metrics.py — pure risk metric functions."""


import pytest

from bot.utils.risk_metrics import (
    compute_vpin,
    mispricing_zscore,
    parametric_var,
    profit_factor,
    ruin_probability,
)


class TestParametricVar:
    def test_basic_var(self):
        # Stable positive returns → VaR near mean
        returns = [0.01, 0.02, 0.01, 0.015, 0.01, 0.02, 0.01]
        var = parametric_var(returns, confidence=0.95)
        assert var < 0.03  # Should be some small value

    def test_volatile_returns(self):
        # Mix of gains and losses
        returns = [0.05, -0.10, 0.03, -0.08, 0.02, -0.12, 0.04, -0.06]
        var = parametric_var(returns, confidence=0.95)
        assert var < 0  # Should be negative (loss)

    def test_insufficient_data(self):
        assert parametric_var([0.01]) == 0.0
        assert parametric_var([]) == 0.0

    def test_zero_volatility(self):
        returns = [0.01, 0.01, 0.01, 0.01]
        var = parametric_var(returns, confidence=0.95)
        # With zero std, VaR = mean
        assert abs(var - 0.01) < 0.001

    def test_confidence_levels(self):
        returns = [0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, -0.02]
        var_90 = parametric_var(returns, confidence=0.90)
        var_95 = parametric_var(returns, confidence=0.95)
        var_99 = parametric_var(returns, confidence=0.99)
        # Higher confidence → worse VaR (more negative)
        assert var_99 <= var_95 <= var_90


class TestComputeVpin:
    def test_balanced_flow(self):
        # Equal buy and sell → VPIN = 0
        assert compute_vpin(100.0, 100.0) == 0.0

    def test_all_buy(self):
        assert compute_vpin(100.0, 0.0) == 1.0

    def test_all_sell(self):
        assert compute_vpin(0.0, 100.0) == 1.0

    def test_moderate_imbalance(self):
        vpin = compute_vpin(70.0, 30.0)
        assert 0.3 < vpin < 0.5

    def test_zero_volume(self):
        assert compute_vpin(0.0, 0.0) == 0.0


class TestMispricingZscore:
    def test_underpriced(self):
        # Model says 70%, market at 60%, σ=5%
        z = mispricing_zscore(0.70, 0.60, 0.05)
        assert z == pytest.approx(2.0, abs=0.01)

    def test_overpriced(self):
        z = mispricing_zscore(0.60, 0.70, 0.05)
        assert z == pytest.approx(-2.0, abs=0.01)

    def test_fair_price(self):
        z = mispricing_zscore(0.65, 0.65, 0.05)
        assert z == pytest.approx(0.0)

    def test_zero_std(self):
        assert mispricing_zscore(0.70, 0.60, 0.0) == 0.0


class TestProfitFactor:
    def test_profitable(self):
        pf = profit_factor(150.0, 100.0)
        assert pf == 1.5

    def test_losing(self):
        pf = profit_factor(50.0, 100.0)
        assert pf == 0.5

    def test_no_losses(self):
        pf = profit_factor(100.0, 0.0)
        assert pf == 10.0  # Capped

    def test_no_trades(self):
        assert profit_factor(0.0, 0.0) == 0.0

    def test_breakeven(self):
        assert profit_factor(100.0, 100.0) == 1.0


class TestRuinProbability:
    def test_losing_strategy(self):
        # 40% win rate → high ruin probability
        p = ruin_probability(0.40, 10.0, 1.0)
        assert p > 0.5

    def test_winning_strategy(self):
        # 60% win rate → low ruin probability
        p = ruin_probability(0.60, 10.0, 1.0)
        assert p < 0.5

    def test_edge_cases(self):
        assert ruin_probability(0.0, 10.0, 1.0) == 1.0
        assert ruin_probability(1.0, 10.0, 1.0) == 0.0
        assert ruin_probability(0.5, 10.0, 0.0) == 1.0

    def test_large_bankroll_small_bet(self):
        # Very favorable: small bets relative to bankroll
        p = ruin_probability(0.55, 100.0, 1.0)
        assert p < 0.01
