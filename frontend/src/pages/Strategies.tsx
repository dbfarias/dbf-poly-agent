import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { fetchStrategies } from "../api/client";
import HelpTooltip from "../components/HelpTooltip";

export default function Strategies() {
  const { data: strategies, isLoading } = useQuery({
    queryKey: ["strategies"],
    queryFn: fetchStrategies,
    refetchInterval: 30000,
  });

  if (isLoading) return <div className="text-zinc-500" data-testid="strategies-loading">Loading...</div>;

  const all = strategies ?? [];
  const defaultStrategies = [
    { name: "time_decay", label: "Time Decay", tier: "Tier 1+", help: "Buys YES on markets very likely to resolve YES that are close to expiring. Profits from the price converging to $1.00 as resolution approaches." },
    { name: "arbitrage", label: "Arbitrage", tier: "Tier 1+", help: "Finds price discrepancies where YES + NO prices don't sum to $1.00 and captures the gap as risk-free profit." },
    { name: "price_divergence", label: "Price Divergence", tier: "Tier 1+", help: "Detects divergences between external data (crypto prices, news sentiment) and contract prices. Targets 0.3-0.8% micro-trades with tight TP/SL." },
    { name: "swing_trading", label: "Swing Trading", tier: "Tier 2+", help: "Buys liquid mid-range markets with confirmed upward momentum and sells for 1.5% profit within hours." },
    { name: "value_betting", label: "Value Betting", tier: "Tier 1+", help: "Identifies markets where the bot's estimated probability differs significantly from the market price, betting on the mispricing." },
    { name: "market_making", label: "Market Making", tier: "Tier 3+", help: "Places both buy and sell orders to earn the bid-ask spread. Requires larger capital for sufficient order sizes." },
  ];

  return (
    <div className="space-y-6" data-testid="strategies-page">
      <h2 className="text-xl font-bold">Strategy Performance</h2>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {defaultStrategies.map(({ name, label, tier, help }) => {
          const s = all.find((x) => x.strategy === name);
          return (
            <div key={name} className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5" data-testid={`strategy-card-${name}`}>
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="font-medium text-white flex items-center">
                    {label}
                    <HelpTooltip text={help} />
                  </h3>
                  <span className="text-xs text-zinc-500">{tier}</span>
                </div>
                <span
                  className={clsx(
                    "px-2 py-0.5 rounded text-xs",
                    s && s.total_trades > 0
                      ? "bg-green-500/20 text-green-300"
                      : "bg-zinc-700/50 text-zinc-400",
                  )}
                  data-testid={`strategy-status-${name}`}
                >
                  {s && s.total_trades > 0 ? "Active" : "Waiting"}
                </span>
              </div>

              {s && s.total_trades > 0 ? (
                <div className="grid grid-cols-3 gap-4 text-center" data-testid={`strategy-metrics-${name}`}>
                  <div>
                    <div className="text-lg font-bold text-white">{s.total_trades}</div>
                    <div className="text-xs text-zinc-500 flex items-center justify-center">Trades <HelpTooltip text="Total trades executed by this strategy." size={11} /></div>
                  </div>
                  <div>
                    <div className="text-lg font-bold text-white">
                      {(s.win_rate * 100).toFixed(0)}%
                    </div>
                    <div className="text-xs text-zinc-500 flex items-center justify-center">Win Rate <HelpTooltip text="Percentage of this strategy's trades that were profitable." size={11} /></div>
                  </div>
                  <div>
                    <div
                      className={clsx(
                        "text-lg font-bold",
                        s.total_pnl >= 0 ? "text-green-400" : "text-red-400",
                      )}
                    >
                      ${s.total_pnl.toFixed(2)}
                    </div>
                    <div className="text-xs text-zinc-500 flex items-center justify-center">PnL <HelpTooltip text="Total profit or loss from this strategy's trades." size={11} /></div>
                  </div>
                  <div>
                    <div className="text-lg font-bold text-white">
                      {(s.avg_edge * 100).toFixed(1)}%
                    </div>
                    <div className="text-xs text-zinc-500 flex items-center justify-center">Avg Edge <HelpTooltip text="Average expected profit margin across trades. Higher edge means the strategy finds better opportunities." size={11} /></div>
                  </div>
                  <div>
                    <div className="text-lg font-bold text-white">{s.sharpe_ratio.toFixed(2)}</div>
                    <div className="text-xs text-zinc-500 flex items-center justify-center">Sharpe <HelpTooltip text="Sharpe Ratio measures risk-adjusted returns. Above 1.0 is good, above 2.0 is excellent. It shows return per unit of risk taken." size={11} /></div>
                  </div>
                  <div>
                    <div className="text-lg font-bold text-white">
                      {s.avg_hold_time_hours.toFixed(1)}h
                    </div>
                    <div className="text-xs text-zinc-500 flex items-center justify-center">Avg Hold <HelpTooltip text="Average time a position is held before closing. Shorter hold times mean faster capital turnover." size={11} /></div>
                  </div>
                </div>
              ) : (
                <div className="text-zinc-500 text-sm py-4 text-center" data-testid={`strategy-empty-${name}`}>
                  No trades yet. Strategy will activate when conditions are met.
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
