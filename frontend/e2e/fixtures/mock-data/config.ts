import type { BotConfig, HealthCheck } from "../../../src/api/client";

export function createBotConfig(overrides?: Partial<BotConfig>): BotConfig {
  return {
    trading_mode: "paper",
    scan_interval_seconds: 300,
    snapshot_interval_seconds: 3600,
    max_daily_loss_pct: 0.10,
    max_drawdown_pct: 0.25,
    ...overrides,
  };
}

export function createHealthCheck(overrides?: Partial<HealthCheck>): HealthCheck {
  return {
    status: "healthy",
    mode: "paper",
    uptime_seconds: 7200,
    engine_running: true,
    cycle_count: 42,
    equity: 12.50,
    ...overrides,
  };
}
