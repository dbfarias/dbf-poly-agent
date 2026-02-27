"""Tests for configuration."""

from pathlib import Path

import pytest

from bot.config import CapitalTier, TierConfig


def test_capital_tier_from_bankroll():
    assert CapitalTier.from_bankroll(5) == CapitalTier.TIER1
    assert CapitalTier.from_bankroll(24.99) == CapitalTier.TIER1
    assert CapitalTier.from_bankroll(25) == CapitalTier.TIER2
    assert CapitalTier.from_bankroll(99.99) == CapitalTier.TIER2
    assert CapitalTier.from_bankroll(100) == CapitalTier.TIER3
    assert CapitalTier.from_bankroll(1000) == CapitalTier.TIER3


def test_tier_config_values():
    # Reset all tiers to defaults in case earlier tests mutated them
    for tier in CapitalTier:
        TierConfig.reset(tier)
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


# ---------------------------------------------------------------------------
# C5 — TierConfig.update() Validation
# ---------------------------------------------------------------------------


class TestTierConfigValidation:
    """TierConfig.update() must reject invalid values atomically."""

    def setup_method(self):
        TierConfig.reset(CapitalTier.TIER1)

    def teardown_method(self):
        TierConfig.reset(CapitalTier.TIER1)

    def test_reject_negative_max_positions(self):
        with pytest.raises(ValueError, match="max_positions"):
            TierConfig.update(CapitalTier.TIER1, {"max_positions": -1})

    def test_reject_zero_max_positions(self):
        with pytest.raises(ValueError, match="max_positions"):
            TierConfig.update(CapitalTier.TIER1, {"max_positions": 0})

    def test_reject_max_positions_above_max(self):
        with pytest.raises(ValueError, match="max_positions"):
            TierConfig.update(CapitalTier.TIER1, {"max_positions": 51})

    def test_reject_negative_pct(self):
        with pytest.raises(ValueError, match="max_per_position_pct"):
            TierConfig.update(CapitalTier.TIER1, {"max_per_position_pct": -0.1})

    def test_reject_pct_above_max(self):
        with pytest.raises(ValueError, match="max_per_position_pct"):
            TierConfig.update(CapitalTier.TIER1, {"max_per_position_pct": 1.5})

    def test_reject_wrong_type_float_for_int(self):
        with pytest.raises(ValueError, match="max_positions"):
            TierConfig.update(CapitalTier.TIER1, {"max_positions": 3.5})

    def test_reject_wrong_type_str(self):
        with pytest.raises(ValueError, match="max_positions"):
            TierConfig.update(CapitalTier.TIER1, {"max_positions": "three"})

    def test_accept_valid_update(self):
        TierConfig.update(CapitalTier.TIER1, {"max_positions": 5})
        assert TierConfig.get(CapitalTier.TIER1)["max_positions"] == 5

    def test_accept_boundary_values(self):
        TierConfig.update(CapitalTier.TIER1, {"max_positions": 1})
        assert TierConfig.get(CapitalTier.TIER1)["max_positions"] == 1
        TierConfig.update(CapitalTier.TIER1, {"max_positions": 50})
        assert TierConfig.get(CapitalTier.TIER1)["max_positions"] == 50

    def test_partial_failure_rolls_back(self):
        """If one value in a batch is invalid, none should be applied."""
        original = TierConfig.get(CapitalTier.TIER1)["max_positions"]
        with pytest.raises(ValueError):
            TierConfig.update(CapitalTier.TIER1, {
                "max_positions": 10,           # valid
                "kelly_fraction": -0.5,        # invalid
            })
        # max_positions must NOT have changed
        assert TierConfig.get(CapitalTier.TIER1)["max_positions"] == original

    def test_unknown_keys_ignored(self):
        """Unknown keys should not cause errors or be stored."""
        TierConfig.update(CapitalTier.TIER1, {"unknown_key": 42})
        assert "unknown_key" not in TierConfig.get(CapitalTier.TIER1)

    def test_accept_int_for_float_field(self):
        """Integer values should be accepted for float fields."""
        TierConfig.update(CapitalTier.TIER1, {"kelly_fraction": 1})
        assert TierConfig.get(CapitalTier.TIER1)["kelly_fraction"] == 1.0


# ---------------------------------------------------------------------------
# M6 — No datetime.utcnow() in source code
# ---------------------------------------------------------------------------


def test_no_utcnow_in_source_code():
    """All Python source files must use datetime.now(timezone.utc) instead of datetime.utcnow()."""
    root = Path(__file__).parent.parent
    violations = []
    for py_file in sorted(root.rglob("*.py")):
        # Skip test files, venv, and __pycache__
        rel = str(py_file.relative_to(root))
        if rel.startswith(("tests/", ".venv/", "__pycache__")):
            continue
        content = py_file.read_text()
        if "datetime.utcnow()" in content:
            violations.append(rel)
    assert violations == [], f"Files still using datetime.utcnow(): {violations}"
