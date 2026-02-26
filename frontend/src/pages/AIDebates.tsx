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
import { formatDateTime } from "../utils/date";

type TabType = "debates" | "reviews" | "risk_debates" | "post_mortems";
type DecisionFilter = "all" | "approved" | "rejected";
type ReviewFilter = "all" | "HOLD" | "EXIT" | "REDUCE" | "INCREASE";
type RiskFilter = "all" | "override" | "upheld";
type PostMortemFilter = "all" | "GOOD" | "BAD" | "NEUTRAL";

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
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-3">
        <h3 className="text-sm font-medium text-zinc-300">LLM Cost vs Trading PnL</h3>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          <span className="text-zinc-400">
            Cost: <span className="text-red-400">${totalCost.toFixed(4)}</span>
          </span>
          <span className="text-zinc-400">
            PnL: <span className={totalPnl >= 0 ? "text-green-400" : "text-red-400"}>
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

  const formattedTs = formatDateTime(event.timestamp);

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
          <div className="text-xs text-zinc-500">{formattedTs}</div>
        </div>
      </div>

      {/* Market data */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-400 mb-3">
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

  const formattedTs = formatDateTime(event.timestamp);

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
          <div className="text-xs text-zinc-500">{formattedTs}</div>
        </div>
      </div>

      {/* Position data */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-400 mb-3">
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

  const formattedTs = formatDateTime(event.timestamp);

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
          <div className="text-xs text-zinc-500">{formattedTs}</div>
        </div>
      </div>

      {/* Market data */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-400 mb-3">
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

function PostMortemCard({ event }: { event: ActivityEvent }) {
  const meta = event.metadata as Record<string, unknown>;
  const pnl = (meta.pnl as number) ?? 0;
  const outcomeQuality = (meta.outcome_quality as string) ?? "?";
  const keyLesson = (meta.key_lesson as string) ?? "";
  const strategyFit = (meta.strategy_fit as string) ?? "?";
  const analysis = (meta.analysis as string) ?? "";
  const exitReason = (meta.exit_reason as string) ?? "";
  const costUsd = (meta.cost_usd as number) ?? 0;

  const formattedTs = formatDateTime(event.timestamp);

  return (
    <div
      className={`bg-[#1e2130] rounded-lg border p-4 ${
        pnl >= 0 ? "border-green-800/50" : "border-red-800/50"
      }`}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className={`px-2 py-0.5 rounded text-xs font-medium ${
                outcomeQuality === "GOOD"
                  ? "bg-green-900/40 text-green-400"
                  : outcomeQuality === "BAD"
                    ? "bg-red-900/40 text-red-400"
                    : "bg-zinc-700 text-zinc-400"
              }`}
            >
              {outcomeQuality}
            </span>
            <span className="text-xs text-zinc-500">{event.strategy}</span>
            <span
              className={`text-xs font-mono ${pnl >= 0 ? "text-green-400" : "text-red-400"}`}
            >
              {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}
            </span>
          </div>
          <p className="text-sm text-zinc-200 mt-1 line-clamp-2">
            {event.title.replace(/^Post-Mortem \((?:GOOD|BAD|NEUTRAL)\): /, "")}
          </p>
        </div>
        <div className="text-right shrink-0">
          <div className="text-xs text-zinc-500">{formattedTs}</div>
        </div>
      </div>

      {/* Meta row */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-400 mb-3">
        <span>Exit: {exitReason}</span>
        <span
          className={`${
            strategyFit === "GOOD_FIT"
              ? "text-green-400"
              : strategyFit === "POOR_FIT"
                ? "text-red-400"
                : "text-zinc-400"
          }`}
        >
          Fit: {strategyFit.replace("_", " ")}
        </span>
        <span>Cost: ${costUsd.toFixed(4)}</span>
      </div>

      {/* Lesson */}
      {keyLesson && (
        <div className="rounded bg-amber-950/20 border border-amber-900/30 p-3 mb-2">
          <span className="text-xs font-medium text-amber-400">KEY LESSON</span>
          <p className="text-xs text-zinc-300 mt-1">{keyLesson}</p>
        </div>
      )}

      {/* Analysis */}
      <div className="rounded bg-[#0f1117] p-3">
        <span className="text-xs font-medium text-purple-400">ANALYSIS</span>
        <p className="text-xs text-zinc-300 mt-1">{analysis}</p>
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
  const [decisionFilter, setDecisionFilter] = useState<DecisionFilter>("all");
  const [reviewFilter, setReviewFilter] = useState<ReviewFilter>("all");
  const [riskFilter, setRiskFilter] = useState<RiskFilter>("all");
  const [postMortemFilter, setPostMortemFilter] = useState<PostMortemFilter>("all");
  const [debatePage, setDebatePage] = useState(0);
  const [reviewPage, setReviewPage] = useState(0);
  const [riskPage, setRiskPage] = useState(0);
  const [postMortemPage, setPostMortemPage] = useState(0);

  const currentPage =
    tab === "debates" ? debatePage
    : tab === "reviews" ? reviewPage
    : tab === "risk_debates" ? riskPage
    : postMortemPage;
  const setCurrentPage = useCallback(
    (p: number) => {
      if (tab === "debates") setDebatePage(p);
      else if (tab === "reviews") setReviewPage(p);
      else if (tab === "risk_debates") setRiskPage(p);
      else setPostMortemPage(p);
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

  const { data: postMortemData, isLoading: postMortemsLoading } = useQuery({
    queryKey: ["activity", "llm_post_mortem", postMortemPage],
    queryFn: () =>
      fetchActivity({
        event_type: "llm_post_mortem",
        limit: PAGE_SIZE,
        offset: postMortemPage * PAGE_SIZE,
      }),
    refetchInterval: 15000,
  });

  const allDebates = debateData?.events ?? [];

  // Apply decision filter to debates
  const debates = allDebates.filter((e) => {
    if (decisionFilter === "all") return true;
    const isApproved = (e.metadata as Record<string, unknown>).approved === true;
    return decisionFilter === "approved" ? isApproved : !isApproved;
  });

  // Apply verdict filter to reviews
  const allReviews = reviewData?.events ?? [];
  const filteredReviews = allReviews.filter((e) => {
    if (reviewFilter === "all") return true;
    return (e.metadata as Record<string, unknown>).verdict === reviewFilter;
  });

  // Apply outcome filter to risk debates
  const allRiskDebates = riskDebateData?.events ?? [];
  const filteredRiskDebates = allRiskDebates.filter((e) => {
    if (riskFilter === "all") return true;
    const isOverride = (e.metadata as Record<string, unknown>).override === true;
    return riskFilter === "override" ? isOverride : !isOverride;
  });

  // Apply quality filter to post-mortems
  const allPostMortems = postMortemData?.events ?? [];
  const filteredPostMortems = allPostMortems.filter((e) => {
    if (postMortemFilter === "all") return true;
    return (e.metadata as Record<string, unknown>).outcome_quality === postMortemFilter;
  });

  const isLoading =
    tab === "debates"
      ? debatesLoading
      : tab === "reviews"
        ? reviewsLoading
        : tab === "risk_debates"
          ? riskDebatesLoading
          : postMortemsLoading;

  const events =
    tab === "debates" ? debates
    : tab === "reviews" ? filteredReviews
    : tab === "risk_debates" ? filteredRiskDebates
    : filteredPostMortems;

  const currentData =
    tab === "debates" ? debateData
    : tab === "reviews" ? reviewData
    : tab === "risk_debates" ? riskDebateData
    : postMortemData;

  // Stats (use allDebates for accurate counts regardless of filter)
  const approvedCount = allDebates.filter(
    (e) => (e.metadata as Record<string, unknown>).approved === true,
  ).length;
  const rejectedCount = allDebates.filter(
    (e) => (e.metadata as Record<string, unknown>).approved === false,
  ).length;
  const exitReviews = allReviews.filter(
    (e) => (e.metadata as Record<string, unknown>).verdict === "EXIT",
  ).length;
  const reduceReviews = allReviews.filter(
    (e) => (e.metadata as Record<string, unknown>).verdict === "REDUCE",
  ).length;
  const increaseReviews = allReviews.filter(
    (e) => (e.metadata as Record<string, unknown>).verdict === "INCREASE",
  ).length;
  const holdReviews = allReviews.filter(
    (e) => (e.metadata as Record<string, unknown>).verdict === "HOLD",
  ).length;
  const totalDebateCost = allDebates.reduce(
    (sum, e) => sum + ((e.metadata as Record<string, unknown>).cost_usd as number ?? 0),
    0,
  );
  const totalReviewCost = allReviews.reduce(
    (sum, e) => sum + ((e.metadata as Record<string, unknown>).cost_usd as number ?? 0),
    0,
  );
  const riskOverrideCount = allRiskDebates.filter(
    (e) => (e.metadata as Record<string, unknown>).override === true,
  ).length;
  const riskUpheldCount = allRiskDebates.filter(
    (e) => (e.metadata as Record<string, unknown>).override === false,
  ).length;
  const totalRiskDebateCost = allRiskDebates.reduce(
    (sum, e) => sum + ((e.metadata as Record<string, unknown>).cost_usd as number ?? 0),
    0,
  );
  const goodPostMortems = allPostMortems.filter(
    (e) => (e.metadata as Record<string, unknown>).outcome_quality === "GOOD",
  ).length;
  const badPostMortems = allPostMortems.filter(
    (e) => (e.metadata as Record<string, unknown>).outcome_quality === "BAD",
  ).length;
  const neutralPostMortems = allPostMortems.filter(
    (e) => (e.metadata as Record<string, unknown>).outcome_quality === "NEUTRAL",
  ).length;
  const totalPostMortemCost = allPostMortems.reduce(
    (sum, e) => sum + ((e.metadata as Record<string, unknown>).cost_usd as number ?? 0),
    0,
  );

  return (
    <div className="space-y-6 max-w-3xl mx-auto" data-testid="ai-debates-page">
      <h2 className="text-xl font-bold">AI Debates</h2>

      {/* Cost vs PnL chart */}
      <CostVsPnlChart />

      {/* Status banner */}
      <div className="grid grid-cols-2 gap-2 sm:gap-3">
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
      <div className="grid grid-cols-2 sm:flex gap-1 bg-[#1a1d29] rounded-lg p-1">
        <button
          onClick={() => { setTab("debates"); setDebatePage(0); }}
          className={`sm:flex-1 px-2 sm:px-3 py-2 rounded text-xs sm:text-sm font-medium transition-colors ${
            tab === "debates"
              ? "bg-indigo-600 text-white"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Debates
          {debates.length > 0 && (
            <span className="ml-1 text-xs opacity-70">
              {approvedCount}/{rejectedCount}
            </span>
          )}
        </button>
        <button
          onClick={() => { setTab("reviews"); setReviewPage(0); }}
          className={`sm:flex-1 px-2 sm:px-3 py-2 rounded text-xs sm:text-sm font-medium transition-colors ${
            tab === "reviews"
              ? "bg-indigo-600 text-white"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Reviews
          {allReviews.length > 0 && (
            <span className="ml-1 text-xs opacity-70 hidden sm:inline">
              {holdReviews}H/{exitReviews}E
            </span>
          )}
        </button>
        <button
          onClick={() => { setTab("risk_debates"); setRiskPage(0); }}
          className={`sm:flex-1 px-2 sm:px-3 py-2 rounded text-xs sm:text-sm font-medium transition-colors ${
            tab === "risk_debates"
              ? "bg-indigo-600 text-white"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Risk
          {allRiskDebates.length > 0 && (
            <span className="ml-1 text-xs opacity-70">
              {riskOverrideCount}/{riskUpheldCount}
            </span>
          )}
        </button>
        <button
          onClick={() => { setTab("post_mortems"); setPostMortemPage(0); }}
          className={`sm:flex-1 px-2 sm:px-3 py-2 rounded text-xs sm:text-sm font-medium transition-colors ${
            tab === "post_mortems"
              ? "bg-indigo-600 text-white"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Post-Mortem
          {allPostMortems.length > 0 && (
            <span className="ml-1 text-xs opacity-70">
              {allPostMortems.length}
            </span>
          )}
        </button>
      </div>

      {/* Stats summary */}
      {tab === "debates" && allDebates.length > 0 && (
        <div className="space-y-2">
          <div className="flex gap-4 text-xs text-zinc-500">
            <span>
              Approval rate:{" "}
              {allDebates.length > 0
                ? ((approvedCount / allDebates.length) * 100).toFixed(0)
                : 0}
              %
            </span>
            <span>Total cost: ${totalDebateCost.toFixed(4)}</span>
          </div>
          {/* Decision filter */}
          <div className="flex gap-1">
            {(["all", "approved", "rejected"] as DecisionFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setDecisionFilter(f)}
                className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                  decisionFilter === f
                    ? f === "approved"
                      ? "bg-green-900/40 text-green-400"
                      : f === "rejected"
                        ? "bg-red-900/40 text-red-400"
                        : "bg-indigo-600 text-white"
                    : "bg-[#1a1d29] text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {f === "all" ? `All (${allDebates.length})` : f === "approved" ? `Approved (${approvedCount})` : `Rejected (${rejectedCount})`}
              </button>
            ))}
          </div>
        </div>
      )}
      {tab === "reviews" && allReviews.length > 0 && (
        <div className="space-y-2">
          <div className="flex gap-4 text-xs text-zinc-500">
            <span>
              Exit rate:{" "}
              {allReviews.length > 0
                ? ((exitReviews / allReviews.length) * 100).toFixed(0)
                : 0}
              %
            </span>
            <span>Total cost: ${totalReviewCost.toFixed(4)}</span>
          </div>
          {/* Verdict filter */}
          <div className="flex flex-wrap gap-1">
            {(["all", "HOLD", "EXIT", "REDUCE", "INCREASE"] as ReviewFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setReviewFilter(f)}
                className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                  reviewFilter === f
                    ? f === "HOLD"
                      ? "bg-blue-900/40 text-blue-400"
                      : f === "EXIT"
                        ? "bg-red-900/40 text-red-400"
                        : f === "REDUCE"
                          ? "bg-amber-900/40 text-amber-400"
                          : f === "INCREASE"
                            ? "bg-green-900/40 text-green-400"
                            : "bg-indigo-600 text-white"
                    : "bg-[#1a1d29] text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {f === "all"
                  ? `All (${allReviews.length})`
                  : f === "HOLD"
                    ? `Hold (${holdReviews})`
                    : f === "EXIT"
                      ? `Exit (${exitReviews})`
                      : f === "REDUCE"
                        ? `Reduce (${reduceReviews})`
                        : `Increase (${increaseReviews})`}
              </button>
            ))}
          </div>
        </div>
      )}
      {tab === "risk_debates" && allRiskDebates.length > 0 && (
        <div className="space-y-2">
          <div className="flex gap-4 text-xs text-zinc-500">
            <span>
              Override rate:{" "}
              {allRiskDebates.length > 0
                ? ((riskOverrideCount / allRiskDebates.length) * 100).toFixed(0)
                : 0}
              %
            </span>
            <span>Total cost: ${totalRiskDebateCost.toFixed(4)}</span>
          </div>
          {/* Outcome filter */}
          <div className="flex gap-1">
            {(["all", "override", "upheld"] as RiskFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setRiskFilter(f)}
                className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                  riskFilter === f
                    ? f === "override"
                      ? "bg-green-900/40 text-green-400"
                      : f === "upheld"
                        ? "bg-zinc-700 text-zinc-300"
                        : "bg-indigo-600 text-white"
                    : "bg-[#1a1d29] text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {f === "all"
                  ? `All (${allRiskDebates.length})`
                  : f === "override"
                    ? `Override (${riskOverrideCount})`
                    : `Upheld (${riskUpheldCount})`}
              </button>
            ))}
          </div>
        </div>
      )}
      {tab === "post_mortems" && allPostMortems.length > 0 && (
        <div className="space-y-2">
          <div className="flex gap-4 text-xs text-zinc-500">
            <span>
              Good rate:{" "}
              {allPostMortems.length > 0
                ? ((goodPostMortems / allPostMortems.length) * 100).toFixed(0)
                : 0}
              %
            </span>
            <span>Total cost: ${totalPostMortemCost.toFixed(4)}</span>
          </div>
          {/* Quality filter */}
          <div className="flex gap-1">
            {(["all", "GOOD", "BAD", "NEUTRAL"] as PostMortemFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setPostMortemFilter(f)}
                className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                  postMortemFilter === f
                    ? f === "GOOD"
                      ? "bg-green-900/40 text-green-400"
                      : f === "BAD"
                        ? "bg-red-900/40 text-red-400"
                        : f === "NEUTRAL"
                          ? "bg-zinc-700 text-zinc-300"
                          : "bg-indigo-600 text-white"
                    : "bg-[#1a1d29] text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {f === "all"
                  ? `All (${allPostMortems.length})`
                  : f === "GOOD"
                    ? `Good (${goodPostMortems})`
                    : f === "BAD"
                      ? `Bad (${badPostMortems})`
                      : `Neutral (${neutralPostMortems})`}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Events list */}
      {isLoading ? (
        <div className="text-center text-zinc-500 py-12">Loading...</div>
      ) : events.length === 0 ? (
        <div className="text-center text-zinc-500 py-12">
          <p className="text-lg mb-2">
            No {tab === "debates" ? "debates" : tab === "reviews" ? "reviews" : tab === "risk_debates" ? "risk debates" : "post-mortems"} yet
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
                : tab === "post_mortems"
                  ? config?.use_llm_post_mortem
                    ? "Post-mortems will appear as trades close."
                    : "Enable Post-Mortem Analysis in Settings to start."
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
              ) : tab === "risk_debates" ? (
                <RiskDebateCard key={event.id} event={event} />
              ) : (
                <PostMortemCard key={event.id} event={event} />
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
