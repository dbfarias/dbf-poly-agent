import type { EquityPoint, PortfolioOverview, Position } from "../../../src/api/client";

export function createPortfolioOverview(overrides?: Partial<PortfolioOverview>): PortfolioOverview {
  return {
    total_equity: 12.50,
    cash_balance: 8.25,
    positions_value: 4.25,
    unrealized_pnl: 0.35,
    realized_pnl_today: 0.18,
    open_positions: 2,
    peak_equity: 13.00,
    tier: "tier_1",
    is_paper: true,
    ...overrides,
  };
}

export function createPosition(overrides?: Partial<Position>): Position {
  return {
    id: 1,
    market_id: "0x1234567890abcdef",
    token_id: "tok_001",
    question: "Will BTC reach $100K by March 2026?",
    outcome: "Yes",
    category: "Crypto",
    strategy: "time_decay",
    side: "BUY",
    size: 10,
    avg_price: 0.45,
    current_price: 0.52,
    cost_basis: 4.50,
    unrealized_pnl: 0.70,
    is_open: true,
    created_at: "2026-02-25T10:00:00Z",
    ...overrides,
  };
}

export function createPositions(): Position[] {
  return [
    createPosition(),
    createPosition({
      id: 2,
      question: "Will ETH merge happen on time?",
      outcome: "No",
      category: "Crypto",
      strategy: "arbitrage",
      avg_price: 0.30,
      current_price: 0.28,
      cost_basis: 3.00,
      unrealized_pnl: -0.20,
    }),
  ];
}

export function createEquityPoints(): EquityPoint[] {
  const now = Date.now();
  return Array.from({ length: 7 }, (_, i) => ({
    timestamp: new Date(now - (6 - i) * 86400000).toISOString(),
    total_equity: 10 + i * 0.4 + Math.random() * 0.5,
    cash_balance: 7 + i * 0.2,
    positions_value: 3 + i * 0.2,
    daily_return_pct: 0.01 + Math.random() * 0.02,
  }));
}
