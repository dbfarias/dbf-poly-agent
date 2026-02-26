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
import HelpTooltip from "../components/HelpTooltip";

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
    <div className="space-y-6 max-w-2xl" data-testid="settings-page">
      <h2 className="text-xl font-bold">Settings</h2>

      {/* Trading Controls */}
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5" data-testid="trading-controls">
        <h3 className="font-medium text-white mb-4">Trading Controls</h3>
        <div className="flex items-center gap-4">
          <div className="flex-1">
            <div className="text-sm text-zinc-400 flex items-center">
              Mode
              <HelpTooltip text="PAPER mode simulates trades without real money. LIVE mode executes real orders on Polymarket. Change this in the .env file." />
            </div>
            <div className="text-lg font-bold text-white mt-1" data-testid="trading-mode">
              {config?.trading_mode.toUpperCase()}
            </div>
          </div>
          <div className="flex-1">
            <div className="text-sm text-zinc-400 flex items-center">
              Status
              <HelpTooltip text="Whether the bot is currently running or paused. Use the button to manually pause/resume trading." />
            </div>
            <div
              className={`text-lg font-bold mt-1 ${risk?.is_paused ? "text-red-400" : "text-green-400"}`}
              data-testid="trading-status"
            >
              {risk?.is_paused ? "PAUSED" : "RUNNING"}
            </div>
          </div>
          <div>
            {risk?.is_paused ? (
              <button
                onClick={() => resumeMut.mutate()}
                className="px-4 py-2 rounded bg-green-600 text-white text-sm font-medium hover:bg-green-700"
                data-testid="resume-btn"
              >
                Resume
              </button>
            ) : (
              <button
                onClick={() => pauseMut.mutate()}
                className="px-4 py-2 rounded bg-red-600 text-white text-sm font-medium hover:bg-red-700"
                data-testid="pause-btn"
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
            <label className="text-sm text-zinc-400 flex items-center">
              Scan Interval (seconds)
              <HelpTooltip text="How often the bot scans Polymarket for new trading opportunities. Lower values find opportunities faster but use more API calls." />
            </label>
            <input
              type="number"
              defaultValue={config?.scan_interval_seconds}
              onChange={(e) => setScanInterval(Number(e.target.value))}
              className="mt-1 w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-2 text-white text-sm"
              data-testid="input-scan-interval"
            />
          </div>
          <div>
            <label className="text-sm text-zinc-400 flex items-center">
              Max Daily Loss (%)
              <HelpTooltip text="Maximum percentage of your bankroll you're willing to lose in a single day. Trading pauses automatically if this limit is hit." />
            </label>
            <input
              type="number"
              step="1"
              defaultValue={config ? config.max_daily_loss_pct * 100 : 10}
              onChange={(e) => setMaxLoss(Number(e.target.value))}
              className="mt-1 w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-2 text-white text-sm"
              data-testid="input-max-daily-loss"
            />
          </div>
          <div>
            <label className="text-sm text-zinc-400 flex items-center">
              Max Drawdown (%)
              <HelpTooltip text="Maximum allowed drop from your portfolio's peak value. If exceeded, all trading stops. This is your ultimate safety net." />
            </label>
            <input
              type="number"
              step="1"
              defaultValue={config ? config.max_drawdown_pct * 100 : 25}
              onChange={(e) => setMaxDD(Number(e.target.value))}
              className="mt-1 w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-2 text-white text-sm"
              data-testid="input-max-drawdown"
            />
          </div>
          <button
            onClick={saveConfig}
            className="px-4 py-2 rounded bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700"
            data-testid="save-config-btn"
          >
            Save Changes
          </button>
        </div>
      </div>

      {/* System Info */}
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5" data-testid="system-info">
        <h3 className="font-medium text-white mb-4">System Info</h3>
        {health && (
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-zinc-400 flex items-center">
                Status
                <HelpTooltip text="Overall system health. 'ok' means the API server and bot are running normally." />
              </span>
              <span className="text-green-400" data-testid="system-status">{health.status}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-400 flex items-center">
                Uptime
                <HelpTooltip text="How long the bot has been running continuously since last restart." />
              </span>
              <span className="text-white" data-testid="system-uptime">{(health.uptime_seconds / 3600).toFixed(1)}h</span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-400 flex items-center">
                Cycle Count
                <HelpTooltip text="Number of market scan cycles completed. Each cycle scans all markets, evaluates strategies, and places trades if opportunities are found." />
              </span>
              <span className="text-white" data-testid="system-cycle-count">{health.cycle_count}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-400 flex items-center">
                Engine
                <HelpTooltip text="The trading engine is the core loop that scans markets and executes trades. 'Running' means it's actively scanning." />
              </span>
              <span
                className={health.engine_running ? "text-green-400" : "text-red-400"}
                data-testid="system-engine"
              >
                {health.engine_running ? "Running" : "Stopped"}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
