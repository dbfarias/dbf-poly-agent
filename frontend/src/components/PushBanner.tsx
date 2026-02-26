import { Bell, X } from "lucide-react";
import { useEffect, useState } from "react";
import { usePushNotifications } from "../hooks/usePushNotifications";

const DISMISSED_KEY = "push-banner-dismissed";

/**
 * One-time banner prompting the user to enable push notifications.
 * Shows on first visit when push is available but not yet subscribed.
 * Dismissed permanently via localStorage.
 */
export default function PushBanner() {
  const { state, subscribe } = usePushNotifications();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // Show banner only when push is available and not yet configured
    const eligible = state === "prompt" || state === "unsubscribed";
    const dismissed = localStorage.getItem(DISMISSED_KEY) === "1";
    setVisible(eligible && !dismissed);
  }, [state]);

  if (!visible) return null;

  const dismiss = () => {
    setVisible(false);
    localStorage.setItem(DISMISSED_KEY, "1");
  };

  const handleEnable = async () => {
    await subscribe();
    dismiss();
  };

  return (
    <div className="bg-indigo-600/90 text-white px-4 py-2.5 flex items-center justify-between gap-3 text-sm">
      <div className="flex items-center gap-2 min-w-0">
        <Bell size={16} className="shrink-0" />
        <span className="truncate">
          Enable push notifications to receive trade alerts on your device
        </span>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <button
          onClick={handleEnable}
          className="px-3 py-1 bg-white text-indigo-700 rounded font-medium hover:bg-indigo-50 transition-colors"
        >
          Enable
        </button>
        <button
          onClick={dismiss}
          className="p-1 hover:bg-white/20 rounded transition-colors"
          aria-label="Dismiss"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}
