import { useState } from "react";

interface LoginProps {
  onLogin: (username: string, password: string) => Promise<boolean>;
  error: string;
  loading: boolean;
}

export default function Login({ onLogin, error, loading }: LoginProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await onLogin(username, password);
  };

  return (
    <div className="flex items-center justify-center min-h-screen bg-[#0f1117]">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-5 sm:p-8 mx-4 sm:mx-0"
        data-testid="login-form"
      >
        <div className="flex flex-col items-center mb-4">
          <img src="/logo.png" alt="DBF PolyBot" className="w-20 h-20 mb-2" />
          <h1 className="text-xl font-bold text-white">PolyBot</h1>
          <p className="text-sm text-zinc-500">Sign in to your dashboard</p>
        </div>

        {error && (
          <div
            className="mb-4 px-3 py-2 rounded bg-red-500/10 border border-red-500/30 text-red-400 text-sm"
            data-testid="login-error"
          >
            {error}
          </div>
        )}

        <div className="space-y-4">
          <div>
            <label className="block text-xs text-zinc-400 mb-1">Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-3 py-2 rounded bg-[#0f1117] border border-[#2a2d3e] text-white text-sm focus:outline-none focus:border-indigo-500"
              autoFocus
              required
              data-testid="login-username"
            />
          </div>
          <div>
            <label className="block text-xs text-zinc-400 mb-1">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-3 py-2 rounded bg-[#0f1117] border border-[#2a2d3e] text-white text-sm focus:outline-none focus:border-indigo-500"
              required
              data-testid="login-password"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="w-full py-2 rounded bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium disabled:opacity-50 transition-colors"
            data-testid="login-submit"
          >
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </div>
      </form>
    </div>
  );
}
