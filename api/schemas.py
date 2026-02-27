"""Response models for the API."""

from datetime import datetime

from pydantic import BaseModel, Field


# Portfolio
class PortfolioOverview(BaseModel):
    total_equity: float
    cash_balance: float
    polymarket_balance: float | None = None
    positions_value: float
    unrealized_pnl: float
    realized_pnl_today: float
    open_positions: int
    peak_equity: float
    tier: str
    is_paper: bool


class PositionResponse(BaseModel):
    id: int
    market_id: str
    token_id: str
    question: str
    outcome: str
    category: str
    strategy: str
    side: str
    size: float
    avg_price: float
    current_price: float
    cost_basis: float
    unrealized_pnl: float
    is_open: bool
    created_at: datetime


class EquityPoint(BaseModel):
    timestamp: datetime
    total_equity: float
    cash_balance: float
    positions_value: float
    daily_return_pct: float


class AllocationItem(BaseModel):
    category: str
    value: float
    percentage: float


# Trades
class TradeResponse(BaseModel):
    id: int
    created_at: datetime
    market_id: str
    question: str
    outcome: str
    side: str
    price: float
    size: float
    cost_usd: float
    strategy: str
    edge: float
    estimated_prob: float
    confidence: float
    reasoning: str
    status: str
    pnl: float
    is_paper: bool


class TradeStats(BaseModel):
    total_trades: int
    winning_trades: int
    total_pnl: float
    win_rate: float


# Strategies
class StrategyPerformance(BaseModel):
    strategy: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    avg_edge: float
    sharpe_ratio: float
    max_drawdown: float
    avg_hold_time_hours: float


# Markets
class MarketOpportunity(BaseModel):
    market_id: str
    question: str
    category: str
    yes_price: float
    no_price: float
    volume: float
    liquidity: float
    end_date: datetime | None
    hours_to_resolution: float | None
    signal_strategy: str
    signal_edge: float
    signal_confidence: float


# Risk
class RiskMetrics(BaseModel):
    tier: str
    bankroll: float
    peak_equity: float
    current_drawdown_pct: float
    max_drawdown_limit_pct: float
    daily_pnl: float
    daily_loss_limit_pct: float
    max_positions: int
    is_paused: bool


class RiskLimits(BaseModel):
    tier: str
    max_positions: int
    max_per_position_pct: float
    daily_loss_limit_pct: float
    max_drawdown_pct: float
    min_edge_pct: float
    min_win_prob: float
    max_per_category_pct: float
    kelly_fraction: float


# Config
class BotConfig(BaseModel):
    trading_mode: str
    scan_interval_seconds: int
    snapshot_interval_seconds: int
    max_daily_loss_pct: float
    max_drawdown_pct: float


class BotConfigUpdate(BaseModel):
    scan_interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    max_daily_loss_pct: float | None = Field(default=None, gt=0.0, le=0.5)
    max_drawdown_pct: float | None = Field(default=None, gt=0.0, le=0.5)


# Health
class HealthCheck(BaseModel):
    status: str = "ok"
    uptime_seconds: float
    engine_running: bool
    cycle_count: int
