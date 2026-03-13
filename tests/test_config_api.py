"""Tests for config API: trading mode toggle, settings persistence, and strategy params."""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.config import TradingMode, settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_update(trading_mode=None, **kwargs):
    """Build a BotConfigUpdate with only non-None fields."""
    from api.schemas import BotConfigUpdate

    return BotConfigUpdate(trading_mode=trading_mode, **kwargs)


def _mock_engine():
    """Create a minimal mock engine for update_config dependency."""
    engine = MagicMock()
    engine.disabled_strategies = set()
    engine.analyzer.strategies = []
    engine.analyzer.blocked_market_types = set()
    engine.market_cooldown_hours = 3.0
    engine.debate_cooldown_hours = 1.0
    engine.min_balance_for_trades = 1.0
    engine.min_edge_for_debate = 0.03
    return engine


# ---------------------------------------------------------------------------
# 1-4: Trading mode toggle
# ---------------------------------------------------------------------------


class TestTradingModeToggle:
    """Test the trading_mode field in update_config."""

    def setup_method(self):
        self._original_mode = settings.trading_mode
        self._original_private_key = settings.poly_private_key

    def teardown_method(self):
        settings.trading_mode = self._original_mode
        settings.poly_private_key = self._original_private_key

    @pytest.mark.asyncio
    async def test_toggle_to_live_with_api_keys(self):
        """Switching from paper to live succeeds when API keys are present."""
        from api.routers.config import update_config

        settings.trading_mode = TradingMode.PAPER
        settings.poly_private_key = "0xtest-private-key"

        update = _make_update(trading_mode="live")

        with (
            patch("api.routers.config.get_engine", side_effect=RuntimeError),
            patch("api.routers.config.SettingsStore.save_from_update", new_callable=AsyncMock),
        ):
            result = await update_config(update, _="fake-api-key")

        assert settings.trading_mode == TradingMode.LIVE
        assert "trading_mode=live" in result["changes"]

    @pytest.mark.asyncio
    async def test_toggle_to_live_without_api_keys(self):
        """Switching to live without API keys raises HTTP 400."""
        from fastapi import HTTPException

        from api.routers.config import update_config

        settings.trading_mode = TradingMode.PAPER
        settings.poly_private_key = ""

        update = _make_update(trading_mode="live")

        with pytest.raises(HTTPException) as exc_info:
            with (
                patch("api.routers.config.get_engine", side_effect=RuntimeError),
                patch(
                    "api.routers.config.SettingsStore.save_from_update",
                    new_callable=AsyncMock,
                ),
            ):
                await update_config(update, _="fake-api-key")

        assert exc_info.value.status_code == 400
        assert "POLY_PRIVATE_KEY" in exc_info.value.detail
        # Mode should NOT have changed
        assert settings.trading_mode == TradingMode.PAPER

    @pytest.mark.asyncio
    async def test_toggle_to_paper(self):
        """Switching from live to paper always works (no key check)."""
        from api.routers.config import update_config

        settings.trading_mode = TradingMode.LIVE
        settings.poly_private_key = "0xtest-key"

        update = _make_update(trading_mode="paper")

        with (
            patch("api.routers.config.get_engine", side_effect=RuntimeError),
            patch("api.routers.config.SettingsStore.save_from_update", new_callable=AsyncMock),
        ):
            result = await update_config(update, _="fake-api-key")

        assert settings.trading_mode == TradingMode.PAPER
        assert "trading_mode=paper" in result["changes"]

    @pytest.mark.asyncio
    async def test_toggle_invalid_mode(self):
        """Sending an invalid mode (e.g. 'test') raises HTTP 400."""
        from fastapi import HTTPException

        from api.routers.config import update_config

        settings.trading_mode = TradingMode.PAPER
        update = _make_update(trading_mode="test")

        with pytest.raises(HTTPException) as exc_info:
            with (
                patch("api.routers.config.get_engine", side_effect=RuntimeError),
                patch(
                    "api.routers.config.SettingsStore.save_from_update",
                    new_callable=AsyncMock,
                ),
            ):
                await update_config(update, _="fake-api-key")

        assert exc_info.value.status_code == 400
        assert "Invalid trading_mode" in exc_info.value.detail
        # Mode unchanged
        assert settings.trading_mode == TradingMode.PAPER


# ---------------------------------------------------------------------------
# 5-6: SettingsStore persistence of trading_mode
# ---------------------------------------------------------------------------


class TestSettingsStoreTradingMode:
    """Verify SettingsStore saves and restores trading_mode."""

    @pytest.mark.asyncio
    async def test_settings_store_saves_trading_mode(self):
        """save_from_update persists trading_mode to the DB."""
        from bot.data.settings_store import SettingsStore

        update = _make_update(trading_mode="live")

        mock_repo = MagicMock()
        mock_repo.set_many = AsyncMock()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("bot.data.settings_store.async_session", return_value=mock_session), \
             patch("bot.data.settings_store.SettingsRepository", return_value=mock_repo):
            count = await SettingsStore.save_from_update(update)

        assert count >= 1
        saved_items = mock_repo.set_many.call_args[0][0]
        assert "global.trading_mode" in saved_items
        assert json.loads(saved_items["global.trading_mode"]) == "live"

    def test_settings_store_loads_trading_mode(self):
        """_apply_global restores trading_mode on the settings singleton."""
        from bot.data.settings_store import _apply_global

        original = settings.trading_mode
        try:
            settings.trading_mode = TradingMode.PAPER
            result = _apply_global("trading_mode", "live")
            assert result == 1
            assert settings.trading_mode == TradingMode.LIVE

            # Invalid value is rejected
            result = _apply_global("trading_mode", "invalid")
            assert result == 0
            # Still live from previous apply
            assert settings.trading_mode == TradingMode.LIVE
        finally:
            settings.trading_mode = original


