import { useQuery } from "@tanstack/react-query";
import { CheckCircle, XCircle } from "lucide-react";
import { fetchDailyPnl } from "../api/client";
import { ChartSkeleton } from "./Skeleton";

/** Format "2026-03-01" → "01/03" in dd/mm. */
function fmtDate(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${d}/${m}`;
}

/** Format "2026-03-01" → "Sáb" weekday abbreviation in BRT. */
function weekday(iso: string): string {
  const d = new Date(iso + "T12:00:00Z");
  return d.toLocaleDateString("pt-BR", {
    weekday: "short",
    timeZone: "America/Sao_Paulo",
  });
}

export default function DailyTargetChart() {
  const { data, isLoading } = useQuery({
    queryKey: ["daily-pnl"],
    queryFn: fetchDailyPnl,
    refetchInterval: 60000,
  });

  if (isLoading) {
    return <ChartSkeleton title="Daily Target Tracker" />;
  }

  if (!data?.length) {
    return (
      <div
        className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4 h-64 flex items-center justify-center text-zinc-500"
        data-testid="daily-target-chart-empty"
      >
        No daily data yet
      </div>
    );
  }

  const daysHit = data.filter((d) => d.hit_target).length;
  const hitRate = data.length > 0 ? (daysHit / data.length) * 100 : 0;
  const streak = computeStreak(data);

  return (
    <div
      className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4"
      data-testid="daily-target-chart"
    >
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-medium text-zinc-400">
          Daily Target Tracker
        </h3>
        <div className="flex items-center gap-3 text-xs text-zinc-500">
          <span className={hitRate >= 50 ? "text-green-400" : "text-yellow-400"}>
            {hitRate.toFixed(0)}% hit rate
          </span>
          {streak > 0 && (
            <span className="text-green-400">
              {streak} day streak
            </span>
          )}
        </div>
      </div>

      {/* Day tiles */}
      <div className="grid grid-cols-4 sm:grid-cols-7 gap-2">
        {data.map((d) => (
          <div
            key={d.date}
            className={`rounded-lg p-2 text-center border ${
              d.hit_target
                ? "bg-green-500/10 border-green-500/30"
                : d.pnl >= 0
                  ? "bg-yellow-500/10 border-yellow-500/30"
                  : "bg-red-500/10 border-red-500/30"
            }`}
          >
            <div className="text-[10px] text-zinc-500">{weekday(d.date)}</div>
            <div className="text-xs font-medium text-zinc-300">
              {fmtDate(d.date)}
            </div>
            <div className="flex justify-center my-1">
              {d.hit_target ? (
                <CheckCircle size={16} className="text-green-400" />
              ) : (
                <XCircle
                  size={16}
                  className={d.pnl >= 0 ? "text-yellow-400" : "text-red-400"}
                />
              )}
            </div>
            <div
              className={`text-xs font-medium ${
                d.pnl >= 0 ? "text-green-400" : "text-red-400"
              }`}
            >
              {d.pnl >= 0 ? "+" : ""}${d.pnl.toFixed(2)}
            </div>
            <div className="text-[10px] text-zinc-600">
              {d.pnl_pct >= 0 ? "+" : ""}{d.pnl_pct}%
            </div>
          </div>
        ))}
      </div>

      {/* Summary row */}
      <div className="flex justify-between mt-3 pt-3 border-t border-[#2a2d3e] text-xs text-zinc-500">
        <span>
          {daysHit} of {data.length} days hit target
        </span>
        <span>
          Avg daily: ${(data.reduce((s, d) => s + d.pnl, 0) / data.length).toFixed(2)}
        </span>
        <span>
          Best: ${Math.max(...data.map((d) => d.pnl)).toFixed(2)}
        </span>
      </div>
    </div>
  );
}

/** Count consecutive target-hit days from the end. */
function computeStreak(
  data: { hit_target: boolean }[],
): number {
  let streak = 0;
  for (let i = data.length - 1; i >= 0; i--) {
    if (data[i].hit_target) {
      streak++;
    } else {
      break;
    }
  }
  return streak;
}
