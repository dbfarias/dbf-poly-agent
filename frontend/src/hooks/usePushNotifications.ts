import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";

type PushState =
  | "unsupported"
  | "ios-needs-install"
  | "denied"
  | "prompt"
  | "subscribed"
  | "unsubscribed"
  | "loading";

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

/** Wait for SW to reach "activated" state. */
async function ensureSwActive(reg: ServiceWorkerRegistration): Promise<void> {
  if (reg.active) return;
  const sw = reg.installing || reg.waiting;
  if (!sw) return;
  if (sw.state === "activated") return;
  await new Promise<void>((resolve) => {
    sw.addEventListener("statechange", function handler() {
      if (sw.state === "activated") {
        sw.removeEventListener("statechange", handler);
        resolve();
      }
    });
  });
}

export function usePushNotifications() {
  const [state, setState] = useState<PushState>("loading");
  const swReg = useRef<ServiceWorkerRegistration | null>(null);

  useEffect(() => {
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    const standaloneMedia = window.matchMedia("(display-mode: standalone)").matches;
    const standaloneNav = (navigator as unknown as { standalone?: boolean }).standalone === true;
    const isStandalone = standaloneMedia || standaloneNav;
    const hasSW = "serviceWorker" in navigator;
    const hasPush = "PushManager" in window;

    if (!hasSW || !hasPush) {
      setState(isIOS && !isStandalone ? "ios-needs-install" : "unsupported");
      return;
    }

    // Eagerly register SW on load so it's active by the time user clicks
    const init = async () => {
      try {
        const reg = await navigator.serviceWorker.register("/sw.js");
        await ensureSwActive(reg);
        await navigator.serviceWorker.ready;
        swReg.current = reg;
      } catch {
        // SW registration failed — push won't work
      }

      const permission = Notification.permission;
      if (permission === "denied") {
        setState("denied");
        return;
      }

      try {
        const reg = await navigator.serviceWorker.getRegistration();
        if (!reg) {
          setState("prompt");
          return;
        }
        const sub = await reg.pushManager.getSubscription();
        setState(sub ? "subscribed" : permission === "default" ? "prompt" : "unsubscribed");
      } catch {
        setState("prompt");
      }
    };

    init();
  }, []);

  const subscribe = useCallback(async () => {
    setState("loading");
    try {
      // Ensure SW is registered
      if (!swReg.current) {
        await navigator.serviceWorker.register("/sw.js");
      }
      // Always use .ready — it returns the registration with .active set
      const reg = await navigator.serviceWorker.ready;
      swReg.current = reg;

      // Get VAPID key from server
      const { data } = await api.get<{ public_key: string }>("/api/push/vapid-key");

      // Subscribe to push
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(data.public_key),
      });

      // Send subscription to server
      const subJson = sub.toJSON();
      await api.post("/api/push/subscribe", {
        endpoint: subJson.endpoint,
        keys: subJson.keys,
      });

      setState("subscribed");
    } catch (err) {
      console.error("Push subscribe failed:", err);
      if (Notification.permission === "denied") {
        setState("denied");
      } else {
        setState("prompt");
      }
    }
  }, []);

  const unsubscribe = useCallback(async () => {
    setState("loading");
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        const endpoint = sub.endpoint;
        await sub.unsubscribe();
        await api.post("/api/push/unsubscribe", { endpoint });
      }
      setState("unsubscribed");
    } catch (err) {
      console.error("Push unsubscribe failed:", err);
      setState("unsubscribed");
    }
  }, []);

  return { state, subscribe, unsubscribe };
}
