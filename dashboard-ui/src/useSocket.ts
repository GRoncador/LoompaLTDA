import { useEffect, useRef, useState } from "react";
import type { LoompaEvent } from "./types";

/** Subscribes to /ws for a factory; reconnects with backoff; returns the latest events (ring buffer). */
export function useSocket(slug: string | null, onEvent: (e: LoompaEvent) => void) {
  const [connected, setConnected] = useState(false);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!slug) return;
    let ws: WebSocket | null = null;
    let closed = false;
    let delay = 500;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws?factory=${slug}`);
      ws.onopen = () => { setConnected(true); delay = 500; };
      ws.onmessage = (m) => {
        try {
          const ev = JSON.parse(m.data) as LoompaEvent;
          if (ev.type !== "ping") handler.current(ev);
        } catch { /* ignore */ }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) { setTimeout(connect, delay); delay = Math.min(delay * 2, 8000); }
      };
    };
    connect();
    return () => { closed = true; ws?.close(); };
  }, [slug]);

  return connected;
}
