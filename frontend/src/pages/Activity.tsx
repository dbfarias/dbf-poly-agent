import { useQuery } from "@tanstack/react-query";
import { formatRelative } from "../utils/date";
import {
  AlertCircle,
  ArrowDown,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock,
  Filter,
  Info,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { useCallback, useState } from "react";
import {
  type ActivityEvent,
  fetchActivity,
  fetchActivityEventTypes,
} from "../api/client";

const EVENT_TYPE_LABELS: Record<string, string> = {
  signal_found: "Signal Found",
  signal_rejected: "Signal Rejected",
  order_placed: "Order Placed",
  order_filled: "Order Filled",
  order_expired: "Order Expired",
  position_closed: "Position Closed",
  exit_triggered: "Exit Triggered",
  cycle_summary: "Cycle Summary",
  bot_event: "Bot Event",
  price_adjust: "Price Adjusted",
};

const LEVEL_CONFIG: Record<
  string,
  { icon: typeof Info; color: string; bg: string; label: string }
> = {
  info: {
    icon: Info,
    color: "text-blue-400",
    bg: "bg-blue-500/10",
    label: "Info",
  },
  success: {
    icon: CheckCircle2,
    color: "text-green-400",
    bg: "bg-green-500/10",
    label: "Success",
  },
  warning: {
    icon: AlertCircle,
    color: "text-yellow-400",
    bg: "bg-yellow-500/10",
    label: "Warning",
  },
  error: {
    icon: XCircle,
    color: "text-red-400",
    bg: "bg-red-500/10",
    label: "Error",
  },
};

const PAGE_SIZE = 50;

function formatTimestamp(ts: string): string {
  return formatRelative(ts);
}

function EventRow({ event }: { event: ActivityEvent }) {
  const [expanded, setExpanded] = useState(false);
  const config = LEVEL_CONFIG[event.level] || LEVEL_CONFIG.info;
  const Icon = config.icon;

  return (
    <div
      className={`border-b border-[#2a2d3e] last:border-0 transition-colors hover:bg-white/[0.02] ${
        expanded ? "bg-white/[0.02]" : ""
      }`}
      data-testid={`activity-row-${event.id}`}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-3 flex items-start gap-3 text-left"
      >
        <div className={`mt-0.5 p-1 rounded ${config.bg}`}>
          <Icon size={14} className={config.color} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium text-zinc-200 truncate max-w-[200px] sm:max-w-[400px]">
              {event.title}
            </span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#2a2d3e] text-zinc-500 shrink-0">
              {EVENT_TYPE_LABELS[event.event_type] || event.event_type}
            </span>
            {event.strategy && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400 shrink-0">
                {event.strategy}
              </span>
            )}
          </div>
          <div className="text-xs text-zinc-500 mt-0.5 flex items-center gap-2">
            <Clock size={10} />
            {formatTimestamp(event.timestamp)}
          </div>
        </div>
        <div className="shrink-0 mt-1">
          {expanded ? (
            <ChevronUp size={14} className="text-zinc-500" />
          ) : (
            <ChevronDown size={14} className="text-zinc-500" />
          )}
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-3 pl-12">
          <pre className="text-xs text-zinc-400 whitespace-pre-wrap font-mono bg-[#0f1117] rounded p-3 leading-relaxed">
            {event.detail}
          </pre>
          {event.market_id && (
            <div className="mt-2 text-[10px] text-zinc-600 truncate">
              Market: {event.market_id}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Activity() {
  const [offset, setOffset] = useState(0);
  const [filterType, setFilterType] = useState<string>("");
  const [filterLevel, setFilterLevel] = useState<string>("");
  const [filterStrategy, setFilterStrategy] = useState<string>("");
  const [showFilters, setShowFilters] = useState(false);

  const params = {
    limit: PAGE_SIZE,
    offset,
    ...(filterType ? { event_type: filterType } : {}),
    ...(filterLevel ? { level: filterLevel } : {}),
    ...(filterStrategy ? { strategy: filterStrategy } : {}),
  };

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["activity", params],
    queryFn: () => fetchActivity(params),
    refetchInterval: 15000,
  });

  const { data: eventTypes } = useQuery({
    queryKey: ["activity-event-types"],
    queryFn: fetchActivityEventTypes,
    staleTime: 60000,
  });

  const clearFilters = useCallback(() => {
    setFilterType("");
    setFilterLevel("");
    setFilterStrategy("");
    setOffset(0);
  }, []);

  const hasFilters = filterType || filterLevel || filterStrategy;
  const events = data?.events ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="space-y-4" data-testid="activity-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold">Activity Log</h2>
        <div className="flex items-center gap-2">
          <span className="text-xs text-zinc-500">
            {total} events{hasFilters ? " (filtered)" : ""}
          </span>
          <button
            onClick={() => setShowFilters(!showFilters)}
            className={`p-1.5 rounded text-sm transition-colors ${
              showFilters || hasFilters
                ? "bg-indigo-500/20 text-indigo-400"
                : "text-zinc-400 hover:text-zinc-200 hover:bg-white/5"
            }`}
            title="Toggle filters"
          >
            <Filter size={16} />
          </button>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-1.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-white/5 transition-colors disabled:opacity-50"
            title="Refresh"
          >
            <RefreshCw size={16} className={isFetching ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {/* Filters */}
      {showFilters && (
        <div
          className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-3 flex flex-wrap items-center gap-3"
          data-testid="activity-filters"
        >
          <select
            value={filterType}
            onChange={(e) => {
              setFilterType(e.target.value);
              setOffset(0);
            }}
            className="bg-[#0f1117] text-sm text-zinc-300 border border-[#2a2d3e] rounded px-2 py-1.5"
          >
            <option value="">All Types</option>
            {(eventTypes ?? []).map((t) => (
              <option key={t} value={t}>
                {EVENT_TYPE_LABELS[t] || t}
              </option>
            ))}
          </select>

          <select
            value={filterLevel}
            onChange={(e) => {
              setFilterLevel(e.target.value);
              setOffset(0);
            }}
            className="bg-[#0f1117] text-sm text-zinc-300 border border-[#2a2d3e] rounded px-2 py-1.5"
          >
            <option value="">All Levels</option>
            <option value="info">Info</option>
            <option value="success">Success</option>
            <option value="warning">Warning</option>
            <option value="error">Error</option>
          </select>

          <select
            value={filterStrategy}
            onChange={(e) => {
              setFilterStrategy(e.target.value);
              setOffset(0);
            }}
            className="bg-[#0f1117] text-sm text-zinc-300 border border-[#2a2d3e] rounded px-2 py-1.5"
          >
            <option value="">All Strategies</option>
            <option value="time_decay">Time Decay</option>
            <option value="arbitrage">Arbitrage</option>
            <option value="price_divergence">Price Divergence</option>
            <option value="swing_trading">Swing Trading</option>
            <option value="value_betting">Value Betting</option>
            <option value="market_making">Market Making</option>
          </select>

          {hasFilters && (
            <button
              onClick={clearFilters}
              className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
            >
              Clear filters
            </button>
          )}
        </div>
      )}

      {/* Events list */}
      <div className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-zinc-500 text-sm">
            Loading activity...
          </div>
        ) : events.length === 0 ? (
          <div className="p-8 text-center text-zinc-500 text-sm">
            {hasFilters
              ? "No events match your filters."
              : "No activity yet. The bot will log events as it runs."}
          </div>
        ) : (
          events.map((event) => <EventRow key={event.id} event={event} />)
        )}
      </div>

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between">
          <button
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            disabled={offset === 0}
            className="px-3 py-1.5 text-xs rounded bg-[#1e2130] border border-[#2a2d3e] text-zinc-400 hover:text-zinc-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Newer
          </button>
          <span className="text-xs text-zinc-500">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <button
            onClick={() => setOffset(offset + PAGE_SIZE)}
            disabled={!data?.has_more}
            className="flex items-center gap-1 px-3 py-1.5 text-xs rounded bg-[#1e2130] border border-[#2a2d3e] text-zinc-400 hover:text-zinc-200 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          >
            Older <ArrowDown size={12} />
          </button>
        </div>
      )}
    </div>
  );
}
