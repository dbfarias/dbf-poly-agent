import { clsx } from "clsx";
import { CheckCircle2, TrendingDown, TrendingUp, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

export interface ToastMessage {
  id: string;
  title: string;
  description?: string;
  variant: "buy" | "sell" | "info" | "success";
  duration?: number;
}

interface ToastItemProps {
  toast: ToastMessage;
  onDismiss: (id: string) => void;
}

function ToastItem({ toast, onDismiss }: ToastItemProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // Trigger enter animation
    requestAnimationFrame(() => setVisible(true));

    const timeout = setTimeout(() => {
      setVisible(false);
      setTimeout(() => onDismiss(toast.id), 300);
    }, toast.duration ?? 5000);

    return () => clearTimeout(timeout);
  }, [toast.id, toast.duration, onDismiss]);

  const icon = {
    buy: <TrendingUp size={16} className="text-green-400" />,
    sell: <TrendingDown size={16} className="text-red-400" />,
    info: <CheckCircle2 size={16} className="text-indigo-400" />,
    success: <CheckCircle2 size={16} className="text-green-400" />,
  }[toast.variant];

  const borderColor = {
    buy: "border-green-500/30",
    sell: "border-red-500/30",
    info: "border-indigo-500/30",
    success: "border-green-500/30",
  }[toast.variant];

  return (
    <div
      className={clsx(
        "flex items-start gap-3 px-4 py-3 rounded-lg border bg-[#1a1d29]/95 backdrop-blur-sm shadow-lg",
        "transition-all duration-300 ease-out",
        borderColor,
        visible ? "translate-x-0 opacity-100" : "translate-x-full opacity-0",
      )}
      data-testid="toast-item"
    >
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-zinc-200">{toast.title}</div>
        {toast.description && (
          <div className="text-xs text-zinc-400 mt-0.5 truncate">{toast.description}</div>
        )}
      </div>
      <button
        onClick={() => {
          setVisible(false);
          setTimeout(() => onDismiss(toast.id), 300);
        }}
        className="shrink-0 text-zinc-500 hover:text-zinc-300 transition-colors"
      >
        <X size={14} />
      </button>
    </div>
  );
}

interface ToastContainerProps {
  toasts: ToastMessage[];
  onDismiss: (id: string) => void;
}

export function ToastContainer({ toasts, onDismiss }: ToastContainerProps) {
  if (toasts.length === 0) return null;

  return (
    <div
      className="fixed top-4 right-4 z-[100] flex flex-col gap-2 w-80 max-w-[calc(100vw-2rem)]"
      data-testid="toast-container"
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}

let _nextId = 0;

export function createToast(
  title: string,
  variant: ToastMessage["variant"],
  description?: string,
  duration?: number,
): ToastMessage {
  return {
    id: `toast-${++_nextId}-${Date.now()}`,
    title,
    variant,
    description,
    duration,
  };
}

/** Hook: manages a toast queue with max 5 visible. */
export function useToasts() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const addToast = useCallback((toast: ToastMessage) => {
    setToasts((prev) => [...prev.slice(-4), toast]);
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return { toasts, addToast, dismissToast };
}
