import { useQuery } from "@tanstack/react-query";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { fetchPortfolio, fetchPositions, fetchRiskLimits, fetchRiskMetrics } from "../api/client";
import HelpTooltip from "../components/HelpTooltip";
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
    <div className="space-y-6" data-testid="risk-page">
      <h2 className="text-xl font-bold">Risk Management</h2>

      {/* Risk Metrics */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4">
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
          testId="drawdown"
          help="How far your portfolio has dropped from its highest value. If this exceeds the max limit, trading pauses automatically to protect your capital."
        />
        <StatCard
          title="Daily PnL"
          value={risk ? `$${risk.daily_pnl.toFixed(2)}` : "—"}
          subtitle={risk ? `Limit: ${(risk.daily_loss_limit_pct * 100).toFixed(0)}%` : undefined}
          trend={risk ? (risk.daily_pnl >= 0 ? "up" : "down") : "neutral"}
          testId="daily-pnl"
          help="Your profit or loss for today. If daily losses exceed the limit (shown in subtitle), trading pauses until the next day."
        />
        <StatCard
          title="Positions"
          value={
            risk ? `${portfolio?.open_positions ?? 0}/${risk.max_positions}` : "—"
          }
          testId="risk-positions"
          help="Current open positions vs. maximum allowed. Your capital tier determines how many simultaneous bets you can hold."
        />
        <StatCard
          title="Trading Status"
          value={risk?.is_paused ? "PAUSED" : "ACTIVE"}
          trend={risk?.is_paused ? "down" : "up"}
          testId="trading-status"
          help="Shows whether the bot is actively looking for trades. It pauses automatically when risk limits are hit, or you can pause it manually from Settings."
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Category Exposure */}
        <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4" data-testid="category-exposure">
          <h3 className="text-sm font-medium text-zinc-400 mb-4 flex items-center">
            Category Exposure
            <HelpTooltip text="Shows how your money is distributed across market categories (crypto, politics, sports, etc.). Diversifying reduces risk from a single category going wrong." />
          </h3>
          {pieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={200} data-testid="category-pie-chart">
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
            <div className="h-[200px] flex items-center justify-center text-zinc-500 text-sm" data-testid="category-exposure-empty">
              No positions
            </div>
          )}
        </div>

        {/* Risk Limits Table */}
        <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4" data-testid="risk-limits-section">
          <h3 className="text-sm font-medium text-zinc-400 mb-4" data-testid="risk-limits-title">
            Risk Limits ({limits?.tier.toUpperCase()})
          </h3>
          {limits && (
            <div className="space-y-3">
              {[
                { label: "Max Positions", value: limits.max_positions, help: "Maximum number of open bets allowed at the same time." },
                { label: "Max Per Position", value: `${(limits.max_per_position_pct * 100).toFixed(0)}%`, help: "Maximum percentage of your bankroll that can be allocated to a single bet." },
                { label: "Daily Loss Limit", value: `${(limits.daily_loss_limit_pct * 100).toFixed(0)}%`, help: "If your losses today exceed this percentage of your bankroll, trading pauses until tomorrow." },
                { label: "Max Drawdown", value: `${(limits.max_drawdown_pct * 100).toFixed(0)}%`, help: "Maximum allowed drop from your peak equity. Exceeding this pauses all trading to protect capital." },
                { label: "Min Edge", value: `${(limits.min_edge_pct * 100).toFixed(0)}%`, help: "Minimum expected profit margin required before the bot places a trade. Higher = more selective." },
                { label: "Min Win Prob", value: `${(limits.min_win_prob * 100).toFixed(0)}%`, help: "Minimum estimated win probability required. The bot only bets on markets it's highly confident about." },
                { label: "Max Per Category", value: `${(limits.max_per_category_pct * 100).toFixed(0)}%`, help: "Maximum percentage of bankroll in any single market category (crypto, politics, etc.) to ensure diversification." },
                { label: "Kelly Fraction", value: `${limits.kelly_fraction}x`, help: "Fraction of the Kelly Criterion used for position sizing. 0.25x means betting 1/4 of the mathematically optimal amount — conservative to reduce variance." },
              ].map(({ label, value, help }, i) => (
                <div key={label} className="flex justify-between text-sm" data-testid={`risk-limit-${i}`}>
                  <span className="text-zinc-400 flex items-center">
                    {label}
                    <HelpTooltip text={help} />
                  </span>
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
