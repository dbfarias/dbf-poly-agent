"""Tests for BaseStrategy tier gating and repr."""

from unittest.mock import MagicMock

from bot.agent.strategies.base import BaseStrategy
from bot.config import CapitalTier


def _make_concrete_strategy(min_tier: CapitalTier, name: str = "test_strat"):
    """Create a concrete subclass of BaseStrategy for testing."""

    class ConcreteStrategy(BaseStrategy):
        async def scan(self, markets):
            return []

        async def should_exit(self, market_id, current_price):
            return False

    ConcreteStrategy.name = name
    ConcreteStrategy.min_tier = min_tier

    return ConcreteStrategy(
        clob_client=MagicMock(),
        gamma_client=MagicMock(),
        cache=MagicMock(),
    )


class TestBaseStrategy:
    def test_tier1_strategy_enabled_for_all_tiers(self):
        strat = _make_concrete_strategy(CapitalTier.TIER1)
        assert strat.is_enabled_for_tier(CapitalTier.TIER1) is True
        assert strat.is_enabled_for_tier(CapitalTier.TIER2) is True
        assert strat.is_enabled_for_tier(CapitalTier.TIER3) is True

    def test_tier2_strategy_disabled_for_tier1(self):
        strat = _make_concrete_strategy(CapitalTier.TIER2)
        assert strat.is_enabled_for_tier(CapitalTier.TIER1) is False

    def test_tier2_strategy_enabled_for_tier2_and_above(self):
        strat = _make_concrete_strategy(CapitalTier.TIER2)
        assert strat.is_enabled_for_tier(CapitalTier.TIER2) is True
        assert strat.is_enabled_for_tier(CapitalTier.TIER3) is True

    def test_tier3_strategy_disabled_for_tier1_and_tier2(self):
        strat = _make_concrete_strategy(CapitalTier.TIER3)
        assert strat.is_enabled_for_tier(CapitalTier.TIER1) is False
        assert strat.is_enabled_for_tier(CapitalTier.TIER2) is False

    def test_tier3_strategy_enabled_for_tier3(self):
        strat = _make_concrete_strategy(CapitalTier.TIER3)
        assert strat.is_enabled_for_tier(CapitalTier.TIER3) is True

    def test_repr_includes_name(self):
        strat = _make_concrete_strategy(CapitalTier.TIER1, name="my_strategy")
        assert "my_strategy" in repr(strat)
