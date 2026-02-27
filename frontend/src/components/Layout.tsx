import { useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BarChart3,
  Brain,
  LineChart,
  LogOut,
  RefreshCw,
  Settings,
  Shield,
  TrendingUp,
} from "lucide-react";
import { useCallback, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useWebSocket } from "../hooks/useWebSocket";

const navItems = [
  { to: "/", icon: LineChart, label: "Dashboard" },
  { to: "/trades", icon: TrendingUp, label: "Trades" },
  { to: "/strategies", icon: BarChart3, label: "Strategies" },
  { to: "/markets", icon: Activity, label: "Markets" },
  { to: "/risk", icon: Shield, label: "Risk" },
  { to: "/learner", icon: Brain, label: "Learner" },
  { to: "/settings", icon: Settings, label: "Settings" },
];

interface LayoutProps {
  onLogout?: () => void;
}

export default function Layout({ onLogout }: LayoutProps) {
  const { isConnected } = useWebSocket();
  const queryClient = useQueryClient();
  const [isRefreshing, setIsRefreshing] = useState(false);

  const handleRefresh = useCallback(async () => {
    setIsRefreshing(true);
    await queryClient.refetchQueries({ type: "active" });
    setTimeout(() => setIsRefreshing(false), 600);
  }, [queryClient]);

  return (
    <div className="flex h-screen bg-[#0f1117]">
      {/* Sidebar */}
      <nav className="w-56 bg-[#1a1d29] border-r border-[#2a2d3e] flex flex-col" data-testid="sidebar">
        <div className="p-4 border-b border-[#2a2d3e]">
          <div className="flex items-center justify-between">
            <h1 className="text-lg font-bold text-white" data-testid="sidebar-title">PolyBot</h1>
            <button
              onClick={handleRefresh}
              disabled={isRefreshing}
              className="p-1.5 rounded text-zinc-400 hover:text-zinc-200 hover:bg-white/5 transition-colors disabled:opacity-50"
              data-testid="refresh-btn"
              title="Refresh all data"
            >
              <RefreshCw size={16} className={isRefreshing ? "animate-spin" : ""} />
            </button>
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
        <div className="flex-1 py-2">
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
      <main className="flex-1 overflow-auto p-6" data-testid="main-content">
        <Outlet />
      </main>
    </div>
  );
}
