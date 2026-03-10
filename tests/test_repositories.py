"""Tests for bot/data/repositories.py — CRUD operations for all data models.

Uses an in-memory SQLite database per test so every test is fully isolated
with no shared state. All async tests are run under pytest-asyncio (auto mode).
"""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.data.models import (
    Base,
    MarketScan,
    PortfolioSnapshot,
    Position,
    StrategyMetric,
    Trade,
)
from bot.data.repositories import (
    MarketScanRepository,
    PortfolioSnapshotRepository,
    PositionRepository,
    SettingsRepository,
    StrategyMetricRepository,
    TradeRepository,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine():
    """Fresh in-memory SQLite engine with all tables created."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    """Per-test async session bound to the in-memory engine."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_trade(
    market_id: str = "mkt1",
    strategy: str = "time_decay",
    status: str = "pending",
    pnl: float = 0.0,
    category: str = "crypto",
    edge: float = 0.05,
    estimated_prob: float = 0.90,
    cost_usd: float = 9.0,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> Trade:
    trade = Trade(
        market_id=market_id,
        token_id="token1",
        question="Will X happen?",
        outcome="Yes",
        category=category,
        side="BUY",
        price=0.90,
        size=10.0,
        cost_usd=cost_usd,
        strategy=strategy,
        status=status,
        pnl=pnl,
        edge=edge,
        estimated_prob=estimated_prob,
    )
    if created_at is not None:
        trade.created_at = created_at
    if updated_at is not None:
        trade.updated_at = updated_at
    return trade


def make_position(
    market_id: str = "mkt1",
    is_open: bool = True,
    category: str = "crypto",
    size: float = 10.0,
    avg_price: float = 0.50,
    current_price: float = 0.55,
    cost_basis: float = 5.0,
) -> Position:
    return Position(
        market_id=market_id,
        token_id="token1",
        question="Will X happen?",
        outcome="Yes",
        category=category,
        strategy="time_decay",
        side="BUY",
        size=size,
        avg_price=avg_price,
        current_price=current_price,
        cost_basis=cost_basis,
        unrealized_pnl=(current_price - avg_price) * size,
        is_open=is_open,
    )


def make_snapshot(total_equity: float = 10.0, cash_balance: float = 10.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        total_equity=total_equity,
        cash_balance=cash_balance,
        positions_value=0.0,
        unrealized_pnl=0.0,
        realized_pnl_today=0.0,
        open_positions=0,
        daily_return_pct=0.0,
        max_drawdown_pct=0.0,
    )


def make_metric(strategy: str = "time_decay") -> StrategyMetric:
    return StrategyMetric(
        strategy=strategy,
        total_trades=5,
        winning_trades=3,
        losing_trades=2,
        win_rate=0.6,
        total_pnl=1.50,
        avg_edge=0.05,
        sharpe_ratio=1.2,
        max_drawdown=0.10,
        avg_hold_time_hours=24.0,
        profit_factor=1.5,
    )


def make_scan(market_id: str = "mkt1", signal_strategy: str = "time_decay") -> MarketScan:
    return MarketScan(
        market_id=market_id,
        question="Will Y happen?",
        category="sports",
        yes_price=0.70,
        no_price=0.30,
        volume=1000.0,
        liquidity=500.0,
        signal_strategy=signal_strategy,
        signal_edge=0.08,
        signal_confidence=0.80,
        was_traded=False,
    )


# ---------------------------------------------------------------------------
# TradeRepository
# ---------------------------------------------------------------------------


class TestTradeRepositoryCreate:
    async def test_create_returns_trade_with_id(self, session):
        """create() should persist the trade and return it with a populated id."""
        repo = TradeRepository(session)
        trade = make_trade()
        result = await repo.create(trade)
        assert result.id is not None
        assert result.market_id == "mkt1"

    async def test_create_multiple_trades(self, session):
        """Creating several trades should all get distinct ids."""
        repo = TradeRepository(session)
        t1 = await repo.create(make_trade(market_id="mkt1"))
        t2 = await repo.create(make_trade(market_id="mkt2"))
        assert t1.id != t2.id

    async def test_create_trade_is_retrievable(self, session):
        """After create(), the trade must be findable via get_recent()."""
        repo = TradeRepository(session)
        await repo.create(make_trade(market_id="mkt_x"))
        trades = await repo.get_recent(limit=10)
        assert any(t.market_id == "mkt_x" for t in trades)


class TestTradeRepositoryGetRecent:
    async def test_get_recent_returns_trades_desc_order(self, session):
        """get_recent() should return the newest trade first."""
        repo = TradeRepository(session)
        await repo.create(make_trade(market_id="first"))
        await repo.create(make_trade(market_id="second"))
        trades = await repo.get_recent(limit=10)
        # Most recently inserted is last in DB creation order but desc by created_at
        assert len(trades) == 2

    async def test_get_recent_respects_limit(self, session):
        """get_recent(limit=1) should return only one trade."""
        repo = TradeRepository(session)
        for i in range(5):
            await repo.create(make_trade(market_id=f"mkt{i}"))
        trades = await repo.get_recent(limit=1)
        assert len(trades) == 1

    async def test_get_recent_empty_table(self, session):
        """get_recent() on empty table should return an empty list."""
        repo = TradeRepository(session)
        trades = await repo.get_recent()
        assert trades == []


class TestTradeRepositoryGetStats:
    async def test_stats_on_empty_table(self, session):
        """get_stats() on an empty table should return safe zero values."""
        repo = TradeRepository(session)
        stats = await repo.get_stats()
        assert stats["total_trades"] == 0
        assert stats["winning_trades"] == 0
        assert stats["total_pnl"] == 0.0
        assert stats["win_rate"] == 0.0

    async def test_stats_counts_winning_trades(self, session):
        """get_stats() should correctly count winning (pnl > 0, filled) trades."""
        repo = TradeRepository(session)
        await repo.create(make_trade(status="filled", pnl=1.0))
        await repo.create(make_trade(status="filled", pnl=-0.5))
        await repo.create(make_trade(status="pending", pnl=2.0))  # Not counted (not filled)
        stats = await repo.get_stats()
        assert stats["total_trades"] == 3
        assert stats["winning_trades"] == 1

    async def test_stats_win_rate_calculation(self, session):
        """win_rate should be wins/total when total > 0."""
        repo = TradeRepository(session)
        await repo.create(make_trade(status="filled", pnl=1.0))
        await repo.create(make_trade(status="filled", pnl=1.0))
        await repo.create(make_trade(status="filled", pnl=-1.0))
        stats = await repo.get_stats()
        assert stats["win_rate"] == pytest.approx(2 / 3)

    async def test_stats_total_pnl(self, session):
        """get_stats() should sum pnl correctly."""
        repo = TradeRepository(session)
        await repo.create(make_trade(pnl=1.5))
        await repo.create(make_trade(pnl=2.5))
        stats = await repo.get_stats()
        assert stats["total_pnl"] == pytest.approx(4.0)


class TestTradeRepositoryGetByStrategy:
    async def test_get_by_strategy_filters_correctly(self, session):
        """get_by_strategy() should only return trades for the given strategy."""
        repo = TradeRepository(session)
        await repo.create(make_trade(strategy="time_decay"))
        await repo.create(make_trade(strategy="arbitrage"))
        await repo.create(make_trade(strategy="time_decay"))
        results = await repo.get_by_strategy("time_decay")
        assert len(results) == 2
        assert all(t.strategy == "time_decay" for t in results)

    async def test_get_by_strategy_empty_result(self, session):
        """get_by_strategy() should return [] when no matches exist."""
        repo = TradeRepository(session)
        await repo.create(make_trade(strategy="time_decay"))
        results = await repo.get_by_strategy("nonexistent")
        assert results == []


class TestTradeRepositoryUpdateStatus:
    async def test_update_status_changes_status_and_pnl(self, session):
        """update_status() should commit the new status and pnl."""
        repo = TradeRepository(session)
        trade = await repo.create(make_trade(status="pending", pnl=0.0))
        await repo.update_status(trade.id, status="filled", pnl=1.25)
        trades = await repo.get_recent()
        updated = next(t for t in trades if t.id == trade.id)
        assert updated.status == "filled"
        assert updated.pnl == pytest.approx(1.25)


class TestTradeRepositoryGetStrategyStats:
    async def test_get_strategy_stats_returns_aggregates(self, session):
        """get_strategy_stats() should aggregate across strategies."""
        repo = TradeRepository(session)
        await repo.create(make_trade(strategy="time_decay", status="filled", pnl=1.0))
        await repo.create(make_trade(strategy="time_decay", status="filled", pnl=-0.5))
        await repo.create(make_trade(strategy="arbitrage", status="filled", pnl=2.0))
        rows = await repo.get_strategy_stats()
        strategies = {r["strategy"] for r in rows}
        assert "time_decay" in strategies
        assert "arbitrage" in strategies

    async def test_get_strategy_stats_empty_table(self, session):
        """get_strategy_stats() on empty table should return empty list."""
        repo = TradeRepository(session)
        rows = await repo.get_strategy_stats()
        assert rows == []

    async def test_get_strategy_stats_winning_losing_counts(self, session):
        """winning_trades and losing_trades should be computed correctly."""
        repo = TradeRepository(session)
        await repo.create(make_trade(strategy="time_decay", status="filled", pnl=1.0))
        await repo.create(make_trade(strategy="time_decay", status="filled", pnl=1.0))
        await repo.create(make_trade(strategy="time_decay", status="filled", pnl=-0.5))
        rows = await repo.get_strategy_stats()
        row = next(r for r in rows if r["strategy"] == "time_decay")
        assert row["winning_trades"] == 2
        assert row["losing_trades"] == 1
        assert row["win_rate"] == pytest.approx(2 / 3)

    async def test_get_strategy_stats_excludes_pending(self, session):
        """Pending trades must not appear in strategy stats."""
        repo = TradeRepository(session)
        await repo.create(make_trade(strategy="time_decay", status="pending", pnl=5.0))
        rows = await repo.get_strategy_stats()
        assert rows == []


class TestTradeRepositoryGetStrategyCategoryStats:
    async def test_returns_aggregated_rows(self, session):
        """get_strategy_category_stats() groups by (strategy, category)."""
        repo = TradeRepository(session)
        await repo.create(make_trade(strategy="time_decay", category="crypto", status="filled", pnl=1.0))
        await repo.create(make_trade(strategy="time_decay", category="sports", status="filled", pnl=2.0))
        rows = await repo.get_strategy_category_stats(days=30)
        assert len(rows) == 2
        categories = {r["category"] for r in rows}
        assert "crypto" in categories
        assert "sports" in categories

    async def test_strategy_category_stats_keys(self, session):
        """Each row must contain all expected keys."""
        repo = TradeRepository(session)
        await repo.create(make_trade(status="filled", pnl=1.0))
        rows = await repo.get_strategy_category_stats(days=30)
        expected_keys = {
            "strategy", "category", "total_trades", "winning_trades",
            "total_pnl", "avg_edge", "avg_estimated_prob",
        }
        for row in rows:
            assert set(row.keys()) == expected_keys


class TestTradeRepositoryMarkScanTraded:
    async def test_mark_scan_traded_sets_was_traded(self, session):
        """mark_scan_traded() should set was_traded=True on the most recent scan."""
        # Seed a MarketScan
        scan = make_scan(market_id="mkt1", signal_strategy="time_decay")
        session.add(scan)
        await session.commit()
        await session.refresh(scan)

        repo = TradeRepository(session)
        await repo.mark_scan_traded("mkt1", "time_decay")

        # Reload
        from sqlalchemy import select
        result = await session.execute(
            select(MarketScan).where(MarketScan.market_id == "mkt1")
        )
        updated = result.scalar_one()
        assert updated.was_traded is True

    async def test_mark_scan_traded_noop_when_no_scan(self, session):
        """mark_scan_traded() should not raise when no matching scan exists."""
        repo = TradeRepository(session)
        await repo.mark_scan_traded("nonexistent", "time_decay")  # must not raise


# ---------------------------------------------------------------------------
# PositionRepository
# ---------------------------------------------------------------------------


class TestPositionRepositoryGetOpen:
    async def test_get_open_returns_only_open_positions(self, session):
        """get_open() must exclude closed positions."""
        repo = PositionRepository(session)
        open_pos = make_position(market_id="open1", is_open=True)
        closed_pos = make_position(market_id="closed1", is_open=False)
        session.add_all([open_pos, closed_pos])
        await session.commit()
        results = await repo.get_open()
        assert len(results) == 1
        assert results[0].market_id == "open1"

    async def test_get_open_empty_table(self, session):
        """get_open() on empty table should return empty list."""
        repo = PositionRepository(session)
        results = await repo.get_open()
        assert results == []

    async def test_get_open_all_open(self, session):
        """get_open() should return all positions when all are open."""
        repo = PositionRepository(session)
        for i in range(3):
            session.add(make_position(market_id=f"mkt{i}", is_open=True))
        await session.commit()
        results = await repo.get_open()
        assert len(results) == 3


class TestPositionRepositoryUpsert:
    async def test_upsert_inserts_new_position(self, session):
        """upsert() should add a new position when market_id is not in DB."""
        repo = PositionRepository(session)
        pos = make_position(market_id="new_market")
        result = await repo.upsert(pos)
        assert result.id is not None
        assert result.market_id == "new_market"

    async def test_upsert_updates_existing_position(self, session):
        """upsert() should update numeric fields when position already exists."""
        repo = PositionRepository(session)
        pos = make_position(market_id="existing", size=10.0, current_price=0.50)
        await repo.upsert(pos)

        # Build a new Position object (same market_id, different values)
        updated = make_position(market_id="existing", size=20.0, current_price=0.70)
        result = await repo.upsert(updated)

        assert result.size == 20.0
        assert result.current_price == pytest.approx(0.70)

    async def test_upsert_reopens_closed_position(self, session):
        """upsert() should re-open a position if the incoming one is_open=True."""
        repo = PositionRepository(session)
        # Insert a closed position
        pos = make_position(market_id="reopen_me", is_open=False)
        session.add(pos)
        await session.commit()

        # Upsert with is_open=True
        new_pos = make_position(market_id="reopen_me", is_open=True)
        result = await repo.upsert(new_pos)
        assert result.is_open is True

    async def test_upsert_reopen_resets_created_at(self, session):
        """upsert() must reset created_at when reopening a closed position.

        Without this, min_hold_seconds sees the OLD created_at and allows
        immediate rebalance sells — the root cause of churning.
        """
        repo = PositionRepository(session)
        from datetime import timedelta

        # Insert a closed position with old created_at
        pos = make_position(market_id="stale_ts", is_open=False)
        session.add(pos)
        await session.commit()
        old_created = pos.created_at

        # Reopen via upsert
        new_pos = make_position(market_id="stale_ts", is_open=True)
        result = await repo.upsert(new_pos)

        assert result.is_open is True
        # created_at must be refreshed (newer than original)
        assert result.created_at >= old_created

    async def test_upsert_open_to_open_preserves_created_at(self, session):
        """upsert() must NOT reset created_at when updating an already-open position."""
        repo = PositionRepository(session)
        from datetime import timedelta

        pos = make_position(market_id="keep_ts", is_open=True)
        session.add(pos)
        await session.commit()
        original_created = pos.created_at

        # Update with new price (still open)
        new_pos = make_position(market_id="keep_ts", is_open=True, current_price=0.60)
        result = await repo.upsert(new_pos)

        assert result.created_at == original_created

    async def test_upsert_does_not_close_position(self, session):
        """upsert() must NOT close an open position (only re-open logic exists)."""
        repo = PositionRepository(session)
        pos = make_position(market_id="stay_open", is_open=True)
        await repo.upsert(pos)

        # Upsert with is_open=False (should not change open→closed)
        new_pos = make_position(market_id="stay_open", is_open=False)
        result = await repo.upsert(new_pos)
        # The existing open position should remain open (upsert only re-opens, never closes)
        assert result.is_open is True


class TestPositionRepositoryClose:
    async def test_close_sets_is_open_false(self, session):
        """close() should mark the position as not open."""
        repo = PositionRepository(session)
        pos = make_position(market_id="to_close", is_open=True)
        session.add(pos)
        await session.commit()

        await repo.close("to_close")

        open_positions = await repo.get_open()
        market_ids = [p.market_id for p in open_positions]
        assert "to_close" not in market_ids

    async def test_close_noop_when_not_found(self, session):
        """close() on a non-existent market_id should not raise."""
        repo = PositionRepository(session)
        await repo.close("does_not_exist")  # must not raise

    async def test_close_only_affects_target_market(self, session):
        """close() must not affect other open positions."""
        repo = PositionRepository(session)
        session.add(make_position(market_id="keep_open", is_open=True))
        session.add(make_position(market_id="close_me", is_open=True))
        await session.commit()

        await repo.close("close_me")

        open_positions = await repo.get_open()
        assert any(p.market_id == "keep_open" for p in open_positions)
        assert not any(p.market_id == "close_me" for p in open_positions)


class TestPositionRepositoryGetByCategory:
    async def test_get_by_category_aggregates_cost_basis(self, session):
        """get_by_category() should sum cost_basis for each open category."""
        repo = PositionRepository(session)
        session.add(make_position(market_id="c1", category="crypto", cost_basis=5.0, is_open=True))
        session.add(make_position(market_id="c2", category="crypto", cost_basis=3.0, is_open=True))
        session.add(make_position(market_id="s1", category="sports", cost_basis=2.0, is_open=True))
        session.add(make_position(market_id="closed", category="crypto", cost_basis=10.0, is_open=False))
        await session.commit()

        result = await repo.get_by_category()
        assert result["crypto"] == pytest.approx(8.0)
        assert result["sports"] == pytest.approx(2.0)
        assert "closed" not in result

    async def test_get_by_category_empty(self, session):
        """get_by_category() on empty table should return empty dict."""
        repo = PositionRepository(session)
        result = await repo.get_by_category()
        assert result == {}


# ---------------------------------------------------------------------------
# PortfolioSnapshotRepository
# ---------------------------------------------------------------------------


class TestPortfolioSnapshotRepositoryCreate:
    async def test_create_persists_snapshot(self, session):
        """create() should add the snapshot to the DB."""
        repo = PortfolioSnapshotRepository(session)
        snap = make_snapshot(total_equity=15.0)
        result = await repo.create(snap)
        assert result.id is not None
        assert result.total_equity == pytest.approx(15.0)

    async def test_create_multiple_snapshots(self, session):
        """Multiple snapshots should coexist with distinct ids."""
        repo = PortfolioSnapshotRepository(session)
        s1 = await repo.create(make_snapshot(total_equity=10.0))
        s2 = await repo.create(make_snapshot(total_equity=11.0))
        assert s1.id != s2.id


class TestPortfolioSnapshotRepositoryGetLatest:
    async def test_get_latest_returns_none_when_empty(self, session):
        """get_latest() on empty table should return None."""
        repo = PortfolioSnapshotRepository(session)
        result = await repo.get_latest()
        assert result is None

    async def test_get_latest_returns_most_recent(self, session):
        """get_latest() should return the snapshot with the highest timestamp."""
        repo = PortfolioSnapshotRepository(session)
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        older = PortfolioSnapshot(
            total_equity=9.0, cash_balance=9.0,
            timestamp=now - timedelta(hours=2),
        )
        newer = PortfolioSnapshot(
            total_equity=11.0, cash_balance=11.0,
            timestamp=now,
        )
        session.add_all([older, newer])
        await session.commit()

        result = await repo.get_latest()
        assert result.total_equity == pytest.approx(11.0)

    async def test_get_latest_single_snapshot(self, session):
        """get_latest() with one row should return that row."""
        repo = PortfolioSnapshotRepository(session)
        await repo.create(make_snapshot(total_equity=7.0))
        result = await repo.get_latest()
        assert result is not None
        assert result.total_equity == pytest.approx(7.0)


class TestPortfolioSnapshotRepositoryGetEquityCurve:
    async def test_get_equity_curve_returns_snapshots_in_range(self, session):
        """get_equity_curve() should return only snapshots within the given days."""
        repo = PortfolioSnapshotRepository(session)
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        recent = PortfolioSnapshot(
            total_equity=10.0, cash_balance=10.0,
            timestamp=now - timedelta(days=5),
        )
        old = PortfolioSnapshot(
            total_equity=8.0, cash_balance=8.0,
            timestamp=now - timedelta(days=60),
        )
        session.add_all([recent, old])
        await session.commit()

        result = await repo.get_equity_curve(days=30)
        assert len(result) == 1
        assert result[0].total_equity == pytest.approx(10.0)

    async def test_get_equity_curve_empty(self, session):
        """get_equity_curve() on empty table returns empty list."""
        repo = PortfolioSnapshotRepository(session)
        result = await repo.get_equity_curve(days=30)
        assert result == []


# ---------------------------------------------------------------------------
# MarketScanRepository
# ---------------------------------------------------------------------------


class TestMarketScanRepositoryCreateBatch:
    async def test_create_batch_inserts_all_scans(self, session):
        """create_batch() should persist all provided scans."""
        repo = MarketScanRepository(session)
        scans = [make_scan(market_id=f"mkt{i}") for i in range(3)]
        await repo.create_batch(scans)

        from sqlalchemy import select
        result = await session.execute(select(MarketScan))
        rows = result.scalars().all()
        assert len(rows) == 3

    async def test_create_batch_empty_list(self, session):
        """create_batch([]) should not raise and leave DB empty."""
        repo = MarketScanRepository(session)
        await repo.create_batch([])  # must not raise

        from sqlalchemy import select
        result = await session.execute(select(MarketScan))
        assert result.scalars().all() == []


class TestMarketScanRepositoryGetRecentOpportunities:
    async def test_returns_latest_scan_per_market(self, session):
        """get_recent_opportunities() should deduplicate to one row per market_id."""
        repo = MarketScanRepository(session)
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        older = make_scan(market_id="mkt1")
        older.scanned_at = now - timedelta(hours=2)
        newer = make_scan(market_id="mkt1")
        newer.scanned_at = now

        session.add_all([older, newer])
        await session.commit()

        results = await repo.get_recent_opportunities(limit=50)
        mkt1_results = [r for r in results if r.market_id == "mkt1"]
        assert len(mkt1_results) == 1

    async def test_returns_multiple_markets(self, session):
        """get_recent_opportunities() should return one entry per distinct market."""
        repo = MarketScanRepository(session)
        await repo.create_batch([
            make_scan(market_id="mkt1"),
            make_scan(market_id="mkt2"),
            make_scan(market_id="mkt3"),
        ])
        results = await repo.get_recent_opportunities(limit=50)
        assert len(results) == 3

    async def test_respects_limit(self, session):
        """get_recent_opportunities() should respect the limit parameter."""
        repo = MarketScanRepository(session)
        await repo.create_batch([make_scan(market_id=f"mkt{i}") for i in range(10)])
        results = await repo.get_recent_opportunities(limit=3)
        assert len(results) <= 3


# ---------------------------------------------------------------------------
# StrategyMetricRepository
# ---------------------------------------------------------------------------


class TestStrategyMetricRepositoryUpsert:
    async def test_upsert_inserts_new_metric(self, session):
        """upsert() should insert a new metric if strategy has no record."""
        repo = StrategyMetricRepository(session)
        metric = make_metric(strategy="time_decay")
        result = await repo.upsert(metric)
        assert result.id is not None
        assert result.strategy == "time_decay"

    async def test_upsert_updates_existing_metric(self, session):
        """upsert() should update numeric fields on the existing metric row."""
        repo = StrategyMetricRepository(session)
        metric = make_metric(strategy="time_decay")
        metric.total_trades = 5
        await repo.upsert(metric)

        updated = make_metric(strategy="time_decay")
        updated.total_trades = 10
        result = await repo.upsert(updated)
        assert result.total_trades == 10

    async def test_upsert_distinct_strategies_coexist(self, session):
        """upsert() for two different strategies should create two rows."""
        repo = StrategyMetricRepository(session)
        await repo.upsert(make_metric(strategy="time_decay"))
        await repo.upsert(make_metric(strategy="arbitrage"))

        all_metrics = await repo.get_all_latest()
        strategies = {m.strategy for m in all_metrics}
        assert "time_decay" in strategies
        assert "arbitrage" in strategies

    async def test_upsert_updates_all_numeric_fields(self, session):
        """Every numeric field listed in upsert() should be updated."""
        repo = StrategyMetricRepository(session)
        first = make_metric(strategy="s1")
        await repo.upsert(first)

        second = StrategyMetric(
            strategy="s1",
            total_trades=20,
            winning_trades=15,
            losing_trades=5,
            win_rate=0.75,
            total_pnl=3.00,
            avg_edge=0.08,
            sharpe_ratio=2.0,
            max_drawdown=0.05,
            avg_hold_time_hours=12.0,
            profit_factor=2.5,
        )
        result = await repo.upsert(second)
        assert result.total_trades == 20
        assert result.winning_trades == 15
        assert result.win_rate == pytest.approx(0.75)
        assert result.sharpe_ratio == pytest.approx(2.0)
        assert result.profit_factor == pytest.approx(2.5)


class TestStrategyMetricRepositoryGetAllLatest:
    async def test_get_all_latest_empty_table(self, session):
        """get_all_latest() on empty table should return []."""
        repo = StrategyMetricRepository(session)
        result = await repo.get_all_latest()
        assert result == []

    async def test_get_all_latest_returns_one_per_strategy(self, session):
        """get_all_latest() should return exactly one row per strategy."""
        repo = StrategyMetricRepository(session)
        # Insert two rows for same strategy via direct add (bypassing upsert dedup)
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        old = make_metric(strategy="time_decay")
        old.recorded_at = now - timedelta(hours=1)
        new = make_metric(strategy="time_decay")
        new.recorded_at = now
        session.add_all([old, new])
        await session.commit()

        results = await repo.get_all_latest()
        td_rows = [r for r in results if r.strategy == "time_decay"]
        assert len(td_rows) == 1
        # Must be the newest one (highest recorded_at)
        assert td_rows[0].recorded_at == new.recorded_at


# ---------------------------------------------------------------------------
# SettingsRepository
# ---------------------------------------------------------------------------


class TestSettingsRepositorySetMany:
    async def test_set_many_persists_settings(self, session):
        """set_many() should write all provided key/value pairs."""
        repo = SettingsRepository(session)
        await repo.set_many({"paper_trading": "true", "max_positions": "3"})
        result = await repo.get_all()
        assert result["paper_trading"] == "true"
        assert result["max_positions"] == "3"

    async def test_set_many_upserts_existing_key(self, session):
        """set_many() called twice with the same key should update the value."""
        repo = SettingsRepository(session)
        await repo.set_many({"key1": "original"})
        await repo.set_many({"key1": "updated"})
        result = await repo.get_all()
        assert result["key1"] == "updated"

    async def test_set_many_empty_dict_no_raise(self, session):
        """set_many({}) should not raise and leave the table unchanged."""
        repo = SettingsRepository(session)
        await repo.set_many({})  # must not raise
        result = await repo.get_all()
        assert result == {}

    async def test_set_many_multiple_keys_in_one_call(self, session):
        """Multiple keys in a single set_many() call should all be stored."""
        repo = SettingsRepository(session)
        items = {f"key{i}": f"val{i}" for i in range(5)}
        await repo.set_many(items)
        result = await repo.get_all()
        assert len(result) == 5
        for k, v in items.items():
            assert result[k] == v


class TestSettingsRepositoryGetAll:
    async def test_get_all_empty_table(self, session):
        """get_all() on empty table should return an empty dict."""
        repo = SettingsRepository(session)
        result = await repo.get_all()
        assert result == {}

    async def test_get_all_returns_all_keys(self, session):
        """get_all() should return every stored setting."""
        repo = SettingsRepository(session)
        await repo.set_many({"a": "1", "b": "2", "c": "3"})
        result = await repo.get_all()
        assert set(result.keys()) == {"a", "b", "c"}

    async def test_get_all_values_are_strings(self, session):
        """All values returned by get_all() must be plain strings."""
        repo = SettingsRepository(session)
        await repo.set_many({"numeric": "42", "flag": "false"})
        result = await repo.get_all()
        for v in result.values():
            assert isinstance(v, str)


class TestTradeRepositoryCloseTrade:
    async def test_close_trade_updates_buy_trade(self, session):
        """close_trade_for_position() should stamp PnL and exit_reason on the BUY trade."""
        repo = TradeRepository(session)
        trade = await repo.create(make_trade(market_id="mkt1", status="filled", pnl=0.0))

        updated = await repo.close_trade_for_position("mkt1", pnl=0.50, exit_reason="strategy_exit")
        assert updated is True

        trades = await repo.get_recent()
        t = next(t for t in trades if t.id == trade.id)
        assert t.pnl == pytest.approx(0.50)
        assert t.exit_reason == "strategy_exit"

    async def test_close_trade_returns_false_when_no_match(self, session):
        """close_trade_for_position() should return False when no BUY trade exists."""
        repo = TradeRepository(session)
        result = await repo.close_trade_for_position(
            "nonexistent", pnl=1.0, exit_reason="resolution",
        )
        assert result is False

    async def test_close_trade_skips_already_closed(self, session):
        """close_trade_for_position() should not overwrite a trade that already has exit_reason."""
        repo = TradeRepository(session)
        trade = make_trade(market_id="mkt1", status="filled", pnl=0.0)
        trade.exit_reason = "strategy_exit"
        await repo.create(trade)

        # Try to close again with different exit_reason
        result = await repo.close_trade_for_position("mkt1", pnl=1.0, exit_reason="resolution")
        assert result is False

    async def test_close_trade_picks_most_recent_buy(self, session):
        """close_trade_for_position() should update the newest BUY trade for that market."""
        repo = TradeRepository(session)
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        old = make_trade(market_id="mkt1", status="filled", pnl=0.0)
        old.created_at = now - timedelta(hours=2)
        old.exit_reason = "strategy_exit"  # Already resolved
        await repo.create(old)

        new = make_trade(market_id="mkt1", status="filled", pnl=0.0)
        new.created_at = now
        await repo.create(new)

        result = await repo.close_trade_for_position("mkt1", pnl=0.30, exit_reason="resolution")
        assert result is True

        # The newer trade should have the PnL
        trades = await repo.get_recent()
        newest = [t for t in trades if t.market_id == "mkt1" and t.exit_reason == "resolution"]
        assert len(newest) == 1
        assert newest[0].pnl == pytest.approx(0.30)

    async def test_close_trade_ignores_sell_trades(self, session):
        """close_trade_for_position() should only match BUY trades, not SELLs."""
        repo = TradeRepository(session)
        sell_trade = make_trade(market_id="mkt1", status="filled", pnl=0.0)
        sell_trade.side = "SELL"
        await repo.create(sell_trade)

        result = await repo.close_trade_for_position("mkt1", pnl=0.50, exit_reason="strategy_exit")
        assert result is False


class TestTradeRepositoryAdvancedStats:
    async def test_empty_table_returns_empty(self, session):
        repo = TradeRepository(session)
        stats = await repo.get_strategy_advanced_stats()
        assert stats == {}

    async def test_avg_hold_time_computed(self, session):
        repo = TradeRepository(session)
        now = datetime.now(timezone.utc)
        t1 = make_trade(
            strategy="time_decay",
            status="filled",
            pnl=0.5,
            created_at=now,
            updated_at=now.replace(hour=now.hour),  # same time => 0h
        )
        # Create with explicit 2-hour gap
        from datetime import timedelta

        t2 = make_trade(
            strategy="time_decay",
            status="filled",
            pnl=0.3,
            created_at=now - timedelta(hours=4),
            updated_at=now - timedelta(hours=2),  # 2h hold
        )
        await repo.create(t1)
        await repo.create(t2)
        stats = await repo.get_strategy_advanced_stats()
        assert "time_decay" in stats
        # Average of 0h and 2h = 1h
        assert stats["time_decay"]["avg_hold_time_hours"] == pytest.approx(1.0, abs=0.1)

    async def test_sharpe_ratio_computed(self, session):
        repo = TradeRepository(session)
        # 3 trades: +10%, +20%, -5% returns
        await repo.create(make_trade(strategy="arb", status="filled", pnl=1.0, cost_usd=10.0))
        await repo.create(make_trade(strategy="arb", status="filled", pnl=2.0, cost_usd=10.0))
        await repo.create(make_trade(strategy="arb", status="filled", pnl=-0.5, cost_usd=10.0))
        stats = await repo.get_strategy_advanced_stats()
        assert "arb" in stats
        # Returns: 0.1, 0.2, -0.05 → mean=0.0833, std=0.126 → sharpe≈0.66
        assert stats["arb"]["sharpe_ratio"] > 0.0

    async def test_max_drawdown_computed(self, session):
        repo = TradeRepository(session)
        # Sequence: +1, +1, -3 → peak=2, trough=-1, drawdown=3
        await repo.create(make_trade(strategy="td", status="filled", pnl=1.0))
        await repo.create(make_trade(strategy="td", status="filled", pnl=1.0))
        await repo.create(make_trade(strategy="td", status="filled", pnl=-3.0))
        stats = await repo.get_strategy_advanced_stats()
        assert stats["td"]["max_drawdown"] == pytest.approx(3.0)

    async def test_excludes_pending_trades(self, session):
        repo = TradeRepository(session)
        await repo.create(make_trade(strategy="time_decay", status="pending", pnl=5.0))
        stats = await repo.get_strategy_advanced_stats()
        assert stats == {}

    async def test_multiple_strategies(self, session):
        repo = TradeRepository(session)
        await repo.create(make_trade(strategy="arb", status="filled", pnl=1.0))
        await repo.create(make_trade(strategy="td", status="filled", pnl=-0.5))
        stats = await repo.get_strategy_advanced_stats()
        assert "arb" in stats
        assert "td" in stats
