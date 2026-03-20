"""CRUD operations for database models."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.data.models import (
    BotSetting,
    CapitalFlow,
    MarketScan,
    PortfolioSnapshot,
    Position,
    StrategyMetric,
    TrackedWallet,
    Trade,
)


class TradeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, trade: Trade) -> Trade:
        self.session.add(trade)
        await self.session.commit()
        await self.session.refresh(trade)
        return trade

    async def update_status(
        self,
        trade_id: int,
        status: str,
        pnl: float = 0.0,
        filled_size: float | None = None,
    ) -> None:
        values: dict = {"status": status, "pnl": pnl}
        if filled_size is not None:
            values["filled_size"] = filled_size
        await self.session.execute(
            update(Trade).where(Trade.id == trade_id).values(**values)
        )
        await self.session.commit()

    async def expire_stale_pending(self, max_age_seconds: int = 600) -> int:
        """Mark pending orders older than max_age_seconds as expired.

        Returns count of expired orders. This handles orders orphaned by
        container restarts (in-memory pending dict is lost on restart).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
        result = await self.session.execute(
            update(Trade)
            .where(Trade.status == "pending", Trade.created_at < cutoff)
            .values(status="expired")
        )
        await self.session.commit()
        return result.rowcount

    async def get_recent(self, limit: int = 50) -> list[Trade]:
        result = await self.session.execute(
            select(Trade).order_by(Trade.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def get_closed_trades(self, limit: int = 2000) -> list[Trade]:
        """Get closed BUY trades (filled + exit_reason set) for learner analysis.

        Unlike get_recent(), this queries directly for resolved positions
        without wasting the limit on SELL/expired/pending trades.
        """
        result = await self.session.execute(
            select(Trade)
            .where(
                Trade.side == "BUY",
                Trade.status.in_(["filled", "completed"]),
                Trade.exit_reason != "",
            )
            .order_by(Trade.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_strategy(self, strategy: str, limit: int = 100) -> list[Trade]:
        result = await self.session.execute(
            select(Trade)
            .where(Trade.strategy == strategy)
            .order_by(Trade.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_stats(self) -> dict:
        total = await self.session.scalar(select(func.count(Trade.id)))
        wins = await self.session.scalar(
            select(func.count(Trade.id)).where(
                Trade.pnl > 0,
                Trade.status.in_(["filled", "completed"]),
            )
        )
        total_pnl = await self.session.scalar(select(func.sum(Trade.pnl))) or 0.0
        total_fees = await self.session.scalar(
            select(func.sum(Trade.fee_amount_usd))
        ) or 0.0
        return {
            "total_trades": total or 0,
            "winning_trades": wins or 0,
            "total_pnl": float(total_pnl),
            "win_rate": (wins / total) if total else 0.0,
            "total_fees_usd": float(total_fees),
        }

    async def get_today_stats(self) -> dict:
        """Get today's trade count and win rate (local trading day)."""
        from bot.config import settings

        # Local midnight in UTC: e.g., BRT midnight = 03:00 UTC
        utc_now = datetime.now(timezone.utc)
        offset = timedelta(hours=settings.timezone_offset_hours)
        local_now = utc_now + offset
        local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start = local_midnight - offset  # Back to UTC
        total = await self.session.scalar(
            select(func.count(Trade.id)).where(
                Trade.created_at >= today_start,
                Trade.status.in_(["filled", "completed"]),
            )
        ) or 0
        wins = await self.session.scalar(
            select(func.count(Trade.id)).where(
                Trade.created_at >= today_start,
                Trade.status.in_(["filled", "completed"]),
                Trade.pnl > 0,
            )
        ) or 0
        return {
            "trades_today": total,
            "wins_today": wins,
            "win_rate_today": wins / total if total > 0 else 0.0,
        }

    async def get_strategy_category_stats(
        self, days: int = 30
    ) -> list[dict]:
        """Get aggregated stats grouped by (strategy, category).

        Returns list of dicts with: strategy, category, total_trades,
        winning_trades, total_pnl, avg_edge, avg_estimated_prob.
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)
        result = await self.session.execute(
            select(
                Trade.strategy,
                Trade.category,
                func.count(Trade.id).label("total_trades"),
                func.sum(
                    case((Trade.pnl > 0, 1), else_=0)
                ).label("winning_trades"),
                func.sum(Trade.pnl).label("total_pnl"),
                func.avg(Trade.edge).label("avg_edge"),
                func.avg(Trade.estimated_prob).label("avg_estimated_prob"),
            )
            .where(Trade.status.in_(["filled", "completed"]), Trade.created_at >= since)
            .group_by(Trade.strategy, Trade.category)
        )
        return [
            {
                "strategy": row.strategy,
                "category": row.category,
                "total_trades": row.total_trades,
                "winning_trades": int(row.winning_trades or 0),
                "total_pnl": float(row.total_pnl or 0.0),
                "avg_edge": float(row.avg_edge or 0.0),
                "avg_estimated_prob": float(row.avg_estimated_prob or 0.0),
            }
            for row in result.all()
        ]

    async def get_strategy_stats(self) -> list[dict]:
        """Get aggregated stats grouped by strategy (all time, filled+completed).

        Returns list of dicts with: strategy, total_trades, winning_trades,
        losing_trades, total_pnl, avg_edge.
        """
        result = await self.session.execute(
            select(
                Trade.strategy,
                func.count(Trade.id).label("total_trades"),
                func.sum(
                    case((Trade.pnl > 0, 1), else_=0)
                ).label("winning_trades"),
                func.sum(Trade.pnl).label("total_pnl"),
                func.avg(Trade.edge).label("avg_edge"),
            )
            .where(Trade.status.in_(["filled", "completed"]))
            .group_by(Trade.strategy)
        )
        rows = []
        for row in result.all():
            total = row.total_trades
            wins = int(row.winning_trades or 0)
            rows.append({
                "strategy": row.strategy,
                "total_trades": total,
                "winning_trades": wins,
                "losing_trades": total - wins,
                "total_pnl": float(row.total_pnl or 0.0),
                "avg_edge": float(row.avg_edge or 0.0),
                "win_rate": wins / total if total > 0 else 0.0,
            })
        return rows

    async def get_strategy_advanced_stats(self) -> dict[str, dict]:
        """Compute sharpe_ratio, max_drawdown, avg_hold_time per strategy.

        Uses individual trade-level data for accurate computation.
        Returns {strategy_name: {sharpe_ratio, max_drawdown, avg_hold_time_hours}}.
        """
        import math

        result = await self.session.execute(
            select(
                Trade.strategy,
                Trade.pnl,
                Trade.cost_usd,
                Trade.created_at,
                Trade.updated_at,
            ).where(Trade.status.in_(["filled", "completed"]))
            .order_by(Trade.strategy, Trade.created_at)
        )
        rows = result.all()

        # Group by strategy
        strategy_trades: dict[str, list] = {}
        for row in rows:
            strategy_trades.setdefault(row.strategy, []).append(row)

        stats: dict[str, dict] = {}
        for name, trades in strategy_trades.items():
            # --- Avg hold time ---
            hold_times = []
            for t in trades:
                if t.created_at and t.updated_at:
                    created = t.created_at
                    updated = t.updated_at
                    # Handle naive datetimes from SQLite
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=timezone.utc)
                    hours = (updated - created).total_seconds() / 3600
                    if hours >= 0:
                        hold_times.append(hours)
            avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0.0

            # --- Sharpe ratio (per-trade returns) ---
            returns = []
            for t in trades:
                if t.cost_usd and t.cost_usd > 0:
                    returns.append(t.pnl / t.cost_usd)
            if len(returns) >= 2:
                mean_r = sum(returns) / len(returns)
                variance = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
                std_r = math.sqrt(variance) if variance > 0 else 0.0
                sharpe = mean_r / std_r if std_r > 0 else 0.0
            else:
                sharpe = 0.0

            # --- Max drawdown (cumulative PnL peak-to-trough) ---
            cumulative = 0.0
            peak = 0.0
            max_dd = 0.0
            for t in trades:
                cumulative += t.pnl
                if cumulative > peak:
                    peak = cumulative
                dd = peak - cumulative
                if dd > max_dd:
                    max_dd = dd

            # Profit factor
            gp = sum(t.pnl for t in trades if t.pnl > 0)
            gl = abs(sum(t.pnl for t in trades if t.pnl < 0))
            pf = gp / gl if gl > 0 else (10.0 if gp > 0 else 0.0)

            stats[name] = {
                "sharpe_ratio": round(sharpe, 3),
                "max_drawdown": round(max_dd, 4),
                "avg_hold_time_hours": round(avg_hold, 2),
                "profit_factor": round(pf, 2),
            }

        return stats

    async def close_trade_for_position(
        self, market_id: str, pnl: float, exit_reason: str,
        close_price: float = 0.0, position_size: float = 0.0,
    ) -> bool:
        """Write final PnL and exit_reason to the original BUY trade for a position.

        Finds the most recent filled BUY trade (without an exit_reason) for the
        given market_id and stamps it with the realized PnL.  This enables the
        learner to compute accurate win-rates and per-strategy performance.

        If fee data is available on the BUY trade, subtracts entry+exit fees
        from the gross PnL to produce net PnL.

        Returns True if a trade was updated, False otherwise.
        """
        from bot.utils.risk_metrics import polymarket_fee

        result = await self.session.execute(
            select(Trade.id, Trade.fee_rate_bps, Trade.fee_amount_usd)
            .where(
                Trade.market_id == market_id,
                Trade.side == "BUY",
                Trade.status.in_(["filled", "completed"]),
                Trade.exit_reason == "",
            )
            .order_by(Trade.created_at.desc())
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return False

        trade_id = row[0]
        fee_rate_bps = row[1] or 0
        entry_fee = row[2] or 0.0

        # Subtract fees from PnL if fee data is available
        net_pnl = pnl
        if fee_rate_bps > 0 and close_price > 0 and position_size > 0:
            fee_rate = fee_rate_bps / 10_000.0
            exit_fee = polymarket_fee(close_price, position_size, fee_rate)
            net_pnl = pnl - entry_fee - exit_fee

        await self.session.execute(
            update(Trade)
            .where(Trade.id == trade_id)
            .values(
                pnl=net_pnl,
                exit_reason=exit_reason,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await self.session.commit()
        return True

    async def get_resolved_with_questions(self, days: int = 90) -> list[Trade]:
        """Return resolved trades from last N days (with exit_reason set).

        Used by PatternAnalyzer to compute historical base rates.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = await self.session.execute(
            select(Trade)
            .where(
                Trade.status.in_(["filled", "completed"]),
                Trade.exit_reason.isnot(None),
                Trade.exit_reason != "",
                Trade.created_at >= cutoff,
            )
            .order_by(Trade.created_at.desc())
        )
        return list(result.scalars().all())

    async def mark_scan_traded(self, market_id: str, strategy: str) -> None:
        """Mark the most recent scan for a market as traded."""
        from bot.data.models import MarketScan

        latest = await self.session.execute(
            select(MarketScan.id)
            .where(
                MarketScan.market_id == market_id,
                MarketScan.signal_strategy == strategy,
            )
            .order_by(MarketScan.scanned_at.desc())
            .limit(1)
        )
        scan_id = latest.scalar_one_or_none()
        if scan_id is not None:
            await self.session.execute(
                update(MarketScan)
                .where(MarketScan.id == scan_id)
                .values(was_traded=True)
            )
            await self.session.commit()


class PositionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, position: Position) -> Position:
        existing = await self.session.execute(
            select(Position).where(Position.market_id == position.market_id)
        )
        existing_pos = existing.scalar_one_or_none()
        if existing_pos:
            for key in ("size", "avg_price", "current_price", "cost_basis", "unrealized_pnl", "token_id", "outcome"):
                setattr(existing_pos, key, getattr(position, key))
            # Reopen if position reappears on Polymarket after being closed
            if position.is_open and not existing_pos.is_open:
                existing_pos.is_open = True
                # Reset created_at so min_hold_seconds works correctly
                # (stale created_at from old position would bypass hold check)
                existing_pos.created_at = datetime.now(timezone.utc)
            existing_pos.updated_at = datetime.now(timezone.utc)
            await self.session.commit()
            return existing_pos
        self.session.add(position)
        await self.session.commit()
        await self.session.refresh(position)
        return position

    async def get_open(self) -> list[Position]:
        result = await self.session.execute(
            select(Position).where(Position.is_open.is_(True))
        )
        return list(result.scalars().all())

    async def close(self, market_id: str) -> None:
        await self.session.execute(
            update(Position)
            .where(Position.market_id == market_id)
            .values(is_open=False, updated_at=datetime.now(timezone.utc))
        )
        await self.session.commit()

    async def get_by_category(self) -> dict[str, float]:
        result = await self.session.execute(
            select(Position.category, func.sum(Position.cost_basis))
            .where(Position.is_open.is_(True))
            .group_by(Position.category)
        )
        return {row[0]: float(row[1]) for row in result.all()}


class PortfolioSnapshotRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, snapshot: PortfolioSnapshot) -> PortfolioSnapshot:
        self.session.add(snapshot)
        await self.session.commit()
        return snapshot

    async def get_equity_curve(self, days: int = 30) -> list[PortfolioSnapshot]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        result = await self.session.execute(
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.timestamp >= since)
            .order_by(PortfolioSnapshot.timestamp)
        )
        return list(result.scalars().all())

    async def get_latest(self) -> PortfolioSnapshot | None:
        result = await self.session.execute(
            select(PortfolioSnapshot).order_by(PortfolioSnapshot.timestamp.desc()).limit(1)
        )
        return result.scalar_one_or_none()


class MarketScanRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_batch(self, scans: list[MarketScan]) -> None:
        self.session.add_all(scans)
        await self.session.commit()

    async def get_recent_opportunities(self, limit: int = 50) -> list[MarketScan]:
        """Get latest scan per market, ordered by most recent scan."""
        # Subquery: latest scan timestamp per market
        latest_per_market = (
            select(
                MarketScan.market_id,
                func.max(MarketScan.scanned_at).label("max_scanned"),
            )
            .group_by(MarketScan.market_id)
            .subquery()
        )
        result = await self.session.execute(
            select(MarketScan)
            .join(
                latest_per_market,
                (MarketScan.market_id == latest_per_market.c.market_id)
                & (MarketScan.scanned_at == latest_per_market.c.max_scanned),
            )
            .order_by(MarketScan.scanned_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


class StrategyMetricRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, metric: StrategyMetric) -> StrategyMetric:
        existing = await self.session.execute(
            select(StrategyMetric)
            .where(StrategyMetric.strategy == metric.strategy)
            .order_by(StrategyMetric.recorded_at.desc())
            .limit(1)
        )
        existing_metric = existing.scalar_one_or_none()
        if existing_metric:
            for key in (
                "total_trades", "winning_trades", "losing_trades", "win_rate",
                "total_pnl", "avg_edge", "sharpe_ratio", "max_drawdown",
                "avg_hold_time_hours", "profit_factor",
            ):
                setattr(existing_metric, key, getattr(metric, key))
            existing_metric.recorded_at = datetime.now(timezone.utc)
            await self.session.commit()
            return existing_metric
        self.session.add(metric)
        await self.session.commit()
        return metric

    async def get_all_latest(self) -> list[StrategyMetric]:
        subq = (
            select(
                StrategyMetric.strategy,
                func.max(StrategyMetric.recorded_at).label("max_date"),
            )
            .group_by(StrategyMetric.strategy)
            .subquery()
        )
        result = await self.session.execute(
            select(StrategyMetric).join(
                subq,
                (StrategyMetric.strategy == subq.c.strategy)
                & (StrategyMetric.recorded_at == subq.c.max_date),
            )
        )
        return list(result.scalars().all())


class CapitalFlowRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, flow: CapitalFlow) -> CapitalFlow:
        self.session.add(flow)
        await self.session.commit()
        await self.session.refresh(flow)
        return flow

    async def get_cumulative_today(self) -> float:
        """Sum of all capital flows for the current trading day."""
        from bot.config import settings as cfg

        utc_now = datetime.now(timezone.utc)
        offset = timedelta(hours=cfg.timezone_offset_hours)
        local_now = utc_now + offset
        local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start = local_midnight - offset

        result = await self.session.scalar(
            select(func.sum(CapitalFlow.amount)).where(
                CapitalFlow.timestamp >= today_start,
            )
        )
        return float(result or 0.0)

    async def get_recent(self, limit: int = 50) -> list[CapitalFlow]:
        result = await self.session.execute(
            select(CapitalFlow)
            .order_by(CapitalFlow.timestamp.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def delete_by_id(self, flow_id: int) -> bool:
        """Delete a capital flow entry by ID. Returns True if found and deleted."""
        result = await self.session.execute(
            select(CapitalFlow).where(CapitalFlow.id == flow_id)
        )
        flow = result.scalar_one_or_none()
        if flow is None:
            return False
        await self.session.delete(flow)
        await self.session.commit()
        return True


class SettingsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def set_many(self, items: dict[str, str]) -> None:
        """Upsert multiple settings in one transaction."""
        now = datetime.now(timezone.utc)
        for key, value in items.items():
            stmt = sqlite_insert(BotSetting).values(
                key=key, value=value, updated_at=now
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[BotSetting.key],
                set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
            )
            await self.session.execute(stmt)
        await self.session.commit()

    async def get(self, key: str) -> str | None:
        """Read a single persisted setting by key."""
        result = await self.session.execute(
            select(BotSetting).where(BotSetting.key == key)
        )
        row = result.scalar_one_or_none()
        return row.value if row else None

    async def get_all(self) -> dict[str, str]:
        """Read all persisted settings."""
        result = await self.session.execute(select(BotSetting))
        return {row.key: row.value for row in result.scalars().all()}


class TrackedWalletRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert(self, wallet: TrackedWallet) -> TrackedWallet:
        """Insert or update a tracked wallet by proxy_address."""
        existing = await self.session.execute(
            select(TrackedWallet).where(
                TrackedWallet.proxy_address == wallet.proxy_address
            )
        )
        existing_wallet = existing.scalar_one_or_none()
        if existing_wallet:
            for key in (
                "username", "pnl_7d", "pnl_30d", "win_rate",
                "volume_30d", "last_trade_at", "is_active", "notes",
            ):
                setattr(existing_wallet, key, getattr(wallet, key))
            existing_wallet.updated_at = datetime.now(timezone.utc)
            await self.session.commit()
            return existing_wallet
        self.session.add(wallet)
        await self.session.commit()
        await self.session.refresh(wallet)
        return wallet

    async def get_active(self) -> list[TrackedWallet]:
        """Get all active tracked wallets."""
        result = await self.session.execute(
            select(TrackedWallet).where(TrackedWallet.is_active.is_(True))
        )
        return list(result.scalars().all())

    async def deactivate_all(self) -> None:
        """Mark all wallets as inactive (before refresh cycle)."""
        await self.session.execute(
            update(TrackedWallet).values(is_active=False)
        )
        await self.session.commit()

    async def get_by_address(self, proxy_address: str) -> TrackedWallet | None:
        """Fetch a single wallet by proxy address."""
        result = await self.session.execute(
            select(TrackedWallet).where(
                TrackedWallet.proxy_address == proxy_address
            )
        )
        return result.scalar_one_or_none()
