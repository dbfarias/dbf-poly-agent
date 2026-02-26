import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { fetchStrategies } from "../api/client";

export default function Strategies() {
  const { data: strategies, isLoading } = useQuery({
    queryKey: ["strategies"],
    queryFn: fetchStrategies,
    refetchInterval: 30000,
  });

  if (isLoading) return <div className="text-zinc-500" data-testid="strategies-loading">Loading...</div>;

  const all = strategies ?? [];
  const defaultStrategies = [
    { name: "time_decay", label: "Time Decay", tier: "Tier 1+" },
    { name: "arbitrage", label: "Arbitrage", tier: "Tier 1+" },
    { name: "value_betting", label: "Value Betting", tier: "Tier 2+" },
    { name: "market_making", label: "Market Making", tier: "Tier 3+" },
  ];

  return (
    <div className="space-y-6" data-testid="strategies-page">
      <h2 className="text-xl font-bold">Strategy Performance</h2>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {defaultStrategies.map(({ name, label, tier }) => {
          const s = all.find((x) => x.strategy === name);
          return (
            <div key={name} className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5" data-testid={`strategy-card-${name}`}>
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h3 className="font-medium text-white">{label}</h3>
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
                    <div className="text-xs text-zinc-500">Trades</div>
                  </div>
                  <div>
                    <div className="text-lg font-bold text-white">
                      {(s.win_rate * 100).toFixed(0)}%
                    </div>
                    <div className="text-xs text-zinc-500">Win Rate</div>
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
                    <div className="text-xs text-zinc-500">PnL</div>
                  </div>
                  <div>
                    <div className="text-lg font-bold text-white">
                      {(s.avg_edge * 100).toFixed(1)}%
                    </div>
                    <div className="text-xs text-zinc-500">Avg Edge</div>
                  </div>
                  <div>
                    <div className="text-lg font-bold text-white">{s.sharpe_ratio.toFixed(2)}</div>
                    <div className="text-xs text-zinc-500">Sharpe</div>
                  </div>
                  <div>
                    <div className="text-lg font-bold text-white">
                      {s.avg_hold_time_hours.toFixed(1)}h
                    </div>
                    <div className="text-xs text-zinc-500">Avg Hold</div>
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
