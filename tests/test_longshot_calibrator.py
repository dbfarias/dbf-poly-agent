"""Tests for longshot bias calibration."""

from bot.research.longshot_calibrator import (
    calibrate_probability,
    calibrated_edge,
    longshot_discount,
)


class TestLongshotDiscount:
    def test_discount_at_1_cent(self):
        assert longshot_discount(0.01) == 0.43

    def test_discount_at_3_cents(self):
        assert longshot_discount(0.03) == 0.70

    def test_discount_at_7_cents(self):
        assert longshot_discount(0.07) == 0.84

    def test_discount_at_15_cents(self):
        assert longshot_discount(0.15) == 0.92

    def test_discount_at_25_cents(self):
        assert longshot_discount(0.25) == 0.96

    def test_discount_at_50_cents(self):
        assert longshot_discount(0.50) == 1.0

    def test_discount_at_99_cents(self):
        assert longshot_discount(0.99) == 1.0

    def test_discount_above_range(self):
        """Prices >= 1.0 return 1.0 (no discount)."""
        assert longshot_discount(1.0) == 1.0

    def test_discount_at_zero(self):
        assert longshot_discount(0.00) == 0.43

    def test_discount_boundary_0_02(self):
        """0.02 falls in the second bucket (0.02-0.05)."""
        assert longshot_discount(0.02) == 0.70

    def test_discount_boundary_0_05(self):
        """0.05 falls in the third bucket (0.05-0.10)."""
        assert longshot_discount(0.05) == 0.84

    def test_discount_boundary_0_10(self):
        """0.10 falls in the fourth bucket (0.10-0.20)."""
        assert longshot_discount(0.10) == 0.92

    def test_discount_boundary_0_30(self):
        """0.30 falls in the fair bucket (0.30-1.00)."""
        assert longshot_discount(0.30) == 1.0


class TestCalibratedEdge:
    def test_calibrated_edge_negative_at_overpriced(self):
        """A $0.01 contract with 1% model prob has negative calibrated edge."""
        # Model says 1%, discount to 0.43%, price is 1% => edge = -0.57%
        edge = calibrated_edge(0.01, 0.01)
        assert edge < 0

    def test_calibrated_edge_positive_at_fair(self):
        """A $0.50 contract with 60% model prob has positive edge."""
        edge = calibrated_edge(0.60, 0.50)
        assert edge > 0
        assert abs(edge - 0.10) < 0.001  # No discount at $0.50

    def test_calibrated_edge_at_cheap_with_strong_signal(self):
        """A $0.03 contract with 10% model prob — discounted but still +EV."""
        edge = calibrated_edge(0.10, 0.03)
        # 10% * 0.70 = 7%, price 3% => edge = 4%
        assert abs(edge - 0.04) < 0.001

    def test_calibrated_edge_at_cheap_overpriced(self):
        """A $0.05 contract with 5% model prob — overpriced after discount."""
        edge = calibrated_edge(0.05, 0.04)
        # 5% * 0.70 = 3.5%, price 4% => edge = -0.5%
        assert edge < 0


class TestCalibrateProbability:
    def test_calibrate_probability_cheap(self):
        """Cheap contract probability gets discounted."""
        result = calibrate_probability(0.10, 0.01)
        assert abs(result - 0.043) < 0.001

    def test_calibrate_probability_fair(self):
        """Fair-priced contract probability unchanged."""
        result = calibrate_probability(0.60, 0.50)
        assert result == 0.60

    def test_calibrate_probability_mid_range(self):
        """Mid-range contract gets partial discount."""
        result = calibrate_probability(0.20, 0.15)
        # 0.20 * 0.92 = 0.184
        assert abs(result - 0.184) < 0.001
