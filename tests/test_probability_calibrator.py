"""Tests for ProbabilityCalibrator — calibrated probability model."""

from unittest.mock import MagicMock

import pytest

from bot.research.probability_calibrator import (
    ProbabilityCalibrator,
    _bin_label,
)

# ---------- Bin label tests ----------


class TestBinLabel:
    def test_bin_50_60(self):
        assert _bin_label(0.55) == "0.50-0.60"

    def test_bin_60_70(self):
        assert _bin_label(0.65) == "0.60-0.70"

    def test_bin_70_80(self):
        assert _bin_label(0.75) == "0.70-0.80"

    def test_bin_80_90(self):
        assert _bin_label(0.85) == "0.80-0.90"

    def test_bin_90_100(self):
        assert _bin_label(0.95) == "0.90-1.00"

    def test_exact_boundary_50(self):
        assert _bin_label(0.50) == "0.50-0.60"

    def test_exact_boundary_60(self):
        assert _bin_label(0.60) == "0.60-0.70"

    def test_exact_100(self):
        assert _bin_label(1.0) == "0.90-1.00"

    def test_below_50(self):
        assert _bin_label(0.45) == ""

    def test_edge_case_0(self):
        assert _bin_label(0.0) == ""


# ---------- Helper to create mock trades ----------


def _make_trade(
    estimated_prob: float,
    pnl: float,
    strategy: str = "value_betting",
    exit_reason: str = "max-age",
):
    trade = MagicMock()
    trade.estimated_prob = estimated_prob
    trade.pnl = pnl
    trade.strategy = strategy
    trade.exit_reason = exit_reason
    return trade


# ---------- ProbabilityCalibrator.train tests ----------


class TestTrain:
    @pytest.mark.asyncio
    async def test_train_with_no_trades(self):
        cal = ProbabilityCalibrator()
        await cal.train([])
        assert not cal.is_trained

    @pytest.mark.asyncio
    async def test_train_with_trades_missing_exit_reason(self):
        """Trades without exit_reason should be filtered out."""
        trades = [_make_trade(0.85, 0.5, exit_reason="") for _ in range(10)]
        cal = ProbabilityCalibrator()
        await cal.train(trades)
        assert not cal.is_trained

    @pytest.mark.asyncio
    async def test_train_with_zero_pnl_filtered(self):
        """Trades with pnl=0 should be filtered out."""
        trades = [_make_trade(0.85, 0.0) for _ in range(10)]
        cal = ProbabilityCalibrator()
        await cal.train(trades)
        assert not cal.is_trained

    @pytest.mark.asyncio
    async def test_train_sets_calibration_factors(self):
        """With enough trades in a bin, factor should be computed."""
        # 5 trades in 0.80-0.90 bin: 3 wins, 2 losses
        # avg estimated = 0.85, actual win rate = 0.6
        # factor = 0.6 / 0.85 = ~0.706
        trades = [
            _make_trade(0.85, 0.5),
            _make_trade(0.84, 0.3),
            _make_trade(0.86, -0.2),
            _make_trade(0.83, 0.4),
            _make_trade(0.87, -0.1),
        ]
        cal = ProbabilityCalibrator()
        await cal.train(trades)
        assert cal.is_trained
        factor = cal._calibration_factors.get("0.80-0.90")
        assert factor is not None
        assert factor == pytest.approx(0.6 / 0.85, abs=0.01)

    @pytest.mark.asyncio
    async def test_train_insufficient_bin_data(self):
        """Bins with <5 trades should have factor=1.0."""
        trades = [_make_trade(0.85, 0.5) for _ in range(3)]
        cal = ProbabilityCalibrator()
        await cal.train(trades)
        assert cal.is_trained
        assert cal._calibration_factors.get("0.80-0.90") == 1.0


# ---------- ProbabilityCalibrator.calibrate tests ----------


