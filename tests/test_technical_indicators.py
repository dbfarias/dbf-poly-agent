"""Tests for technical indicators — RSI, MACD, VWAP, CVD."""


from bot.research.technical_indicators import (
    _ema,
    compute_cvd,
    compute_macd,
    compute_rsi,
    compute_vwap,
)


class TestRSI:
    def test_rsi_basic_uptrend(self) -> None:
        """Pure uptrend should yield RSI close to 100."""
        prices = [float(i) for i in range(20)]  # 0, 1, 2, ..., 19
        rsi = compute_rsi(prices, period=14)
        assert rsi is not None
        assert rsi > 90.0

    def test_rsi_basic_downtrend(self) -> None:
        """Pure downtrend should yield RSI close to 0."""
        prices = [float(20 - i) for i in range(20)]  # 20, 19, ..., 1
        rsi = compute_rsi(prices, period=14)
        assert rsi is not None
        assert rsi < 10.0

    def test_rsi_insufficient_data(self) -> None:
        """Less than period + 1 data points returns None."""
        rsi = compute_rsi([100.0, 101.0, 102.0], period=14)
        assert rsi is None

    def test_rsi_flat_prices(self) -> None:
        """Flat prices should yield RSI = 100 (no losses)."""
        prices = [50.0] * 20
        rsi = compute_rsi(prices, period=14)
        assert rsi is not None
        # All gains are 0, all losses are 0 → avg_loss = 0 → RSI = 100
        assert rsi == 100.0

    def test_rsi_range(self) -> None:
        """RSI should always be between 0 and 100."""
        # Oscillating prices
        prices = [100.0 + (i % 3) * 2 - 2 for i in range(50)]
        rsi = compute_rsi(prices, period=14)
        assert rsi is not None
        assert 0 <= rsi <= 100

    def test_rsi_custom_period(self) -> None:
        prices = [float(i) for i in range(30)]
        rsi_7 = compute_rsi(prices, period=7)
        rsi_14 = compute_rsi(prices, period=14)
        assert rsi_7 is not None
        assert rsi_14 is not None


class TestMACD:
    def test_macd_basic(self) -> None:
        """MACD should return a tuple of 3 floats."""
        # 50 prices trending up
        prices = [100.0 + i * 0.5 for i in range(50)]
        result = compute_macd(prices)
        assert result is not None
        macd_line, signal_line, histogram = result
        assert isinstance(macd_line, float)
        assert isinstance(signal_line, float)
        assert isinstance(histogram, float)
        # MACD = fast EMA - slow EMA; in uptrend, fast > slow → positive
        assert macd_line > 0

    def test_macd_insufficient_data(self) -> None:
        """Too few data points returns None."""
        prices = [100.0] * 10
        result = compute_macd(prices)
        assert result is None

    def test_macd_histogram_is_difference(self) -> None:
        prices = [100.0 + i * 0.3 for i in range(60)]
        result = compute_macd(prices)
        assert result is not None
        macd_line, signal_line, histogram = result
        assert abs(histogram - (macd_line - signal_line)) < 1e-10

    def test_macd_downtrend(self) -> None:
        prices = [200.0 - i * 0.5 for i in range(50)]
        result = compute_macd(prices)
        assert result is not None
        macd_line, _, _ = result
        assert macd_line < 0  # Downtrend


class TestVWAP:
    def test_vwap_basic(self) -> None:
        prices = [100.0, 101.0, 102.0]
        volumes = [1000.0, 2000.0, 1500.0]
        vwap = compute_vwap(prices, volumes)
        assert vwap is not None
        expected = (100*1000 + 101*2000 + 102*1500) / (1000+2000+1500)
        assert abs(vwap - expected) < 1e-10

    def test_vwap_empty_lists(self) -> None:
        assert compute_vwap([], []) is None

    def test_vwap_mismatched_lengths(self) -> None:
        assert compute_vwap([100.0, 101.0], [1000.0]) is None

    def test_vwap_zero_volume(self) -> None:
        assert compute_vwap([100.0, 101.0], [0.0, 0.0]) is None

    def test_vwap_single_value(self) -> None:
        vwap = compute_vwap([50.0], [100.0])
        assert vwap == 50.0


class TestCVD:
    def test_cvd_balanced(self) -> None:
        buys = [100.0, 200.0, 150.0]
        sells = [100.0, 200.0, 150.0]
        assert compute_cvd(buys, sells) == 0.0

    def test_cvd_buy_pressure(self) -> None:
        buys = [100.0, 200.0]
        sells = [50.0, 50.0]
        assert compute_cvd(buys, sells) == 200.0  # 300 - 100

    def test_cvd_sell_pressure(self) -> None:
        buys = [50.0]
        sells = [200.0]
        assert compute_cvd(buys, sells) == -150.0

    def test_cvd_empty(self) -> None:
        assert compute_cvd([], []) == 0.0


class TestEMA:
    def test_ema_basic(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = _ema(values, period=3)
        assert result is not None
        assert len(result) == 3  # len(values) - period + 1
        # First value is SMA of first 3: (1+2+3)/3 = 2.0
        assert result[0] == 2.0

    def test_ema_insufficient_data(self) -> None:
        result = _ema([1.0, 2.0], period=5)
        assert result is None

    def test_ema_single_period(self) -> None:
        values = [10.0, 20.0, 30.0]
        result = _ema(values, period=1)
        assert result is not None
        assert len(result) == 3
