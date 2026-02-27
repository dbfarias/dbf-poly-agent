"""SQLAlchemy ORM models for the trading bot database."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Market info
    market_id: Mapped[str] = mapped_column(String(128), index=True)
    token_id: Mapped[str] = mapped_column(String(128))
    question: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String(32), default="")
    category: Mapped[str] = mapped_column(String(64), default="")

    # Order info
    order_id: Mapped[str] = mapped_column(String(128), default="")
    side: Mapped[str] = mapped_column(String(8))  # BUY/SELL
    price: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)
    filled_size: Mapped[float] = mapped_column(Float, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # Strategy info
    strategy: Mapped[str] = mapped_column(String(64), index=True)
    edge: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_prob: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reasoning: Mapped[str] = mapped_column(Text, default="")

    # Result
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    exit_reason: Mapped[str] = mapped_column(String(64), default="")
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (Index("idx_trades_strategy_status", "strategy", "status"),)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    market_id: Mapped[str] = mapped_column(String(128), index=True, unique=True)
    token_id: Mapped[str] = mapped_column(String(128))
    question: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String(32), default="")
    category: Mapped[str] = mapped_column(String(64), default="")
    strategy: Mapped[str] = mapped_column(String(64), default="")

    side: Mapped[str] = mapped_column(String(8))
    size: Mapped[float] = mapped_column(Float, default=0.0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    cost_basis: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)

    is_open: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    total_equity: Mapped[float] = mapped_column(Float)
    cash_balance: Mapped[float] = mapped_column(Float)
    positions_value: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl_today: Mapped[float] = mapped_column(Float, default=0.0)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    daily_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)


class MarketScan(Base):
    __tablename__ = "market_scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    market_id: Mapped[str] = mapped_column(String(128), index=True)
    question: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(64), default="")
    yes_price: Mapped[float] = mapped_column(Float, default=0.0)
    no_price: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    liquidity: Mapped[float] = mapped_column(Float, default=0.0)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    hours_to_resolution: Mapped[float | None] = mapped_column(Float, nullable=True)

    signal_strategy: Mapped[str] = mapped_column(String(64), default="")
    signal_edge: Mapped[float] = mapped_column(Float, default=0.0)
    signal_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    was_traded: Mapped[bool] = mapped_column(Boolean, default=False)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    level: Mapped[str] = mapped_column(String(16))  # info, warning, error, critical
    category: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class StrategyMetric(Base):
    __tablename__ = "strategy_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    strategy: Mapped[str] = mapped_column(String(64), index=True)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    winning_trades: Mapped[int] = mapped_column(Integer, default=0)
    losing_trades: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    total_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    avg_edge: Mapped[float] = mapped_column(Float, default=0.0)
    sharpe_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    avg_hold_time_hours: Mapped[float] = mapped_column(Float, default=0.0)
