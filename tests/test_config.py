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
    assert t1["max_positions"] == 3
    assert t1["max_per_position_pct"] == 0.55
    assert t1["max_deployed_pct"] == 0.80
    assert t1["max_per_category_pct"] == 0.55
    assert t1["min_win_prob"] == 0.65
    assert t1["kelly_fraction"] == 0.20

    t2 = TierConfig.get(CapitalTier.TIER2)
    assert t2["max_positions"] == 6
    assert t2["max_deployed_pct"] == 0.80

    t3 = TierConfig.get(CapitalTier.TIER3)
    assert t3["max_positions"] == 15
    assert t3["max_deployed_pct"] == 0.85


def test_tier_risk_increases():
    """Higher tiers allow more positions for diversification."""
    t1 = TierConfig.get(CapitalTier.TIER1)
    t2 = TierConfig.get(CapitalTier.TIER2)
    t3 = TierConfig.get(CapitalTier.TIER3)
    # More positions in higher tiers
    assert t3["max_positions"] >= t2["max_positions"] >= t1["max_positions"]
    # Smaller per-position allocation in higher tiers (more diversified)
    assert t1["max_per_position_pct"] >= t2["max_per_position_pct"]
