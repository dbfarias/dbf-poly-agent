import { useCallback, useEffect, useRef, useState } from "react";
import { getWsUrl } from "../api/client";

export interface WSMessage {
  type: string;
  event?: string;
  data?: Record<string, unknown>;
  timestamp?: string;
}

type MessageHandler = (msg: WSMessage) => void;

/** Registered listeners for specific message types (e.g. "trade"). */
const _listeners: Map<string, Set<MessageHandler>> = new Map();

export function useWebSocket(url?: string) {
  const wsUrl = url || getWsUrl("/ws/live");
  const ws = useRef<WebSocket | null>(null);
  const [lastMessage, setLastMessage] = useState<WSMessage | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();

  const connect = useCallback(() => {
    try {
      ws.current = new WebSocket(wsUrl);

      ws.current.onopen = () => setIsConnected(true);

      ws.current.onmessage = (event) => {
        try {
          const data: WSMessage = JSON.parse(event.data);
          setLastMessage(data);

          // Notify type-specific listeners
          const handlers = _listeners.get(data.type);
          if (handlers) {
            handlers.forEach((fn) => fn(data));
          }
        } catch {
          // ignore non-JSON messages
        }
      };

      ws.current.onclose = () => {
        setIsConnected(false);
        reconnectTimer.current = setTimeout(connect, 5000);
      };

      ws.current.onerror = () => {
        ws.current?.close();
      };
    } catch {
      reconnectTimer.current = setTimeout(connect, 5000);
    }
  }, [wsUrl]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      ws.current?.close();
    };
  }, [connect]);

  return { lastMessage, isConnected };
}

/** Subscribe to a specific WS message type. Returns an unsubscribe function. */
export function onWsMessage(type: string, handler: MessageHandler): () => void {
  if (!_listeners.has(type)) {
    _listeners.set(type, new Set());
  }
  _listeners.get(type)!.add(handler);

  return () => {
    _listeners.get(type)?.delete(handler);
  };
}
