"""Tests for configuration."""

from bot.config import CapitalTier, TierConfig


def test_capital_tier_from_bankroll():
    assert CapitalTier.from_bankroll(5) == CapitalTier.TIER1
    assert CapitalTier.from_bankroll(24.99) == CapitalTier.TIER1
    assert CapitalTier.from_bankroll(25) == CapitalTier.TIER2
    assert CapitalTier.from_bankroll(99.99) == CapitalTier.TIER2
    assert CapitalTier.from_bankroll(100) == CapitalTier.TIER3
    assert CapitalTier.from_bankroll(1000) == CapitalTier.TIER3


def test_tier_config_values():
    t1 = TierConfig.get(CapitalTier.TIER1)
    assert t1["max_positions"] == 1
    assert t1["min_win_prob"] == 0.85

    t2 = TierConfig.get(CapitalTier.TIER2)
    assert t2["max_positions"] == 3

    t3 = TierConfig.get(CapitalTier.TIER3)
    assert t3["max_positions"] == 10


def test_tier_risk_increases():
    """Lower tiers should have stricter risk limits."""
    t1 = TierConfig.get(CapitalTier.TIER1)
    t3 = TierConfig.get(CapitalTier.TIER3)
    assert t1["min_win_prob"] > t3["min_win_prob"]
    assert t1["min_edge_pct"] > t3["min_edge_pct"]
