import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchTrades } from "../api/client";

export default function WinLossChart() {
  const { data: trades, isLoading } = useQuery({
    queryKey: ["trades-for-chart"],
    queryFn: () => fetchTrades(100),
    refetchInterval: 15000,
  });

  if (isLoading) {
    return (
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4 h-64 flex items-center justify-center text-zinc-500">
        Loading...
      </div>
    );
  }

  // Filter to closed trades with non-zero PnL
  const closedTrades = (trades ?? []).filter(
    (t) => t.status === "filled" && t.pnl !== 0
  );

  if (closedTrades.length === 0) {
    return (
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4 h-64 flex items-center justify-center text-zinc-500">
        No closed trades yet
      </div>
    );
  }

  // Per-trade bars sorted by date (oldest first)
  const chartData = closedTrades
    .slice()
    .reverse()
    .map((t, i) => ({
      name: `#${t.id}`,
      pnl: Number(t.pnl.toFixed(2)),
      index: i,
    }));

  // Summary stats
  const wins = closedTrades.filter((t) => t.pnl > 0).length;
  const losses = closedTrades.filter((t) => t.pnl < 0).length;
  const totalPnl = closedTrades.reduce((sum, t) => sum + t.pnl, 0);

  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4" data-testid="winloss-chart">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-zinc-400">Trade P&amp;L</h3>
        <div className="flex items-center gap-3 text-xs">
          <span className="text-green-400">{wins}W</span>
          <span className="text-red-400">{losses}L</span>
          <span className={totalPnl >= 0 ? "text-green-400" : "text-red-400"}>
            ${totalPnl.toFixed(2)}
          </span>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={chartData}>
          <XAxis dataKey="name" tick={{ fill: "#71717a", fontSize: 10 }} />
          <YAxis tick={{ fill: "#71717a", fontSize: 11 }} />
          <Tooltip
            contentStyle={{
              background: "#1e2130",
              border: "1px solid #2a2d3e",
              borderRadius: 8,
              color: "#e4e4e7",
            }}
            formatter={(value: number) => [`$${value.toFixed(2)}`, "P&L"]}
          />
          <Legend
            payload={[
              { value: "Win", type: "rect", color: "#22c55e" },
              { value: "Loss", type: "rect", color: "#ef4444" },
            ]}
            wrapperStyle={{ fontSize: 12 }}
          />
          <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
            {chartData.map((entry) => (
              <Cell
                key={entry.index}
                fill={entry.pnl >= 0 ? "#22c55e" : "#ef4444"}
                fillOpacity={0.8}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
