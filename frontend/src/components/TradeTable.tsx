import { clsx } from "clsx";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";
import type { Trade } from "../api/client";
import { formatDateTime } from "../utils/date";

interface TradeTableProps {
  trades: Trade[];
  compact?: boolean;
  testIdPrefix?: string;
}

export default function TradeTable({ trades, compact, testIdPrefix = "trade" }: TradeTableProps) {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  if (!trades.length) {
    return <div className="text-zinc-500 text-sm py-8 text-center" data-testid={`${testIdPrefix}-table-empty`}>No trades yet</div>;
  }

  const toggle = (id: number) => setExpandedId(expandedId === id ? null : id);

  return (
    <div className="overflow-x-auto" data-testid={`${testIdPrefix}-table`}>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-zinc-500 text-xs border-b border-[#2a2d3e]">
            <th className="w-6"></th>
            <th className="text-left py-2 px-3 hidden sm:table-cell">Time</th>
            <th className="text-left py-2 px-3">Market</th>
            <th className="text-left py-2 px-3 hidden md:table-cell">Strategy</th>
            <th className="text-right py-2 px-3">Side</th>
            <th className="text-right py-2 px-3 hidden sm:table-cell">Price</th>
            <th className="text-right py-2 px-3 hidden md:table-cell">Size</th>
            {!compact && <th className="text-right py-2 px-3 hidden lg:table-cell">Edge</th>}
            <th className="text-right py-2 px-3">PnL</th>
            <th className="text-right py-2 px-3">Status</th>
          </tr>
        </thead>
        <tbody data-testid={`${testIdPrefix}-table-body`}>
          {trades.map((t) => (
            <Fragment key={t.id}>
              <tr
                className="border-b border-[#2a2d3e]/50 hover:bg-white/5 cursor-pointer"
                data-testid={`${testIdPrefix}-row-${t.id}`}
                onClick={() => toggle(t.id)}
              >
                <td className="py-2 pl-2 text-zinc-500">
                  {expandedId === t.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </td>
                <td className="py-2 px-3 text-zinc-400 hidden sm:table-cell">
                  {formatDateTime(t.created_at)}
                </td>
                <td className="py-2 px-3 max-w-32 sm:max-w-48 truncate" title={t.question}>
                  {t.question}
                </td>
                <td className="py-2 px-3 hidden md:table-cell">
                  <span className="px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 text-xs">
                    {t.strategy}
                  </span>
                </td>
                <td
                  className={clsx("py-2 px-3 text-right font-medium", {
                    "text-green-400": t.side === "BUY",
                    "text-red-400": t.side === "SELL",
                  })}
                >
                  {t.side}
                </td>
                <td className="py-2 px-3 text-right hidden sm:table-cell">${t.price.toFixed(3)}</td>
                <td className="py-2 px-3 text-right hidden md:table-cell">${t.cost_usd.toFixed(2)}</td>
                {!compact && (
                  <td className="py-2 px-3 text-right hidden lg:table-cell">{(t.edge * 100).toFixed(1)}%</td>
                )}
                <td
                  className={clsx("py-2 px-3 text-right font-medium", {
                    "text-green-400": t.pnl > 0,
                    "text-red-400": t.pnl < 0,
                    "text-zinc-400": t.pnl === 0,
                  })}
                >
                  ${t.pnl.toFixed(2)}
                </td>
                <td className="py-2 px-3 text-right">
                  <span
                    className={clsx("px-2 py-0.5 rounded text-xs", {
                      "bg-green-500/20 text-green-300": t.status === "filled" || t.status === "completed",
                      "bg-yellow-500/20 text-yellow-300": t.status === "pending",
                      "bg-red-500/20 text-red-300": t.status === "cancelled" || t.status === "expired",
                    })}
                  >
                    {t.status}
                  </span>
                </td>
              </tr>
              {expandedId === t.id && (
                <tr key={`${t.id}-detail`} className="bg-[#161825]">
                  <td colSpan={compact ? 9 : 10} className="px-4 sm:px-6 py-4">
                    <TradeDetail trade={t} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TradeDetail({ trade: t }: { trade: Trade }) {
  return (
    <div className="space-y-3" data-testid={`trade-detail-${t.id}`}>
      {/* Reasoning */}
      <div>
        <div className="text-xs text-zinc-500 mb-1">Decision Reasoning</div>
        <div className="text-sm text-zinc-300 bg-[#1e2130] rounded px-3 py-2">
          {t.reasoning || "No reasoning recorded"}
        </div>
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricBox label="Estimated Prob" value={`${(t.estimated_prob * 100).toFixed(1)}%`} />
        <MetricBox label="Market Price" value={`$${t.price.toFixed(3)}`} />
        <MetricBox label="Edge" value={`${(t.edge * 100).toFixed(2)}%`} />
        <MetricBox label="Confidence" value={`${(t.confidence * 100).toFixed(0)}%`} />
        <MetricBox label="Outcome" value={t.outcome} />
        <MetricBox label="Shares" value={t.size.toFixed(1)} />
        <MetricBox label="Cost" value={`$${t.cost_usd.toFixed(2)}`} />
        <MetricBox label="Paper" value={t.is_paper ? "Yes" : "Live"} />
      </div>

      {/* Market ID */}
      <div className="text-xs text-zinc-600 font-mono truncate">
        Market: {t.market_id}
      </div>
    </div>
  );
}

function MetricBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-[#1e2130] rounded px-3 py-2">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className="text-sm font-medium text-zinc-200">{value}</div>
    </div>
  );
}
