import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  fetchConfig,
  fetchHealth,
  fetchRiskMetrics,
  pauseTrading,
  resumeTrading,
  updateConfig,
} from "../api/client";

export default function Settings() {
  const queryClient = useQueryClient();

  const { data: config } = useQuery({
    queryKey: ["config"],
    queryFn: fetchConfig,
  });
  const { data: risk } = useQuery({
    queryKey: ["risk-metrics"],
    queryFn: fetchRiskMetrics,
    refetchInterval: 5000,
  });
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 10000,
  });

  const [scanInterval, setScanInterval] = useState<number | null>(null);
  const [maxLoss, setMaxLoss] = useState<number | null>(null);
  const [maxDD, setMaxDD] = useState<number | null>(null);

  const pauseMut = useMutation({
    mutationFn: pauseTrading,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["risk-metrics"] }),
  });
  const resumeMut = useMutation({
    mutationFn: resumeTrading,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["risk-metrics"] }),
  });
  const configMut = useMutation({
    mutationFn: updateConfig,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["config"] }),
  });

  const saveConfig = () => {
    const update: Record<string, number> = {};
    if (scanInterval !== null) update.scan_interval_seconds = scanInterval;
    if (maxLoss !== null) update.max_daily_loss_pct = maxLoss / 100;
    if (maxDD !== null) update.max_drawdown_pct = maxDD / 100;
    if (Object.keys(update).length > 0) configMut.mutate(update);
  };

  return (
    <div className="space-y-6 max-w-2xl">
      <h2 className="text-xl font-bold">Settings</h2>

      {/* Trading Controls */}
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
        <h3 className="font-medium text-white mb-4">Trading Controls</h3>
        <div className="flex items-center gap-4">
          <div className="flex-1">
            <div className="text-sm text-zinc-400">Mode</div>
            <div className="text-lg font-bold text-white mt-1">
              {config?.trading_mode.toUpperCase()}
            </div>
          </div>
          <div className="flex-1">
            <div className="text-sm text-zinc-400">Status</div>
            <div className={`text-lg font-bold mt-1 ${risk?.is_paused ? "text-red-400" : "text-green-400"}`}>
              {risk?.is_paused ? "PAUSED" : "RUNNING"}
            </div>
          </div>
          <div>
            {risk?.is_paused ? (
              <button
                onClick={() => resumeMut.mutate()}
                className="px-4 py-2 rounded bg-green-600 text-white text-sm font-medium hover:bg-green-700"
              >
                Resume
              </button>
            ) : (
              <button
                onClick={() => pauseMut.mutate()}
                className="px-4 py-2 rounded bg-red-600 text-white text-sm font-medium hover:bg-red-700"
              >
                Pause
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Risk Parameters */}
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
        <h3 className="font-medium text-white mb-4">Risk Parameters</h3>
        <div className="space-y-4">
          <div>
            <label className="text-sm text-zinc-400">Scan Interval (seconds)</label>
            <input
              type="number"
              defaultValue={config?.scan_interval_seconds}
              onChange={(e) => setScanInterval(Number(e.target.value))}
              className="mt-1 w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-2 text-white text-sm"
            />
          </div>
          <div>
            <label className="text-sm text-zinc-400">Max Daily Loss (%)</label>
            <input
              type="number"
              step="1"
              defaultValue={config ? config.max_daily_loss_pct * 100 : 10}
              onChange={(e) => setMaxLoss(Number(e.target.value))}
              className="mt-1 w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-2 text-white text-sm"
            />
          </div>
          <div>
            <label className="text-sm text-zinc-400">Max Drawdown (%)</label>
            <input
              type="number"
              step="1"
              defaultValue={config ? config.max_drawdown_pct * 100 : 25}
              onChange={(e) => setMaxDD(Number(e.target.value))}
              className="mt-1 w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-2 text-white text-sm"
            />
          </div>
          <button
            onClick={saveConfig}
            className="px-4 py-2 rounded bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700"
          >
            Save Changes
          </button>
        </div>
      </div>

      {/* System Info */}
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
        <h3 className="font-medium text-white mb-4">System Info</h3>
        {health && (
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-zinc-400">Status</span>
              <span className="text-green-400">{health.status}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-400">Uptime</span>
              <span className="text-white">{(health.uptime_seconds / 3600).toFixed(1)}h</span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-400">Cycle Count</span>
              <span className="text-white">{health.cycle_count}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-400">Engine</span>
              <span className={health.engine_running ? "text-green-400" : "text-red-400"}>
                {health.engine_running ? "Running" : "Stopped"}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
