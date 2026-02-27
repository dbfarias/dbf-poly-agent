import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // On mount, check if we have a valid session cookie via /api/auth/me
  useEffect(() => {
    api
      .get("/api/auth/me")
      .then(() => setIsAuthenticated(true))
      .catch(() => setIsAuthenticated(false));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setLoading(true);
    setError("");
    try {
      await api.post("/api/auth/login", { username, password });
      // Cookie is set automatically by the browser (httpOnly)
      setIsAuthenticated(true);
      return true;
    } catch (err: unknown) {
      const msg =
        err && typeof err === "object" && "response" in err
          ? (err as { response?: { data?: { detail?: string } } }).response
              ?.data?.detail || "Login failed"
          : "Connection error";
      setError(msg);
      return false;
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/api/auth/logout");
    } catch {
      // Ignore errors — cookie will be cleared anyway
    }
    setIsAuthenticated(false);
  }, []);

  return {
    login,
    logout,
    error,
    loading,
    // null = still checking, true = authenticated, false = not authenticated
    isAuthenticated: isAuthenticated === true,
    isLoading: isAuthenticated === null,
  };
}
