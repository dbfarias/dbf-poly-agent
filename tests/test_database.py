"""Tests for bot/data/database.py — migration allowlist and validation."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

import pytest

from bot.data.database import ALLOWED_TABLES, _COLUMN_NAME_RE


class TestMigrationAllowlist:
    """Verify table/column validation in migrations."""

    def test_allowed_tables_contains_expected(self):
        assert "trades" in ALLOWED_TABLES
        assert "positions" in ALLOWED_TABLES
        assert "portfolio_snapshots" in ALLOWED_TABLES
        assert "bot_activity" in ALLOWED_TABLES

    def test_allowed_tables_is_frozenset(self):
        assert isinstance(ALLOWED_TABLES, frozenset)

    def test_column_regex_accepts_valid_names(self):
        valid = ["exit_reason", "pnl", "created_at", "a", "some_col_123"]
        for name in valid:
            assert _COLUMN_NAME_RE.match(name), f"Should accept '{name}'"

    def test_column_regex_rejects_invalid_names(self):
        invalid = [
            "1abc",            # starts with digit
            "_private",        # starts with underscore
            "DROP TABLE",      # spaces / SQL injection
            "col;DROP",        # semicolon
            "",                # empty
            "A" * 65,          # too long
            "CamelCase",       # uppercase
        ]
        for name in invalid:
            assert not _COLUMN_NAME_RE.match(name), f"Should reject '{name}'"
