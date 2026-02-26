import type { Trade, TradeStats } from "../../../src/api/client";

export function createTrade(overrides?: Partial<Trade>): Trade {
  return {
    id: 1,
    created_at: "2026-02-25T14:30:00Z",
    market_id: "0xabc123",
    question: "Will BTC reach $100K by March 2026?",
    outcome: "Yes",
    side: "BUY",
    price: 0.45,
    size: 10,
    cost_usd: 4.50,
    strategy: "time_decay",
    edge: 0.05,
    estimated_prob: 0.50,
    confidence: 0.72,
    reasoning: "Strong momentum detected",
    status: "filled",
    pnl: 0.35,
    is_paper: true,
    ...overrides,
  };
}

export function createTrades(count = 5): Trade[] {
  return Array.from({ length: count }, (_, i) =>
    createTrade({
      id: i + 1,
      question: `Market question #${i + 1}`,
      strategy: ["time_decay", "arbitrage", "value_betting", "market_making"][i % 4],
      side: i % 2 === 0 ? "BUY" : "SELL",
      price: 0.30 + i * 0.1,
      cost_usd: 3.0 + i * 0.5,
      edge: 0.02 + i * 0.01,
      pnl: i % 3 === 0 ? -0.15 : 0.25 + i * 0.05,
      status: ["filled", "pending", "completed", "cancelled", "filled"][i % 5],
      created_at: new Date(Date.now() - i * 3600000).toISOString(),
    }),
  );
}

export function createTradeStats(overrides?: Partial<TradeStats>): TradeStats {
  return {
    total_trades: 34,
    winning_trades: 22,
    total_pnl: 1.85,
    win_rate: 0.647,
    ...overrides,
  };
}
