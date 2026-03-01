import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import {
  fetchConfig,
  fetchHealth,
  fetchRiskMetrics,
  pauseTrading,
  resetRiskState,
  resumeTrading,
  updateConfig,
} from "../api/client";
import HelpTooltip from "../components/HelpTooltip";

/** Toast notification state */
interface Toast {
  message: string;
  type: "success" | "error";
  id: number;
}

let toastId = 0;

/** Inline number input with label */
function NumberField({
  label,
  tooltip,
  value,
  onChange,
  step = 1,
  min,
  max,
  suffix = "",
  testId,
}: {
  label: string;
  tooltip: string;
  value: number | undefined;
  onChange: (v: number) => void;
  step?: number;
  min?: number;
  max?: number;
  suffix?: string;
  testId?: string;
}) {
  return (
    <div>
      <label className="text-sm text-zinc-400 flex items-center">
        {label}
        {suffix && <span className="ml-1 text-zinc-600">{suffix}</span>}
        <HelpTooltip text={tooltip} />
      </label>
      <input
        type="number"
        step={step}
        min={min}
        max={max}
        value={value ?? ""}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-1 w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-2 text-white text-sm"
        data-testid={testId}
      />
    </div>
  );
}

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

  // Toast notifications
  const [toasts, setToasts] = useState<Toast[]>([]);

  const addToast = useCallback((message: string, type: "success" | "error") => {
    const id = ++toastId;
    setToasts((prev) => [...prev, { message, type, id }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);

  // Local state for editable fields — initialized from server config
  const [general, setGeneral] = useState({
    scan_interval_seconds: 0,
    max_daily_loss_pct: 0,
    max_drawdown_pct: 0,
  });
  const [tier, setTier] = useState<Record<string, number>>({});
  const [quality, setQuality] = useState<Record<string, number>>({});
  const [strategyParams, setStrategyParams] = useState<
    Record<string, Record<string, number>>
  >({});
  const [disabledStrategies, setDisabledStrategies] = useState<Set<string>>(
    new Set(),
  );

  // Sync local state when config loads
  useEffect(() => {
    if (!config) return;
    setGeneral({
      scan_interval_seconds: config.scan_interval_seconds,
      max_daily_loss_pct: config.max_daily_loss_pct * 100,
      max_drawdown_pct: config.max_drawdown_pct * 100,
    });
    setTier(config.tier_config);
    setQuality(config.quality_params);
    setStrategyParams(config.strategy_params);
    setDisabledStrategies(new Set(config.disabled_strategies ?? []));
  }, [config]);

  const pauseMut = useMutation({
    mutationFn: pauseTrading,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["risk-metrics"] });
      addToast("Trading paused", "success");
    },
    onError: () => addToast("Failed to pause trading", "error"),
  });

  const resumeMut = useMutation({
    mutationFn: resumeTrading,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["risk-metrics"] });
      addToast("Trading resumed", "success");
    },
    onError: () => addToast("Failed to resume trading", "error"),
  });

  const resetMut = useMutation({
    mutationFn: resetRiskState,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["risk-metrics"] });
      queryClient.invalidateQueries({ queryKey: ["portfolio"] });
      addToast(
        `Risk state reset. Equity: $${data.equity.toFixed(2)}`,
        "success",
      );
    },
    onError: () => addToast("Failed to reset risk state", "error"),
  });

  const configMut = useMutation({
    mutationFn: updateConfig,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["config"] });
      const n = data.changes?.length ?? 0;
      addToast(`Saved ${n} change${n !== 1 ? "s" : ""}`, "success");
    },
    onError: () => addToast("Failed to save settings", "error"),
  });

  const saveGeneral = () => {
    configMut.mutate({
      scan_interval_seconds: general.scan_interval_seconds,
      max_daily_loss_pct: general.max_daily_loss_pct / 100,
      max_drawdown_pct: general.max_drawdown_pct / 100,
    });
  };

  const saveTierConfig = () => {
    configMut.mutate({ tier_config: tier });
  };

  const saveQuality = () => {
    configMut.mutate({ quality_params: quality });
  };

  const saveStrategy = (name: string) => {
    configMut.mutate({ strategy_params: { [name]: strategyParams[name] } });
  };

  const updateTierField = (key: string, value: number) => {
    setTier((prev) => ({ ...prev, [key]: value }));
  };

  const updateQualityField = (key: string, value: number) => {
    setQuality((prev) => ({ ...prev, [key]: value }));
  };

  const updateStrategyField = (
    strategy: string,
    key: string,
    value: number,
  ) => {
    setStrategyParams((prev) => ({
      ...prev,
      [strategy]: { ...prev[strategy], [key]: value },
    }));
  };

  const TIER_LABELS: Record<string, [string, string]> = {
    max_positions: ["Max Positions", "Maximum number of simultaneous open positions."],
    max_per_position_pct: ["Max Per Position (%)", "Maximum percentage of bankroll in a single position."],
    max_deployed_pct: ["Max Deployed (%)", "Maximum percentage of bankroll deployed across all positions."],
    daily_loss_limit_pct: ["Daily Loss Limit (%)", "Trading pauses if daily losses exceed this percentage."],
    max_drawdown_pct: ["Max Drawdown (%)", "Trading stops if portfolio drops this much from peak."],
    min_edge_pct: ["Min Edge (%)", "Minimum edge (estimated_prob - market_price) required to trade."],
    min_win_prob: ["Min Win Probability", "Minimum estimated win probability to consider a trade."],
    max_per_category_pct: ["Max Per Category (%)", "Maximum exposure to a single market category."],
    kelly_fraction: ["Kelly Fraction", "Fraction of Kelly criterion used for position sizing (0.25 = quarter-Kelly)."],
  };

  const QUALITY_LABELS: Record<string, [string, string]> = {
    max_spread: ["Max Spread ($)", "Maximum bid-ask spread allowed. Markets with wider spreads are filtered out."],
    max_category_positions: ["Max Category Positions", "Maximum open positions allowed per market category."],
    min_bid_ratio: ["Min Bid Ratio", "Best bid must be at least this fraction of fair price. Prevents entering markets with no exit liquidity (e.g. bids at $0.001)."],
    min_volume_24h: ["Min 24h Volume ($)", "Minimum trading volume in the last 24 hours. Filters out dead/inactive markets."],
    stop_loss_pct: ["Stop Loss (%)", "Exit a position if it loses this percentage from entry price."],
    near_worthless_price: ["Near Worthless Price ($)", "Always exit positions below this price."],
    default_exit_price: ["Default Exit Price ($)", "Exit threshold for positions with no strategy-specific exit rule."],
  };

  const STRATEGY_LABELS: Record<string, [string, string]> = {
    MAX_HOURS_TO_RESOLUTION: ["Max Hours to Resolution", "Only trade markets resolving within this timeframe."],
    MIN_IMPLIED_PROB: ["Min Implied Probability", "Minimum implied probability (from price) to consider."],
    MAX_PRICE: ["Max Price ($)", "Maximum price to buy at. Higher = closer to certainty."],
    MIN_PRICE: ["Min Price ($)", "Minimum price to buy at. Lower = more speculative."],
    MIN_EDGE: ["Min Edge", "Minimum edge (estimated prob - market price) for this strategy."],
    CONFIDENCE_BASE: ["Base Confidence", "Starting confidence level before bonuses are applied."],
    MIN_ARB_EDGE: ["Min Arb Edge", "Minimum arbitrage edge (sum of prices - 1.0) to trade."],
    MIN_SPREAD: ["Min Spread ($)", "Minimum spread required for market making."],
    MAX_SPREAD: ["Max Spread ($)", "Maximum spread for market making positions."],
    IMBALANCE_THRESHOLD: ["Imbalance Threshold", "Price imbalance threshold for value betting signals."],
    MIN_DIVERGENCE_PCT: ["Min Divergence (%)", "Minimum divergence between external data and contract price to trigger a trade."],
    TAKE_PROFIT_PCT: ["Take Profit (%)", "Exit position when profit reaches this percentage."],
    STOP_LOSS_PCT: ["Stop Loss (%)", "Exit position when loss reaches this percentage."],
    MAX_HOLD_HOURS_CRYPTO: ["Max Hold Hours (Crypto)", "Maximum hours to hold a crypto-related divergence trade."],
    MAX_HOLD_HOURS_OTHER: ["Max Hold Hours (Other)", "Maximum hours to hold a non-crypto divergence trade."],
    MIN_MOMENTUM: ["Min Momentum", "Minimum price momentum (consecutive rising ticks) to trigger a swing trade."],
    MAX_HOLD_HOURS: ["Max Hold Hours", "Maximum hours to hold a position before forced exit."],
    MIN_HOURS_LEFT: ["Min Hours Left", "Minimum hours to market resolution for entry."],
  };

  const isTierPct = (key: string) =>
    key.endsWith("_pct") || key === "kelly_fraction" || key === "min_win_prob";

  return (
    <div className="space-y-6 max-w-2xl mx-auto" data-testid="settings-page">
      <h2 className="text-xl font-bold">Settings</h2>

      {/* Toast notifications */}
      <div className="fixed top-4 right-4 z-50 space-y-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`px-4 py-2 rounded-lg shadow-lg text-sm font-medium animate-fade-in ${
              t.type === "success"
                ? "bg-green-600 text-white"
                : "bg-red-600 text-white"
            }`}
          >
            {t.type === "success" ? "OK" : "Error"} &mdash; {t.message}
          </div>
        ))}
      </div>

      {/* Trading Controls */}
      <div
        className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5"
        data-testid="trading-controls"
      >
        <h3 className="font-medium text-white mb-4">Trading Controls</h3>
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4">
          <div className="flex items-center gap-4 sm:flex-1 w-full sm:w-auto">
            <div className="flex-1">
              <div className="text-sm text-zinc-400 flex items-center">
                Mode
                <HelpTooltip text="PAPER mode simulates trades without real money. LIVE mode executes real orders on Polymarket. Change this in the .env file." />
              </div>
              <div
                className="text-lg font-bold text-white mt-1"
                data-testid="trading-mode"
              >
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
            <div className="flex-1">
              <div className="text-sm text-zinc-400 flex items-center">
                Tier
                <HelpTooltip text="Capital tier determines risk parameters. Tier 1: $5-$25, Tier 2: $25-$100, Tier 3: $100+." />
              </div>
              <div className="text-lg font-bold text-indigo-400 mt-1">
                {config?.current_tier?.toUpperCase()}
              </div>
            </div>
          </div>
          <div className="flex gap-2 w-full sm:w-auto">
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
            <button
              onClick={() => {
                if (
                  window.confirm(
                    "Reset daily PnL and peak equity to current values?",
                  )
                ) {
                  resetMut.mutate();
                }
              }}
              className="px-4 py-2 rounded bg-amber-600 text-white text-sm font-medium hover:bg-amber-700"
              data-testid="reset-risk-btn"
            >
              Reset PnL
            </button>
          </div>
        </div>
      </div>

      {/* Strategy Toggles */}
      <div
        className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5"
        data-testid="strategy-toggles"
      >
        <h3 className="font-medium text-white mb-1">Strategy Toggles</h3>
        <p className="text-xs text-zinc-500 mb-4">
          Enable or disable individual strategies. Disabled strategies will not
          scan for opportunities.
        </p>
        <div className="space-y-3">
          {[
            { name: "arbitrage", label: "Arbitrage" },
            { name: "time_decay", label: "Time Decay" },
            { name: "price_divergence", label: "Price Divergence" },
            { name: "swing_trading", label: "Swing Trading" },
            { name: "value_betting", label: "Value Betting" },
            { name: "market_making", label: "Market Making" },
          ].map(({ name, label }) => {
            const isEnabled = !disabledStrategies.has(name);
            return (
              <div
                key={name}
                className="flex items-center justify-between"
                data-testid={`strategy-toggle-${name}`}
              >
                <span className="text-sm text-zinc-300">{label}</span>
                <button
                  onClick={() => {
                    const next = new Set(disabledStrategies);
                    if (isEnabled) {
                      next.add(name);
                    } else {
                      next.delete(name);
                    }
                    setDisabledStrategies(next);
                    configMut.mutate({
                      disabled_strategies: Array.from(next),
                    });
                  }}
                  className={`relative w-11 h-6 rounded-full transition-colors ${
                    isEnabled ? "bg-green-600" : "bg-zinc-700"
                  }`}
                  data-testid={`toggle-${name}`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${
                      isEnabled ? "translate-x-5" : "translate-x-0"
                    }`}
                  />
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* General Settings */}
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
        <h3 className="font-medium text-white mb-4">General</h3>
        <div className="space-y-4">
          <NumberField
            label="Scan Interval"
            suffix="(seconds)"
            tooltip="How often the bot scans for new trading opportunities."
            value={general.scan_interval_seconds}
            onChange={(v) =>
              setGeneral((p) => ({ ...p, scan_interval_seconds: v }))
            }
            min={5}
            max={3600}
            testId="input-scan-interval"
          />
          <NumberField
            label="Max Daily Loss"
            suffix="(%)"
            tooltip="Trading pauses automatically if daily losses exceed this percentage."
            value={general.max_daily_loss_pct}
            onChange={(v) =>
              setGeneral((p) => ({ ...p, max_daily_loss_pct: v }))
            }
            step={1}
            min={1}
            max={50}
            testId="input-max-daily-loss"
          />
          <NumberField
            label="Max Drawdown"
            suffix="(%)"
            tooltip="Maximum allowed drop from portfolio's peak value."
            value={general.max_drawdown_pct}
            onChange={(v) =>
              setGeneral((p) => ({ ...p, max_drawdown_pct: v }))
            }
            step={1}
            min={1}
            max={50}
            testId="input-max-drawdown"
          />
          <button
            onClick={saveGeneral}
            disabled={configMut.isPending}
            className="px-4 py-2 rounded bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
            data-testid="save-general-btn"
          >
            {configMut.isPending ? "Saving..." : "Save General"}
          </button>
        </div>
      </div>

      {/* Tier Config */}
      {Object.keys(tier).length > 0 && (
        <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
          <h3 className="font-medium text-white mb-1">
            Risk Parameters{" "}
            <span className="text-indigo-400">
              ({config?.current_tier?.toUpperCase()})
            </span>
          </h3>
          <p className="text-xs text-zinc-500 mb-4">
            These apply to the current capital tier. Changes take effect
            immediately.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {Object.entries(tier).map(([key, value]) => {
              const [label, tooltip] = TIER_LABELS[key] ?? [key, ""];
              return (
                <NumberField
                  key={key}
                  label={label}
                  tooltip={tooltip}
                  value={isTierPct(key) ? value * 100 : value}
                  onChange={(v) =>
                    updateTierField(key, isTierPct(key) ? v / 100 : v)
                  }
                  step={isTierPct(key) ? 1 : 1}
                  testId={`tier-${key}`}
                />
              );
            })}
          </div>
          <button
            onClick={saveTierConfig}
            disabled={configMut.isPending}
            className="mt-4 px-4 py-2 rounded bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
            data-testid="save-tier-btn"
          >
            {configMut.isPending ? "Saving..." : "Save Risk Params"}
          </button>
        </div>
      )}

      {/* Quality Filters */}
      {Object.keys(quality).length > 0 && (
        <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5">
          <h3 className="font-medium text-white mb-1">Quality Filters</h3>
          <p className="text-xs text-zinc-500 mb-4">
            Market quality and stop-loss thresholds.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {Object.entries(quality).map(([key, value]) => {
              const [label, tooltip] = QUALITY_LABELS[key] ?? [key, ""];
              const isPct = key.endsWith("_pct");
              return (
                <NumberField
                  key={key}
                  label={label}
                  tooltip={tooltip}
                  value={isPct ? value * 100 : value}
                  onChange={(v) =>
                    updateQualityField(key, isPct ? v / 100 : v)
                  }
                  step={isPct ? 1 : 0.01}
                  testId={`quality-${key}`}
                />
              );
            })}
          </div>
          <button
            onClick={saveQuality}
            disabled={configMut.isPending}
            className="mt-4 px-4 py-2 rounded bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
            data-testid="save-quality-btn"
          >
            {configMut.isPending ? "Saving..." : "Save Quality Filters"}
          </button>
        </div>
      )}

      {/* Strategy Parameters */}
      {Object.entries(strategyParams).map(([name, params]) => (
        <div
          key={name}
          className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5"
        >
          <h3 className="font-medium text-white mb-1">
            Strategy:{" "}
            <span className="text-indigo-400">
              {name.replace(/_/g, " ")}
            </span>
          </h3>
          <p className="text-xs text-zinc-500 mb-4">
            Parameters for the {name} strategy. Changes take effect next cycle.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {Object.entries(params).map(([key, value]) => {
              const [label, tooltip] = STRATEGY_LABELS[key] ?? [key, ""];
              return (
                <NumberField
                  key={key}
                  label={label}
                  tooltip={tooltip}
                  value={value}
                  onChange={(v) => updateStrategyField(name, key, v)}
                  step={0.01}
                  testId={`strategy-${name}-${key}`}
                />
              );
            })}
          </div>
          <button
            onClick={() => saveStrategy(name)}
            disabled={configMut.isPending}
            className="mt-4 px-4 py-2 rounded bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
            data-testid={`save-strategy-${name}-btn`}
          >
            {configMut.isPending ? "Saving..." : `Save ${name}`}
          </button>
        </div>
      ))}

      {/* System Info */}
      <div
        className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5"
        data-testid="system-info"
      >
        <h3 className="font-medium text-white mb-4">System Info</h3>
        {health && (
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-zinc-400 flex items-center">
                Status
                <HelpTooltip text="Overall system health." />
              </span>
              <span className="text-green-400" data-testid="system-status">
                {health.status}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-400 flex items-center">
                Uptime
                <HelpTooltip text="How long the bot has been running since last restart." />
              </span>
              <span className="text-white" data-testid="system-uptime">
                {(health.uptime_seconds / 3600).toFixed(1)}h
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-400 flex items-center">
                Cycle Count
                <HelpTooltip text="Number of market scan cycles completed." />
              </span>
              <span className="text-white" data-testid="system-cycle-count">
                {health.cycle_count}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-400 flex items-center">
                Engine
                <HelpTooltip text="The trading engine's current state." />
              </span>
              <span
                className={
                  health.engine_running ? "text-green-400" : "text-red-400"
                }
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
