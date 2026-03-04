import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchDailyPnl } from "../api/client";
import { ChartSkeleton } from "./Skeleton";

/** Format "2026-03-01" → "01/03" in BRT-friendly dd/mm. */
function fmtDate(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
}

function barColor(pnl: number, hitTarget: boolean): string {
  if (pnl < 0) return "#ef4444"; // red
  if (hitTarget) return "#22c55e"; // green
  return "#eab308"; // yellow — positive but missed target
}

export default function DailyPnlChart() {
  const { data, isLoading } = useQuery({
    queryKey: ["daily-pnl"],
    queryFn: fetchDailyPnl,
    refetchInterval: 60000,
  });

  if (isLoading) {
    return <ChartSkeleton title="Daily P&L" />;
  }

  if (!data?.length) {
    return (
      <div
        className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4 h-64 flex items-center justify-center text-zinc-500"
        data-testid="daily-pnl-chart-empty"
      >
        No daily data yet
      </div>
    );
  }

  const chartData = data.map((d) => ({
    date: fmtDate(d.date),
    pnl: Number(d.pnl.toFixed(2)),
    pnl_pct: d.pnl_pct,
    target: Number(d.target.toFixed(2)),
    hit: d.hit_target,
    startEq: d.start_equity,
    endEq: d.end_equity,
  }));

  const daysHit = data.filter((d) => d.hit_target).length;
  const totalPnl = data.reduce((s, d) => s + d.pnl, 0);

  return (
    <div
      className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4"
      data-testid="daily-pnl-chart"
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-zinc-400">
          Daily P&L
        </h3>
        <div className="flex items-center gap-3 text-xs text-zinc-500">
          <span className={totalPnl >= 0 ? "text-green-400" : "text-red-400"}>
            Total: ${totalPnl.toFixed(2)}
          </span>
          <span>
            {daysHit}/{data.length} days on target
          </span>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <BarChart data={chartData}>
          <XAxis
            dataKey="date"
            tick={{ fill: "#71717a", fontSize: 11 }}
          />
          <YAxis
            tick={{ fill: "#71717a", fontSize: 11 }}
            tickFormatter={(v: number) => `$${v.toFixed(0)}`}
          />
          <Tooltip
            contentStyle={{
              background: "#1e2130",
              border: "1px solid #2a2d3e",
              borderRadius: 8,
              color: "#e4e4e7",
              fontSize: 12,
            }}
            formatter={(value: number, name: string) => {
              if (name === "pnl") return [`$${value.toFixed(2)}`, "P&L"];
              return [value, name];
            }}
            labelFormatter={(label: string) => `Day: ${label}`}
            content={({ active, payload, label }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0].payload;
              return (
                <div className="bg-[#1e2130] border border-[#2a2d3e] rounded-lg p-3 text-xs">
                  <div className="font-medium text-zinc-300 mb-1">{label}</div>
                  <div className={d.pnl >= 0 ? "text-green-400" : "text-red-400"}>
                    P&L: ${d.pnl.toFixed(2)} ({d.pnl_pct > 0 ? "+" : ""}{d.pnl_pct}%)
                  </div>
                  <div className="text-zinc-500">Target: ${d.target.toFixed(2)}</div>
                  <div className="text-zinc-500">
                    ${d.startEq.toFixed(2)} → ${d.endEq.toFixed(2)}
                  </div>
                  <div className={d.hit ? "text-green-400" : "text-yellow-400"}>
                    {d.hit ? "Target hit!" : "Below target"}
                  </div>
                </div>
              );
            }}
          />
          <ReferenceLine y={0} stroke="#3f3f46" strokeDasharray="3 3" />
          <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
            {chartData.map((entry, i) => (
              <Cell key={i} fill={barColor(entry.pnl, entry.hit)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      {/* Legend */}
      <div className="flex justify-center gap-4 mt-2 text-xs text-zinc-500">
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-sm bg-green-500" /> Hit target
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-sm bg-yellow-500" /> Positive
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-sm bg-red-500" /> Negative
        </span>
      </div>
    </div>
  );
}
