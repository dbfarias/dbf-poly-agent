import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { useAuth } from "./hooks/useAuth";
import Activity from "./pages/Activity";
import AIDebates from "./pages/AIDebates";
import Dashboard from "./pages/Dashboard";
import Learner from "./pages/Learner";
import Login from "./pages/Login";
import Markets from "./pages/Markets";
import Report from "./pages/Report";
import Research from "./pages/Research";
import Risk from "./pages/Risk";
import Settings from "./pages/Settings";
import Strategies from "./pages/Strategies";
import Trades from "./pages/Trades";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      staleTime: 5000,
    },
  },
});

function AuthenticatedApp({ onLogout }: { onLogout: () => void }) {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout onLogout={onLogout} />}>
          <Route index element={<Dashboard />} />
          <Route path="trades" element={<Trades />} />
          <Route path="strategies" element={<Strategies />} />
          <Route path="markets" element={<Markets />} />
          <Route path="risk" element={<Risk />} />
          <Route path="research" element={<Research />} />
          <Route path="report" element={<Report />} />
          <Route path="learner" element={<Learner />} />
          <Route path="ai-debates" element={<AIDebates />} />
          <Route path="activity" element={<Activity />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default function App() {
  const { isAuthenticated, isLoading, login, logout, error, loading } =
    useAuth();

  return (
    <QueryClientProvider client={queryClient}>
      {isLoading ? (
        <div className="flex h-screen items-center justify-center bg-[#0f1117]">
          <div className="text-zinc-400 text-sm">Loading...</div>
        </div>
      ) : isAuthenticated ? (
        <AuthenticatedApp onLogout={logout} />
      ) : (
        <Login onLogin={login} error={error} loading={loading} />
      )}
    </QueryClientProvider>
  );
}
