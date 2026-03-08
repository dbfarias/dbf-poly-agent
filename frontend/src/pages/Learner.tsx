import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { clsx } from "clsx";
import { formatDateTime } from "../utils/date";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  fetchLearnerCalibration,
  fetchLearnerMultipliers,
  fetchLearnerPauses,
  unpauseStrategy,
} from "../api/client";
import HelpTooltip from "../components/HelpTooltip";

const STATUS_COLORS: Record<string, string> = {
  relaxed: "text-green-400 bg-green-500/20",
  normal: "text-blue-400 bg-blue-500/20",
  cautious: "text-yellow-400 bg-yellow-500/20",
  strict: "text-red-400 bg-red-500/20",
  boosted: "text-green-400 bg-green-500/20",
  neutral: "text-blue-400 bg-blue-500/20",
  penalized: "text-red-400 bg-red-500/20",
};

const STRATEGY_LABELS: Record<string, string> = {
  time_decay: "Time Decay",
  arbitrage: "Arbitrage",
  value_betting: "Value Betting",
  market_making: "Market Making",
};

export default function Learner() {
  const queryClient = useQueryClient();

  const { data: multipliers, isLoading: loadingMult } = useQuery({
    queryKey: ["learner-multipliers"],
    queryFn: fetchLearnerMultipliers,
    refetchInterval: 30000,
  });

  const { data: calibration, isLoading: loadingCal } = useQuery({
    queryKey: ["learner-calibration"],
    queryFn: fetchLearnerCalibration,
    refetchInterval: 30000,
  });

  const { data: pauses, isLoading: loadingPause } = useQuery({
    queryKey: ["learner-pauses"],
    queryFn: fetchLearnerPauses,
    refetchInterval: 10000,
  });

  const isLoading = loadingMult || loadingCal || loadingPause;

  if (isLoading) {
    return <div className="text-zinc-500">Loading learner data...</div>;
  }

  const lastComputed = multipliers?.last_computed
    ? formatDateTime(multipliers.last_computed)
    : "Not yet computed";

  return (
    <div className="space-y-6" data-testid="learner-page">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center">
            Adaptive Learning
            <HelpTooltip text="The learner analyzes trade history every 5 minutes and adjusts strategy parameters in real time. It computes edge multipliers, category confidences, and calibration data." />
          </h2>
          <p className="text-xs text-zinc-500 mt-1">
            Last computed: {lastComputed}
          </p>
        </div>
        {multipliers && multipliers.paused_strategies.length > 0 && (
          <span className="px-3 py-1 rounded-full text-xs bg-red-500/20 text-red-400 font-medium">
            {multipliers.paused_strategies.length} strategy paused
          </span>
        )}
      </div>

      {/* Strategy Pause Status */}
      <PausePanel pauses={pauses} onUnpause={() => queryClient.invalidateQueries({ queryKey: ["learner-pauses"] })} />

      {/* Edge Multipliers */}
      <MultipliersPanel multipliers={multipliers} />

      {/* Category Confidences */}
      <CategoryPanel multipliers={multipliers} />

      {/* Brier Scores */}
      <BrierScoresPanel multipliers={multipliers} />

      {/* Calibration Chart */}
      <CalibrationPanel calibration={calibration} />
    </div>
  );
}

/* ─── Pause Panel ────────────────────────────────────────────── */

