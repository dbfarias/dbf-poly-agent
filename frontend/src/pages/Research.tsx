import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import {
  fetchResearchMarkets,
  fetchResearchStatus,
  type ResearchMarket,
} from "../api/client";
import HelpTooltip from "../components/HelpTooltip";
import { formatDateTime, formatTime } from "../utils/date";

function sentimentColor(score: number): string {
  if (score > 0.3) return "text-green-400";
  if (score < -0.3) return "text-red-400";
  return "text-zinc-400";
}

function sentimentBg(score: number): string {
  if (score > 0.3) return "bg-green-500/20";
  if (score < -0.3) return "bg-red-500/20";
  return "bg-zinc-500/20";
}

function multiplierColor(mult: number): string {
  if (mult < 0.95) return "text-green-400";
  if (mult > 1.05) return "text-red-400";
  return "text-blue-400";
}

function sentimentLabel(score: number): string {
  if (score > 0.5) return "Very Positive";
  if (score > 0.3) return "Positive";
  if (score > 0.1) return "Slightly Positive";
  if (score > -0.1) return "Neutral";
  if (score > -0.3) return "Slightly Negative";
  if (score > -0.5) return "Negative";
  return "Very Negative";
}

export default function Research() {
  const { data: status, isLoading: loadingStatus } = useQuery({
    queryKey: ["research-status"],
    queryFn: fetchResearchStatus,
    refetchInterval: 30000,
  });

  const { data: markets, isLoading: loadingMarkets } = useQuery({
    queryKey: ["research-markets"],
    queryFn: fetchResearchMarkets,
    refetchInterval: 60000,
  });

  const isLoading = loadingStatus || loadingMarkets;

  if (isLoading) {
    return <div className="text-zinc-500">Loading research data...</div>;
  }

  return (
    <div className="space-y-6" data-testid="research-page">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center">
            Research Engine
            <HelpTooltip text="The research engine scans Google News and CoinGecko every 30 minutes, runs VADER sentiment analysis on headlines, and feeds a research_multiplier into the trading edge calculation. Zero API cost." />
          </h2>
          <p className="text-xs text-zinc-500 mt-1">
            Last scan:{" "}
            {status?.last_scan
              ? formatDateTime(status.last_scan)
              : "Not yet scanned"}
          </p>
        </div>
        <StatusBadge status={status} />
      </div>

      {/* Engine Stats */}
      <StatsPanel status={status} marketCount={markets?.length ?? 0} />

      {/* Overall Sentiment Gauge */}
      <SentimentGauge markets={markets ?? []} />

      {/* Per-Market Sentiment Table */}
      <MarketSentimentTable markets={markets ?? []} />
    </div>
  );
}

/* --- Status Badge --- */
function StatusBadge({
  status,
}: {
  status: ReturnType<typeof fetchResearchStatus> extends Promise<infer T>
    ? T | undefined
    : never;
}) {
  if (!status) return null;
  return (
    <span
      className={clsx(
        "px-3 py-1 rounded-full text-xs font-medium",
        status.running
          ? "bg-green-500/20 text-green-400"
          : "bg-zinc-500/20 text-zinc-400",
      )}
    >
      {status.running ? "Running" : "Stopped"}
    </span>
  );
}

/* --- Stats Panel --- */
function StatsPanel({
  status,
  marketCount,
}: {
  status: ReturnType<typeof fetchResearchStatus> extends Promise<infer T>
    ? T | undefined
    : never;
  marketCount: number;
}) {
  if (!status) return null;

  const stats = [
    {
      label: "Markets Analyzed",
      value: status.markets_scanned,
    },
    {
      label: "Cached Results",
      value: status.cached_markets,
    },
    {
      label: "With Sentiment",
      value: marketCount,
    },
    {
      label: "Scan Interval",
      value: `${Math.round(status.scan_interval_seconds / 60)}min`,
    },
  ];

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {stats.map((s) => (
        <div
          key={s.label}
          className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4 text-center"
        >
          <div className="text-lg font-bold text-white font-mono">
            {s.value}
          </div>
          <div className="text-xs text-zinc-500 mt-1">{s.label}</div>
        </div>
      ))}
    </div>
  );
}

