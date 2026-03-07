import { useQuery } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fetchActivity, fetchConfig, fetchLlmCosts } from "../api/client";
import type { ActivityEvent } from "../api/client";

type TabType = "debates" | "reviews" | "risk_debates";

function CostVsPnlChart() {
  const { data: costs } = useQuery({
    queryKey: ["llm-costs"],
    queryFn: fetchLlmCosts,
    refetchInterval: 60000,
  });

  if (!costs || costs.length === 0) {
    return null;
  }

  const totalCost = costs.reduce((s, c) => s + c.total_cost, 0);
  const totalPnl = costs.reduce((s, c) => s + c.daily_pnl, 0);
  const netProfit = totalPnl - totalCost;

  return (
    <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-zinc-300">LLM Cost vs Trading PnL</h3>
        <div className="flex gap-4 text-xs">
          <span className="text-zinc-400">
            Total Cost: <span className="text-red-400">${totalCost.toFixed(4)}</span>
          </span>
          <span className="text-zinc-400">
            Total PnL: <span className={totalPnl >= 0 ? "text-green-400" : "text-red-400"}>
              {totalPnl >= 0 ? "+" : ""}${totalPnl.toFixed(4)}
            </span>
          </span>
          <span className="text-zinc-400">
            Net: <span className={netProfit >= 0 ? "text-green-400" : "text-red-400"}>
              {netProfit >= 0 ? "+" : ""}${netProfit.toFixed(4)}
            </span>
          </span>
        </div>
      </div>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={costs} margin={{ top: 5, right: 5, left: -10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3e" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: "#71717a" }}
            tickFormatter={(v: string) => v.slice(5)}
          />
          <YAxis tick={{ fontSize: 10, fill: "#71717a" }} />
          <Tooltip
            contentStyle={{
              backgroundColor: "#1e2130",
              border: "1px solid #2a2d3e",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(value: number, name: string) => [
              `$${value.toFixed(4)}`,
              name,
            ]}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="daily_pnl" name="Trading PnL" fill="#22c55e" radius={[2, 2, 0, 0]} />
          <Bar dataKey="total_cost" name="LLM Cost" fill="#ef4444" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function DebateCard({ event }: { event: ActivityEvent }) {
  const meta = event.metadata as Record<string, unknown>;
  const approved = meta.approved === true;
  const proposerVerdict = (meta.proposer_verdict as string) ?? "?";
  const proposerConf = (meta.proposer_confidence as number) ?? 0;
  const proposerReasoning = (meta.proposer_reasoning as string) ?? "";
  const challengerVerdict = (meta.challenger_verdict as string) ?? "?";
  const challengerRisk = (meta.challenger_risk as string) ?? "?";
  const challengerObjections = (meta.challenger_objections as string) ?? "";
  const counterRebuttal = (meta.counter_rebuttal as string) ?? "";
  const counterConviction = (meta.counter_conviction as number) ?? 0;
  const finalVerdict = (meta.final_verdict as string) ?? "";
  const finalReasoning = (meta.final_reasoning as string) ?? "";
  const edge = (meta.edge as number) ?? 0;
  const price = (meta.price as number) ?? 0;
  const costUsd = (meta.cost_usd as number) ?? 0;

  const ts = new Date(event.timestamp);
  const timeStr = ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const dateStr = ts.toLocaleDateString([], { month: "short", day: "numeric" });

  return (
    <div
      className={`bg-[#1e2130] rounded-lg border p-4 ${
        approved ? "border-green-800/50" : "border-red-800/50"
      }`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className={`px-2 py-0.5 rounded text-xs font-medium ${
                approved
                  ? "bg-green-900/40 text-green-400"
                  : "bg-red-900/40 text-red-400"
              }`}
            >
              {approved ? "APPROVED" : "REJECTED"}
            </span>
            <span className="text-xs text-zinc-500">{event.strategy}</span>
          </div>
          <p className="text-sm text-zinc-200 mt-1 line-clamp-2">
            {event.title.replace(/^AI Debate (Approved|Rejected): /, "")}
          </p>
        </div>
        <div className="text-right shrink-0">
          <div className="text-xs text-zinc-500">{dateStr}</div>
          <div className="text-xs text-zinc-400">{timeStr}</div>
        </div>
      </div>

      {/* Market data */}
      <div className="flex gap-4 text-xs text-zinc-400 mb-3">
        <span>Price: ${price.toFixed(3)}</span>
        <span>Edge: {(edge * 100).toFixed(1)}%</span>
        <span>Cost: ${costUsd.toFixed(4)}</span>
      </div>

      {/* Proposer */}
      <div className="rounded bg-[#0f1117] p-3 mb-2">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-medium text-indigo-400">PROPOSER</span>
          <span
            className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
              proposerVerdict === "BUY"
                ? "bg-green-900/40 text-green-400"
                : "bg-zinc-700 text-zinc-400"
            }`}
          >
            {proposerVerdict}
          </span>
          <span className="text-[10px] text-zinc-500">
            Confidence: {(proposerConf * 100).toFixed(0)}%
          </span>
        </div>
        <p className="text-xs text-zinc-300">{proposerReasoning}</p>
      </div>

      {/* Challenger */}
      <div className="rounded bg-[#0f1117] p-3">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-medium text-amber-400">CHALLENGER</span>
          <span
            className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
              challengerVerdict === "APPROVE"
                ? "bg-green-900/40 text-green-400"
                : challengerVerdict === "REJECT"
                  ? "bg-red-900/40 text-red-400"
                  : "bg-zinc-700 text-zinc-400"
            }`}
          >
            {challengerVerdict}
          </span>
          <span
            className={`text-[10px] ${
              challengerRisk === "HIGH"
                ? "text-red-400"
                : challengerRisk === "MEDIUM"
                  ? "text-amber-400"
                  : "text-green-400"
            }`}
          >
            Risk: {challengerRisk}
          </span>
        </div>
        <p className="text-xs text-zinc-300">{challengerObjections}</p>
      </div>

      {/* Multi-round counter (only if present) */}
      {counterRebuttal && (
        <>
          <div className="rounded bg-indigo-950/20 border border-indigo-900/30 p-3 mt-2">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xs font-medium text-indigo-400">COUNTER-ARGUMENT</span>
              <span className="text-[10px] text-zinc-500">
                Conviction: {(counterConviction * 100).toFixed(0)}%
              </span>
            </div>
            <p className="text-xs text-zinc-300">{counterRebuttal}</p>
          </div>

          {finalVerdict && (
            <div className="rounded bg-[#0f1117] p-3 mt-2">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs font-medium text-amber-400">FINAL VERDICT</span>
                <span
                  className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                    finalVerdict === "APPROVE"
                      ? "bg-green-900/40 text-green-400"
                      : "bg-red-900/40 text-red-400"
                  }`}
                >
                  {finalVerdict}
                </span>
              </div>
              <p className="text-xs text-zinc-300">{finalReasoning}</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ReviewCard({ event }: { event: ActivityEvent }) {
  const meta = event.metadata as Record<string, unknown>;
  const verdict = (meta.verdict as string) ?? "?";
  const urgency = (meta.urgency as string) ?? "?";
  const reasoning = (meta.reasoning as string) ?? "";
  const entryPrice = (meta.entry_price as number) ?? 0;
  const currentPrice = (meta.current_price as number) ?? 0;
  const pnl = (meta.unrealized_pnl as number) ?? 0;
  const costUsd = (meta.cost_usd as number) ?? 0;

  const ts = new Date(event.timestamp);
  const timeStr = ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const dateStr = ts.toLocaleDateString([], { month: "short", day: "numeric" });

  const pnlPct = entryPrice > 0 ? ((currentPrice - entryPrice) / entryPrice) * 100 : 0;

  return (
    <div
      className={`bg-[#1e2130] rounded-lg border p-4 ${
        verdict === "EXIT" ? "border-amber-800/50" : "border-[#2a2d3e]"
      }`}
    >
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className={`px-2 py-0.5 rounded text-xs font-medium ${
                verdict === "EXIT"
                  ? "bg-red-900/40 text-red-400"
                  : verdict === "REDUCE"
                    ? "bg-amber-900/40 text-amber-400"
                    : verdict === "INCREASE"
                      ? "bg-green-900/40 text-green-400"
                      : "bg-blue-900/40 text-blue-400"
              }`}
            >
              {verdict}
            </span>
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] ${
                urgency === "HIGH"
                  ? "bg-red-900/40 text-red-400"
                  : urgency === "MEDIUM"
                    ? "bg-amber-900/40 text-amber-400"
                    : "bg-zinc-700 text-zinc-400"
              }`}
            >
              {urgency}
            </span>
            <span className="text-xs text-zinc-500">{event.strategy}</span>
          </div>
          <p className="text-sm text-zinc-200 mt-1 line-clamp-2">
            {event.title.replace(/^AI Review: (HOLD|EXIT|REDUCE|INCREASE) \((LOW|MEDIUM|HIGH)\) — /, "")}
          </p>
        </div>
        <div className="text-right shrink-0">
          <div className="text-xs text-zinc-500">{dateStr}</div>
          <div className="text-xs text-zinc-400">{timeStr}</div>
        </div>
      </div>

      {/* Position data */}
      <div className="flex gap-4 text-xs text-zinc-400 mb-3">
        <span>Entry: ${entryPrice.toFixed(3)}</span>
        <span>Now: ${currentPrice.toFixed(3)}</span>
        <span className={pnl >= 0 ? "text-green-400" : "text-red-400"}>
          PnL: {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)} ({pnlPct >= 0 ? "+" : ""}
          {pnlPct.toFixed(1)}%)
        </span>
        <span>Cost: ${costUsd.toFixed(4)}</span>
      </div>

      {/* Reasoning */}
      <div className="rounded bg-[#0f1117] p-3">
        <span className="text-xs font-medium text-purple-400">ANALYSIS</span>
        <p className="text-xs text-zinc-300 mt-1">{reasoning}</p>
      </div>
    </div>
  );
}

function RiskDebateCard({ event }: { event: ActivityEvent }) {
  const meta = event.metadata as Record<string, unknown>;
  const override = meta.override === true;
  const rejectionReason = (meta.rejection_reason as string) ?? "";
  const proposerRebuttal = (meta.proposer_rebuttal as string) ?? "";
  const analystVerdict = (meta.analyst_verdict as string) ?? "?";
  const analystReasoning = (meta.analyst_reasoning as string) ?? "";
  const adjustedSizePct = (meta.adjusted_size_pct as number) ?? 0;
  const edge = (meta.edge as number) ?? 0;
  const price = (meta.price as number) ?? 0;
  const costUsd = (meta.cost_usd as number) ?? 0;

  const ts = new Date(event.timestamp);
  const timeStr = ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const dateStr = ts.toLocaleDateString([], { month: "short", day: "numeric" });

  return (
    <div
      className={`bg-[#1e2130] rounded-lg border p-4 ${
        override ? "border-green-800/50" : "border-zinc-700/50"
      }`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className={`px-2 py-0.5 rounded text-xs font-medium ${
                override
                  ? "bg-green-900/40 text-green-400"
                  : "bg-zinc-700 text-zinc-400"
              }`}
            >
              {override ? "OVERRIDDEN" : "UPHELD"}
            </span>
            <span className="text-xs text-zinc-500">{event.strategy}</span>
            {override && adjustedSizePct > 0 && (
              <span className="text-[10px] text-amber-400">
                Size: {(adjustedSizePct * 100).toFixed(0)}%
              </span>
            )}
          </div>
          <p className="text-sm text-zinc-200 mt-1 line-clamp-2">
            {event.title.replace(/^Risk Debate (Overridden|Upheld): /, "")}
          </p>
        </div>
        <div className="text-right shrink-0">
          <div className="text-xs text-zinc-500">{dateStr}</div>
          <div className="text-xs text-zinc-400">{timeStr}</div>
        </div>
      </div>

      {/* Market data */}
      <div className="flex gap-4 text-xs text-zinc-400 mb-3">
        <span>Price: ${price.toFixed(3)}</span>
        <span>Edge: {(edge * 100).toFixed(1)}%</span>
        <span>Cost: ${costUsd.toFixed(4)}</span>
      </div>

      {/* Risk rejection reason */}
      <div className="rounded bg-red-950/30 border border-red-900/30 p-3 mb-2">
        <span className="text-xs font-medium text-red-400">REJECTION</span>
        <p className="text-xs text-zinc-300 mt-1">{rejectionReason}</p>
      </div>

      {/* Proposer rebuttal */}
      <div className="rounded bg-orange-950/20 border border-orange-900/30 p-3 mb-2">
        <span className="text-xs font-medium text-orange-400">PROPOSER REBUTTAL</span>
        <p className="text-xs text-zinc-300 mt-1">{proposerRebuttal}</p>
      </div>

      {/* Analyst verdict */}
      <div className="rounded bg-cyan-950/20 border border-cyan-900/30 p-3">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-medium text-cyan-400">RISK ANALYST</span>
          <span
            className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
              analystVerdict === "CONCEDE"
                ? "bg-green-900/40 text-green-400"
                : "bg-zinc-700 text-zinc-400"
            }`}
          >
            {analystVerdict}
          </span>
        </div>
        <p className="text-xs text-zinc-300">{analystReasoning}</p>
      </div>
    </div>
  );
}

const PAGE_SIZE = 20;

function Pagination({
  page,
  hasMore,
  total,
  onPrev,
  onNext,
}: {
  page: number;
  hasMore: boolean;
  total: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  if (total === 0) return null;
  return (
    <div className="flex items-center justify-between pt-2">
      <span className="text-xs text-zinc-500">
        {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
      </span>
      <div className="flex gap-2">
        <button
          onClick={onPrev}
          disabled={page === 0}
          className="px-3 py-1 rounded text-xs font-medium bg-[#1a1d29] text-zinc-400 hover:text-zinc-200 disabled:opacity-30 disabled:cursor-not-allowed"
        >
          Prev
        </button>
        <button
          onClick={onNext}
          disabled={!hasMore}
          className="px-3 py-1 rounded text-xs font-medium bg-[#1a1d29] text-zinc-400 hover:text-zinc-200 disabled:opacity-30 disabled:cursor-not-allowed"
        >
          Next
        </button>
      </div>
    </div>
  );
}

export default function AIDebates() {
  const [tab, setTab] = useState<TabType>("debates");
  const [debatePage, setDebatePage] = useState(0);
  const [reviewPage, setReviewPage] = useState(0);
  const [riskPage, setRiskPage] = useState(0);

  const currentPage = tab === "debates" ? debatePage : tab === "reviews" ? reviewPage : riskPage;
  const setCurrentPage = useCallback(
    (p: number) => {
      if (tab === "debates") setDebatePage(p);
      else if (tab === "reviews") setReviewPage(p);
      else setRiskPage(p);
    },
    [tab],
  );

  const { data: config } = useQuery({
    queryKey: ["config"],
    queryFn: fetchConfig,
  });

  const { data: debateData, isLoading: debatesLoading } = useQuery({
    queryKey: ["activity", "llm_debate", debatePage],
    queryFn: () =>
      fetchActivity({
        event_type: "llm_debate",
        limit: PAGE_SIZE,
        offset: debatePage * PAGE_SIZE,
      }),
    refetchInterval: 15000,
  });

  const { data: reviewData, isLoading: reviewsLoading } = useQuery({
    queryKey: ["activity", "llm_review", reviewPage],
    queryFn: () =>
      fetchActivity({
        event_type: "llm_review",
        limit: PAGE_SIZE,
        offset: reviewPage * PAGE_SIZE,
      }),
    refetchInterval: 15000,
  });

  const { data: riskDebateData, isLoading: riskDebatesLoading } = useQuery({
    queryKey: ["activity", "llm_risk_debate", riskPage],
    queryFn: () =>
      fetchActivity({
        event_type: "llm_risk_debate",
        limit: PAGE_SIZE,
        offset: riskPage * PAGE_SIZE,
      }),
    refetchInterval: 15000,
  });

  const debates = debateData?.events ?? [];
  const reviews = reviewData?.events ?? [];
  const riskDebates = riskDebateData?.events ?? [];

  const isLoading =
    tab === "debates"
      ? debatesLoading
      : tab === "reviews"
        ? reviewsLoading
        : riskDebatesLoading;

  const events =
    tab === "debates" ? debates : tab === "reviews" ? reviews : riskDebates;

  const currentData = tab === "debates" ? debateData : tab === "reviews" ? reviewData : riskDebateData;

  // Stats
  const approvedCount = debates.filter(
    (e) => (e.metadata as Record<string, unknown>).approved === true,
  ).length;
  const rejectedCount = debates.filter(
    (e) => (e.metadata as Record<string, unknown>).approved === false,
  ).length;
  const exitReviews = reviews.filter(
    (e) => (e.metadata as Record<string, unknown>).verdict === "EXIT",
  ).length;
  const reduceReviews = reviews.filter(
    (e) => (e.metadata as Record<string, unknown>).verdict === "REDUCE",
  ).length;
  const increaseReviews = reviews.filter(
    (e) => (e.metadata as Record<string, unknown>).verdict === "INCREASE",
  ).length;
  const holdReviews = reviews.filter(
    (e) => (e.metadata as Record<string, unknown>).verdict === "HOLD",
  ).length;
  const totalDebateCost = debates.reduce(
    (sum, e) => sum + ((e.metadata as Record<string, unknown>).cost_usd as number ?? 0),
    0,
  );
  const totalReviewCost = reviews.reduce(
    (sum, e) => sum + ((e.metadata as Record<string, unknown>).cost_usd as number ?? 0),
    0,
  );
  const riskOverrideCount = riskDebates.filter(
    (e) => (e.metadata as Record<string, unknown>).override === true,
  ).length;
  const riskUpheldCount = riskDebates.filter(
    (e) => (e.metadata as Record<string, unknown>).override === false,
  ).length;
  const totalRiskDebateCost = riskDebates.reduce(
    (sum, e) => sum + ((e.metadata as Record<string, unknown>).cost_usd as number ?? 0),
    0,
  );

  return (
    <div className="space-y-6 max-w-3xl mx-auto" data-testid="ai-debates-page">
      <h2 className="text-xl font-bold">AI Debates</h2>

      {/* Cost vs PnL chart */}
      <CostVsPnlChart />

      {/* Status banner */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-3">
          <div className="text-xs text-zinc-500">Debate Gate</div>
          <div
            className={`text-sm font-medium mt-1 ${
              config?.use_llm_debate ? "text-green-400" : "text-zinc-500"
            }`}
          >
            {config?.use_llm_debate ? "Active" : "Off"}
          </div>
        </div>
        <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-3">
          <div className="text-xs text-zinc-500">Position Reviewer</div>
          <div
            className={`text-sm font-medium mt-1 ${
              config?.use_llm_reviewer ? "text-green-400" : "text-zinc-500"
            }`}
          >
            {config?.use_llm_reviewer ? "Active" : "Off"}
          </div>
        </div>
        <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-3">
          <div className="text-xs text-zinc-500">Today&apos;s Cost</div>
          <div className="text-sm font-mono text-white mt-1">
            ${(config?.llm_today_cost ?? 0).toFixed(4)}
          </div>
        </div>
        <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-3">
          <div className="text-xs text-zinc-500">Budget Left</div>
          <div className="text-sm font-mono text-white mt-1">
            ${Math.max(0, (config?.llm_daily_budget ?? 3) - (config?.llm_today_cost ?? 0)).toFixed(2)}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-[#1a1d29] rounded-lg p-1">
        <button
          onClick={() => setTab("debates")}
          className={`flex-1 px-3 py-2 rounded text-sm font-medium transition-colors ${
            tab === "debates"
              ? "bg-indigo-600 text-white"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Trade Debates
          {debates.length > 0 && (
            <span className="ml-1 text-xs opacity-70">
              {approvedCount}/{rejectedCount}
            </span>
          )}
        </button>
        <button
          onClick={() => setTab("reviews")}
          className={`flex-1 px-3 py-2 rounded text-sm font-medium transition-colors ${
            tab === "reviews"
              ? "bg-indigo-600 text-white"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Reviews
          {reviews.length > 0 && (
            <span className="ml-1 text-xs opacity-70">
              {holdReviews}H/{reduceReviews}R/{exitReviews}E/{increaseReviews}I
            </span>
          )}
        </button>
        <button
          onClick={() => setTab("risk_debates")}
          className={`flex-1 px-3 py-2 rounded text-sm font-medium transition-colors ${
            tab === "risk_debates"
              ? "bg-indigo-600 text-white"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Risk Debates
          {riskDebates.length > 0 && (
            <span className="ml-1 text-xs opacity-70">
              {riskOverrideCount}/{riskUpheldCount}
            </span>
          )}
        </button>
      </div>

      {/* Stats summary */}
      {tab === "debates" && debates.length > 0 && (
        <div className="flex gap-4 text-xs text-zinc-500">
          <span>
            Approval rate:{" "}
            {debates.length > 0
              ? ((approvedCount / debates.length) * 100).toFixed(0)
              : 0}
            %
          </span>
          <span>Total cost: ${totalDebateCost.toFixed(4)}</span>
        </div>
      )}
      {tab === "reviews" && reviews.length > 0 && (
        <div className="flex gap-4 text-xs text-zinc-500">
          <span>
            Exit rate:{" "}
            {reviews.length > 0
              ? ((exitReviews / reviews.length) * 100).toFixed(0)
              : 0}
            %
          </span>
          <span>Total cost: ${totalReviewCost.toFixed(4)}</span>
        </div>
      )}
      {tab === "risk_debates" && riskDebates.length > 0 && (
        <div className="flex gap-4 text-xs text-zinc-500">
          <span>
            Override rate:{" "}
            {riskDebates.length > 0
              ? ((riskOverrideCount / riskDebates.length) * 100).toFixed(0)
              : 0}
            %
          </span>
          <span>Total cost: ${totalRiskDebateCost.toFixed(4)}</span>
        </div>
      )}

      {/* Events list */}
      {isLoading ? (
        <div className="text-center text-zinc-500 py-12">Loading...</div>
      ) : events.length === 0 ? (
        <div className="text-center text-zinc-500 py-12">
          <p className="text-lg mb-2">
            No {tab === "debates" ? "debates" : tab === "reviews" ? "reviews" : "risk debates"} yet
          </p>
          <p className="text-sm">
            {tab === "debates"
              ? config?.use_llm_debate
                ? "Debates will appear here as the bot evaluates trade signals."
                : "Enable the AI Debate Gate in Settings to start."
              : tab === "reviews"
                ? config?.use_llm_reviewer
                  ? "Reviews will appear as the AI checks open positions."
                  : "Enable the Position Reviewer in Settings to start."
                : config?.use_llm_debate
                  ? "Risk debates will appear when AI challenges risk rejections."
                  : "Enable the AI Debate Gate in Settings to start."}
          </p>
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {events.map((event) =>
              tab === "debates" ? (
                <DebateCard key={event.id} event={event} />
              ) : tab === "reviews" ? (
                <ReviewCard key={event.id} event={event} />
              ) : (
                <RiskDebateCard key={event.id} event={event} />
              ),
            )}
          </div>
          <Pagination
            page={currentPage}
            hasMore={currentData?.has_more ?? false}
            total={currentData?.total ?? 0}
            onPrev={() => setCurrentPage(Math.max(0, currentPage - 1))}
            onNext={() => setCurrentPage(currentPage + 1)}
          />
        </>
      )}
    </div>
  );
}
