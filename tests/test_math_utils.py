"""Tests for mathematical utility functions."""

from bot.utils.math_utils import (
    current_drawdown,
    edge,
    expected_value,
    kelly_criterion,
    max_drawdown,
    position_size_usd,
    quarter_kelly,
    sharpe_ratio,
    sortino_ratio,
)


def test_kelly_criterion_positive_edge():
    # Win prob 0.90, market price 0.85 → positive edge
    f = kelly_criterion(0.90, 0.85)
    assert f > 0
    assert f < 1


def test_kelly_criterion_no_edge():
    # Win prob == market price → no bet
    assert kelly_criterion(0.85, 0.85) == 0.0


def test_kelly_criterion_negative_edge():
    # Win prob < market price → no bet
    assert kelly_criterion(0.80, 0.85) == 0.0


def test_kelly_criterion_boundary():
    assert kelly_criterion(0.5, 0.0) == 0.0  # price=0 → division
    assert kelly_criterion(0.5, 1.0) == 0.0  # price=1 → division


def test_quarter_kelly():
    full = kelly_criterion(0.90, 0.85)
    quarter = quarter_kelly(0.90, 0.85)
    assert abs(quarter - 0.25 * full) < 1e-10


def test_expected_value_positive():
    ev = expected_value(0.95, 0.90, 10.0)
    assert ev > 0  # High prob, good price → positive EV


def test_expected_value_negative():
    ev = expected_value(0.50, 0.90, 10.0)
    assert ev < 0  # Fair coin at 0.90 → negative EV


def test_edge_calculation():
    assert abs(edge(0.90, 0.85) - 0.05) < 1e-10
    assert edge(0.85, 0.85) == 0.0


def test_sharpe_ratio_no_data():
    assert sharpe_ratio([]) == 0.0
    assert sharpe_ratio([0.01]) == 0.0


def test_sharpe_ratio_positive():
    # Consistent positive returns → high Sharpe
    returns = [0.01] * 30
    sr = sharpe_ratio(returns)
    assert sr > 0


def test_sharpe_ratio_zero_std():
    # All same returns → zero std → 0
    returns = [0.0] * 10
    assert sharpe_ratio(returns) == 0.0


def test_sortino_ratio():
    returns = [0.01, 0.02, -0.005, 0.01, -0.01, 0.03]
    sr = sortino_ratio(returns)
    assert sr > 0


def test_max_drawdown_empty():
    assert max_drawdown([]) == 0.0
    assert max_drawdown([100.0]) == 0.0


def test_max_drawdown_no_drawdown():
    assert max_drawdown([100, 110, 120, 130]) == 0.0


def test_max_drawdown_with_drawdown():
    curve = [100, 110, 105, 120, 90, 100]
    dd = max_drawdown(curve)
    # Peak is 120, trough is 90 → 25% drawdown
    assert abs(dd - 0.25) < 1e-10


def test_current_drawdown():
    assert current_drawdown(90, 100) == 0.10
    assert current_drawdown(100, 100) == 0.0
    assert current_drawdown(100, 0) == 0.0


def test_position_size_basic():
    size = position_size_usd(100.0, 0.10, 0.20, min_order_usd=5.0)
    assert size == 10.0  # 100 * 0.10 = 10, under 20% cap


def test_position_size_capped():
    size = position_size_usd(100.0, 0.50, 0.20, min_order_usd=5.0)
    assert size == 20.0  # 100 * 0.50 = 50, but capped at 20%


def test_position_size_below_min_skips():
    # Kelly says $0.10, below $1.00 min → skip (never inflate)
    size = position_size_usd(10.0, 0.01, 1.0, min_order_usd=1.0)
    assert size == 0.0


def test_position_size_default_min_is_one():
    # Verify default min_order_usd=1.0
    size = position_size_usd(10.0, 0.005, 1.0)
    assert size == 0.0  # 10 * 0.005 = 0.05, below default $1.00 min


def test_position_size_above_min_passes():
    # Kelly says $1.50, above $1.00 min → trade
    size = position_size_usd(10.0, 0.15, 1.0, min_order_usd=1.0)
    assert size == 1.5
