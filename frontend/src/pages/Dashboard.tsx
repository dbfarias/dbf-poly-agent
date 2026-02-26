import { useQuery } from "@tanstack/react-query";
import { DollarSign, Layers, Target, TrendingUp } from "lucide-react";
import { fetchPortfolio, fetchPositions, fetchTrades, fetchTradeStats } from "../api/client";
import EquityChart from "../components/EquityChart";
import HelpTooltip from "../components/HelpTooltip";
import StatCard from "../components/StatCard";
import TradeTable from "../components/TradeTable";

export default function Dashboard() {
  const { data: portfolio } = useQuery({
    queryKey: ["portfolio"],
    queryFn: fetchPortfolio,
    refetchInterval: 10000,
  });
  const { data: stats } = useQuery({
    queryKey: ["trade-stats"],
    queryFn: fetchTradeStats,
    refetchInterval: 30000,
  });
  const { data: positions } = useQuery({
    queryKey: ["positions"],
    queryFn: fetchPositions,
    refetchInterval: 10000,
  });
  const { data: recentTrades } = useQuery({
    queryKey: ["recent-trades"],
    queryFn: () => fetchTrades(10),
    refetchInterval: 15000,
  });

  return (
    <div className="space-y-6" data-testid="dashboard-page">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold">Dashboard</h2>
        {portfolio && (
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1">
              <span
                className={`px-2 py-1 rounded text-xs font-medium ${
                  portfolio.is_paper
                    ? "bg-yellow-500/20 text-yellow-300"
                    : "bg-green-500/20 text-green-300"
                }`}
                data-testid="trading-mode-badge"
              >
                {portfolio.is_paper ? "PAPER" : "LIVE"}
              </span>
              <HelpTooltip text="PAPER mode simulates trades without using real money. LIVE mode places real orders on Polymarket with your USDC balance." />
            </span>
            <span className="flex items-center gap-1">
              <span
                className="px-2 py-1 rounded bg-indigo-500/20 text-indigo-300 text-xs font-medium"
                data-testid="tier-badge"
              >
                {portfolio.tier.toUpperCase()}
              </span>
              <HelpTooltip text="Capital tier determines your risk limits. Tier 1: $5-$25 (1 position, conservative). Tier 2: $25-$100 (3 positions). Tier 3: $100+ (10 positions, full strategy access)." />
            </span>
          </div>
        )}
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Equity"
          value={`$${portfolio?.total_equity.toFixed(2) ?? "—"}`}
          icon={<DollarSign size={16} />}
          testId="total-equity"
          help="Your total portfolio value: cash balance plus the current market value of all open positions."
        />
        <StatCard
          title="Today's PnL"
          value={`$${portfolio?.realized_pnl_today.toFixed(2) ?? "—"}`}
          trend={
            portfolio?.realized_pnl_today
              ? portfolio.realized_pnl_today > 0
                ? "up"
                : portfolio.realized_pnl_today < 0
                  ? "down"
                  : "neutral"
              : "neutral"
          }
          icon={<TrendingUp size={16} />}
          testId="todays-pnl"
          help="Profit or loss realized today from closed trades. Green means profit, red means loss."
        />
        <StatCard
          title="Win Rate"
          value={stats ? `${(stats.win_rate * 100).toFixed(0)}%` : "—"}
          subtitle={stats ? `${stats.winning_trades}/${stats.total_trades} trades` : undefined}
          icon={<Target size={16} />}
          testId="win-rate"
          help="Percentage of trades that ended in profit. Shows winning trades out of total trades completed."
        />
        <StatCard
          title="Open Positions"
          value={portfolio?.open_positions ?? 0}
          subtitle={`$${portfolio?.positions_value.toFixed(2) ?? "0"} value`}
          icon={<Layers size={16} />}
          testId="open-positions"
          help="Number of active bets currently held. The subtitle shows their total current market value."
        />
      </div>

      {/* Equity Chart */}
      <EquityChart />

      {/* Active Positions */}
      {positions && positions.length > 0 && (
        <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4" data-testid="active-positions-section">
          <h3 className="text-sm font-medium text-zinc-400 mb-3">Active Positions</h3>
          <div className="space-y-2">
            {positions.map((p) => (
              <div
                key={p.id}
                className="flex items-center justify-between py-2 px-3 rounded bg-[#0f1117]/50"
                data-testid={`position-row-${p.id}`}
              >
                <div className="flex-1 min-w-0">
                  <div className="text-sm truncate">{p.question}</div>
                  <div className="text-xs text-zinc-500">
                    {p.outcome} · {p.strategy} · ${p.avg_price.toFixed(3)}
                  </div>
                </div>
                <div className="text-right ml-4">
                  <div className="text-sm">${p.current_price.toFixed(3)}</div>
                  <div
                    className={`text-xs font-medium ${
                      p.unrealized_pnl >= 0 ? "text-green-400" : "text-red-400"
                    }`}
                  >
                    ${p.unrealized_pnl.toFixed(2)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Recent Trades */}
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4" data-testid="recent-trades-section">
        <h3 className="text-sm font-medium text-zinc-400 mb-3">Recent Trades</h3>
        <TradeTable trades={recentTrades ?? []} compact />
      </div>
    </div>
  );
}