class TestCalibrate:
    @pytest.mark.asyncio
    async def test_untrained_returns_original(self):
        cal = ProbabilityCalibrator()
        assert cal.calibrate(0.85) == 0.85

    @pytest.mark.asyncio
    async def test_calibrate_adjusts_probability(self):
        # Set up known calibration factors
        cal = ProbabilityCalibrator()
        cal._calibration_factors = {"0.80-0.90": 0.7}
        cal._trained = True

        result = cal.calibrate(0.85)
        # 0.85 * 0.7 = 0.595
        assert result == pytest.approx(0.595, abs=0.001)

    @pytest.mark.asyncio
    async def test_calibrate_clamps_low(self):
        cal = ProbabilityCalibrator()
        cal._calibration_factors = {"0.50-0.60": 0.01}
        cal._trained = True

        result = cal.calibrate(0.55)
        assert result >= 0.01

    @pytest.mark.asyncio
    async def test_calibrate_clamps_high(self):
        cal = ProbabilityCalibrator()
        cal._calibration_factors = {"0.90-1.00": 1.5}
        cal._trained = True

        result = cal.calibrate(0.95)
        assert result <= 0.99

    @pytest.mark.asyncio
    async def test_calibrate_below_50_unchanged(self):
        """Probabilities below 0.50 have no bin, returned as-is."""
        cal = ProbabilityCalibrator()
        cal._trained = True
        assert cal.calibrate(0.45) == 0.45


# ---------- Brier score tests ----------


class TestBrierScore:
    def test_perfect_predictions(self):
        """All wins predicted at 1.0 should give Brier = 0."""
        trades = [_make_trade(1.0, 0.5) for _ in range(5)]
        cal = ProbabilityCalibrator()
        score = cal.brier_score(trades)
        assert score == pytest.approx(0.0, abs=0.001)

    def test_worst_predictions(self):
        """All wins predicted at 0.0 should give Brier = 1.0."""
        trades = [_make_trade(0.0, 0.5) for _ in range(5)]
        cal = ProbabilityCalibrator()
        score = cal.brier_score(trades)
        assert score == pytest.approx(1.0, abs=0.001)

    def test_mixed_predictions(self):
        """Known Brier score for mixed outcomes."""
        trades = [
            _make_trade(0.8, 0.5),   # win, (0.8-1)^2 = 0.04
            _make_trade(0.8, -0.3),  # loss, (0.8-0)^2 = 0.64
        ]
        cal = ProbabilityCalibrator()
        score = cal.brier_score(trades)
        assert score == pytest.approx((0.04 + 0.64) / 2, abs=0.001)

    def test_empty_trades(self):
        cal = ProbabilityCalibrator()
        assert cal.brier_score([]) == 0.0

    def test_no_resolved_trades(self):
        """Trades without exit_reason should be filtered."""
        trades = [_make_trade(0.8, 0.5, exit_reason="") for _ in range(5)]
        cal = ProbabilityCalibrator()
        assert cal.brier_score(trades) == 0.0


# ---------- Per-strategy Brier tests ----------


class TestPerStrategyBrier:
    def test_single_strategy(self):
        trades = [
            _make_trade(0.8, 0.5, strategy="value_betting"),
            _make_trade(0.7, -0.3, strategy="value_betting"),
        ]
        cal = ProbabilityCalibrator()
        scores = cal.per_strategy_brier(trades)
        assert "value_betting" in scores
        expected = ((0.8 - 1) ** 2 + (0.7 - 0) ** 2) / 2
        assert scores["value_betting"] == pytest.approx(expected, abs=0.001)

    def test_multiple_strategies(self):
        trades = [
            _make_trade(0.9, 0.5, strategy="value_betting"),
            _make_trade(0.6, -0.2, strategy="time_decay"),
        ]
        cal = ProbabilityCalibrator()
        scores = cal.per_strategy_brier(trades)
        assert "value_betting" in scores
        assert "time_decay" in scores
        assert scores["value_betting"] == pytest.approx((0.9 - 1) ** 2, abs=0.001)
        assert scores["time_decay"] == pytest.approx((0.6 - 0) ** 2, abs=0.001)

    def test_empty_trades(self):
        cal = ProbabilityCalibrator()
        assert cal.per_strategy_brier([]) == {}