function PausePanel({ pauses, onUnpause }: {
  pauses: ReturnType<typeof fetchLearnerPauses> extends Promise<infer T> ? T | undefined : never;
  onUnpause: () => void;
}) {
  const [loadingStrategy, setLoadingStrategy] = useState<string | null>(null);

  if (!pauses) return null;

  const handleUnpause = async (strategy: string) => {
    setLoadingStrategy(strategy);
    try {
      await unpauseStrategy(strategy);
      onUnpause();
    } catch {
      // Error is shown via the API interceptor
    } finally {
      setLoadingStrategy(null);
    }
  };

  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
      <h3 className="text-sm font-medium text-white mb-4 flex items-center">
        Strategy Status
        <HelpTooltip text="Shows if any strategy has been auto-paused by the learner. A strategy is paused when its last 10 trades have less than 30% win rate and total PnL below -$1. It resumes after 24h cooldown." />
      </h3>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {pauses.strategies.map((s) => (
          <div
            key={s.strategy}
            className={clsx(
              "rounded-lg border p-3 text-center",
              s.is_admin_disabled
                ? "border-zinc-600/30 bg-zinc-800/30"
                : s.is_paused
                  ? "border-red-500/30 bg-red-500/5"
                  : "border-[#2a2d3e] bg-[#0f1117]",
            )}
          >
            <div className="text-sm font-medium text-white truncate">
              {STRATEGY_LABELS[s.strategy] || s.strategy}
            </div>
            {s.is_admin_disabled ? (
              <div className="text-xs text-zinc-500 mt-1 font-medium">
                DISABLED
              </div>
            ) : s.is_paused ? (
              <>
                <div className="text-xs text-red-400 mt-1 font-medium">
                  PAUSED
                </div>
                {s.pause_info && (
                  <div className="text-xs text-zinc-500 mt-1">
                    {s.pause_info.remaining_hours.toFixed(1)}h remaining
                  </div>
                )}
                <button
                  onClick={() => handleUnpause(s.strategy)}
                  disabled={loadingStrategy === s.strategy}
                  className="mt-2 px-3 py-1 rounded text-xs font-medium bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {loadingStrategy === s.strategy ? "Unpausing..." : "Unpause"}
                </button>
              </>
            ) : (
              <div className="text-xs text-green-400 mt-1">Active</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─── Edge Multipliers Panel ─────────────────────────────────── */

function MultipliersPanel({ multipliers }: { multipliers: ReturnType<typeof fetchLearnerMultipliers> extends Promise<infer T> ? T | undefined : never }) {
  if (!multipliers) return null;

  const edgeList = multipliers.edge_multipliers;

  if (edgeList.length === 0) {
    return (
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
        <h3 className="text-sm font-medium text-white mb-3 flex items-center">
          Edge Multipliers
          <HelpTooltip text="Edge multipliers adjust the minimum required edge per strategy+category. A multiplier below 1.0 means the strategy is performing well and requirements are relaxed. Above 1.0 means stricter requirements." />
        </h3>
        <p className="text-sm text-zinc-500 text-center py-4">
          No trade data yet. Multipliers will appear after the first trades.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
      <h3 className="text-sm font-medium text-white mb-4 flex items-center">
        Edge Multipliers by Strategy + Category
        <HelpTooltip text="Edge multipliers adjust the minimum required edge per strategy+category. Below 1.0x = relaxed (winning). 1.0x = normal. Above 1.0x = strict (losing or cautious)." />
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-zinc-500 text-xs border-b border-[#2a2d3e]">
              <th className="text-left py-2 pr-3">Strategy</th>
              <th className="text-left py-2 pr-3 hidden sm:table-cell">Category</th>
              <th className="text-center py-2 pr-3">Mult.</th>
              <th className="text-center py-2 pr-3 hidden sm:table-cell">Win Rate</th>
              <th className="text-center py-2 pr-3 hidden md:table-cell">Trades</th>
              <th className="text-right py-2 pr-3">PnL</th>
              <th className="text-center py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {edgeList.map((em, i) => (
              <tr key={i} className="border-b border-[#2a2d3e]/50">
                <td className="py-2 pr-3 text-white text-xs sm:text-sm">
                  {STRATEGY_LABELS[em.strategy] || em.strategy}
                </td>
                <td className="py-2 pr-3 text-zinc-400 capitalize hidden sm:table-cell">
                  {em.category}
                </td>
                <td className="py-2 pr-3 text-center">
                  <span
                    className={clsx(
                      "font-mono font-bold",
                      em.multiplier < 1.0
                        ? "text-green-400"
                        : em.multiplier <= 1.0
                          ? "text-blue-400"
                          : em.multiplier <= 1.2
                            ? "text-yellow-400"
                            : "text-red-400",
                    )}
                  >
                    {em.multiplier.toFixed(2)}x
                  </span>
                </td>
                <td className="py-2 pr-3 text-center text-white hidden sm:table-cell">
                  {em.win_rate !== null
                    ? `${(em.win_rate * 100).toFixed(0)}%`
                    : "—"}
                </td>
                <td className="py-2 pr-3 text-center text-zinc-400 hidden md:table-cell">
                  {em.total_trades}
                </td>
                <td
                  className={clsx(
                    "py-2 pr-3 text-right font-mono",
                    em.total_pnl >= 0 ? "text-green-400" : "text-red-400",
                  )}
                >
                  ${em.total_pnl.toFixed(4)}
                </td>
                <td className="py-2 text-center">
                  <span
                    className={clsx(
                      "px-2 py-0.5 rounded text-xs",
                      STATUS_COLORS[em.status] || "text-zinc-400 bg-zinc-700/50",
                    )}
                  >
                    {em.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─── Category Confidences Panel ─────────────────────────────── */

function CategoryPanel({ multipliers }: { multipliers: ReturnType<typeof fetchLearnerMultipliers> extends Promise<infer T> ? T | undefined : never }) {
  if (!multipliers) return null;

  const catList = multipliers.category_confidences;

  if (catList.length === 0) {
    return (
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
        <h3 className="text-sm font-medium text-white mb-3 flex items-center">
          Category Confidence
          <HelpTooltip text="Category confidence shows how well the bot performs in each market category. Higher confidence means more exposure is allowed." />
        </h3>
        <p className="text-sm text-zinc-500 text-center py-4">
          No category data yet.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
      <h3 className="text-sm font-medium text-white mb-4 flex items-center">
        Category Confidence
        <HelpTooltip text="Shows how well the bot performs per market category. Boosted categories get more exposure. Penalized categories are avoided. Confidence adjusts after 10+ trades." />
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {catList.map((cat) => (
          <div
            key={cat.category}
            className="rounded-lg border border-[#2a2d3e] bg-[#0f1117] p-3"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-white capitalize">
                {cat.category}
              </span>
              <span
                className={clsx(
                  "px-2 py-0.5 rounded text-xs",
                  STATUS_COLORS[cat.status] || "text-zinc-400 bg-zinc-700/50",
                )}
              >
                {cat.status}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-1 sm:gap-2 text-center">
              <div>
                <div
                  className={clsx(
                    "text-base sm:text-lg font-bold font-mono",
                    cat.confidence >= 1.0 ? "text-green-400" : "text-yellow-400",
                  )}
                >
                  {cat.confidence.toFixed(1)}x
                </div>
                <div className="text-[10px] sm:text-xs text-zinc-500">Conf</div>
              </div>
              <div>
                <div className="text-base sm:text-lg font-bold text-white">
                  {(cat.win_rate * 100).toFixed(0)}%
                </div>
                <div className="text-[10px] sm:text-xs text-zinc-500">Win</div>
              </div>
              <div>
                <div
                  className={clsx(
                    "text-base sm:text-lg font-bold",
                    cat.total_pnl >= 0 ? "text-green-400" : "text-red-400",
                  )}
                >
                  ${cat.total_pnl.toFixed(2)}
                </div>
                <div className="text-[10px] sm:text-xs text-zinc-500">PnL</div>
              </div>
            </div>
            <div className="mt-2 text-xs text-zinc-500 text-center">
              {cat.total_trades} trades
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─── Brier Scores Panel ────────────────────────────────────── */

function BrierScoresPanel({ multipliers }: { multipliers: ReturnType<typeof fetchLearnerMultipliers> extends Promise<infer T> ? T | undefined : never }) {
  if (!multipliers) return null;

  const brierScores = multipliers.brier_scores ?? {};
  const entries = Object.entries(brierScores);

  if (entries.length === 0) return null;

  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
      <h3 className="text-sm font-medium text-white mb-4 flex items-center">
        Brier Scores by Strategy
        <HelpTooltip text="Brier score measures prediction accuracy (0 = perfect, 0.25 = random). Lower is better. A score below 0.15 indicates good calibration." />
      </h3>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {entries.map(([strategy, score]) => (
          <div
            key={strategy}
            className="rounded-lg border border-[#2a2d3e] bg-[#0f1117] p-3 text-center"
          >
            <div className="text-sm font-medium text-white mb-1 capitalize">
              {STRATEGY_LABELS[strategy] || strategy}
            </div>
            <div
              className={clsx(
                "text-2xl font-bold font-mono",
                score <= 0.15
                  ? "text-green-400"
                  : score <= 0.25
                    ? "text-yellow-400"
                    : "text-red-400",
              )}
            >
              {score.toFixed(3)}
            </div>
            <div className="text-[10px] text-zinc-500 mt-1">
              {score <= 0.15 ? "Good" : score <= 0.25 ? "Fair" : "Poor"}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─── Calibration Chart Panel ────────────────────────────────── */

function CalibrationPanel({ calibration }: { calibration: ReturnType<typeof fetchLearnerCalibration> extends Promise<infer T> ? T | undefined : never }) {
  if (!calibration) return null;

  const buckets = calibration.buckets;

  // Prepare chart data
  const chartData = buckets.map((b) => ({
    name: `${b.bucket}%`,
    estimated: b.estimated_prob,
    actual: b.actual_win_rate,
    trades: b.total_trades,
  }));

  const hasData = buckets.some((b) => b.total_trades > 0);

  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
      <h3 className="text-sm font-medium text-white mb-4 flex items-center">
        Probability Calibration
        <HelpTooltip text="Compares the bot's estimated probability vs actual win rate per bucket. If the bars match, the bot is well-calibrated. If actual is lower than estimated, the bot is overconfident." />
      </h3>

      {!hasData ? (
        <p className="text-sm text-zinc-500 text-center py-4">
          Not enough trades for calibration data. Need 5+ trades per bucket.
        </p>
      ) : (
        <>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={chartData} barGap={4}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
              <XAxis
                dataKey="name"
                tick={{ fill: "#71717a", fontSize: 12 }}
                axisLine={{ stroke: "#2a2d3e" }}
              />
              <YAxis
                tick={{ fill: "#71717a", fontSize: 12 }}
                axisLine={{ stroke: "#2a2d3e" }}
                domain={[0, 100]}
                tickFormatter={(v: number) => `${v}%`}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#1e2130",
                  border: "1px solid #2a2d3e",
                  borderRadius: "8px",
                  fontSize: "12px",
                }}
                labelStyle={{ color: "#fff" }}
                formatter={(value: number, name: string) => [
                  `${value.toFixed(1)}%`,
                  name === "estimated" ? "Estimated Prob" : "Actual Win Rate",
                ]}
              />
              <Legend
                wrapperStyle={{ fontSize: "12px", color: "#a1a1aa" }}
                formatter={(value: string) =>
                  value === "estimated" ? "Estimated Prob" : "Actual Win Rate"
                }
              />
              <Bar dataKey="estimated" fill="#6366f1" radius={[4, 4, 0, 0]} />
              <Bar dataKey="actual" radius={[4, 4, 0, 0]}>
                {chartData.map((entry, idx) => (
                  <Cell
                    key={idx}
                    fill={
                      entry.actual >= entry.estimated * 0.8
                        ? "#22c55e"
                        : "#ef4444"
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>

          {/* Bucket details table */}
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-zinc-500 text-xs border-b border-[#2a2d3e]">
                  <th className="text-left py-2">Bucket</th>
                  <th className="text-center py-2">Est.</th>
                  <th className="text-center py-2">Actual</th>
                  <th className="text-center py-2 hidden sm:table-cell">Trades</th>
                  <th className="text-center py-2 hidden sm:table-cell">W/L</th>
                  <th className="text-center py-2">Cal.?</th>
                </tr>
              </thead>
              <tbody>
                {buckets.map((b) => (
                  <tr key={b.bucket} className="border-b border-[#2a2d3e]/50">
                    <td className="py-2 text-white">{b.bucket}%</td>
                    <td className="py-2 text-center text-indigo-400 font-mono">
                      {b.estimated_prob.toFixed(1)}%
                    </td>
                    <td
                      className={clsx(
                        "py-2 text-center font-mono",
                        b.actual_win_rate >= b.estimated_prob * 0.8
                          ? "text-green-400"
                          : "text-red-400",
                      )}
                    >
                      {b.actual_win_rate.toFixed(1)}%
                    </td>
                    <td className="py-2 text-center text-zinc-400 hidden sm:table-cell">
                      {b.total_trades}
                    </td>
                    <td className="py-2 text-center hidden sm:table-cell">
                      <span className="text-green-400">{b.wins}</span>
                      <span className="text-zinc-600"> / </span>
                      <span className="text-red-400">{b.losses}</span>
                    </td>
                    <td className="py-2 text-center">
                      {b.total_trades < 5 ? (
                        <span className="text-zinc-500 text-xs">
                          Need 5+ trades
                        </span>
                      ) : b.is_calibrated ? (
                        <span className="text-green-400 text-xs">Yes</span>
                      ) : (
                        <span className="text-red-400 text-xs">No</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
