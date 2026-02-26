import type { MarketOpportunity } from "../../../src/api/client";

export function createMarketOpportunity(overrides?: Partial<MarketOpportunity>): MarketOpportunity {
  return {
    market_id: "0xmarket001",
    question: "Will SpaceX launch Starship successfully?",
    category: "Science",
    yes_price: 0.65,
    no_price: 0.35,
    volume: 125000,
    liquidity: 50000,
    end_date: "2026-04-01T00:00:00Z",
    hours_to_resolution: 720,
    signal_strategy: "time_decay",
    signal_edge: 0.045,
    signal_confidence: 0.78,
    ...overrides,
  };
}

export function createMarketList(count = 5): MarketOpportunity[] {
  const questions = [
    "Will SpaceX launch Starship successfully?",
    "Will Fed cut rates in Q2 2026?",
    "Will BTC reach $150K by June?",
    "Will AI regulation pass in EU?",
    "Will unemployment drop below 3.5%?",
  ];

  return Array.from({ length: count }, (_, i) =>
    createMarketOpportunity({
      market_id: `0xmarket${String(i).padStart(3, "0")}`,
      question: questions[i % questions.length],
      category: ["Science", "Finance", "Crypto", "Politics", "Economics"][i % 5],
      yes_price: 0.30 + i * 0.1,
      no_price: 0.70 - i * 0.1,
      volume: 50000 + i * 25000,
      hours_to_resolution: i === 2 ? null : 100 + i * 200,
      signal_strategy: i < 3 ? "time_decay" : "",
      signal_edge: i < 3 ? 0.02 + i * 0.015 : 0,
      signal_confidence: i < 3 ? 0.65 + i * 0.1 : 0,
    }),
  );
}
