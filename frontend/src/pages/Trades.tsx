import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";
import { fetchTrades, fetchTradeStats, type Trade } from "../api/client";
import StatCard from "../components/StatCard";
import TradeTable from "../components/TradeTable";

export default function Trades() {
  const [strategy, setStrategy] = useState<string>("");
  const [limit, setLimit] = useState(50);

  const { data: trades } = useQuery({
    queryKey: ["trades", limit, strategy],
    queryFn: () => fetchTrades(limit, strategy || undefined),
    refetchInterval: 15000,
  });
  const { data: stats } = useQuery({
    queryKey: ["trade-stats"],
    queryFn: fetchTradeStats,
    refetchInterval: 30000,
  });

  const exportCSV = () => {
    if (!trades?.length) return;
    const headers = "Date,Market,Strategy,Side,Price,Size,Edge,PnL,Status\n";
    const rows = trades
      .map(
        (t) =>
          `${t.created_at},${t.question.replace(/,/g, ";")},${t.strategy},${t.side},${t.price},${t.cost_usd},${t.edge},${t.pnl},${t.status}`,
      )
      .join("\n");
    const blob = new Blob([headers + rows], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `trades_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6" data-testid="trades-page">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold">Trade History</h2>
        <button
          onClick={exportCSV}
          className="flex items-center gap-2 px-3 py-1.5 rounded bg-[#1e2130] border border-[#2a2d3e] text-sm text-zinc-300 hover:bg-white/5"
          data-testid="export-csv-btn"
        >
          <Download size={14} /> Export CSV
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Trades"
          value={stats?.total_trades ?? 0}
          testId="total-trades"
          help="Total number of trades executed by the bot since it started running."
        />
        <StatCard
          title="Win Rate"
          value={stats ? `${(stats.win_rate * 100).toFixed(0)}%` : "—"}
          testId="trades-win-rate"
          help="Percentage of completed trades that were profitable. A win rate above 50% with positive edge means the strategy is working."
        />
        <StatCard
          title="Total PnL"
          value={`$${stats?.total_pnl.toFixed(2) ?? "0"}`}
          trend={stats?.total_pnl ? (stats.total_pnl > 0 ? "up" : "down") : "neutral"}
          testId="total-pnl"
          help="Total profit or loss across all completed trades. This is your cumulative realized return."
        />
        <StatCard
          title="Winning"
          value={stats?.winning_trades ?? 0}
          testId="winning-trades"
          help="Number of trades that closed with a profit."
        />
      </div>

      {/* Filters */}
      <div className="flex gap-3">
        <select
          value={strategy}
          onChange={(e) => setStrategy(e.target.value)}
          className="bg-[#1e2130] border border-[#2a2d3e] rounded px-3 py-1.5 text-sm text-zinc-300"
          data-testid="strategy-filter"
        >
          <option value="">All strategies</option>
          <option value="time_decay">Time Decay</option>
          <option value="arbitrage">Arbitrage</option>
          <option value="value_betting">Value Betting</option>
          <option value="market_making">Market Making</option>
        </select>
        <select
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
          className="bg-[#1e2130] border border-[#2a2d3e] rounded px-3 py-1.5 text-sm text-zinc-300"
          data-testid="limit-filter"
        >
          <option value={25}>25 trades</option>
          <option value={50}>50 trades</option>
          <option value={100}>100 trades</option>
        </select>
      </div>

      {/* Trade Table */}
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4">
        <TradeTable trades={trades ?? []} />
      </div>
    </div>
  );
}
