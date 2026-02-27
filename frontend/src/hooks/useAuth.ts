import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

const TOKEN_KEY = "polybot_token";

export function useAuth() {
  const [token, setToken] = useState<string | null>(() =>
    sessionStorage.getItem(TOKEN_KEY),
  );
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Set Authorization header whenever token changes
  useEffect(() => {
    if (token) {
      api.defaults.headers.common["Authorization"] = `Bearer ${token}`;
      sessionStorage.setItem(TOKEN_KEY, token);
    } else {
      delete api.defaults.headers.common["Authorization"];
      sessionStorage.removeItem(TOKEN_KEY);
    }
  }, [token]);

  const login = useCallback(async (username: string, password: string) => {
    setLoading(true);
    setError("");
    try {
      const res = await api.post<{ token: string }>("/api/auth/login", {
        username,
        password,
      });
      setToken(res.data.token);
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

  const logout = useCallback(() => {
    setToken(null);
  }, []);

  return { token, login, logout, error, loading, isAuthenticated: !!token };
}
