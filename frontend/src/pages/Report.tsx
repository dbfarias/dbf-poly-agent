import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import {
  fetchDailyReport,
  type DailyReport,
  type ReportAlert,
  type ReportMarket,
  type ReportStrategy,
} from "../api/client";
import HelpTooltip from "../components/HelpTooltip";
import { formatDateTime, formatRelative } from "../utils/date";

function sentimentColor(score: number): string {
  if (score > 0.3) return "text-green-400";
  if (score < -0.3) return "text-red-400";
  return "text-zinc-400";
}

function pnlColor(value: number): string {
  if (value > 0) return "text-green-400";
  if (value < 0) return "text-red-400";
  return "text-zinc-400";
}

function severityStyles(severity: string): string {
  if (severity === "danger") return "bg-red-500/10 border-red-500/30 text-red-400";
  return "bg-amber-500/10 border-amber-500/30 text-amber-400";
}

function severityIcon(severity: string): string {
  return severity === "danger" ? "!!!" : "!";
}

/* --- Loading Skeleton --- */
function Skeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <div
            key={i}
            className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4 h-20"
          >
            <div className="h-4 bg-[#2a2d3e] rounded w-16 mx-auto mb-2" />
            <div className="h-3 bg-[#2a2d3e] rounded w-20 mx-auto" />
          </div>
        ))}
      </div>
      {/* Table */}
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
        <div className="h-5 bg-[#2a2d3e] rounded w-40 mb-4" />
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-8 bg-[#2a2d3e]/50 rounded mb-2" />
        ))}
      </div>
      {/* Strategy + Alerts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5 h-48" />
        <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5 h-48" />
      </div>
    </div>
  );
}

/* --- Portfolio Summary Cards --- */
function PortfolioSummary({ data }: { data: DailyReport["portfolio_summary"] }) {
  const cards = [
    {
      label: "Total Equity",
      value: `$${data.total_equity.toFixed(2)}`,
      color: "text-white",
    },
    {
      label: "Daily PnL",
      value: `${data.daily_pnl >= 0 ? "+" : ""}$${data.daily_pnl.toFixed(2)}`,
      sub: `${data.daily_return_pct >= 0 ? "+" : ""}${data.daily_return_pct.toFixed(1)}%`,
      color: pnlColor(data.daily_pnl),
    },
    {
      label: "Cash / Positions",
      value: `$${data.cash_balance.toFixed(2)}`,
      sub: `$${data.positions_value.toFixed(2)} deployed`,
      color: "text-white",
    },
    {
      label: "Open Positions",
      value: String(data.open_positions),
      sub: `Mode: ${data.trading_mode}`,
      color: "text-white",
    },
    {
      label: "Daily Target",
      value: `${data.daily_progress_pct.toFixed(0)}%`,
      sub: `Target: ${data.daily_target_pct}%/day`,
      color: data.daily_progress_pct >= 100 ? "text-green-400" : "text-amber-400",
    },
  ];

  return (
    <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
      {cards.map((c) => (
        <div
          key={c.label}
          className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4 text-center"
        >
          <div className={clsx("text-lg font-bold font-mono", c.color)}>
            {c.value}
          </div>
          {c.sub && (
            <div className="text-[11px] text-zinc-500 mt-0.5">{c.sub}</div>
          )}
          <div className="text-xs text-zinc-500 mt-1">{c.label}</div>
        </div>
      ))}
    </div>
  );
}

/* --- Top Opportunities Table --- */
function TopOpportunities({ markets }: { markets: ReportMarket[] }) {
  if (markets.length === 0) {
    return (
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
        <h3 className="text-sm font-medium text-white mb-3 flex items-center">
          Top Opportunities
          <HelpTooltip text="Markets ranked by absolute sentiment strength. Higher sentiment confidence = stronger signal for the trading engine." />
        </h3>
        <p className="text-sm text-zinc-500 text-center py-4">
          No research data available yet. The engine will scan markets shortly.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
      <h3 className="text-sm font-medium text-white mb-4 flex items-center">
        Top Opportunities
        <HelpTooltip text="Markets ranked by absolute sentiment strength. Higher sentiment confidence = stronger signal for the trading engine." />
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-zinc-500 text-xs border-b border-[#2a2d3e]">
              <th className="text-left py-2 pr-3">Market</th>
              <th className="text-center py-2 pr-3 hidden sm:table-cell">Category</th>
              <th className="text-center py-2 pr-3">Sentiment</th>
              <th className="text-center py-2 pr-3 hidden md:table-cell">Confidence</th>
              <th className="text-center py-2 pr-3">Multiplier</th>
              <th className="text-center py-2 pr-3 hidden md:table-cell">Articles</th>
              <th className="text-center py-2 pr-3 hidden lg:table-cell">Flags</th>
              <th className="text-right py-2 hidden sm:table-cell">End Date</th>
            </tr>
          </thead>
          <tbody>
            {markets.map((m) => (
              <tr
                key={m.market_id}
                className="border-b border-[#2a2d3e]/50 hover:bg-white/[0.02]"
              >
                <td className="py-2.5 pr-3 text-white max-w-[300px]">
                  <div className="truncate" title={m.question}>
                    {m.question}
                  </div>
                </td>
                <td className="py-2.5 pr-3 text-center hidden sm:table-cell">
                  {m.category ? (
                    <span className="px-2 py-0.5 rounded text-xs bg-indigo-900/30 text-indigo-400 capitalize">
                      {m.category}
                    </span>
                  ) : (
                    <span className="text-zinc-600 text-xs">-</span>
                  )}
                </td>
                <td className="py-2.5 pr-3 text-center">
                  <span
                    className={clsx(
                      "font-mono font-bold",
                      sentimentColor(m.sentiment_score),
                    )}
                  >
                    {m.sentiment_score >= 0 ? "+" : ""}
                    {m.sentiment_score.toFixed(3)}
                  </span>
                </td>
                <td className="py-2.5 pr-3 text-center hidden md:table-cell">
                  <span
                    className={clsx(
                      "font-mono",
                      m.confidence >= 0.5 ? "text-green-400" : "text-zinc-500",
                    )}
                  >
                    {(m.confidence * 100).toFixed(0)}%
                  </span>
                </td>
                <td className="py-2.5 pr-3 text-center">
                  <span
                    className={clsx(
                      "font-mono font-bold",
                      m.research_multiplier < 0.95
                        ? "text-green-400"
                        : m.research_multiplier > 1.05
                          ? "text-red-400"
                          : "text-blue-400",
                    )}
                  >
                    {m.research_multiplier.toFixed(3)}x
                  </span>
                </td>
                <td className="py-2.5 pr-3 text-center text-zinc-400 hidden md:table-cell">
                  {m.article_count}
                </td>
                <td className="py-2.5 pr-3 text-center hidden lg:table-cell">
                  <div className="flex items-center justify-center gap-1">
                    {m.is_volume_anomaly && (
                      <span
                        className="px-1.5 py-0.5 rounded text-[10px] bg-amber-900/30 text-amber-400"
                        title="Volume anomaly detected"
                      >
                        VOL
                      </span>
                    )}
                    {m.whale_activity && (
                      <span
                        className="px-1.5 py-0.5 rounded text-[10px] bg-purple-900/30 text-purple-400"
                        title="Whale activity detected"
                      >
                        WHALE
                      </span>
                    )}
                    {!m.is_volume_anomaly && !m.whale_activity && (
                      <span className="text-zinc-600 text-xs">-</span>
                    )}
                  </div>
                </td>
                <td className="py-2.5 text-right text-zinc-500 text-xs hidden sm:table-cell">
                  {m.end_date ? formatDateTime(m.end_date) : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* --- Strategy Health --- */
function StrategyHealth({ strategies }: { strategies: ReportStrategy[] }) {
  if (strategies.length === 0) {
    return (
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
        <h3 className="text-sm font-medium text-white mb-3">Strategy Health</h3>
        <p className="text-sm text-zinc-500 text-center py-4">
          No strategy data yet.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
      <h3 className="text-sm font-medium text-white mb-4 flex items-center">
        Strategy Health
        <HelpTooltip text="Aggregated performance per strategy. The learner auto-pauses strategies with low win rates." />
      </h3>
      <div className="space-y-2">
        {strategies.map((s) => (
          <div
            key={s.name}
            className="flex items-center justify-between py-2 px-3 rounded bg-[#0f1117]/50"
          >
            <div className="flex items-center gap-2">
              <span className="text-sm text-white font-medium capitalize">
                {s.name.replace(/_/g, " ")}
              </span>
              {s.is_paused && (
                <span className="px-1.5 py-0.5 rounded text-[10px] bg-red-500/20 text-red-400 font-medium">
                  PAUSED
                </span>
              )}
            </div>
            <div className="flex items-center gap-4 text-xs">
              <span className="text-zinc-500">
                {s.total_trades} trades
              </span>
              <span
                className={clsx(
                  "font-mono",
                  s.win_rate >= 50 ? "text-green-400" : "text-red-400",
                )}
              >
                WR {s.win_rate.toFixed(0)}%
              </span>
              <span className={clsx("font-mono font-bold", pnlColor(s.total_pnl))}>
                ${s.total_pnl >= 0 ? "+" : ""}
                {s.total_pnl.toFixed(2)}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* --- Risk Alerts --- */
function RiskAlerts({ alerts }: { alerts: ReportAlert[] }) {
  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
      <h3 className="text-sm font-medium text-white mb-4 flex items-center">
        Risk Alerts
        <HelpTooltip text="Active risk warnings: category concentration, daily loss threshold, drawdown, and stuck positions." />
      </h3>
      {alerts.length === 0 ? (
        <div className="text-center py-4">
          <span className="px-3 py-1.5 rounded-full text-xs font-medium bg-green-500/20 text-green-400">
            All Clear
          </span>
          <p className="text-xs text-zinc-500 mt-2">No active risk alerts.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {alerts.map((a, i) => (
            <div
              key={i}
              className={clsx(
                "flex items-start gap-2 px-3 py-2.5 rounded border text-sm",
                severityStyles(a.severity),
              )}
            >
              <span className="font-bold text-xs shrink-0 mt-0.5">
                {severityIcon(a.severity)}
              </span>
              <span>{a.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* --- Market Sentiment Overview --- */
function SentimentOverview({
  data,
}: {
  data: DailyReport["sentiment_overview"];
}) {
  const barPosition = Math.round((data.avg_sentiment + 1) * 50);

  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
      <h3 className="text-sm font-medium text-white mb-4 flex items-center">
        Market Sentiment
        <HelpTooltip text="Overall market mood from the research engine. Positive sentiment lowers edge requirements; negative raises them." />
      </h3>
      {data.total_markets === 0 ? (
        <p className="text-sm text-zinc-500 text-center py-4">
          No sentiment data available yet.
        </p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Sentiment Score + Bar */}
          <div className="text-center">
            <div
              className={clsx(
                "text-3xl font-bold font-mono",
                sentimentColor(data.avg_sentiment),
              )}
            >
              {data.avg_sentiment >= 0 ? "+" : ""}
              {data.avg_sentiment.toFixed(4)}
            </div>
            <div className="text-xs text-zinc-500 mt-1">
              Average Sentiment
            </div>
            <div className="mt-3 h-2 bg-[#0f1117] rounded-full relative">
              <div
                className="absolute top-0 h-2 w-2 rounded-full bg-white"
                style={{ left: `${barPosition}%` }}
              />
              <div className="absolute -bottom-4 left-0 text-[10px] text-red-400">
                -1
              </div>
              <div className="absolute -bottom-4 left-1/2 -translate-x-1/2 text-[10px] text-zinc-500">
                0
              </div>
              <div className="absolute -bottom-4 right-0 text-[10px] text-green-400">
                +1
              </div>
            </div>
          </div>

          {/* Breakdown */}
          <div className="flex items-center justify-center gap-6">
            <div className="text-center">
              <div className="text-xl font-bold text-green-400 font-mono">
                {data.positive}
              </div>
              <div className="text-[11px] text-zinc-500">Positive</div>
            </div>
            <div className="text-center">
              <div className="text-xl font-bold text-zinc-400 font-mono">
                {data.neutral}
              </div>
              <div className="text-[11px] text-zinc-500">Neutral</div>
            </div>
            <div className="text-center">
              <div className="text-xl font-bold text-red-400 font-mono">
                {data.negative}
              </div>
              <div className="text-[11px] text-zinc-500">Negative</div>
            </div>
            <div className="text-center">
              <div className="text-xl font-bold text-white font-mono">
                {data.total_markets}
              </div>
              <div className="text-[11px] text-zinc-500">Total</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* --- Main Report Page --- */
export default function Report() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["daily-report"],
    queryFn: fetchDailyReport,
    refetchInterval: 60000,
  });

  if (isLoading) {
    return <Skeleton />;
  }

  if (error || !data) {
    return (
      <div className="text-center py-12">
        <p className="text-zinc-400 text-sm">
          Failed to load daily report. The engine may still be starting up.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="report-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white">Daily Report</h2>
          <p className="text-xs text-zinc-500 mt-1">
            Generated: {formatRelative(data.generated_at)} ({formatDateTime(data.generated_at)})
          </p>
        </div>
      </div>

      {/* Portfolio Summary */}
      <PortfolioSummary data={data.portfolio_summary} />

      {/* Market Sentiment */}
      <SentimentOverview data={data.sentiment_overview} />

      {/* Top Opportunities */}
      <TopOpportunities markets={data.top_markets} />

      {/* Strategy Health + Risk Alerts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <StrategyHealth strategies={data.strategy_health} />
        <RiskAlerts alerts={data.risk_alerts} />
      </div>
    </div>
  );
}
