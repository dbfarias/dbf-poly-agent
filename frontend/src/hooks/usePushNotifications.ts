import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

type PushState =
  | "unsupported"
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

export function usePushNotifications() {
  const [state, setState] = useState<PushState>("loading");

  useEffect(() => {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      setState("unsupported");
      return;
    }

    const checkState = async () => {
      const permission = Notification.permission;
      if (permission === "denied") {
        setState("denied");
        return;
      }

      try {
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        setState(sub ? "subscribed" : permission === "default" ? "prompt" : "unsubscribed");
      } catch {
        setState("prompt");
      }
    };

    checkState();
  }, []);

  const subscribe = useCallback(async () => {
    setState("loading");
    try {
      // Register SW if not already registered
      const reg = await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;

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
      // Check if permission was denied during the flow
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
