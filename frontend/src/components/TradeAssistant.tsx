import { useState, useRef, useEffect } from "react";
import { Send, Terminal, CheckCircle, XCircle, Loader2 } from "lucide-react";
import { executeTradeAssistant, type AssistantResponse } from "../api/client";

export default function TradeAssistant() {
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<AssistantResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [result]);

  const handleSubmit = async () => {
    if (!message.trim() || isLoading) return;
    setIsLoading(true);
    setResult(null);
    try {
      const response = await executeTradeAssistant(message);
      setResult(response);
      if (response.success) {
        setMessage("");
      }
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } }; message?: string };
      setResult({
        success: false,
        log: ["Request failed"],
        error: axiosErr.response?.data?.detail || axiosErr.message || "Unknown error",
        market_title: null,
        outcome: null,
        side: null,
        price: null,
        shares: null,
        cost: null,
        order_id: null,
      });
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div
      className="bg-[#1e2130] rounded-lg border border-[#2a2d3e] p-4"
      data-testid="trade-assistant"
    >
      <div className="flex items-center gap-2 mb-3">
        <Terminal size={16} className="text-indigo-400" />
        <h3 className="text-sm font-medium text-zinc-300">Trade Assistant</h3>
      </div>

      {/* Input row */}
      <div className="flex gap-2">
        <input
          type="text"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder='Ex: Buy No on Uruguay $5 https://polymarket.com/...'
          disabled={isLoading}
          className="flex-1 bg-[#0f1117] border border-[#2a2d3e] rounded-md px-3 py-2 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
          data-testid="assistant-input"
        />
        <button
          onClick={handleSubmit}
          disabled={!message.trim() || isLoading}
          className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/40 disabled:cursor-not-allowed rounded-md text-sm font-medium text-white transition-colors flex items-center gap-1.5 shrink-0"
          data-testid="assistant-submit"
        >
          {isLoading ? (
            <Loader2 size={14} className="animate-spin" />
          ) : (
            <Send size={14} />
          )}
          Send
        </button>
      </div>
      <p className="text-xs text-zinc-600 mt-1">Enter to send</p>

      {/* Loading state */}
      {isLoading && !result && (
        <div className="mt-3 flex items-center gap-2 text-sm text-zinc-400">
          <Loader2 size={14} className="animate-spin" />
          <span>Processing trade request...</span>
        </div>
      )}

      {/* Result area */}
      {result && (
        <div
          ref={logRef}
          className="mt-3 max-h-64 overflow-y-auto rounded-md bg-[#0f1117] border border-[#2a2d3e] p-3 space-y-1.5"
          data-testid="assistant-result"
        >
          {/* Log entries */}
          {result.log.map((entry, i) => (
            <div
              key={i}
              className={`flex items-start gap-2 text-sm py-1 px-2 rounded border-l-2 ${
                result.success
                  ? "border-l-green-500/60 text-zinc-300"
                  : "border-l-red-500/60 text-zinc-300"
              }`}
            >
              {result.success ? (
                <CheckCircle size={13} className="text-green-400 mt-0.5 shrink-0" />
              ) : (
                <XCircle size={13} className="text-red-400 mt-0.5 shrink-0" />
              )}
              <span className="break-all">{entry}</span>
            </div>
          ))}

          {/* Error message */}
          {result.error && (
            <div className="flex items-start gap-2 text-sm py-1 px-2 rounded border-l-2 border-l-red-500/60 text-red-300">
              <XCircle size={13} className="text-red-400 mt-0.5 shrink-0" />
              <span className="break-all">{result.error}</span>
            </div>
          )}

          {/* Trade details on success */}
          {result.success && result.market_title && (
            <div className="mt-2 pt-2 border-t border-[#2a2d3e] space-y-1 text-sm text-zinc-400">
              <div>
                <span className="text-zinc-500">Market:</span>{" "}
                <span className="text-zinc-200">{result.market_title}</span>
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {result.side && (
                  <span>
                    <span className="text-zinc-500">Side:</span>{" "}
                    <span className="text-zinc-200">{result.side} {result.outcome}</span>
                  </span>
                )}
                {result.price !== null && (
                  <span>
                    <span className="text-zinc-500">Price:</span>{" "}
                    <span className="text-zinc-200">${result.price.toFixed(3)}</span>
                  </span>
                )}
                {result.shares !== null && (
                  <span>
                    <span className="text-zinc-500">Shares:</span>{" "}
                    <span className="text-zinc-200">{result.shares.toFixed(2)}</span>
                  </span>
                )}
                {result.cost !== null && (
                  <span>
                    <span className="text-zinc-500">Cost:</span>{" "}
                    <span className="text-zinc-200">${result.cost.toFixed(2)}</span>
                  </span>
                )}
              </div>
              {result.order_id && (
                <div>
                  <span className="text-zinc-500">Order ID:</span>{" "}
                  <span className="text-zinc-200 font-mono text-xs">{result.order_id}</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
