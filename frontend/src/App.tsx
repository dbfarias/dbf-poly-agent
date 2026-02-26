import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Markets from "./pages/Markets";
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

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="trades" element={<Trades />} />
            <Route path="strategies" element={<Strategies />} />
            <Route path="markets" element={<Markets />} />
            <Route path="risk" element={<Risk />} />
            <Route path="settings" element={<Settings />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
