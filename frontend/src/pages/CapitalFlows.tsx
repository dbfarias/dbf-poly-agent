import { useQuery } from "@tanstack/react-query";
import { ArrowDownLeft, ArrowUpRight, Banknote } from "lucide-react";
import { fetchCapitalFlows } from "../api/client";
import { formatDateTime } from "../utils/date";
import StatCard from "../components/StatCard";

export default function CapitalFlows() {
  const { data: flows, isLoading } = useQuery({
    queryKey: ["capital-flows"],
    queryFn: () => fetchCapitalFlows(200),
    refetchInterval: 30000,
  });

  const deposits = flows?.filter((f) => f.flow_type === "deposit") ?? [];
  const withdrawals = flows?.filter((f) => f.flow_type === "withdrawal") ?? [];
  const totalDeposited = deposits.reduce((s, f) => s + f.amount, 0);
  const totalWithdrawn = withdrawals.reduce((s, f) => s + Math.abs(f.amount), 0);
  const netFlow = totalDeposited - totalWithdrawn;

  return (
    <div className="space-y-6" data-testid="capital-flows-page">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold">Capital Flows</h2>
        <span className="text-xs text-zinc-500">
          {flows?.length ?? 0} records
        </span>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <StatCard
          title="Total Deposited"
          value={`$${totalDeposited.toFixed(2)}`}
          icon={<ArrowDownLeft size={16} />}
          trend="up"
          testId="total-deposited"
        />
        <StatCard
          title="Total Withdrawn"
          value={`$${totalWithdrawn.toFixed(2)}`}
          icon={<ArrowUpRight size={16} />}
          trend={totalWithdrawn > 0 ? "down" : "neutral"}
          testId="total-withdrawn"
        />
        <StatCard
          title="Net Capital"
          value={`$${netFlow.toFixed(2)}`}
          icon={<Banknote size={16} />}
          trend={netFlow > 0 ? "up" : netFlow < 0 ? "down" : "neutral"}
          testId="net-capital"
        />
      </div>

      {/* Flows table */}
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-zinc-500 text-sm">Loading...</div>
        ) : !flows?.length ? (
          <div className="p-8 text-center text-zinc-500 text-sm" data-testid="no-flows">
            No deposits or withdrawals recorded yet.
            <br />
            <span className="text-xs text-zinc-600 mt-1 block">
              Capital flows are automatically detected when your Polymarket balance changes or INITIAL_BANKROLL is updated.
            </span>
          </div>
        ) : (
          <table className="w-full text-sm" data-testid="flows-table">
            <thead>
              <tr className="border-b border-[#2a2d3e] text-xs text-zinc-500">
                <th className="text-left px-4 py-3 font-medium">Date</th>
                <th className="text-left px-4 py-3 font-medium">Type</th>
                <th className="text-right px-4 py-3 font-medium">Amount</th>
                <th className="text-left px-4 py-3 font-medium hidden sm:table-cell">Source</th>
                <th className="text-left px-4 py-3 font-medium hidden md:table-cell">Note</th>
                <th className="text-center px-4 py-3 font-medium hidden sm:table-cell">Mode</th>
              </tr>
            </thead>
            <tbody>
              {flows.map((flow) => (
                <tr
                  key={flow.id}
                  className="border-b border-[#2a2d3e]/50 hover:bg-white/[0.02] transition-colors"
                  data-testid={`flow-row-${flow.id}`}
                >
                  <td className="px-4 py-3 text-zinc-300">
                    {formatDateTime(flow.timestamp)}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
                        flow.flow_type === "deposit"
                          ? "bg-green-500/20 text-green-400"
                          : "bg-red-500/20 text-red-400"
                      }`}
                    >
                      {flow.flow_type === "deposit" ? (
                        <ArrowDownLeft size={12} />
                      ) : (
                        <ArrowUpRight size={12} />
                      )}
                      {flow.flow_type === "deposit" ? "Deposit" : "Withdrawal"}
                    </span>
                  </td>
                  <td
                    className={`px-4 py-3 text-right font-mono font-medium ${
                      flow.amount >= 0 ? "text-green-400" : "text-red-400"
                    }`}
                  >
                    {flow.amount >= 0 ? "+" : ""}${Math.abs(flow.amount).toFixed(2)}
                  </td>
                  <td className="px-4 py-3 text-zinc-500 hidden sm:table-cell capitalize">
                    {flow.source}
                  </td>
                  <td className="px-4 py-3 text-zinc-500 hidden md:table-cell truncate max-w-[200px]">
                    {flow.note || "—"}
                  </td>
                  <td className="px-4 py-3 text-center hidden sm:table-cell">
                    <span
                      className={`px-2 py-0.5 rounded text-xs ${
                        flow.is_paper
                          ? "bg-yellow-500/20 text-yellow-300"
                          : "bg-green-500/20 text-green-300"
                      }`}
                    >
                      {flow.is_paper ? "Paper" : "Live"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