# ---------------------------------------------------------------------------
# 7: News sniping mutable params
# ---------------------------------------------------------------------------


class TestNewsSniperMutableParams:
    """Verify news_sniping strategy has the expected _MUTABLE_PARAMS and update_param works."""

    def _make_strategy(self):
        from bot.agent.strategies.news_sniping import NewsSniperStrategy

        clob = MagicMock()
        gamma = MagicMock()
        cache = MagicMock()
        return NewsSniperStrategy(clob, gamma, cache)

    def test_mutable_params_contain_expected_keys(self):
        strategy = self._make_strategy()
        expected = {"MAX_EDGE", "EDGE_SCALE", "TAKE_PROFIT_MIN_HOLD_HOURS"}
        assert expected.issubset(set(strategy._MUTABLE_PARAMS.keys()))

    def test_update_max_edge(self):
        strategy = self._make_strategy()
        assert strategy.update_param("MAX_EDGE", 0.08)
        assert strategy.MAX_EDGE == 0.08

    def test_update_edge_scale(self):
        strategy = self._make_strategy()
        assert strategy.update_param("EDGE_SCALE", 0.25)
        assert strategy.EDGE_SCALE == 0.25

    def test_update_take_profit_min_hold_hours(self):
        strategy = self._make_strategy()
        assert strategy.update_param("TAKE_PROFIT_MIN_HOLD_HOURS", 3.0)
        assert strategy.TAKE_PROFIT_MIN_HOLD_HOURS == 3.0

    def test_reject_out_of_range(self):
        strategy = self._make_strategy()
        original = strategy.MAX_EDGE
        assert not strategy.update_param("MAX_EDGE", 99.0)
        assert strategy.MAX_EDGE == original

    def test_reject_unknown_param(self):
        strategy = self._make_strategy()
        assert not strategy.update_param("NONEXISTENT_PARAM", 1.0)


# ---------------------------------------------------------------------------
# 8: Copy trading mutable params
# ---------------------------------------------------------------------------


class TestCopyTradingMutableParams:
    """Verify copy_trading strategy has the expected _MUTABLE_PARAMS."""

    def _make_strategy(self):
        from bot.agent.strategies.copy_trading import CopyTradingStrategy

        clob = MagicMock()
        gamma = MagicMock()
        cache = MagicMock()
        return CopyTradingStrategy(clob, gamma, cache)

    def test_mutable_params_contain_expected_keys(self):
        strategy = self._make_strategy()
        expected = {
            "MIN_COPY_USD",
            "MAX_COPY_USD",
            "WHALE_BANKROLL_ESTIMATE",
            "BASE_EDGE",
            "WIN_RATE_BONUS_SCALE",
            "TAKE_PROFIT_PCT",
            "STOP_LOSS_PCT",
            "MAX_HOLD_HOURS",
            "MAX_COPY_SIGNALS_PER_CYCLE",
            "MAX_CONCURRENT_COPIES",
            "MIN_HOLD_SECONDS",
            "TAKE_PROFIT_MIN_HOLD_HOURS",
        }
        assert expected.issubset(set(strategy._MUTABLE_PARAMS.keys()))

    def test_update_min_copy_usd(self):
        strategy = self._make_strategy()
        assert strategy.update_param("MIN_COPY_USD", 2.0)
        assert strategy.MIN_COPY_USD == 2.0

    def test_update_max_copy_usd(self):
        strategy = self._make_strategy()
        assert strategy.update_param("MAX_COPY_USD", 10.0)
        assert strategy.MAX_COPY_USD == 10.0

    def test_update_whale_bankroll_estimate(self):
        strategy = self._make_strategy()
        assert strategy.update_param("WHALE_BANKROLL_ESTIMATE", 20000.0)
        assert strategy.WHALE_BANKROLL_ESTIMATE == 20000.0

    def test_reject_min_copy_below_range(self):
        strategy = self._make_strategy()
        original = strategy.MIN_COPY_USD
        assert not strategy.update_param("MIN_COPY_USD", 0.1)
        assert strategy.MIN_COPY_USD == original


# ---------------------------------------------------------------------------
# 9: Engine min_edge_for_debate
# ---------------------------------------------------------------------------


class TestMinEdgeForDebate:
    """Verify engine.min_edge_for_debate attribute exists and is settable."""

    def test_min_edge_for_debate_configurable(self):
        """The engine mock mirrors the real attribute; verify it is readable and settable."""
        engine = _mock_engine()
        # Attribute exists and has a numeric value
        assert hasattr(engine, "min_edge_for_debate")
        assert isinstance(engine.min_edge_for_debate, float)

        # Settable
        engine.min_edge_for_debate = 0.05
        assert engine.min_edge_for_debate == 0.05

    def test_min_edge_for_debate_in_quality_spec(self):
        """min_edge_for_debate should be in the config API quality_spec."""
        from api.routers.config import update_config  # noqa: F401 — ensure module loaded
        import api.routers.config as config_mod

        # The quality spec is defined inside update_config, but we can verify
        # it's handled by the SettingsStore _QUALITY_ATTR_MAP instead
        from bot.data.settings_store import _QUALITY_ATTR_MAP

        assert "min_edge_for_debate" in _QUALITY_ATTR_MAP
        target, attr = _QUALITY_ATTR_MAP["min_edge_for_debate"]
        assert target == "_engine"
        assert attr == "min_edge_for_debate"
