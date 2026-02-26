import { useQuery } from "@tanstack/react-query";
import { clsx } from "clsx";
import { fetchMarkets } from "../api/client";

export default function Markets() {
  const { data: markets, isLoading } = useQuery({
    queryKey: ["markets"],
    queryFn: () => fetchMarkets(30),
    refetchInterval: 60000,
  });

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-bold">Market Scanner</h2>

      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e]">
        {isLoading ? (
          <div className="p-8 text-center text-zinc-500">Scanning markets...</div>
        ) : !markets?.length ? (
          <div className="p-8 text-center text-zinc-500">No opportunities found</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-zinc-500 text-xs border-b border-[#2a2d3e]">
                  <th className="text-left py-3 px-4">Market</th>
                  <th className="text-right py-3 px-4">YES</th>
                  <th className="text-right py-3 px-4">NO</th>
                  <th className="text-right py-3 px-4">Volume</th>
                  <th className="text-right py-3 px-4">Hours Left</th>
                  <th className="text-left py-3 px-4">Strategy</th>
                  <th className="text-right py-3 px-4">Edge</th>
                  <th className="text-right py-3 px-4">Confidence</th>
                </tr>
              </thead>
              <tbody>
                {markets.map((m, i) => (
                  <tr
                    key={`${m.market_id}-${i}`}
                    className="border-b border-[#2a2d3e]/50 hover:bg-white/5"
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
