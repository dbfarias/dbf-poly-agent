import { useCallback, useEffect, useRef, useState } from "react";
import { getWsUrl } from "../api/client";

interface WSMessage {
  type: string;
  data?: unknown;
  timestamp?: string;
}

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
          const data = JSON.parse(event.data);
          setLastMessage(data);
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
