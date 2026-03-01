"""Tests for bot activity logging."""

import json
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import bot.data.activity as activity_module
from bot.data.activity import (
    MAX_ACTIVITY_ROWS,
    MAX_SCAN_ROWS,
    _meta,
    log_bot_event,
    log_cycle_summary,
    log_daily_target_reached,
    log_exit_triggered,
    log_liquidity_rejected,
    log_order_expired,
    log_order_filled,
    log_order_placed,
    log_position_closed,
    log_price_adjustment,
    log_rebalance,
    log_risk_limit_hit,
    log_signal_found,
    log_signal_rejected,
    log_strategy_paused,
    prune_old_activity,
)
from bot.data.models import Base, BotActivity, MarketScan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def mem_engine():
    """In-memory SQLite engine with all tables created (reused per test)."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def patched_async_session(mem_engine):
    """Patch bot.data.activity.async_session so _write uses the in-memory DB."""
    factory = async_sessionmaker(mem_engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def _fake_session():
        async with factory() as sess:
            yield sess

    with patch.object(activity_module, "async_session", _fake_session):
        yield factory


async def _fetch_all_activities(factory) -> list[BotActivity]:
    """Helper: load all BotActivity rows from the in-memory DB."""
    from sqlalchemy import select
    async with factory() as sess:
        result = await sess.execute(select(BotActivity).order_by(BotActivity.id))
        return list(result.scalars().all())


async def _fetch_all_scans(factory) -> list[MarketScan]:
    """Helper: load all MarketScan rows from the in-memory DB."""
    from sqlalchemy import select
    async with factory() as sess:
        result = await sess.execute(select(MarketScan).order_by(MarketScan.id))
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# _meta utility
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# BotActivity model construction
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Smoke tests (no DB — confirm graceful failure, not exception propagation)
# ---------------------------------------------------------------------------


class TestActivityLogFunctions:
    """Verify log functions create correct BotActivity objects.

    These test the logic without DB writes (DB calls may fail in test env).
    """

    async def test_log_signal_found_does_not_raise(self):
        """log_signal_found should not raise even if DB is unavailable."""
        await log_signal_found(
            strategy="time_decay",
            market_id="mkt1",
            question="Will X happen?",
            edge=0.05,
            price=0.92,
            prob=0.97,
            hours=48.0,
        )

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


# ---------------------------------------------------------------------------
# log_order_placed — covers lines 101-103 (level/title branching)
# ---------------------------------------------------------------------------


class TestLogOrderPlaced:
    async def test_filled_order_uses_success_level(self, patched_async_session):
        """When status='filled', level must be 'success' and title says 'filled'."""
        await log_order_placed(
            strategy="time_decay",
            market_id="mkt1",
            question="Will X happen?",
            side="BUY",
            price=0.90,
            size_usd=5.0,
            shares=5.0,
            status="filled",
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert rows[0].level == "success"
        assert "filled" in rows[0].title.lower()

    async def test_pending_order_uses_info_level(self, patched_async_session):
        """When status != 'filled', level must be 'info' and title says 'placed'."""
        await log_order_placed(
            strategy="time_decay",
            market_id="mkt2",
            question="Will Y happen?",
            side="BUY",
            price=0.80,
            size_usd=4.0,
            shares=5.0,
            status="pending",
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert rows[0].level == "info"
        assert "placed" in rows[0].title.lower()

    async def test_order_placed_metadata_contains_expected_keys(self, patched_async_session):
        """metadata_json must include side, price, size_usd, shares, status."""
        await log_order_placed(
            strategy="time_decay",
            market_id="mkt3",
            question="Will Z happen?",
            side="SELL",
            price=0.95,
            size_usd=3.0,
            shares=3.0,
            status="filled",
        )
        rows = await _fetch_all_activities(patched_async_session)
        meta = json.loads(rows[0].metadata_json)
        assert meta["side"] == "SELL"
        assert meta["status"] == "filled"
        assert meta["price"] == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# log_order_expired — covers line 125
# ---------------------------------------------------------------------------


class TestLogOrderExpired:
    async def test_order_expired_creates_warning_event(self, patched_async_session):
        """log_order_expired should create a 'warning' level order_expired event."""
        await log_order_expired(
            market_id="mkt1",
            order_id="ord-abc-123456",
            age_seconds=300.0,
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert rows[0].event_type == "order_expired"
        assert rows[0].level == "warning"

    async def test_order_expired_metadata(self, patched_async_session):
        """order_id and age_seconds should be in metadata."""
        order_id = "ord-xyz-987654"
        await log_order_expired(market_id="mkt1", order_id=order_id, age_seconds=120.0)
        rows = await _fetch_all_activities(patched_async_session)
        meta = json.loads(rows[0].metadata_json)
        assert meta["order_id"] == order_id
        assert meta["age_seconds"] == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# log_order_filled — covers line 140
# ---------------------------------------------------------------------------


class TestLogOrderFilled:
    async def test_order_filled_creates_success_event(self, patched_async_session):
        """log_order_filled should create a 'success' level order_filled event."""
        await log_order_filled(
            market_id="mkt1",
            order_id="ord-fill-001",
            strategy="time_decay",
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert rows[0].event_type == "order_filled"
        assert rows[0].level == "success"
        assert rows[0].strategy == "time_decay"


# ---------------------------------------------------------------------------
# log_position_closed — covers lines 158-160 (positive and negative pnl paths)
# ---------------------------------------------------------------------------


class TestLogPositionClosed:
    async def test_positive_pnl_uses_success_level(self, patched_async_session):
        """Positive pnl should produce level='success' and a '+' sign in title."""
        await log_position_closed(
            market_id="mkt1",
            question="Will X happen?",
            strategy="time_decay",
            pnl=1.50,
            exit_reason="time_decay_exit",
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert rows[0].level == "success"
        assert "+" in rows[0].title

    async def test_negative_pnl_uses_warning_level(self, patched_async_session):
        """Negative pnl should produce level='warning' with no '+' sign."""
        await log_position_closed(
            market_id="mkt2",
            question="Will Y happen?",
            strategy="arbitrage",
            pnl=-0.75,
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert rows[0].level == "warning"
        assert "+" not in rows[0].title

    async def test_zero_pnl_uses_success_level(self, patched_async_session):
        """Zero pnl (break-even) should be treated as success (>= 0)."""
        await log_position_closed(
            market_id="mkt3",
            question="Will Z happen?",
            strategy="time_decay",
            pnl=0.0,
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert rows[0].level == "success"

    async def test_exit_reason_included_in_detail(self, patched_async_session):
        """When exit_reason is provided it must appear in the detail field."""
        await log_position_closed(
            market_id="mkt4",
            question="Q",
            strategy="time_decay",
            pnl=1.0,
            exit_reason="stop_loss",
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert "stop_loss" in rows[0].detail

    async def test_no_exit_reason_omits_reason_from_detail(self, patched_async_session):
        """When exit_reason is omitted the detail must not contain 'Reason:'."""
        await log_position_closed(
            market_id="mkt5",
            question="Q",
            strategy="time_decay",
            pnl=1.0,
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert "Reason:" not in rows[0].detail


# ---------------------------------------------------------------------------
# log_exit_triggered — covers line 181
# ---------------------------------------------------------------------------


class TestLogExitTriggered:
    async def test_exit_triggered_creates_info_event(self, patched_async_session):
        """log_exit_triggered should produce an exit_triggered info event."""
        await log_exit_triggered(
            market_id="mkt1",
            question="Will X happen?",
            strategy="time_decay",
            current_price=0.30,
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert rows[0].event_type == "exit_triggered"
        assert rows[0].level == "info"
        assert rows[0].strategy == "time_decay"

    async def test_exit_triggered_metadata_has_price(self, patched_async_session):
        """current_price should be stored in metadata."""
        await log_exit_triggered(
            market_id="mkt1",
            question="Will X happen?",
            strategy="time_decay",
            current_price=0.25,
        )
        rows = await _fetch_all_activities(patched_async_session)
        meta = json.loads(rows[0].metadata_json)
        assert meta["current_price"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# log_liquidity_rejected — covers line 198
# ---------------------------------------------------------------------------


class TestLogLiquidityRejected:
    async def test_liquidity_rejected_creates_warning_event(self, patched_async_session):
        """log_liquidity_rejected should create a signal_rejected warning event."""
        await log_liquidity_rejected(
            market_id="mkt1",
            reason="Spread too wide",
            spread=0.15,
            best_bid=0.40,
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert rows[0].event_type == "signal_rejected"
        assert rows[0].level == "warning"
        assert rows[0].title == "Liquidity check failed"

    async def test_liquidity_rejected_optional_params(self, patched_async_session):
        """spread and best_bid are optional — must not raise when omitted."""
        await log_liquidity_rejected(market_id="mkt2", reason="No liquidity")
        rows = await _fetch_all_activities(patched_async_session)
        meta = json.loads(rows[0].metadata_json)
        assert meta["spread"] is None
        assert meta["best_bid"] is None

    async def test_liquidity_rejected_metadata_has_reason(self, patched_async_session):
        """reason must be stored in metadata."""
        await log_liquidity_rejected(market_id="mkt1", reason="thin book")
        rows = await _fetch_all_activities(patched_async_session)
        meta = json.loads(rows[0].metadata_json)
        assert meta["reason"] == "thin book"


# ---------------------------------------------------------------------------
# log_cycle_summary — covers line 220
# ---------------------------------------------------------------------------


class TestLogCycleSummary:
    async def test_cycle_summary_creates_info_event(self, patched_async_session):
        """log_cycle_summary should create a cycle_summary info event."""
        await log_cycle_summary(
            cycle=42,
            equity=12.50,
            signals_found=5,
            signals_approved=2,
            orders_placed=1,
            pending_orders=0,
            urgency=1.5,
            daily_progress=0.40,
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert rows[0].event_type == "cycle_summary"
        assert rows[0].level == "info"
        assert "42" in rows[0].title

    async def test_cycle_summary_metadata_completeness(self, patched_async_session):
        """All input fields must appear in metadata_json."""
        await log_cycle_summary(
            cycle=1,
            equity=10.0,
            signals_found=3,
            signals_approved=1,
            orders_placed=1,
            pending_orders=2,
            urgency=0.8,
            daily_progress=0.10,
        )
        rows = await _fetch_all_activities(patched_async_session)
        meta = json.loads(rows[0].metadata_json)
        assert meta["cycle"] == 1
        assert meta["equity"] == pytest.approx(10.0)
        assert meta["signals_found"] == 3
        assert meta["pending_orders"] == 2
        assert meta["urgency"] == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# log_bot_event — covers line 247
# ---------------------------------------------------------------------------


class TestLogBotEvent:
    async def test_bot_event_creates_correct_event(self, patched_async_session):
        """log_bot_event should create a bot_event with the given level and title."""
        await log_bot_event(
            title="Bot started",
            detail="Paper mode active",
            level="info",
            metadata={"version": "1.0"},
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert rows[0].event_type == "bot_event"
        assert rows[0].level == "info"
        assert rows[0].title == "Bot started"
        assert rows[0].detail == "Paper mode active"

    async def test_bot_event_defaults(self, patched_async_session):
        """log_bot_event with only title should use default level='info'."""
        await log_bot_event(title="minimal event")
        rows = await _fetch_all_activities(patched_async_session)
        assert rows[0].level == "info"
        meta = json.loads(rows[0].metadata_json)
        assert meta == {}

    async def test_bot_event_error_level(self, patched_async_session):
        """log_bot_event supports error level for critical events."""
        await log_bot_event(title="Critical failure", level="error")
        rows = await _fetch_all_activities(patched_async_session)
        assert rows[0].level == "error"


# ---------------------------------------------------------------------------
# log_rebalance — covers lines 267-268 (sign branch for closed_pnl)
# ---------------------------------------------------------------------------


class TestLogRebalance:
    async def test_rebalance_positive_pnl_sign(self, patched_async_session):
        """When closed_pnl >= 0, the sign prefix should be '+'."""
        await log_rebalance(
            closed_market_id="old_mkt",
            closed_question="Old question",
            closed_strategy="time_decay",
            closed_pnl=0.50,
            new_market_id="new_mkt",
            new_question="New question",
            new_strategy="arbitrage",
            new_edge=0.08,
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert "+$0.50" in rows[0].detail

    async def test_rebalance_negative_pnl_sign(self, patched_async_session):
        """When closed_pnl < 0, there should be no '+' prefix and pnl in detail."""
        await log_rebalance(
            closed_market_id="old_mkt",
            closed_question="Old question",
            closed_strategy="time_decay",
            closed_pnl=-0.30,
            new_market_id="new_mkt",
            new_question="New question",
            new_strategy="arbitrage",
            new_edge=0.10,
        )
        rows = await _fetch_all_activities(patched_async_session)
        # sign="" for negative — source formats as f"${closed_pnl:.2f}" → "$-0.30"
        assert "+$" not in rows[0].detail
        assert "$-0.30" in rows[0].detail

    async def test_rebalance_metadata_contains_both_markets(self, patched_async_session):
        """Both closed and new market ids/strategies must appear in metadata."""
        await log_rebalance(
            closed_market_id="closed_id",
            closed_question="Q1",
            closed_strategy="strategy_a",
            closed_pnl=1.0,
            new_market_id="new_id",
            new_question="Q2",
            new_strategy="strategy_b",
            new_edge=0.05,
        )
        rows = await _fetch_all_activities(patched_async_session)
        meta = json.loads(rows[0].metadata_json)
        assert meta["closed_market_id"] == "closed_id"
        assert meta["new_market_id"] == "new_id"
        assert meta["closed_strategy"] == "strategy_a"
        assert meta["new_strategy"] == "strategy_b"


# ---------------------------------------------------------------------------
# log_price_adjustment — covers lines 297-298
# ---------------------------------------------------------------------------


class TestLogPriceAdjustment:
    async def test_price_adjustment_creates_info_event(self, patched_async_session):
        """log_price_adjustment should create a price_adjust info event."""
        await log_price_adjustment(
            market_id="mkt1",
            strategy="time_decay",
            signal_price=0.90,
            actual_price=0.92,
            reason="order book shift",
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert rows[0].event_type == "price_adjust"
        assert rows[0].level == "info"

    async def test_price_adjustment_slippage_in_metadata(self, patched_async_session):
        """Slippage (actual - signal) must be computed and stored in metadata."""
        await log_price_adjustment(
            market_id="mkt1",
            strategy="time_decay",
            signal_price=0.88,
            actual_price=0.91,
            reason="best_ask",
        )
        rows = await _fetch_all_activities(patched_async_session)
        meta = json.loads(rows[0].metadata_json)
        assert meta["slippage"] == pytest.approx(0.03)
        assert meta["signal_price"] == pytest.approx(0.88)
        assert meta["actual_price"] == pytest.approx(0.91)

    async def test_price_adjustment_negative_slippage(self, patched_async_session):
        """Negative slippage (price improved) should also be stored correctly."""
        await log_price_adjustment(
            market_id="mkt1",
            strategy="time_decay",
            signal_price=0.90,
            actual_price=0.88,
            reason="improved bid",
        )
        rows = await _fetch_all_activities(patched_async_session)
        meta = json.loads(rows[0].metadata_json)
        assert meta["slippage"] == pytest.approx(-0.02)


# ---------------------------------------------------------------------------
# prune_old_activity — covers lines 320-355
# ---------------------------------------------------------------------------


class TestPruneOldActivity:
    async def test_prune_does_nothing_when_under_limit(self, patched_async_session):
        """When row count is below MAX_ACTIVITY_ROWS, nothing should be deleted."""
        # Insert 3 rows — well below the 5000 limit
        factory = patched_async_session
        async with factory() as sess:
            for i in range(3):
                sess.add(BotActivity(
                    event_type="cycle_summary",
                    level="info",
                    title=f"Cycle #{i}",
                ))
            await sess.commit()

        await prune_old_activity()

        rows = await _fetch_all_activities(factory)
        assert len(rows) == 3

    async def test_prune_removes_excess_activity_rows(self, patched_async_session):
        """When BotActivity rows exceed MAX_ACTIVITY_ROWS, oldest should be pruned."""
        from datetime import timedelta
        from unittest.mock import patch as _patch

        factory = patched_async_session

        # Temporarily lower the max so we can test with a small dataset
        with _patch.object(activity_module, "MAX_ACTIVITY_ROWS", 3):
            async with factory() as sess:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                for i in range(5):
                    row = BotActivity(
                        event_type="cycle_summary",
                        level="info",
                        title=f"Cycle #{i}",
                        timestamp=now + timedelta(seconds=i),
                    )
                    sess.add(row)
                await sess.commit()

            await prune_old_activity()

        rows = await _fetch_all_activities(factory)
        # Oldest 2 should have been deleted; 3 remain
        assert len(rows) == 3
        # Remaining rows should be the newest ones (Cycle #2, #3, #4)
        titles = {r.title for r in rows}
        assert "Cycle #0" not in titles
        assert "Cycle #1" not in titles

    async def test_prune_removes_excess_scan_rows(self, patched_async_session):
        """When MarketScan rows exceed MAX_SCAN_ROWS, oldest should be pruned."""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch as _patch

        factory = patched_async_session

        with _patch.object(activity_module, "MAX_SCAN_ROWS", 2):
            async with factory() as sess:
                now = datetime.now(timezone.utc)
                for i in range(4):
                    scan = MarketScan(
                        market_id=f"mkt{i}",
                        question=f"Q{i}",
                        signal_strategy="time_decay",
                        scanned_at=now + timedelta(seconds=i),
                    )
                    sess.add(scan)
                await sess.commit()

            await prune_old_activity()

        scans = await _fetch_all_scans(factory)
        # Oldest 2 should have been pruned; 2 remain
        assert len(scans) == 2
        market_ids = {s.market_id for s in scans}
        assert "mkt0" not in market_ids
        assert "mkt1" not in market_ids

    async def test_prune_handles_exception_gracefully(self):
        """prune_old_activity should catch exceptions and not propagate them."""
        with patch.object(activity_module, "async_session") as mock_cm:
            # Make the context manager raise an exception
            mock_cm.side_effect = RuntimeError("DB unavailable")
            await prune_old_activity()  # must not raise

    async def test_prune_when_both_tables_under_limit(self, patched_async_session):
        """With both tables under their limits, prune should be a no-op."""
        factory = patched_async_session
        # Ensure empty tables
        await prune_old_activity()
        rows = await _fetch_all_activities(factory)
        scans = await _fetch_all_scans(factory)
        assert rows == []
        assert scans == []


# ---------------------------------------------------------------------------
# log_signal_found with hours=None (branch coverage)
# ---------------------------------------------------------------------------


class TestLogSignalFoundBranch:
    async def test_signal_found_without_hours(self, patched_async_session):
        """hours=None should produce a detail without the hours suffix."""
        await log_signal_found(
            strategy="time_decay",
            market_id="mkt1",
            question="Will X happen?",
            edge=0.05,
            price=0.90,
            prob=0.95,
            hours=None,
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert "to resolution" not in rows[0].detail

    async def test_signal_found_with_hours(self, patched_async_session):
        """When hours is provided, detail should include 'to resolution'."""
        await log_signal_found(
            strategy="time_decay",
            market_id="mkt1",
            question="Will X happen?",
            edge=0.05,
            price=0.90,
            prob=0.95,
            hours=72.0,
        )
        rows = await _fetch_all_activities(patched_async_session)
        assert "to resolution" in rows[0].detail


# ---------------------------------------------------------------------------
# log_strategy_paused — covers lines 289-309
# ---------------------------------------------------------------------------


class TestLogStrategyPaused:
    async def test_creates_warning_event(self, patched_async_session):
        """log_strategy_paused should create a warning-level bot_event."""
        await log_strategy_paused(strategy="time_decay", win_rate=0.25, total_pnl=-2.5)
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert rows[0].event_type == "bot_event"
        assert rows[0].level == "warning"
        assert "time_decay" in rows[0].title

    async def test_metadata_contains_reason_and_stats(self, patched_async_session):
        """metadata_json should include auto_pause reason, win_rate, and total_pnl."""
        await log_strategy_paused(strategy="arbitrage", win_rate=0.20, total_pnl=-3.0)
        rows = await _fetch_all_activities(patched_async_session)
        meta = json.loads(rows[0].metadata_json)
        assert meta["reason"] == "auto_pause"
        assert meta["win_rate"] == pytest.approx(0.20)
        assert meta["total_pnl"] == pytest.approx(-3.0)


# ---------------------------------------------------------------------------
# log_risk_limit_hit — covers lines 312-328
# ---------------------------------------------------------------------------


class TestLogRiskLimitHit:
    async def test_creates_error_event(self, patched_async_session):
        """log_risk_limit_hit should create an error-level bot_event."""
        await log_risk_limit_hit(limit_type="daily_loss", current=0.12, threshold=0.10)
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert rows[0].event_type == "bot_event"
        assert rows[0].level == "error"
        assert "daily_loss" in rows[0].title

    async def test_metadata_contains_limit_details(self, patched_async_session):
        """metadata_json should include limit_type, current, and threshold."""
        await log_risk_limit_hit(limit_type="max_drawdown", current=0.25, threshold=0.20)
        rows = await _fetch_all_activities(patched_async_session)
        meta = json.loads(rows[0].metadata_json)
        assert meta["limit_type"] == "max_drawdown"
        assert meta["current"] == pytest.approx(0.25)
        assert meta["threshold"] == pytest.approx(0.20)


# ---------------------------------------------------------------------------
# log_daily_target_reached — covers lines 331-347
# ---------------------------------------------------------------------------


class TestLogDailyTargetReached:
    async def test_creates_success_event(self, patched_async_session):
        """log_daily_target_reached should create a success-level bot_event."""
        await log_daily_target_reached(equity=15.0, daily_pnl=0.20, target_pct=0.01)
        rows = await _fetch_all_activities(patched_async_session)
        assert len(rows) == 1
        assert rows[0].event_type == "bot_event"
        assert rows[0].level == "success"
        assert "target" in rows[0].title.lower()

    async def test_metadata_contains_financial_details(self, patched_async_session):
        """metadata_json should include equity, daily_pnl, and target_pct."""
        await log_daily_target_reached(equity=20.0, daily_pnl=0.30, target_pct=0.015)
        rows = await _fetch_all_activities(patched_async_session)
        meta = json.loads(rows[0].metadata_json)
        assert meta["equity"] == pytest.approx(20.0)
        assert meta["daily_pnl"] == pytest.approx(0.30)
        assert meta["target_pct"] == pytest.approx(0.015)
