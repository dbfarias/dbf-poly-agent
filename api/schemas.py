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
    polymarket_pnl_today: float = 0.0
    open_positions: int
    peak_equity: float
    day_start_equity: float = 0.0
    tier: str
    is_paper: bool
    daily_target_pct: float = 0.01
    daily_target_usd: float = 0.0
    daily_progress_pct: float = 0.0
    stuck_positions: list[str] = []


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
    entry_price: float = 0.0
    exit_reason: str | None = None
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


class StrategyStatus(BaseModel):
    """Live runtime status for a strategy, combining admin/tier/learner state."""

    name: str
    label: str
    min_tier: str
    is_tier_available: bool
    is_admin_disabled: bool
    is_learner_paused: bool
    pause_remaining_hours: float = 0.0
    is_active: bool  # tier available AND not disabled AND not paused
    total_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0


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
    daily_target_pct: float
    # Current tier parameters
    current_tier: str
    tier_config: dict
    # Strategy parameters
    strategy_params: dict
    # Quality filter parameters
    quality_params: dict
    # Disabled strategies
    disabled_strategies: list[str] = []


class BotConfigUpdate(BaseModel):
    scan_interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    max_daily_loss_pct: float | None = Field(default=None, gt=0.0, le=0.5)
    max_drawdown_pct: float | None = Field(default=None, gt=0.0, le=0.5)
    daily_target_pct: float | None = Field(default=None, gt=0.0, le=0.2)
    # Tier config overrides
    tier_config: dict | None = None
    # Strategy parameter overrides
    strategy_params: dict | None = None
    # Quality filter overrides
    quality_params: dict | None = None
    # Disabled strategies (full replacement list)
    disabled_strategies: list[str] | None = None


# Activity
class ActivityEvent(BaseModel):
    id: int
    timestamp: datetime
    event_type: str
    level: str
    title: str
    detail: str
    metadata: dict = {}
    market_id: str = ""
    strategy: str = ""


class ActivityResponse(BaseModel):
    events: list[ActivityEvent]
    total: int
    has_more: bool


# Health
class HealthCheck(BaseModel):
    status: str = "ok"
    uptime_seconds: float
    engine_running: bool = False
    cycle_count: int = 0
