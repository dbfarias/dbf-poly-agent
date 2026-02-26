import { useQuery } from "@tanstack/react-query";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { fetchPortfolio, fetchPositions, fetchRiskLimits, fetchRiskMetrics } from "../api/client";
import StatCard from "../components/StatCard";

const COLORS = ["#6366f1", "#22c55e", "#eab308", "#ef4444", "#8b5cf6", "#06b6d4"];

export default function Risk() {
  const { data: risk } = useQuery({
    queryKey: ["risk-metrics"],
    queryFn: fetchRiskMetrics,
    refetchInterval: 10000,
  });
  const { data: limits } = useQuery({
    queryKey: ["risk-limits"],
    queryFn: fetchRiskLimits,
    refetchInterval: 30000,
  });
  const { data: portfolio } = useQuery({
    queryKey: ["portfolio"],
    queryFn: fetchPortfolio,
    refetchInterval: 10000,
  });
  const { data: positions } = useQuery({
    queryKey: ["positions"],
    queryFn: fetchPositions,
    refetchInterval: 10000,
  });

  // Build category allocation data
  const categoryData = positions?.reduce(
    (acc, p) => {
      const cat = p.category || "Unknown";
      acc[cat] = (acc[cat] || 0) + p.cost_basis;
      return acc;
    },
    {} as Record<string, number>,
  );
  const pieData = Object.entries(categoryData ?? {}).map(([name, value]) => ({
    name,
    value: Number(value.toFixed(2)),
  }));

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-bold">Risk Management</h2>

      {/* Risk Metrics */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Current Drawdown"
          value={risk ? `${(risk.current_drawdown_pct * 100).toFixed(1)}%` : "—"}
          subtitle={risk ? `Max: ${(risk.max_drawdown_limit_pct * 100).toFixed(0)}%` : undefined}
          trend={
            risk
              ? risk.current_drawdown_pct > risk.max_drawdown_limit_pct * 0.5
                ? "down"
                : "neutral"
              : "neutral"
          }
        />
        <StatCard
          title="Daily PnL"
          value={risk ? `$${risk.daily_pnl.toFixed(2)}` : "—"}
          subtitle={risk ? `Limit: ${(risk.daily_loss_limit_pct * 100).toFixed(0)}%` : undefined}
          trend={risk ? (risk.daily_pnl >= 0 ? "up" : "down") : "neutral"}
        />
        <StatCard
          title="Positions"
          value={
            risk ? `${portfolio?.open_positions ?? 0}/${risk.max_positions}` : "—"
          }
        />
        <StatCard
          title="Trading Status"
          value={risk?.is_paused ? "PAUSED" : "ACTIVE"}
          trend={risk?.is_paused ? "down" : "up"}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Category Exposure */}
        <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4">
          <h3 className="text-sm font-medium text-zinc-400 mb-4">Category Exposure</h3>
          {pieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80}>
                  {pieData.map((_, i) => (
                    <Cell key={i} fill={COLORS[i % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: "#1e2130",
                    border: "1px solid #2a2d3e",
                    borderRadius: 8,
                    color: "#e4e4e7",
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <div className="h-[200px] flex items-center justify-center text-zinc-500 text-sm">
              No positions
            </div>
          )}
        </div>

        {/* Risk Limits Table */}
        <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4">
          <h3 className="text-sm font-medium text-zinc-400 mb-4">
            Risk Limits ({limits?.tier.toUpperCase()})
          </h3>
          {limits && (
            <div className="space-y-3">
              {[
                { label: "Max Positions", value: limits.max_positions },
                { label: "Max Per Position", value: `${(limits.max_per_position_pct * 100).toFixed(0)}%` },
                { label: "Daily Loss Limit", value: `${(limits.daily_loss_limit_pct * 100).toFixed(0)}%` },
                { label: "Max Drawdown", value: `${(limits.max_drawdown_pct * 100).toFixed(0)}%` },
                { label: "Min Edge", value: `${(limits.min_edge_pct * 100).toFixed(0)}%` },
                { label: "Min Win Prob", value: `${(limits.min_win_prob * 100).toFixed(0)}%` },
                { label: "Max Per Category", value: `${(limits.max_per_category_pct * 100).toFixed(0)}%` },
                { label: "Kelly Fraction", value: `${limits.kelly_fraction}x` },
              ].map(({ label, value }) => (
                <div key={label} className="flex justify-between text-sm">
                  <span className="text-zinc-400">{label}</span>
                  <span className="text-white font-medium">{value}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
