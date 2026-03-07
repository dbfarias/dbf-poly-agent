import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { fetchActivity, fetchConfig } from "../api/client";
import type { ActivityEvent } from "../api/client";

type TabType = "debates" | "reviews";

function DebateCard({ event }: { event: ActivityEvent }) {
  const meta = event.metadata as Record<string, unknown>;
  const approved = meta.approved as boolean;
  const proposerVerdict = (meta.proposer_verdict as string) ?? "?";
  const proposerConf = (meta.proposer_confidence as number) ?? 0;
  const proposerReasoning = (meta.proposer_reasoning as string) ?? "";
  const challengerVerdict = (meta.challenger_verdict as string) ?? "?";
  const challengerRisk = (meta.challenger_risk as string) ?? "?";
  const challengerObjections = (meta.challenger_objections as string) ?? "";
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
            {event.title.replace(/^AI Review: (HOLD|EXIT) \((LOW|MEDIUM|HIGH)\) — /, "")}
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

export default function AIDebates() {
  const [tab, setTab] = useState<TabType>("debates");

  const { data: config } = useQuery({
    queryKey: ["config"],
    queryFn: fetchConfig,
  });

  const { data: debateData, isLoading: debatesLoading } = useQuery({
    queryKey: ["activity", "llm_debate"],
    queryFn: () => fetchActivity({ event_type: "llm_debate", limit: 50 }),
    refetchInterval: 15000,
  });

  const { data: reviewData, isLoading: reviewsLoading } = useQuery({
    queryKey: ["activity", "llm_review"],
    queryFn: () => fetchActivity({ event_type: "llm_review", limit: 50 }),
    refetchInterval: 15000,
  });

  const debates = debateData?.events ?? [];
  const reviews = reviewData?.events ?? [];
  const isLoading = tab === "debates" ? debatesLoading : reviewsLoading;
  const events = tab === "debates" ? debates : reviews;

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

  return (
    <div className="space-y-6 max-w-3xl mx-auto" data-testid="ai-debates-page">
      <h2 className="text-xl font-bold">AI Debates</h2>

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
          className={`flex-1 px-4 py-2 rounded text-sm font-medium transition-colors ${
            tab === "debates"
              ? "bg-indigo-600 text-white"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Trade Debates
          {debates.length > 0 && (
            <span className="ml-2 text-xs opacity-70">
              {approvedCount} approved / {rejectedCount} rejected
            </span>
          )}
        </button>
        <button
          onClick={() => setTab("reviews")}
          className={`flex-1 px-4 py-2 rounded text-sm font-medium transition-colors ${
            tab === "reviews"
              ? "bg-indigo-600 text-white"
              : "text-zinc-400 hover:text-zinc-200"
          }`}
        >
          Position Reviews
          {reviews.length > 0 && (
            <span className="ml-2 text-xs opacity-70">
              {holdReviews}H / {reduceReviews}R / {exitReviews}E / {increaseReviews}I
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

      {/* Events list */}
      {isLoading ? (
        <div className="text-center text-zinc-500 py-12">Loading...</div>
      ) : events.length === 0 ? (
        <div className="text-center text-zinc-500 py-12">
          <p className="text-lg mb-2">No {tab === "debates" ? "debates" : "reviews"} yet</p>
          <p className="text-sm">
            {tab === "debates"
              ? config?.use_llm_debate
                ? "Debates will appear here as the bot evaluates trade signals."
                : "Enable the AI Debate Gate in Settings to start."
              : config?.use_llm_reviewer
                ? "Reviews will appear as the AI checks open positions."
                : "Enable the Position Reviewer in Settings to start."}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {events.map((event) =>
            tab === "debates" ? (
              <DebateCard key={event.id} event={event} />
            ) : (
              <ReviewCard key={event.id} event={event} />
            ),
          )}
        </div>
      )}
    </div>
  );
}