/* --- Sentiment Gauge --- */
function SentimentGauge({ markets }: { markets: ResearchMarket[] }) {
  if (markets.length === 0) {
    return (
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
        <h3 className="text-sm font-medium text-white mb-3">
          Overall Market Sentiment
        </h3>
        <p className="text-sm text-zinc-500 text-center py-4">
          No research data yet. The engine will scan markets shortly.
        </p>
      </div>
    );
  }

  const avgSentiment =
    markets.reduce((sum, m) => sum + m.sentiment_score, 0) / markets.length;
  const avgMultiplier =
    markets.reduce((sum, m) => sum + m.research_multiplier, 0) / markets.length;
  const totalArticles = markets.reduce((sum, m) => sum + m.article_count, 0);

  // Sentiment bar position: map [-1, 1] to [0, 100]
  const barPosition = Math.round((avgSentiment + 1) * 50);

  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
      <h3 className="text-sm font-medium text-white mb-4 flex items-center">
        Overall Market Sentiment
        <HelpTooltip text="Aggregate sentiment across all analyzed markets. Positive sentiment lowers edge requirements (more permissive), negative raises them (more cautious)." />
      </h3>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Sentiment Score */}
        <div className="text-center">
          <div
            className={clsx(
              "text-3xl font-bold font-mono",
              sentimentColor(avgSentiment),
            )}
          >
            {avgSentiment >= 0 ? "+" : ""}
            {avgSentiment.toFixed(3)}
          </div>
          <div className="text-xs text-zinc-500 mt-1">
            {sentimentLabel(avgSentiment)}
          </div>
          {/* Visual bar */}
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

        {/* Avg Multiplier */}
        <div className="text-center">
          <div
            className={clsx(
              "text-3xl font-bold font-mono",
              multiplierColor(avgMultiplier),
            )}
          >
            {avgMultiplier.toFixed(3)}x
          </div>
          <div className="text-xs text-zinc-500 mt-1">Avg Edge Multiplier</div>
        </div>

        {/* Total Articles */}
        <div className="text-center">
          <div className="text-3xl font-bold text-white font-mono">
            {totalArticles}
          </div>
          <div className="text-xs text-zinc-500 mt-1">Articles Analyzed</div>
        </div>
      </div>
    </div>
  );
}

/* --- Market Sentiment Table --- */
function MarketSentimentTable({ markets }: { markets: ResearchMarket[] }) {
  if (markets.length === 0) return null;

  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
      <h3 className="text-sm font-medium text-white mb-4 flex items-center">
        Per-Market Sentiment
        <HelpTooltip text="Sentiment analysis per market. Markets with strong sentiment (positive or negative) appear first. Click to see top headlines." />
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-zinc-500 text-xs border-b border-[#2a2d3e]">
              <th className="text-left py-2 pr-3">Keywords</th>
              <th className="text-center py-2 pr-3">Sentiment</th>
              <th className="text-center py-2 pr-3">Label</th>
              <th className="text-center py-2 pr-3">Multiplier</th>
              <th className="text-center py-2 pr-3">Articles</th>
              <th className="text-center py-2 pr-3">Confidence</th>
              <th className="text-right py-2">Updated</th>
            </tr>
          </thead>
          <tbody>
            {markets.map((m) => (
              <MarketRow key={m.market_id} market={m} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MarketRow({ market }: { market: ResearchMarket }) {
  return (
    <>
      <tr className="border-b border-[#2a2d3e]/50">
        <td className="py-2 pr-3 text-white max-w-[200px] truncate">
          {market.keywords.join(", ")}
        </td>
        <td className="py-2 pr-3 text-center">
          <span
            className={clsx(
              "font-mono font-bold",
              sentimentColor(market.sentiment_score),
            )}
          >
            {market.sentiment_score >= 0 ? "+" : ""}
            {market.sentiment_score.toFixed(3)}
          </span>
        </td>
        <td className="py-2 pr-3 text-center">
          <span
            className={clsx(
              "px-2 py-0.5 rounded text-xs",
              sentimentBg(market.sentiment_score),
              sentimentColor(market.sentiment_score),
            )}
          >
            {sentimentLabel(market.sentiment_score)}
          </span>
        </td>
        <td className="py-2 pr-3 text-center">
          <span
            className={clsx(
              "font-mono font-bold",
              multiplierColor(market.research_multiplier),
            )}
          >
            {market.research_multiplier.toFixed(3)}x
          </span>
        </td>
        <td className="py-2 pr-3 text-center text-zinc-400">
          {market.article_count}
        </td>
        <td className="py-2 pr-3 text-center">
          <span
            className={clsx(
              "font-mono",
              market.confidence >= 0.5 ? "text-green-400" : "text-zinc-500",
            )}
          >
            {(market.confidence * 100).toFixed(0)}%
          </span>
        </td>
        <td className="py-2 text-right text-zinc-500 text-xs">
          {formatTime(market.updated_at)}
        </td>
      </tr>
      {/* Top Headlines */}
      {market.top_headlines.length > 0 && (
        <tr className="border-b border-[#2a2d3e]/30">
          <td colSpan={7} className="py-1 px-4">
            <div className="space-y-0.5">
              {market.top_headlines.slice(0, 3).map((h, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span
                    className={clsx(
                      "w-12 text-right font-mono shrink-0",
                      sentimentColor(h.sentiment),
                    )}
                  >
                    {h.sentiment >= 0 ? "+" : ""}
                    {h.sentiment.toFixed(2)}
                  </span>
                  <span className="text-zinc-400 truncate">{h.title}</span>
                  <span className="text-zinc-600 shrink-0">{h.source}</span>
                </div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
