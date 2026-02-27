"""CRUD operations for database models."""

from datetime import datetime, timedelta

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.data.models import (
    MarketScan,
    PortfolioSnapshot,
    Position,
    StrategyMetric,
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

    async def update_status(self, trade_id: int, status: str, pnl: float = 0.0) -> None:
        await self.session.execute(
            update(Trade).where(Trade.id == trade_id).values(status=status, pnl=pnl)
        )
        await self.session.commit()

    async def get_recent(self, limit: int = 50) -> list[Trade]:
        result = await self.session.execute(
            select(Trade).order_by(Trade.created_at.desc()).limit(limit)
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
        return {
            "total_trades": total or 0,
            "winning_trades": wins or 0,
            "total_pnl": float(total_pnl),
            "win_rate": (wins / total) if total else 0.0,
        }

    async def get_strategy_category_stats(
        self, days: int = 30
    ) -> list[dict]:
        """Get aggregated stats grouped by (strategy, category).

        Returns list of dicts with: strategy, category, total_trades,
        winning_trades, total_pnl, avg_edge, avg_estimated_prob.
        """
        since = datetime.utcnow() - timedelta(days=days)
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
            for key in ("size", "avg_price", "current_price", "cost_basis", "unrealized_pnl"):
                setattr(existing_pos, key, getattr(position, key))
            existing_pos.updated_at = datetime.utcnow()
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
            .values(is_open=False, updated_at=datetime.utcnow())
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
        since = datetime.utcnow() - timedelta(days=days)
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
                "total_pnl", "avg_edge", "sharpe_ratio", "max_drawdown", "avg_hold_time_hours",
            ):
                setattr(existing_metric, key, getattr(metric, key))
            existing_metric.recorded_at = datetime.utcnow()
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
