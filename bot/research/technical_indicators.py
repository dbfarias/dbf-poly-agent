"""Pure-function technical indicators for crypto price analysis.

All functions are stateless and operate on plain lists — no external dependencies.
"""


def compute_rsi(prices: list[float], period: int = 14) -> float | None:
    """Compute Relative Strength Index (0-100).

    RSI = 100 - 100 / (1 + avg_gain / avg_loss)
    Returns None if insufficient data (need period + 1 prices).
    """
    if len(prices) < period + 1:
        return None

    gains: list[float] = []
    losses: list[float] = []

    for i in range(1, len(prices)):
        delta = prices[i] - prices[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(delta))

    # Initial average (simple average of first `period` values)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Smoothed average (Wilder's method) for remaining values
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_macd(
    prices: list[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[float, float, float] | None:
    """Compute MACD (Moving Average Convergence Divergence).

    Returns (macd_line, signal_line, histogram) or None if insufficient data.
    Needs at least slow_period + signal_period prices.
    """
    if len(prices) < slow_period + signal_period:
        return None

    fast_ema = _ema(prices, fast_period)
    slow_ema = _ema(prices, slow_period)

    if fast_ema is None or slow_ema is None:
        return None

    # MACD line for each point where both EMAs exist
    # Align from the end (slow_ema is shorter)
    offset = len(fast_ema) - len(slow_ema)
    macd_values = [
        fast_ema[offset + i] - slow_ema[i] for i in range(len(slow_ema))
    ]

    if len(macd_values) < signal_period:
        return None

    signal_ema = _ema(macd_values, signal_period)
    if signal_ema is None or not signal_ema:
        return None

    macd_line = macd_values[-1]
    signal_line = signal_ema[-1]
    histogram = macd_line - signal_line

    return (macd_line, signal_line, histogram)


def compute_vwap(prices: list[float], volumes: list[float]) -> float | None:
    """Compute Volume-Weighted Average Price.

    VWAP = sum(price * volume) / sum(volume)
    Returns None if no data or zero total volume.
    """
    if not prices or not volumes or len(prices) != len(volumes):
        return None

    total_volume = sum(volumes)
    if total_volume == 0:
        return None

    weighted_sum = sum(p * v for p, v in zip(prices, volumes))
    return weighted_sum / total_volume


def compute_cvd(
    buy_volumes: list[float], sell_volumes: list[float]
) -> float:
    """Compute Cumulative Volume Delta.

    CVD = sum(buy_volumes) - sum(sell_volumes)
    Positive = buying pressure, negative = selling pressure.
    """
    return sum(buy_volumes) - sum(sell_volumes)


def _ema(values: list[float], period: int) -> list[float] | None:
    """Compute Exponential Moving Average series.

    Returns list of EMA values (length = len(values) - period + 1),
    or None if insufficient data.
    """
    if len(values) < period:
        return None

    multiplier = 2.0 / (period + 1)
    # Seed with SMA of first `period` values
    sma = sum(values[:period]) / period
    ema_values = [sma]

    for i in range(period, len(values)):
        new_ema = (values[i] - ema_values[-1]) * multiplier + ema_values[-1]
        ema_values.append(new_ema)

    return ema_values
