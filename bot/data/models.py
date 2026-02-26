"""SQLAlchemy ORM models for the trading bot database."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utc_now, onupdate=_utc_now
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

    # Fees
    fee_rate_bps: Mapped[int] = mapped_column(Integer, default=0)  # Fee rate in basis points
    fee_amount_usd: Mapped[float] = mapped_column(Float, default=0.0)  # Computed fee in USD

    # Copy trading
    source_wallet: Mapped[str] = mapped_column(String(128), default="")

    # Result
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    exit_reason: Mapped[str] = mapped_column(String(64), default="")
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)  # For SELL: original buy price
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True)

    # Debate tracking
    # Values: "auto_approved", "challenger_override", "debate_passed", "debate_rejected", ""
    debate_path: Mapped[str] = mapped_column(String(32), default="")
    # The research_multiplier value (from news/sentiment) applied at trade time
    research_multiplier_applied: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (Index("idx_trades_strategy_status", "strategy", "status"),)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utc_now, onupdate=_utc_now
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
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)

    total_equity: Mapped[float] = mapped_column(Float)
    cash_balance: Mapped[float] = mapped_column(Float)
    positions_value: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl_today: Mapped[float] = mapped_column(Float, default=0.0)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    daily_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    trading_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)


class MarketScan(Base):
    __tablename__ = "market_scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scanned_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)

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



class BotSetting(Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)  # JSON-encoded
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


class BotActivity(Base):
    __tablename__ = "bot_activity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)

    event_type: Mapped[str] = mapped_column(String(32), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")  # info, success, warning, error
    title: Mapped[str] = mapped_column(String(256))
    detail: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    market_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    strategy: Mapped[str] = mapped_column(String(64), default="")

    __table_args__ = (Index("idx_activity_type_ts", "event_type", "timestamp"),)


class TrackedWallet(Base):
    __tablename__ = "tracked_wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utc_now, onupdate=_utc_now
    )

    proxy_address: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(128), default="")
    pnl_7d: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_30d: Mapped[float] = mapped_column(Float, default=0.0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    volume_30d: Mapped[float] = mapped_column(Float, default=0.0)
    last_trade_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")


class CapitalFlow(Base):
    """Records deposits and withdrawals to separate from trading PnL."""

    __tablename__ = "capital_flows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)
    amount: Mapped[float] = mapped_column(Float)  # +deposit / -withdrawal
    flow_type: Mapped[str] = mapped_column(String(32))  # deposit / withdrawal
    source: Mapped[str] = mapped_column(String(32), default="polymarket")  # polymarket / config
    note: Mapped[str] = mapped_column(Text, default="")
    is_paper: Mapped[bool] = mapped_column(Boolean, default=False)


class StrategyMetric(Base):
    __tablename__ = "strategy_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)

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
    profit_factor: Mapped[float] = mapped_column(Float, default=0.0)


class Watcher(Base):
    __tablename__ = "watchers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utc_now, onupdate=_utc_now
    )

    # Market
    market_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    token_id: Mapped[str] = mapped_column(String(256), default="")
    question: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String(32), default="")

    # Config
    keywords: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    thesis: Mapped[str] = mapped_column(Text, default="")
    max_exposure_usd: Mapped[float] = mapped_column(Float, default=20.0)
    stop_loss_pct: Mapped[float] = mapped_column(Float, default=0.25)
    max_age_hours: Mapped[float] = mapped_column(Float, default=168.0)  # 7 days
    check_interval_sec: Mapped[int] = mapped_column(Integer, default=900)  # 15 min

    # State
    status: Mapped[str] = mapped_column(
        String(16), index=True, default="active"
    )  # active, paused, completed, killed
    current_exposure: Mapped[float] = mapped_column(Float, default=0.0)
    avg_entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    scale_count: Mapped[int] = mapped_column(Integer, default=0)
    max_scale_count: Mapped[int] = mapped_column(Integer, default=3)
    highest_price: Mapped[float] = mapped_column(Float, default=0.0)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_news_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Market end date (for termination checks)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Trigger
    source_strategy: Mapped[str] = mapped_column(String(64), default="")
    auto_created: Mapped[bool] = mapped_column(Boolean, default=False)


class WatcherDecision(Base):
    __tablename__ = "watcher_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)
    watcher_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)

    # Possible values: scale_up, hold, scale_down, exit, check
    decision: Mapped[str] = mapped_column(String(16), default="")
    signals_json: Mapped[str] = mapped_column(Text, default="{}")
    reasoning: Mapped[str] = mapped_column(Text, default="")
    action_taken: Mapped[str] = mapped_column(
        String(32), default=""
    )  # placed_order, held, exited, blocked_by_risk
    size_usd: Mapped[float] = mapped_column(Float, default=0.0)
    price_at_decision: Mapped[float | None] = mapped_column(Float, nullable=True)
