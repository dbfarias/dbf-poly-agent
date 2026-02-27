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
    assert t1["max_positions"] == 5
    assert t1["max_per_position_pct"] == 0.35
    assert t1["max_deployed_pct"] == 0.70
    assert t1["max_per_category_pct"] == 0.40
    assert t1["min_win_prob"] == 0.80
    assert t1["kelly_fraction"] == 0.25

    t2 = TierConfig.get(CapitalTier.TIER2)
    assert t2["max_positions"] == 8
    assert t2["max_deployed_pct"] == 0.80

    t3 = TierConfig.get(CapitalTier.TIER3)
    assert t3["max_positions"] == 15
    assert t3["max_deployed_pct"] == 0.85


def test_tier_risk_increases():
    """Higher tiers allow more positions; Tier 1 is aggressive for small bankrolls."""
    t1 = TierConfig.get(CapitalTier.TIER1)
    t2 = TierConfig.get(CapitalTier.TIER2)
    t3 = TierConfig.get(CapitalTier.TIER3)
    # Tier 1 needs aggressive sizing to meet Polymarket minimums
    assert t1["max_per_position_pct"] >= t2["max_per_position_pct"]
    # Tier 3 allows the most positions
    assert t3["max_positions"] >= t1["max_positions"]
