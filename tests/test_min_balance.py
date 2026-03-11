"""Tests for minimum balance trade-skipping feature."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(**overrides):
    """Construct a TradingEngine with all external clients patched out."""
    with patch("bot.agent.engine.PolymarketClient"), \
         patch("bot.agent.engine.GammaClient"), \
         patch("bot.agent.engine.DataApiClient"), \
         patch("bot.agent.engine.MarketCache"), \
         patch("bot.agent.engine.WebSocketManager"), \
         patch("bot.agent.engine.HeartbeatManager"):
        from bot.agent.engine import TradingEngine
        engine = TradingEngine()
        for attr, val in overrides.items():
            setattr(engine, attr, val)
        # Rewire closer references
        engine.closer.order_manager = engine.order_manager
        engine.closer.portfolio = engine.portfolio
        engine.closer.risk_manager = engine.risk_manager
        return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMinBalanceForTrades:
    """Verify _evaluate_signals returns early when cash is below threshold."""

    @pytest.mark.asyncio
    async def test_signals_skipped_when_cash_below_threshold(self):
        """When cash < min_balance_for_trades, signals are skipped entirely."""
        engine = _make_engine()

        # Set portfolio cash below threshold
        engine.portfolio._cash = 0.50
        engine.min_balance_for_trades = 1.0

        # Mock scan_markets — should NOT be called
        engine.analyzer.scan_markets = AsyncMock(return_value=[])

        result = await engine._evaluate_signals()

        assert result == (0, 0, 0)
        engine.analyzer.scan_markets.assert_not_called()

    @pytest.mark.asyncio
    async def test_signals_proceed_when_cash_above_threshold(self):
        """When cash >= min_balance_for_trades, signal evaluation proceeds."""
        engine = _make_engine()

        # Set portfolio cash above threshold
        engine.portfolio._cash = 5.0
        engine.min_balance_for_trades = 1.0

        # Mock scan_markets — should be called when cash is sufficient
        engine.analyzer.scan_markets = AsyncMock(return_value=[])

        result = await engine._evaluate_signals()

        assert result == (0, 0, 0)
        engine.analyzer.scan_markets.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_signals_skipped_at_exact_threshold(self):
        """When cash == min_balance_for_trades, signals should proceed (not <)."""
        engine = _make_engine()

        engine.portfolio._cash = 1.0
        engine.min_balance_for_trades = 1.0

        engine.analyzer.scan_markets = AsyncMock(return_value=[])

        await engine._evaluate_signals()

        # Exactly at threshold should proceed (condition is strictly <)
        engine.analyzer.scan_markets.assert_called_once()

    @pytest.mark.asyncio
    async def test_signals_skipped_zero_balance(self):
        """When cash is 0, signals are skipped."""
        engine = _make_engine()

        engine.portfolio._cash = 0.0
        engine.min_balance_for_trades = 1.0

        engine.analyzer.scan_markets = AsyncMock(return_value=[])

        result = await engine._evaluate_signals()

        assert result == (0, 0, 0)
        engine.analyzer.scan_markets.assert_not_called()

    def test_default_setting_value(self):
        """The default min_balance_for_trades should be 1.0."""
        from bot.config import Settings
        field = Settings.model_fields["min_balance_for_trades"]
        assert field.default == 1.0

    def test_engine_inherits_setting(self):
        """Engine should initialize min_balance_for_trades from settings."""
        engine = _make_engine()
        from bot.config import settings
        assert engine.min_balance_for_trades == settings.min_balance_for_trades

    @pytest.mark.asyncio
    async def test_custom_threshold(self):
        """Custom threshold values are respected."""
        engine = _make_engine()

        engine.portfolio._cash = 3.0
        # Set a higher threshold — $3 cash should be skipped at $5 threshold
        engine.min_balance_for_trades = 5.0

        engine.analyzer.scan_markets = AsyncMock(return_value=[])

        result = await engine._evaluate_signals()

        assert result == (0, 0, 0)
        engine.analyzer.scan_markets.assert_not_called()
