import { useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BarChart3,
  Brain,
  FileText,
  LineChart,
  LogOut,
  Menu,
  MessageSquare,
  Newspaper,
  RefreshCw,
  ScrollText,
  Settings,
  Shield,
  TrendingUp,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { onWsMessage, useWebSocket, type WSMessage } from "../hooks/useWebSocket";
import PushBanner from "./PushBanner";
import { createToast, ToastContainer, useToasts } from "./Toast";

const navItems = [
  { to: "/", icon: LineChart, label: "Dashboard" },
  { to: "/trades", icon: TrendingUp, label: "Trades" },
  { to: "/strategies", icon: BarChart3, label: "Strategies" },
  { to: "/markets", icon: Activity, label: "Markets" },
  { to: "/risk", icon: Shield, label: "Risk" },
  { to: "/research", icon: Newspaper, label: "Research" },
  { to: "/report", icon: FileText, label: "Report" },
  { to: "/learner", icon: Brain, label: "Learner" },
  { to: "/ai-debates", icon: MessageSquare, label: "AI Debates" },
  { to: "/activity", icon: ScrollText, label: "Activity" },
  { to: "/settings", icon: Settings, label: "Settings" },
];

interface LayoutProps {
  onLogout?: () => void;
}

export default function Layout({ onLogout }: LayoutProps) {
  const { isConnected } = useWebSocket();
  const queryClient = useQueryClient();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const location = useLocation();
  const { toasts, addToast, dismissToast } = useToasts();

  // Close sidebar on route change (mobile)
  useEffect(() => {
    setSidebarOpen(false);
  }, [location.pathname]);

  // Listen for trade events from WebSocket
  useEffect(() => {
    return onWsMessage("trade", (msg: WSMessage) => {
      const d = msg.data ?? {};
      const side = String(d.side ?? "");
      const strategy = String(d.strategy ?? "");
      const price = Number(d.price ?? 0);
      const size = Number(d.size ?? 0);
      const pnl = d.pnl != null ? Number(d.pnl) : null;
      const question = String(d.question ?? "Trade executed");

      const variant = side === "SELL" ? "sell" : "buy";
      const title = side === "SELL"
        ? `Sold ${size.toFixed(0)} shares`
        : `Bought ${size.toFixed(0)} shares`;
      const pnlStr = pnl != null ? ` | P&L: $${pnl.toFixed(2)}` : "";
      const description = `${strategy} @ $${price.toFixed(3)}${pnlStr} — ${question.slice(0, 60)}`;

      addToast(createToast(title, variant, description));

      // Refetch queries so dashboard updates immediately
      queryClient.refetchQueries({ type: "active" });
    });
  }, [addToast, queryClient]);

  const handleRefresh = useCallback(() => {
    setIsRefreshing(true);
    window.location.reload();
  }, []);

  return (
    <div className="flex h-screen bg-[#0f1117]">
      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/60 z-40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <nav
        className={`fixed lg:static inset-y-0 left-0 z-50 w-56 bg-[#1a1d29] border-r border-[#2a2d3e] flex flex-col transform transition-transform duration-200 ease-in-out lg:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
        data-testid="sidebar"
        aria-label="Main navigation"
      >
        <div className="p-4 border-b border-[#2a2d3e]">
          <div className="flex items-center justify-between">
            <h1 className="text-lg font-bold text-white" data-testid="sidebar-title">PolyBot</h1>
            <div className="flex items-center gap-1">
              <button
                onClick={handleRefresh}
                disabled={isRefreshing}
                className="p-1.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-white/5 transition-colors disabled:opacity-50"
                data-testid="refresh-btn"
                title="Refresh all data"
              >
                <RefreshCw size={16} className={isRefreshing ? "animate-spin" : ""} />
              </button>
              <button
                onClick={() => setSidebarOpen(false)}
                className="p-1.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-white/5 transition-colors lg:hidden"
                aria-label="Close menu"
              >
                <X size={16} />
              </button>
            </div>
          </div>
          <div className="flex items-center gap-1.5 mt-1">
            <div
              className={`w-2 h-2 rounded-full ${isConnected ? "bg-green-500" : "bg-red-500"}`}
              data-testid="ws-indicator"
            />
            <span className="text-xs text-zinc-400" data-testid="ws-status-text">
              {isConnected ? "Connected" : "Disconnected"}
            </span>
          </div>
        </div>
        <div className="flex-1 py-2 overflow-y-auto">
          {navItems.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
                  isActive
                    ? "text-indigo-400 bg-indigo-500/10 border-r-2 border-indigo-400"
                    : "text-zinc-400 hover:text-zinc-200 hover:bg-white/5"
                }`
              }
              end={to === "/"}
              data-testid={`nav-${label.toLowerCase()}`}
            >
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </div>
        {onLogout && (
          <div className="p-3 border-t border-[#2a2d3e]">
            <button
              onClick={onLogout}
              className="flex items-center gap-2 w-full px-3 py-2 rounded text-sm text-zinc-400 hover:text-zinc-200 hover:bg-white/5 transition-colors"
              data-testid="logout-btn"
            >
              <LogOut size={16} />
              Sign Out
            </button>
          </div>
        )}
      </nav>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Mobile top bar */}
        <header className="lg:hidden flex items-center justify-between px-4 py-3 bg-[#1a1d29] border-b border-[#2a2d3e]">
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-1.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-white/5 transition-colors"
            data-testid="mobile-menu-btn"
            aria-label="Open menu"
          >
            <Menu size={20} />
          </button>
          <span className="text-sm font-bold text-white">PolyBot</span>
          <div className="flex items-center gap-1.5">
            <div
              className={`w-2 h-2 rounded-full ${isConnected ? "bg-green-500" : "bg-red-500"}`}
            />
            <button
              onClick={handleRefresh}
              disabled={isRefreshing}
              className="p-1.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-white/5 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={16} className={isRefreshing ? "animate-spin" : ""} />
            </button>
          </div>
        </header>
        <PushBanner />
        <main className="flex-1 overflow-auto p-3 md:p-6" data-testid="main-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
