import axios from "axios";

const API_BASE = import.meta.env.VITE_API_URL || "";

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 15000,
});

// Types matching API schemas
export interface PortfolioOverview {
  total_equity: number;
  cash_balance: number;
  positions_value: number;
  unrealized_pnl: number;
  realized_pnl_today: number;
  open_positions: number;
  peak_equity: number;
  tier: string;
  is_paper: boolean;
}

export interface Position {
  id: number;
  market_id: string;
  token_id: string;
  question: string;
  outcome: string;
  category: string;
  strategy: string;
  side: string;
  size: number;
  avg_price: number;
  current_price: number;
  cost_basis: number;
  unrealized_pnl: number;
  is_open: boolean;
  created_at: string;
}

export interface EquityPoint {
  timestamp: string;
  total_equity: number;
  cash_balance: number;
  positions_value: number;
  daily_return_pct: number;
}

export interface Trade {
  id: number;
  created_at: string;
  market_id: string;
  question: string;
  outcome: string;
  side: string;
  price: number;
  size: number;
  cost_usd: number;
  strategy: string;
  edge: number;
  estimated_prob: number;
  confidence: number;
  reasoning: string;
  status: string;
  pnl: number;
  is_paper: boolean;
}

export interface TradeStats {
  total_trades: number;
  winning_trades: number;
  total_pnl: number;
  win_rate: number;
}

export interface StrategyPerformance {
  strategy: string;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_pnl: number;
  avg_edge: number;
  sharpe_ratio: number;
  max_drawdown: number;
  avg_hold_time_hours: number;
}

export interface MarketOpportunity {
  market_id: string;
  question: string;
  category: string;
  yes_price: number;
  no_price: number;
  volume: number;
  liquidity: number;
  end_date: string | null;
  hours_to_resolution: number | null;
  signal_strategy: string;
  signal_edge: number;
  signal_confidence: number;
}

export interface RiskMetrics {
  tier: string;
  bankroll: number;
  peak_equity: number;
  current_drawdown_pct: number;
  max_drawdown_limit_pct: number;
  daily_pnl: number;
  daily_loss_limit_pct: number;
  max_positions: number;
  is_paused: boolean;
}

export interface RiskLimits {
  tier: string;
  max_positions: number;
  max_per_position_pct: number;
  daily_loss_limit_pct: number;
  max_drawdown_pct: number;
  min_edge_pct: number;
  min_win_prob: number;
  max_per_category_pct: number;
  kelly_fraction: number;
}

export interface BotConfig {
  trading_mode: string;
  scan_interval_seconds: number;
  snapshot_interval_seconds: number;
  max_daily_loss_pct: number;
  max_drawdown_pct: number;
}

export interface HealthCheck {
  status: string;
  mode: string;
  uptime_seconds: number;
  engine_running: boolean;
  cycle_count: number;
  equity: number;
}

// API functions
export const fetchPortfolio = () =>
  api.get<PortfolioOverview>("/api/portfolio/overview").then((r) => r.data);

export const fetchPositions = () =>
  api.get<Position[]>("/api/portfolio/positions").then((r) => r.data);

export const fetchEquityCurve = (days = 30) =>
  api.get<EquityPoint[]>(`/api/portfolio/equity-curve?days=${days}`).then((r) => r.data);

export const fetchTrades = (limit = 50, strategy?: string) =>
  api.get<Trade[]>("/api/trades/history", { params: { limit, strategy } }).then((r) => r.data);

export const fetchTradeStats = () =>
  api.get<TradeStats>("/api/trades/stats").then((r) => r.data);

export const fetchStrategies = () =>
  api.get<StrategyPerformance[]>("/api/strategies/performance").then((r) => r.data);

export const fetchMarkets = (limit = 20) =>
  api.get<MarketOpportunity[]>(`/api/markets/scanner?limit=${limit}`).then((r) => r.data);

export const fetchRiskMetrics = () =>
  api.get<RiskMetrics>("/api/risk/metrics").then((r) => r.data);

export const fetchRiskLimits = () =>
  api.get<RiskLimits>("/api/risk/limits").then((r) => r.data);

export const fetchConfig = () =>
  api.get<BotConfig>("/api/config/").then((r) => r.data);

export const updateConfig = (data: Partial<BotConfig>) =>
  api.put("/api/config/", data).then((r) => r.data);

export const pauseTrading = () =>
  api.post("/api/trading/pause").then((r) => r.data);

export const resumeTrading = () =>
  api.post("/api/trading/resume").then((r) => r.data);

export const fetchHealth = () =>
  api.get<HealthCheck>("/api/health").then((r) => r.data);
