"""Tests for bot activity logging."""

import json

import pytest

from bot.data.activity import MAX_ACTIVITY_ROWS, MAX_SCAN_ROWS, _meta, log_signal_found, log_signal_rejected
from bot.data.models import BotActivity


def test_meta_serializes_dict():
    result = _meta({"key": "value", "num": 42})
    parsed = json.loads(result)
    assert parsed["key"] == "value"
    assert parsed["num"] == 42


def test_meta_handles_non_serializable():
    """datetime and other objects should be handled via default=str."""
    from datetime import datetime

    result = _meta({"ts": datetime(2024, 1, 1)})
    parsed = json.loads(result)
    assert "2024" in parsed["ts"]


def test_bot_activity_model():
    event = BotActivity(
        event_type="signal_found",
        level="info",
        title="Test signal",
        detail="Some detail",
        market_id="mkt1",
        strategy="time_decay",
        metadata_json='{"edge": 0.05}',
    )
    assert event.event_type == "signal_found"
    assert event.level == "info"
    assert event.title == "Test signal"
    assert event.market_id == "mkt1"
    assert event.strategy == "time_decay"


class TestActivityLogFunctions:
    """Verify log functions create correct BotActivity objects.

    These test the logic without DB writes (DB calls may fail in test env).
    """

    @pytest.mark.asyncio
    async def test_log_signal_found_does_not_raise(self):
        """log_signal_found should not raise even if DB is unavailable."""
        # This will fail DB write (no DB in tests) but should not raise
        await log_signal_found(
            strategy="time_decay",
            market_id="mkt1",
            question="Will X happen?",
            edge=0.05,
            price=0.92,
            prob=0.97,
            hours=48.0,
        )

    @pytest.mark.asyncio
    async def test_log_signal_rejected_does_not_raise(self):
        await log_signal_rejected(
            strategy="time_decay",
            market_id="mkt1",
            question="Will X happen?",
            reason="Edge too low",
            edge=0.01,
            price=0.95,
        )


# ---------------------------------------------------------------------------
# M3 — MarketScan pruning constants
# ---------------------------------------------------------------------------


class TestPruneConstants:
    def test_max_scan_rows_exists(self):
        assert MAX_SCAN_ROWS == 5000

    def test_max_activity_rows_exists(self):
        assert MAX_ACTIVITY_ROWS == 5000
