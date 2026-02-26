import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fetchEquityCurve } from "../api/client";

export default function EquityChart({ days = 30 }: { days?: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["equity-curve", days],
    queryFn: () => fetchEquityCurve(days),
    refetchInterval: 60000,
  });

  if (isLoading) {
    return (
      <div
        className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4 h-64 flex items-center justify-center text-zinc-500"
        data-testid="equity-chart-loading"
      >
        Loading...
      </div>
    );
  }

  if (!data?.length) {
    return (
      <div
        className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4 h-64 flex items-center justify-center text-zinc-500"
        data-testid="equity-chart-empty"
      >
        No data yet
      </div>
    );
  }

  const chartData = data.map((p) => ({
    time: new Date(p.timestamp).toLocaleDateString(),
    equity: p.total_equity,
    cash: p.cash_balance,
  }));

  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4" data-testid="equity-chart">
      <h3 className="text-sm font-medium text-zinc-400 mb-4" data-testid="equity-chart-title">Equity Curve</h3>
      <ResponsiveContainer width="100%" height={240}>
        <AreaChart data={chartData}>
          <defs>
            <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#6366f1" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis dataKey="time" tick={{ fill: "#71717a", fontSize: 11 }} />
          <YAxis tick={{ fill: "#71717a", fontSize: 11 }} domain={["auto", "auto"]} />
          <Tooltip
            contentStyle={{
              background: "#1e2130",
              border: "1px solid #2a2d3e",
              borderRadius: 8,
              color: "#e4e4e7",
            }}
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke="#6366f1"
            fill="url(#eqGrad)"
            strokeWidth={2}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
