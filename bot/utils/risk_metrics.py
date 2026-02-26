"""Risk metrics: VaR, VPIN, Z-Score, Profit Factor, Ruin Probability.

Pure functions — no state, no side effects.
"""

import math


def parametric_var(returns: list[float], confidence: float = 0.95) -> float:
    """Parametric VaR = μ - z·σ.

    Returns a negative number representing the worst expected daily loss
    at the given confidence level. E.g., -0.05 means 5% loss.
    """
    if len(returns) < 2:
        return 0.0

    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std_dev = math.sqrt(variance)

    # Z-scores for common confidence levels
    z_map = {0.90: 1.282, 0.95: 1.645, 0.99: 2.326}
    z = z_map.get(confidence, 1.645)

    return mean - z * std_dev


def compute_vpin(buy_volume: float, sell_volume: float) -> float:
    """Volume-synchronized Probability of Informed Trading.

    VPIN = |V_buy - V_sell| / (V_buy + V_sell).
    Range [0, 1]. Higher = more toxic/informed flow.
    """
    total = buy_volume + sell_volume
    if total <= 0:
        return 0.0
    return abs(buy_volume - sell_volume) / total


def mispricing_zscore(
    model_prob: float, market_price: float, std_dev: float
) -> float:
    """Z-score for mispricing detection.

    Z = (p_model - p_market) / σ.
    Positive = model thinks market is underpriced (buy signal).
    Negative = model thinks market is overpriced.
    """
    if std_dev <= 0:
        return 0.0
    return (model_prob - market_price) / std_dev


def profit_factor(gross_profit: float, gross_loss: float) -> float:
    """Profit Factor = gross_profit / gross_loss.

    > 1.0 = profitable, > 1.5 = healthy, > 2.0 = excellent.
    Returns 0.0 if no losses (infinite PF capped).
    """
    if gross_loss <= 0:
        return 0.0 if gross_profit <= 0 else 10.0  # Cap at 10
    return gross_profit / gross_loss


def polymarket_fee(
    price: float, shares: float, fee_rate: float, exponent: float = 2.0,
) -> float:
    """Compute Polymarket taker fee in USD.

    Formula: shares * price * fee_rate * (price * (1 - price))^exponent
    Most markets have fee_rate=0 (politics, economics).
    Crypto/esports use fee_rate=0.25 or 0.0175.
    """
    if fee_rate <= 0 or shares <= 0 or price <= 0 or price >= 1:
        return 0.0
    return shares * price * fee_rate * (price * (1 - price)) ** exponent


def ruin_probability(
    win_rate: float, bankroll: float, bet_size: float
) -> float:
    """Probability of ruin: P(ruin) = ((1-p)/p)^(B/b).

    Assumes even-money bets. Returns value in [0, 1].
    """
    if win_rate <= 0 or win_rate >= 1:
        return 1.0 if win_rate <= 0.5 else 0.0
    if bet_size <= 0 or bankroll <= 0:
        return 1.0

    ratio = (1.0 - win_rate) / win_rate
    exponent = bankroll / bet_size

    # Avoid overflow for large exponents
    if ratio >= 1.0:
        return 1.0
    try:
        return min(1.0, ratio ** exponent)
    except OverflowError:
        return 1.0 if ratio > 1.0 else 0.0
