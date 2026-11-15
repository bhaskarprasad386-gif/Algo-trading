"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { getDashboardWsUrl } from "@/lib/config";

export type PagePushStatus = "CONNECTING" | "LIVE" | "RECONNECTING" | "DISCONNECTED";
type PagePushValue = {
  page: string | null;
  status: PagePushStatus;
  data: Record<string, unknown> | null;
  lastMessageAt: number | null;
  resync: () => void;
};
const PagePushContext = createContext<PagePushValue>({
  page: null, status: "DISCONNECTED", data: null, lastMessageAt: null, resync: () => undefined,
});

export function PagePushProvider({ page, children }: { page: string | null; children: ReactNode }) {
  const [status, setStatus] = useState<PagePushStatus>("DISCONNECTED");
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const sequenceRef = useRef(0);
  const resyncPendingRef = useRef(false);

  const resync = useCallback(() => {
    const socket = socketRef.current;
    if (!page || socket?.readyState !== WebSocket.OPEN || resyncPendingRef.current) return;
    resyncPendingRef.current = true;
    socket.send(JSON.stringify({ action: "resync", page }));
  }, [page]);

  useEffect(() => {
    if (!page) {
      setStatus("DISCONNECTED");
      setData(null);
      setLastMessageAt(null);
      return;
    }
    let disposed = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let retryMs = 1000;

    const connect = () => {
      if (disposed) return;
      setStatus("CONNECTING");
      let socket: WebSocket;
      try {
        socket = new WebSocket(getDashboardWsUrl() + "/ws/page-updates");
      } catch {
        setStatus("RECONNECTING");
        retryTimer = setTimeout(connect, retryMs);
        retryMs = Math.min(15000, retryMs * 2);
        return;
      }
      socketRef.current = socket;
      socket.onopen = () => {
        if (disposed) return;
        retryMs = 1000;
        setStatus("LIVE");
        socket.send(JSON.stringify({ action: "subscribe", page }));
      };
      socket.onmessage = (event) => {
        if (disposed) return;
        setLastMessageAt(Date.now());
        let message: Record<string, unknown>;
        try { message = JSON.parse(String(event.data)) as Record<string, unknown>; }
        catch { return; }
        if (message.type !== "page_update" || message.page !== page) return;
        const sequence = Number(message.sequence);
        const eventType = String(message.event || "");
        if (!Number.isSafeInteger(sequence) || sequence <= 0) return;
        if (eventType === "SNAPSHOT") {
          sequenceRef.current = sequence;
          resyncPendingRef.current = false;
          setData(message.data && typeof message.data === "object" ? message.data as Record<string, unknown> : {});
          return;
        }
        if (sequenceRef.current && sequence !== sequenceRef.current + 1) {
          resync();
          return;
        }
        sequenceRef.current = sequence;
        if (eventType === "PATCH" && message.data && typeof message.data === "object") {
          setData((previous) => ({ ...(previous || {}), ...(message.data as Record<string, unknown>) }));
        } else if (eventType === "REMOVE" && message.data && typeof message.data === "object") {
          setData((previous) => ({ ...(previous || {}), ...(message.data as Record<string, unknown>) }));
        }
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        if (disposed) return;
        setStatus("RECONNECTING");
        retryTimer = setTimeout(connect, retryMs);
        retryMs = Math.min(15000, retryMs * 2);
      };
    };

    sequenceRef.current = 0;
    resyncPendingRef.current = false;
    setData(null);
    connect();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [page, resync]);

  return (
    <PagePushContext.Provider value={{ page, status, data, lastMessageAt, resync }}>
      {children}
    </PagePushContext.Provider>
  );
}

export function usePagePush(expectedPage: string) {
  const value = useContext(PagePushContext);
  if (value.page !== expectedPage) {
    return { status: "DISCONNECTED" as const, data: null, lastMessageAt: null, resync: () => undefined };
  }
  return value;
}
