import type { RiskLimits, RiskMetrics } from "../../../src/api/client";

export function createRiskMetrics(overrides?: Partial<RiskMetrics>): RiskMetrics {
  return {
    tier: "tier_1",
    bankroll: 12.50,
    peak_equity: 13.00,
    current_drawdown_pct: 0.038,
    max_drawdown_limit_pct: 0.25,
    daily_pnl: 0.18,
    daily_loss_limit_pct: 0.10,
    max_positions: 3,
    is_paused: false,
    ...overrides,
  };
}

export function createRiskLimits(overrides?: Partial<RiskLimits>): RiskLimits {
  return {
    tier: "tier_1",
    max_positions: 3,
    max_per_position_pct: 0.15,
    daily_loss_limit_pct: 0.10,
    max_drawdown_pct: 0.25,
    min_edge_pct: 0.03,
    min_win_prob: 0.55,
    max_per_category_pct: 0.40,
    kelly_fraction: 0.25,
    ...overrides,
  };
}
