import axios from "axios";

const API_BASE = import.meta.env.VITE_API_URL || "";

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 15000,
  withCredentials: true,
});

// Auto-logout on 401 (expired session)
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (
      err.response?.status === 401 &&
      !err.config?.url?.includes("/auth/login") &&
      !err.config?.url?.includes("/auth/me")
    ) {
      window.location.reload();
    }
    return Promise.reject(err);
  },
);

export const getWsUrl = (path: string): string => {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const host = API_BASE || window.location.host;
  // Cookie-based auth: browser sends httpOnly cookie automatically for same-origin.
  // WebSocket API doesn't send cookies, so we still need a token param as fallback.
  // The /api/auth/me response could provide a short-lived WS token in the future.
  return `${proto}//${host}${path}`;
};

// Types matching API schemas
export interface PortfolioOverview {
  total_equity: number;
  cash_balance: number;
  positions_value: number;
  unrealized_pnl: number;
  realized_pnl_today: number;
  polymarket_pnl_today: number;
  open_positions: number;
  peak_equity: number;
  day_start_equity: number;
  is_paper: boolean;
  daily_target_pct: number;
  daily_target_usd: number;
  daily_progress_pct: number;
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
  entry_price: number;
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

export interface StrategyStatus {
  name: string;
  label: string;
  is_admin_disabled: boolean;
  is_learner_paused: boolean;
  pause_remaining_hours: number;
  is_active: boolean;
  total_trades: number;
  win_rate: number;
  total_pnl: number;
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
  daily_target_pct: number;
  use_llm_sentiment: boolean;
  use_llm_debate: boolean;
  use_llm_reviewer: boolean;
  use_multi_round_debate: boolean;
  use_llm_keywords: boolean;
  use_llm_post_mortem: boolean;
  use_auto_claim: boolean;
  llm_daily_budget: number;
  llm_today_cost: number;
  risk_config: Record<string, number>;
  strategy_params: Record<string, Record<string, number>>;
  quality_params: Record<string, number>;
  disabled_strategies: string[];
  blocked_market_types: string[];
}

export interface ConfigUpdateResponse {
  status: string;
  changes: string[];
}

export interface HealthCheck {
  status: string;
  uptime_seconds: number;
  engine_running: boolean;
  cycle_count: number;
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

export const fetchStrategyStatus = () =>
  api.get<StrategyStatus[]>("/api/strategies/status").then((r) => r.data);

export const fetchMarkets = (limit = 20) =>
  api.get<MarketOpportunity[]>(`/api/markets/scanner?limit=${limit}`).then((r) => r.data);

export const fetchRiskMetrics = () =>
  api.get<RiskMetrics>("/api/risk/metrics").then((r) => r.data);

export const fetchRiskLimits = () =>
  api.get<RiskLimits>("/api/risk/limits").then((r) => r.data);

export const fetchConfig = () =>
  api.get<BotConfig>("/api/config/").then((r) => r.data);

export const updateConfig = (data: Record<string, unknown>) =>
  api.put<ConfigUpdateResponse>("/api/config/", data).then((r) => r.data);

export const pauseTrading = () =>
  api.post("/api/config/trading/pause").then((r) => r.data);

export const resumeTrading = () =>
  api.post("/api/config/trading/resume").then((r) => r.data);

export const fetchHealth = () =>
  api.get<HealthCheck>("/api/health").then((r) => r.data);

export interface RiskResetResponse {
  status: string;
  equity: number;
  daily_pnl: number;
  peak_equity: number;
}

export const resetRiskState = () =>
  api.post<RiskResetResponse>("/api/config/risk/reset").then((r) => r.data);

// Learner types
export interface EdgeMultiplier {
  strategy: string;
  category: string;
  multiplier: number;
  win_rate: number | null;
  total_trades: number;
  total_pnl: number;
  avg_edge: number;
  status: string;
}

export interface CategoryConfidence {
  category: string;
  confidence: number;
  total_trades: number;
  win_rate: number;
  total_pnl: number;
  status: string;
}

export interface LearnerMultipliers {
  edge_multipliers: EdgeMultiplier[];
  category_confidences: CategoryConfidence[];
  paused_strategies: string[];
  brier_scores: Record<string, number>;
  last_computed: string | null;
}

export interface CalibrationBucket {
  bucket: string;
  estimated_prob: number;
  actual_win_rate: number;
  calibration_ratio: number;
  total_trades: number;
  wins: number;
  losses: number;
  is_calibrated: boolean;
}

export interface LearnerCalibration {
  buckets: CalibrationBucket[];
  last_computed: string | null;
}

export interface StrategyPauseInfo {
  strategy: string;
  paused_at: string;
  elapsed_hours: number;
  remaining_hours: number;
  expires_at: string;
}

export interface StrategyPauseStatus {
  strategy: string;
  is_paused: boolean;
  is_admin_disabled: boolean;
  pause_info: StrategyPauseInfo | null;
}

export interface LearnerPauses {
  strategies: StrategyPauseStatus[];
  active_pauses: number;
  last_computed: string | null;
}

// Learner API functions
export const fetchLearnerMultipliers = () =>
  api.get<LearnerMultipliers>("/api/learner/multipliers").then((r) => r.data);

export const fetchLearnerCalibration = () =>
  api.get<LearnerCalibration>("/api/learner/calibration").then((r) => r.data);

export const fetchLearnerPauses = () =>
  api.get<LearnerPauses>("/api/learner/pauses").then((r) => r.data);

export const unpauseStrategy = (strategy: string) =>
  api.post<{ strategy: string; was_paused: boolean; status: string }>(
    "/api/learner/unpause",
    { strategy }
  ).then((r) => r.data);

export const triggerResearchScan = () =>
  api.post<{ status: string; markets_scanned: number }>(
    "/api/research/scan"
  ).then((r) => r.data);

// Daily PnL types
export interface DailyPnlPoint {
  date: string;
  start_equity: number;
  end_equity: number;
  pnl: number;
  pnl_pct: number;
  target: number;
  hit_target: boolean;
}

export const fetchDailyPnl = () =>
  api.get<DailyPnlPoint[]>("/api/portfolio/daily-pnl").then((r) => r.data);

// Activity types
export interface ActivityEvent {
  id: number;
  timestamp: string;
  event_type: string;
  level: string;
  title: string;
  detail: string;
  metadata: Record<string, unknown>;
  market_id: string;
  strategy: string;
}

export interface ActivityResponse {
  events: ActivityEvent[];
  total: number;
  has_more: boolean;
}

// Activity API functions
export const fetchActivity = (params: {
  limit?: number;
  offset?: number;
  event_type?: string;
  level?: string;
  strategy?: string;
}) =>
  api
    .get<ActivityResponse>("/api/activity/", { params })
    .then((r) => r.data);

export const fetchActivityEventTypes = () =>
  api.get<string[]>("/api/activity/event-types").then((r) => r.data);

// Research types
export interface ResearchHeadline {
  title: string;
  source: string;
  sentiment: number;
  published: string;
  url?: string;
}

export interface ResearchMarket {
  market_id: string;
  keywords: string[];
  sentiment_score: number;
  confidence: number;
  research_multiplier: number;
  crypto_sentiment: number;
  updated_at: string;
  article_count: number;
  is_volume_anomaly: boolean;
  whale_activity: boolean;
  market_category: string;
  resolution_condition: string;
  resolution_source: string;
  historical_base_rate: number;
  top_headlines: ResearchHeadline[];
}

export interface ResearchStatus {
  running: boolean;
  scan_interval_seconds: number;
  max_markets: number;
  cached_markets: number;
  last_scan: string | null;
  markets_scanned: number;
}

// Research API functions
export const fetchResearchStatus = () =>
  api.get<ResearchStatus>("/api/research/status").then((r) => r.data);

export const fetchResearchMarkets = () =>
  api.get<ResearchMarket[]>("/api/research/markets").then((r) => r.data);

// LLM Cost tracking
export interface LlmDailyCost {
  date: string;
  debate_cost: number;
  review_cost: number;
  risk_debate_cost: number;
  total_cost: number;
  daily_pnl: number;
  net_profit: number;
}

export const fetchLlmCosts = () =>
  api.get<LlmDailyCost[]>("/api/activity/llm-costs").then((r) => r.data);

// Daily Report types
export interface ReportMarket {
  market_id: string;
  question: string;
  sentiment_score: number;
  confidence: number;
  research_multiplier: number;
  category: string;
  article_count: number;
  end_date: string | null;
  is_volume_anomaly: boolean;
  whale_activity: boolean;
  updated_at: string;
}

export interface ReportStrategy {
  name: string;
  win_rate: number;
  total_pnl: number;
  total_trades: number;
  is_paused: boolean;
}

export interface ReportAlert {
  type: string;
  severity: "warning" | "danger";
  message: string;
}

export interface ReportSentiment {
  avg_sentiment: number;
  total_markets: number;
  positive: number;
  negative: number;
  neutral: number;
}

export interface ReportPortfolioSummary {
  total_equity: number;
  cash_balance: number;
  positions_value: number;
  day_start_equity: number;
  daily_pnl: number;
  daily_return_pct: number;
  open_positions: number;
  trading_mode: string;
  daily_target_pct: number;
  daily_progress_pct: number;
}

export interface DailyReport {
  generated_at: string;
  portfolio_summary: ReportPortfolioSummary;
  top_markets: ReportMarket[];
  strategy_health: ReportStrategy[];
  risk_alerts: ReportAlert[];
  sentiment_overview: ReportSentiment;
}

export const fetchDailyReport = () =>
  api.get<DailyReport>("/api/report/daily").then((r) => r.data);

// Capital Flow types
export interface CapitalFlow {
  id: number;
  timestamp: string;
  amount: number;
  flow_type: string;
  source: string;
  note: string;
  is_paper: boolean;
}

export const fetchCapitalFlows = (limit = 50) =>
  api.get<CapitalFlow[]>("/api/portfolio/capital-flows", { params: { limit } }).then((r) => r.data);

// Trade Assistant types
export interface AssistantRequest {
  message: string;
}

export interface AssistantResponse {
  success: boolean;
  log: string[];
  mode: string | null;
  market_title: string | null;
  outcome: string | null;
  side: string | null;
  price: number | null;
  shares: number | null;
  cost: number | null;
  order_id: string | null;
  error: string | null;
}

export const executeTradeAssistant = async (message: string): Promise<AssistantResponse> => {
  const { data } = await api.post<AssistantResponse>("/api/assistant/execute", { message });
  return data;
};

export const analyzeAssistant = async (message: string): Promise<AssistantResponse> => {
  const { data } = await api.post<AssistantResponse>("/api/assistant/analyze", { message });
  return data;
};

// Sell Position types
export interface SellPositionRequest {
  market_id: string;
  size?: number;
}

export interface SellPositionResponse {
  success: boolean;
  market_id: string;
  question: string | null;
  size_sold: number | null;
  price: number | null;
  proceeds: number | null;
  order_id: string | null;
  error: string | null;
}

export const sellPosition = async (req: SellPositionRequest): Promise<SellPositionResponse> => {
  const { data } = await api.post<SellPositionResponse>("/api/portfolio/positions/sell", req);
  return data;
};

// Watcher types
export interface WatcherItem {
  id: number;
  created_at: string;
  updated_at: string;
  market_id: string;
  token_id: string;
  question: string;
  outcome: string;
  keywords: string;
  thesis: string;
  max_exposure_usd: number;
  stop_loss_pct: number;
  max_age_hours: number;
  check_interval_sec: number;
  status: string;
  current_exposure: number;
  avg_entry_price: number;
  scale_count: number;
  max_scale_count: number;
  highest_price: number;
  last_check_at: string | null;
  last_news_at: string | null;
  end_date: string | null;
  source_strategy: string;
  auto_created: boolean;
}

export interface CreateWatcherBody {
  market_id: string;
  token_id?: string;
  question?: string;
  outcome?: string;
  keywords?: string[];
  thesis?: string;
  current_price?: number;
  max_exposure_usd?: number;
  stop_loss_pct?: number;
  max_age_hours?: number;
}

// Watcher API functions
export const fetchWatchers = async (): Promise<WatcherItem[]> => {
  const { data } = await api.get<WatcherItem[]>("/api/watchers");
  return data;
};

export const killWatcher = async (id: number): Promise<{ success: boolean; watcher_id: number }> => {
  const { data } = await api.post(`/api/watchers/${id}/kill`);
  return data;
};

export const createWatcher = async (body: CreateWatcherBody): Promise<WatcherItem> => {
  const { data } = await api.post<WatcherItem>("/api/watchers", body);
  return data;
};
