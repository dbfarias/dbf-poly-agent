"""Longshot bias calibration based on empirical prediction market data.

Research (72.1M trades, Becker 2026) shows cheap contracts are
systematically overpriced. This module applies a discount factor
to estimated probabilities for low-priced contracts.
"""

# Empirical discount factors by price range (from Becker 2026 study)
# (min_price, max_price, discount_factor)
# A factor of 0.43 means: if model says 1% prob at $0.01, actual is 0.43%
_LONGSHOT_BIAS_TABLE: tuple[tuple[float, float, float], ...] = (
    (0.00, 0.02, 0.43),  # $0.01 range: 57% overpriced
    (0.02, 0.05, 0.70),  # $0.02-0.05: 30% overpriced
    (0.05, 0.10, 0.84),  # $0.05-0.10: 16% overpriced
    (0.10, 0.20, 0.92),  # $0.10-0.20: 8% overpriced
    (0.20, 0.30, 0.96),  # $0.20-0.30: 4% overpriced
    (0.30, 1.00, 1.00),  # $0.30+: roughly fair
)


def longshot_discount(price: float) -> float:
    """Get the longshot bias discount factor for a given price.

    Returns a multiplier (0-1) to apply to the model's estimated
    probability. Lower prices get bigger discounts because cheap
    contracts are systematically overpriced.
    """
    for lo, hi, factor in _LONGSHOT_BIAS_TABLE:
        if lo <= price < hi:
            return factor
    return 1.0


def calibrate_probability(model_prob: float, market_price: float) -> float:
    """Apply longshot bias calibration to a model probability.

    If the market price is cheap (<$0.30), the model's probability
    estimate is discounted because cheap contracts are overpriced.
    """
    discount = longshot_discount(market_price)
    return model_prob * discount


def calibrated_edge(model_prob: float, market_price: float) -> float:
    """Calculate edge after longshot bias calibration."""
    calibrated = calibrate_probability(model_prob, market_price)
    return calibrated - market_price
