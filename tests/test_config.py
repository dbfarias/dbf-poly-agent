"""Tests for configuration."""

from pathlib import Path

import pytest

from bot.config import RiskConfig


def test_risk_config_values():
    RiskConfig.reset()
    config = RiskConfig.get()
    assert config["max_positions"] == 6
    assert config["max_per_position_pct"] == 0.40
    assert config["max_deployed_pct"] == 0.60
    assert config["max_per_category_pct"] == 0.40
    assert config["min_win_prob"] == 0.55
    assert config["kelly_fraction"] == 0.35


# ---------------------------------------------------------------------------
# C5 — RiskConfig.update() Validation
# ---------------------------------------------------------------------------


class TestRiskConfigValidation:
    """RiskConfig.update() must reject invalid values atomically."""

    def setup_method(self):
        RiskConfig.reset()

    def teardown_method(self):
        RiskConfig.reset()

    def test_reject_negative_max_positions(self):
        with pytest.raises(ValueError, match="max_positions"):
            RiskConfig.update({"max_positions": -1})

    def test_reject_zero_max_positions(self):
        with pytest.raises(ValueError, match="max_positions"):
            RiskConfig.update({"max_positions": 0})

    def test_reject_max_positions_above_max(self):
        with pytest.raises(ValueError, match="max_positions"):
            RiskConfig.update({"max_positions": 51})

    def test_reject_negative_pct(self):
        with pytest.raises(ValueError, match="max_per_position_pct"):
            RiskConfig.update({"max_per_position_pct": -0.1})

    def test_reject_pct_above_max(self):
        with pytest.raises(ValueError, match="max_per_position_pct"):
            RiskConfig.update({"max_per_position_pct": 1.5})

    def test_reject_wrong_type_float_for_int(self):
        with pytest.raises(ValueError, match="max_positions"):
            RiskConfig.update({"max_positions": 3.5})

    def test_reject_wrong_type_str(self):
        with pytest.raises(ValueError, match="max_positions"):
            RiskConfig.update({"max_positions": "three"})

    def test_accept_valid_update(self):
        RiskConfig.update({"max_positions": 5})
        assert RiskConfig.get()["max_positions"] == 5

    def test_accept_boundary_values(self):
        RiskConfig.update({"max_positions": 1})
        assert RiskConfig.get()["max_positions"] == 1
        RiskConfig.update({"max_positions": 50})
        assert RiskConfig.get()["max_positions"] == 50

    def test_partial_failure_rolls_back(self):
        """If one value in a batch is invalid, none should be applied."""
        original = RiskConfig.get()["max_positions"]
        with pytest.raises(ValueError):
            RiskConfig.update({
                "max_positions": 10,           # valid
                "kelly_fraction": -0.5,        # invalid
            })
        # max_positions must NOT have changed
        assert RiskConfig.get()["max_positions"] == original

    def test_unknown_keys_ignored(self):
        """Unknown keys should not cause errors or be stored."""
        RiskConfig.update({"unknown_key": 42})
        assert "unknown_key" not in RiskConfig.get()

    def test_accept_int_for_float_field(self):
        """Integer values should be accepted for float fields."""
        RiskConfig.update({"kelly_fraction": 1})
        assert RiskConfig.get()["kelly_fraction"] == 1.0


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
