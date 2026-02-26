"""CRUD operations for database models."""

from datetime import datetime, timedelta

from sqlalchemy import func, select, update
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
            select(func.count(Trade.id)).where(Trade.pnl > 0, Trade.status == "completed")
        )
        total_pnl = await self.session.scalar(select(func.sum(Trade.pnl))) or 0.0
        return {
            "total_trades": total or 0,
            "winning_trades": wins or 0,
            "total_pnl": float(total_pnl),
            "win_rate": (wins / total) if total else 0.0,
        }


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

    async def get_recent_opportunities(self, limit: int = 20) -> list[MarketScan]:
        result = await self.session.execute(
            select(MarketScan)
            .where(MarketScan.signal_edge > 0)
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
