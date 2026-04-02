import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Plus, Skull, X } from "lucide-react";
import { useState } from "react";
import {
  type CreateWatcherBody,
  type WatcherItem,
  createWatcher,
  fetchWatchers,
  killWatcher,
} from "../api/client";
import { formatRelative } from "../utils/date";

const STATUS_BADGE: Record<string, { bg: string; text: string; label: string }> = {
  active: { bg: "bg-green-500/20", text: "text-green-400", label: "Active" },
  completed: { bg: "bg-zinc-500/20", text: "text-zinc-400", label: "Completed" },
  killed: { bg: "bg-red-500/20", text: "text-red-400", label: "Killed" },
  paused: { bg: "bg-yellow-500/20", text: "text-yellow-400", label: "Paused" },
};

function StatusBadge({ status }: { status: string }) {
  const cfg = STATUS_BADGE[status] ?? STATUS_BADGE.completed;
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${cfg.bg} ${cfg.text}`}>
      {cfg.label}
    </span>
  );
}

function WatcherCard({
  w,
  onKill,
  killing,
}: {
  w: WatcherItem;
  onKill: (id: number) => void;
  killing: boolean;
}) {
  const currentPrice = w.current_price || w.highest_price || w.avg_entry_price;
  const pnl =
    w.avg_entry_price > 0
      ? ((currentPrice - w.avg_entry_price) / w.avg_entry_price) * w.current_exposure
      : 0;
  const pnlPct =
    w.avg_entry_price > 0
      ? ((currentPrice - w.avg_entry_price) / w.avg_entry_price) * 100
      : 0;

  return (
    <div className="bg-[#1a1d29] border border-[#2a2d3e] rounded-lg p-4">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <StatusBadge status={w.status} />
            {w.auto_created && (
              <span className="px-1.5 py-0.5 rounded text-[10px] bg-indigo-500/20 text-indigo-400">
                Auto
              </span>
            )}
            {w.source_strategy && (
              <span className="text-[10px] text-zinc-500">{w.source_strategy}</span>
            )}
          </div>
          <p className="text-sm text-zinc-200 truncate" title={w.question}>
            {w.question || w.market_id.slice(0, 20)}
          </p>
        </div>
        {w.status === "active" && (
          <button
            onClick={() => onKill(w.id)}
            disabled={killing}
            className="flex items-center gap-1 px-2 py-1 rounded text-xs text-red-400 hover:bg-red-500/10 border border-red-500/30 transition-colors disabled:opacity-50"
            title="Kill watcher"
          >
            <Skull size={12} />
            Kill
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3 text-xs">
        <div>
          <span className="text-zinc-500 block">Entry</span>
          <span className="text-zinc-200">${w.avg_entry_price.toFixed(3)}</span>
        </div>
        <div>
          <span className="text-zinc-500 block">Current</span>
          <span className="text-zinc-200">${currentPrice.toFixed(3)}</span>
        </div>
        <div>
          <span className="text-zinc-500 block">P&L</span>
          <span className={pnl >= 0 ? "text-green-400" : "text-red-400"}>
            ${pnl.toFixed(2)} ({pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(1)}%)
          </span>
        </div>
        <div>
          <span className="text-zinc-500 block">Exposure</span>
          <span className="text-zinc-200">
            ${w.current_exposure.toFixed(2)} / ${w.max_exposure_usd.toFixed(0)}
          </span>
        </div>
        <div>
          <span className="text-zinc-500 block">Scales</span>
          <span className="text-zinc-200">
            {w.scale_count} / {w.max_scale_count}
          </span>
        </div>
        <div>
          <span className="text-zinc-500 block">Stop Loss</span>
          <span className="text-zinc-200">{(w.stop_loss_pct * 100).toFixed(0)}%</span>
        </div>
        <div>
          <span className="text-zinc-500 block">Last Check</span>
          <span className="text-zinc-200">
            {w.last_check_at ? formatRelative(w.last_check_at) : "Never"}
          </span>
        </div>
        <div>
          <span className="text-zinc-500 block">Created</span>
          <span className="text-zinc-200">{formatRelative(w.created_at)}</span>
        </div>
      </div>

      {w.thesis && (
        <div className="mt-3 pt-2 border-t border-[#2a2d3e]">
          <p className="text-[11px] text-zinc-500 line-clamp-2">{w.thesis}</p>
        </div>
      )}
    </div>
  );
}

function CreateWatcherForm({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<CreateWatcherBody>({
    market_id: "",
    question: "",
    thesis: "",
    current_price: 0.5,
    max_exposure_usd: 20,
    stop_loss_pct: 0.25,
    max_age_hours: 168,
  });

  const mutation = useMutation({
    mutationFn: createWatcher,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["watchers"] });
      onClose();
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.market_id) return;
    mutation.mutate(form);
  };

  return (
    <div className="bg-[#1a1d29] border border-[#2a2d3e] rounded-lg p-4 mb-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-zinc-200">Create Watcher</h3>
        <button
          onClick={onClose}
          className="p-1 rounded text-zinc-400 hover:text-zinc-200 hover:bg-white/5"
        >
          <X size={14} />
        </button>
      </div>
      <form onSubmit={handleSubmit} className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
        <div className="sm:col-span-2">
          <label className="text-zinc-500 text-xs block mb-1">Market ID *</label>
          <input
            type="text"
            value={form.market_id}
            onChange={(e) => setForm({ ...form, market_id: e.target.value })}
            className="w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-1.5 text-zinc-200 text-xs"
            placeholder="0x1234..."
            required
          />
        </div>
        <div className="sm:col-span-2">
          <label className="text-zinc-500 text-xs block mb-1">Question</label>
          <input
            type="text"
            value={form.question ?? ""}
            onChange={(e) => setForm({ ...form, question: e.target.value })}
            className="w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-1.5 text-zinc-200 text-xs"
            placeholder="Will X happen before Y?"
          />
        </div>
        <div className="sm:col-span-2">
          <label className="text-zinc-500 text-xs block mb-1">Thesis</label>
          <input
            type="text"
            value={form.thesis ?? ""}
            onChange={(e) => setForm({ ...form, thesis: e.target.value })}
            className="w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-1.5 text-zinc-200 text-xs"
            placeholder="Why this market will move..."
          />
        </div>
        <div>
          <label className="text-zinc-500 text-xs block mb-1">Current Price</label>
          <input
            type="number"
            step="0.01"
            min="0"
            max="1"
            value={form.current_price}
            onChange={(e) => setForm({ ...form, current_price: parseFloat(e.target.value) || 0 })}
            className="w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-1.5 text-zinc-200 text-xs"
          />
        </div>
        <div>
          <label className="text-zinc-500 text-xs block mb-1">Max Exposure ($)</label>
          <input
            type="number"
            step="1"
            min="1"
            max="100"
            value={form.max_exposure_usd}
            onChange={(e) => setForm({ ...form, max_exposure_usd: parseFloat(e.target.value) || 20 })}
            className="w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-1.5 text-zinc-200 text-xs"
          />
        </div>
        <div>
          <label className="text-zinc-500 text-xs block mb-1">Stop Loss %</label>
          <input
            type="number"
            step="0.05"
            min="0.05"
            max="1"
            value={form.stop_loss_pct}
            onChange={(e) => setForm({ ...form, stop_loss_pct: parseFloat(e.target.value) || 0.25 })}
            className="w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-1.5 text-zinc-200 text-xs"
          />
        </div>
        <div>
          <label className="text-zinc-500 text-xs block mb-1">Max Age (hours)</label>
          <input
            type="number"
            step="24"
            min="1"
            max="720"
            value={form.max_age_hours}
            onChange={(e) => setForm({ ...form, max_age_hours: parseFloat(e.target.value) || 168 })}
            className="w-full bg-[#0f1117] border border-[#2a2d3e] rounded px-3 py-1.5 text-zinc-200 text-xs"
          />
        </div>
        <div className="sm:col-span-2 flex justify-end gap-2 mt-1">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 transition-colors"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={mutation.isPending || !form.market_id}
            className="px-3 py-1.5 text-xs rounded bg-indigo-600 text-white hover:bg-indigo-500 disabled:opacity-50 transition-colors"
          >
            {mutation.isPending ? "Creating..." : "Create Watcher"}
          </button>
        </div>
        {mutation.isError && (
          <p className="sm:col-span-2 text-xs text-red-400">
            {(mutation.error as Error)?.message || "Failed to create watcher"}
          </p>
        )}
      </form>
    </div>
  );
}

export default function Watchers() {
  const [showCreate, setShowCreate] = useState(false);
  const queryClient = useQueryClient();

  const { data: watchers, isLoading, error } = useQuery({
    queryKey: ["watchers"],
    queryFn: fetchWatchers,
    refetchInterval: 30000,
  });

  const killMutation = useMutation({
    mutationFn: killWatcher,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["watchers"] }),
  });

  const activeWatchers = watchers?.filter((w) => w.status === "active") ?? [];
  const inactiveWatchers = watchers?.filter((w) => w.status !== "active") ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Eye size={20} className="text-indigo-400" />
          <h2 className="text-lg font-bold text-white">Trade Watchers</h2>
          {activeWatchers.length > 0 && (
            <span className="px-2 py-0.5 rounded-full text-xs bg-green-500/20 text-green-400">
              {activeWatchers.length} active
            </span>
          )}
        </div>
        <button
          onClick={() => setShowCreate((v) => !v)}
          className="flex items-center gap-1 px-3 py-1.5 text-xs rounded bg-indigo-600 text-white hover:bg-indigo-500 transition-colors"
        >
          <Plus size={14} />
          New Watcher
        </button>
      </div>

      {showCreate && <CreateWatcherForm onClose={() => setShowCreate(false)} />}

      {isLoading && (
        <div className="text-center py-8 text-zinc-500 text-sm">Loading watchers...</div>
      )}

      {error && (
        <div className="text-center py-8 text-red-400 text-sm">
          Failed to load watchers
        </div>
      )}

      {!isLoading && watchers && watchers.length === 0 && (
        <div className="text-center py-12 text-zinc-500">
          <Eye size={32} className="mx-auto mb-2 opacity-30" />
          <p className="text-sm">No watchers yet</p>
          <p className="text-xs mt-1">
            Watchers are created automatically after qualifying trades, or manually above.
          </p>
        </div>
      )}

      {activeWatchers.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-xs font-medium text-zinc-500 uppercase tracking-wider">
            Active ({activeWatchers.length})
          </h3>
          {activeWatchers.map((w) => (
            <WatcherCard
              key={w.id}
              w={w}
              onKill={(id) => killMutation.mutate(id)}
              killing={killMutation.isPending}
            />
          ))}
        </div>
      )}

      {inactiveWatchers.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-xs font-medium text-zinc-500 uppercase tracking-wider">
            History ({inactiveWatchers.length})
          </h3>
          {inactiveWatchers.map((w) => (
            <WatcherCard
              key={w.id}
              w={w}
              onKill={(id) => killMutation.mutate(id)}
              killing={killMutation.isPending}
            />
          ))}
        </div>
      )}
    </div>
  );
}
