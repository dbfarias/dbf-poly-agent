import { clsx } from "clsx";
import type { Trade } from "../api/client";

interface TradeTableProps {
  trades: Trade[];
  compact?: boolean;
}

export default function TradeTable({ trades, compact }: TradeTableProps) {
  if (!trades.length) {
    return <div className="text-zinc-500 text-sm py-8 text-center">No trades yet</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-zinc-500 text-xs border-b border-[#2a2d3e]">
            <th className="text-left py-2 px-3">Time</th>
            <th className="text-left py-2 px-3">Market</th>
            <th className="text-left py-2 px-3">Strategy</th>
            <th className="text-right py-2 px-3">Side</th>
            <th className="text-right py-2 px-3">Price</th>
            <th className="text-right py-2 px-3">Size</th>
            {!compact && <th className="text-right py-2 px-3">Edge</th>}
            <th className="text-right py-2 px-3">PnL</th>
            <th className="text-right py-2 px-3">Status</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="border-b border-[#2a2d3e]/50 hover:bg-white/5">
              <td className="py-2 px-3 text-zinc-400">
                {new Date(t.created_at).toLocaleString()}
              </td>
              <td className="py-2 px-3 max-w-48 truncate" title={t.question}>
                {t.question}
              </td>
              <td className="py-2 px-3">
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
              <td className="py-2 px-3 text-right">${t.price.toFixed(3)}</td>
              <td className="py-2 px-3 text-right">${t.cost_usd.toFixed(2)}</td>
              {!compact && (
                <td className="py-2 px-3 text-right">{(t.edge * 100).toFixed(1)}%</td>
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
                    "bg-red-500/20 text-red-300": t.status === "cancelled",
                  })}
                >
                  {t.status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
