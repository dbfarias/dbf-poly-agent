"""Tests for spread penalty and calibration gap edge adjustments."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.engine import TradingEngine


def _make_signal(edge=0.10, market_price=0.50, estimated_prob=0.70, strategy="time_decay"):
    """Create a minimal mock signal for edge adjustment tests."""
    signal = MagicMock()
    signal.edge = edge
    signal.market_price = market_price
    signal.estimated_prob = estimated_prob
    signal.strategy = strategy
    signal.market_id = "0xabc123"
    signal.token_id = "12345"
    signal.metadata = {}
    return signal


def _make_engine():
    """Create a minimal TradingEngine mock for testing _apply_edge_adjustments."""
    engine = object.__new__(TradingEngine)
    engine.clob_client = MagicMock()
    engine.learner = MagicMock()
    engine.learner.calibrator = MagicMock()
    engine.learner.calibrator.is_trained = False
    engine.spread_penalty_factor = TradingEngine.SPREAD_PENALTY_FACTOR
    engine.cal_gap_weight = TradingEngine.CAL_GAP_WEIGHT
    return engine


class TestSpreadPenalty:
    @pytest.mark.asyncio
    async def test_spread_reduces_edge(self):
        """Spread of $0.04 with penalty factor 0.5 should reduce edge by 0.02."""
        engine = _make_engine()

        book = MagicMock()
        book.spread = 0.04
        engine.clob_client.get_order_book = AsyncMock(return_value=book)

        signal = _make_signal(edge=0.10)

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.is_paper = False
            await engine._apply_edge_adjustments(signal)

        expected_penalty = 0.04 * TradingEngine.SPREAD_PENALTY_FACTOR
        assert abs(signal.edge - (0.10 - expected_penalty)) < 0.001
        assert "edge_adjustments" in signal.metadata
        assert signal.metadata["edge_adjustments"]["spread"] == 0.04

    @pytest.mark.asyncio
    async def test_zero_spread_no_penalty(self):
        """Zero spread should not change edge."""
        engine = _make_engine()

        book = MagicMock()
        book.spread = 0.0
        engine.clob_client.get_order_book = AsyncMock(return_value=book)

        signal = _make_signal(edge=0.10)

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.is_paper = False
            await engine._apply_edge_adjustments(signal)

        assert signal.edge == 0.10

    @pytest.mark.asyncio
    async def test_paper_mode_skips_spread(self):
        """Paper mode should not fetch orderbook or apply spread penalty."""
        engine = _make_engine()
        engine.clob_client.get_order_book = AsyncMock()

        signal = _make_signal(edge=0.10)

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.is_paper = True
            await engine._apply_edge_adjustments(signal)

        assert signal.edge == 0.10
        engine.clob_client.get_order_book.assert_not_called()

    @pytest.mark.asyncio
    async def test_orderbook_error_graceful(self):
        """Orderbook fetch failure should not crash or change edge."""
        engine = _make_engine()
        engine.clob_client.get_order_book = AsyncMock(side_effect=Exception("timeout"))

        signal = _make_signal(edge=0.10)

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.is_paper = False
            await engine._apply_edge_adjustments(signal)

        assert signal.edge == 0.10


class TestCalibrationGap:
    @pytest.mark.asyncio
    async def test_overconfident_reduces_edge(self):
        """If calibrated prob < estimated, edge should be penalized."""
        engine = _make_engine()

        signal = _make_signal(edge=0.10, estimated_prob=0.75)
        signal.metadata["calibrated_prob"] = 0.60  # 15% overconfident

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.is_paper = True  # Skip spread
            await engine._apply_edge_adjustments(signal)

        gap = 0.75 - 0.60  # 0.15
        expected_penalty = gap * TradingEngine.CAL_GAP_WEIGHT
        assert abs(signal.edge - (0.10 - expected_penalty)) < 0.001
        assert signal.metadata["edge_adjustments"]["calibration_gap"] == 0.15

    @pytest.mark.asyncio
    async def test_underconfident_no_penalty(self):
        """If calibrated prob >= estimated, no penalty should apply."""
        engine = _make_engine()

        signal = _make_signal(edge=0.10, estimated_prob=0.60)
        signal.metadata["calibrated_prob"] = 0.65  # Underconfident — no penalty

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.is_paper = True
            await engine._apply_edge_adjustments(signal)

        assert signal.edge == 0.10

    @pytest.mark.asyncio
    async def test_no_calibration_no_penalty(self):
        """Without calibrated_prob in metadata, no calibration penalty."""
        engine = _make_engine()

        signal = _make_signal(edge=0.10)

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.is_paper = True
            await engine._apply_edge_adjustments(signal)

        assert signal.edge == 0.10


class TestCombinedAdjustments:
    @pytest.mark.asyncio
    async def test_spread_plus_calibration(self):
        """Both penalties should stack."""
        engine = _make_engine()

        book = MagicMock()
        book.spread = 0.06
        engine.clob_client.get_order_book = AsyncMock(return_value=book)

        signal = _make_signal(edge=0.15, estimated_prob=0.80)
        signal.metadata["calibrated_prob"] = 0.64  # 20% gap

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.is_paper = False
            await engine._apply_edge_adjustments(signal)

        spread_penalty = 0.06 * TradingEngine.SPREAD_PENALTY_FACTOR
        cal_penalty = 0.16 * TradingEngine.CAL_GAP_WEIGHT
        expected = 0.15 - spread_penalty - cal_penalty
        assert abs(signal.edge - expected) < 0.001

        adj = signal.metadata["edge_adjustments"]
        assert "spread" in adj
        assert "calibration_gap" in adj

    @pytest.mark.asyncio
    async def test_edge_can_go_negative(self):
        """Edge can go negative after adjustments — risk manager will reject."""
        engine = _make_engine()

        book = MagicMock()
        book.spread = 0.10  # 10 cent spread
        engine.clob_client.get_order_book = AsyncMock(return_value=book)

        signal = _make_signal(edge=0.03, estimated_prob=0.80)
        signal.metadata["calibrated_prob"] = 0.50  # Heavily overconfident

        with patch("bot.agent.engine.settings") as mock_settings:
            mock_settings.is_paper = False
            await engine._apply_edge_adjustments(signal)

        # Spread penalty: 0.10 * 0.5 = 0.05
        # Cal penalty: 0.30 * 0.3 = 0.09
        # Total: 0.03 - 0.05 - 0.09 = -0.11
        assert signal.edge < 0
