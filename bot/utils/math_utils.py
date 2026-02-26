"""Mathematical utilities for trading: Kelly criterion, Sharpe ratio, drawdown."""

import math


def kelly_criterion(win_prob: float, market_price: float) -> float:
    """Calculate optimal Kelly fraction.

    f* = (p - c) / (1 - c)
    where p = estimated real probability, c = market price (cost).
    Returns fraction of bankroll to bet (0 to 1).
    """
    if not (0.0 <= win_prob <= 1.0):
        return 0.0
    if market_price >= 1.0 or market_price <= 0.0:
        return 0.0
    if win_prob <= market_price:
        return 0.0

    f = (win_prob - market_price) / (1.0 - market_price)
    return max(0.0, min(1.0, f))


def quarter_kelly(win_prob: float, market_price: float) -> float:
    """Quarter-Kelly sizing for conservative risk management."""
    return 0.25 * kelly_criterion(win_prob, market_price)


def expected_value(win_prob: float, market_price: float, size: float) -> float:
    """Expected value of a trade.

    EV = size * (win_prob * (1 - price) - (1 - win_prob) * price)
    """
    win_payout = size * (1.0 - market_price)
    loss_amount = size * market_price
    return win_prob * win_payout - (1.0 - win_prob) * loss_amount


def edge(win_prob: float, market_price: float) -> float:
    """Calculate edge: estimated prob - market implied prob."""
    return win_prob - market_price


def sharpe_ratio(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """Calculate annualized Sharpe ratio from daily returns."""
    if len(returns) < 2:
        return 0.0
    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
    std_dev = math.sqrt(variance)
    if std_dev == 0:
        return 0.0
    daily_sharpe = (mean_ret - risk_free_rate) / std_dev
    return daily_sharpe * math.sqrt(365)


def sortino_ratio(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """Calculate annualized Sortino ratio (downside deviation only)."""
    if len(returns) < 2:
        return 0.0
    mean_ret = sum(returns) / len(returns)
    downside_returns = [min(0, r - risk_free_rate) for r in returns]
    downside_var = sum(r**2 for r in downside_returns) / len(downside_returns)
    downside_dev = math.sqrt(downside_var)
    if downside_dev == 0:
        return 0.0
    return (mean_ret - risk_free_rate) / downside_dev * math.sqrt(365)


def max_drawdown(equity_curve: list[float]) -> float:
    """Calculate maximum drawdown from an equity curve.

    Returns a value between 0 and 1 (e.g., 0.15 = 15% drawdown).
    """
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        dd = (peak - value) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return max_dd


def current_drawdown(current_equity: float, peak_equity: float) -> float:
    """Calculate current drawdown from peak."""
    if peak_equity <= 0:
        return 0.0
    return max(0.0, (peak_equity - current_equity) / peak_equity)


def position_size_usd(
    bankroll: float,
    kelly_frac: float,
    max_per_position_pct: float,
    min_order_usd: float = 1.0,
) -> float:
    """Calculate position size in USD, respecting constraints.

    Returns 0.0 if Kelly-sized position is below min_order_usd — never
    inflates a position beyond what Kelly recommends.
    """
    if bankroll <= 0:
        return 0.0
    size = bankroll * kelly_frac
    max_size = bankroll * max_per_position_pct
    size = min(size, max_size)
    if size < min_order_usd:
        return 0.0
    return round(size, 2)
