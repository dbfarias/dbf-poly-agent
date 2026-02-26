import type { StrategyPerformance } from "../../../src/api/client";

export function createStrategyPerformance(overrides?: Partial<StrategyPerformance>): StrategyPerformance {
  return {
    strategy: "time_decay",
    total_trades: 18,
    winning_trades: 12,
    losing_trades: 6,
    win_rate: 0.667,
    total_pnl: 1.25,
    avg_edge: 0.042,
    sharpe_ratio: 1.85,
    max_drawdown: 0.08,
    avg_hold_time_hours: 36.5,
    ...overrides,
  };
}

export function createAllStrategies(): StrategyPerformance[] {
  return [
    createStrategyPerformance(),
    createStrategyPerformance({
      strategy: "arbitrage",
      total_trades: 8,
      winning_trades: 6,
      losing_trades: 2,
      win_rate: 0.75,
      total_pnl: 0.60,
      avg_edge: 0.031,
      sharpe_ratio: 2.10,
      max_drawdown: 0.03,
      avg_hold_time_hours: 4.2,
    }),
    createStrategyPerformance({
      strategy: "value_betting",
      total_trades: 0,
      winning_trades: 0,
      losing_trades: 0,
      win_rate: 0,
      total_pnl: 0,
      avg_edge: 0,
      sharpe_ratio: 0,
      max_drawdown: 0,
      avg_hold_time_hours: 0,
    }),
    createStrategyPerformance({
      strategy: "market_making",
      total_trades: 0,
      winning_trades: 0,
      losing_trades: 0,
      win_rate: 0,
      total_pnl: 0,
      avg_edge: 0,
      sharpe_ratio: 0,
      max_drawdown: 0,
      avg_hold_time_hours: 0,
    }),
  ];
}
