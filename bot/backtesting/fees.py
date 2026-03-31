"""Polymarket fee model -- exact non-linear formula.

Polymarket charges fees that scale with price uncertainty:
- Maximum fees (~2%) at p=0.5 (most uncertain)
- Minimal fees near p=0 or p=1 (nearly certain outcomes)
- Sports markets use exponent=2 for even lower fees at extremes
"""


def polymarket_fee(
    quantity: float,
    price: float,
    fee_rate: float = 0.02,
    exponent: float = 1.0,
) -> float:
    """Calculate Polymarket trading fee.

    Formula: fee = qty * price * fee_rate * (price * (1 - price))^exponent

    For prices near 0.5: max fees (~2%)
    For prices near 0 or 1: minimal fees (approaches 0%)
    Sports markets use exponent=2 for even lower fees at extremes.

    Args:
        quantity: Number of shares.
        price: Price per share (0 < price < 1).
        fee_rate: Base fee rate (default 2%).
        exponent: 1 for crypto, 2 for sports.

    Returns:
        Fee amount in USD.
    """
    if price <= 0 or price >= 1:
        return 0.0
    return quantity * price * fee_rate * (price * (1 - price)) ** exponent


def net_profit(
    entry_price: float,
    exit_price: float,
    quantity: float,
    fee_rate: float = 0.02,
    exponent: float = 1.0,
) -> float:
    """Calculate net profit after entry and exit fees.

    Args:
        entry_price: Price at entry (0 < p < 1).
        exit_price: Price at exit (0 < p < 1).
        quantity: Number of shares.
        fee_rate: Base fee rate.
        exponent: Fee exponent.

    Returns:
        Net profit after fees.
    """
    entry_fee = polymarket_fee(quantity, entry_price, fee_rate, exponent)
    exit_fee = polymarket_fee(quantity, exit_price, fee_rate, exponent)
    gross = (exit_price - entry_price) * quantity
    return gross - entry_fee - exit_fee
