import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { fetchMarkets } from "../api/client";
import HelpTooltip from "../components/HelpTooltip";

export default function Markets() {
  const { data: markets, isLoading } = useQuery({
    queryKey: ["markets"],
    queryFn: () => fetchMarkets(30),
    refetchInterval: 60000,
  });

  return (
    <div className="space-y-6" data-testid="markets-page">
      <h2 className="text-xl font-bold">Market Scanner</h2>

      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e]">
        {isLoading ? (
          <div className="p-8 text-center text-zinc-500" data-testid="markets-loading">Scanning markets...</div>
        ) : !markets?.length ? (
          <div className="p-8 text-center text-zinc-500" data-testid="markets-empty">No opportunities found</div>
        ) : (
          <div className="overflow-x-auto" data-testid="markets-table">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-zinc-500 text-xs border-b border-[#2a2d3e]">
                  <th className="text-left py-3 px-4">Market</th>
                  <th className="text-right py-3 px-4">
                    <span className="inline-flex items-center">YES <HelpTooltip text="Current price of the YES outcome. $0.90 means the market thinks there's a 90% chance of YES." size={11} /></span>
                  </th>
                  <th className="text-right py-3 px-4">
                    <span className="inline-flex items-center">NO <HelpTooltip text="Current price of the NO outcome. YES + NO should roughly equal $1.00." size={11} /></span>
                  </th>
                  <th className="text-right py-3 px-4">
                    <span className="inline-flex items-center">Volume <HelpTooltip text="Total amount traded on this market. Higher volume means more liquidity and tighter spreads." size={11} /></span>
                  </th>
                  <th className="text-right py-3 px-4">
                    <span className="inline-flex items-center">Hours Left <HelpTooltip text="Estimated hours until the market resolves (outcome is determined). Shorter timeframes reduce uncertainty." size={11} /></span>
                  </th>
                  <th className="text-left py-3 px-4">
                    <span className="inline-flex items-center">Strategy <HelpTooltip text="Which bot strategy detected this opportunity: time_decay (expiring markets), arbitrage (price gaps), value_betting (mispriced odds), market_making (spread capture)." size={11} /></span>
                  </th>
                  <th className="text-right py-3 px-4">
                    <span className="inline-flex items-center">Edge <HelpTooltip text="The expected profit margin on this trade as a percentage. Higher edge = more potential profit. Only trades above the minimum edge threshold are executed." size={11} /></span>
                  </th>
                  <th className="text-right py-3 px-4">
                    <span className="inline-flex items-center">Confidence <HelpTooltip text="How confident the bot is in its prediction. Based on market data, volume, and time to resolution." size={11} /></span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {markets.map((m, i) => (
                  <tr
                    key={`${m.market_id}-${i}`}
                    className="border-b border-[#2a2d3e]/50 hover:bg-white/5"
                    data-testid={`market-row-${i}`}
                  >
                    <td className="py-2.5 px-4 max-w-64 truncate" title={m.question}>
                      {m.question}
                    </td>
                    <td className="py-2.5 px-4 text-right text-green-400">
                      ${m.yes_price.toFixed(2)}
                    </td>
                    <td className="py-2.5 px-4 text-right text-red-400">
                      ${m.no_price.toFixed(2)}
                    </td>
                    <td className="py-2.5 px-4 text-right text-zinc-400">
                      ${(m.volume / 1000).toFixed(0)}k
                    </td>
                    <td className="py-2.5 px-4 text-right text-zinc-400">
                      {m.hours_to_resolution?.toFixed(0) ?? "—"}
                    </td>
                    <td className="py-2.5 px-4">
                      {m.signal_strategy && (
                        <span className="px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 text-xs">
                          {m.signal_strategy}
                        </span>
                      )}
                    </td>
                    <td
                      className={clsx("py-2.5 px-4 text-right font-medium", {
                        "text-green-400": m.signal_edge > 0.03,
                        "text-yellow-400": m.signal_edge > 0.01,
                        "text-zinc-400": m.signal_edge <= 0.01,
                      })}
                      data-testid={`market-edge-${i}`}
                    >
                      {m.signal_edge > 0 ? `${(m.signal_edge * 100).toFixed(1)}%` : "—"}
                    </td>
                    <td className="py-2.5 px-4 text-right text-zinc-400">
                      {m.signal_confidence > 0
                        ? `${(m.signal_confidence * 100).toFixed(0)}%`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
