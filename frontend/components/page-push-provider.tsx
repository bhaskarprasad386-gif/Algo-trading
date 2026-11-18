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
  command: (name: string, payload?: Record<string, unknown>) => Promise<Record<string, unknown>>;
};
const PagePushContext = createContext<PagePushValue>({
  page: null, status: "DISCONNECTED", data: null, lastMessageAt: null, resync: () => undefined,
  command: async () => { throw new Error("Scanner WebSocket is not connected"); },
});

export function PagePushProvider({ page, children }: { page: string | null; children: ReactNode }) {
  const [status, setStatus] = useState<PagePushStatus>("DISCONNECTED");
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const sequenceRef = useRef(0);
  const resyncPendingRef = useRef(false);
  const pendingCommandsRef = useRef(new Map<string, { resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void; timeout: ReturnType<typeof setTimeout> }>());
  const command = useCallback((name: string, payload: Record<string, unknown> = {}) => new Promise<Record<string, unknown>>((resolve, reject) => {
    const socket = socketRef.current;
    if (!page || socket?.readyState !== WebSocket.OPEN) { reject(new Error("Scanner WebSocket is not connected")); return; }
    const requestId = String(Date.now()) + "-" + Math.random().toString(36).slice(2, 10);
    const timeout = setTimeout(() => { pendingCommandsRef.current.delete(requestId); reject(new Error("Scanner command timed out")); }, 15000);
    pendingCommandsRef.current.set(requestId, { resolve, reject, timeout });
    try { socket.send(JSON.stringify({ action: "command", page, command: name, payload, request_id: requestId })); }
    catch (error) { clearTimeout(timeout); pendingCommandsRef.current.delete(requestId); reject(error instanceof Error ? error : new Error("Could not send scanner command")); }
  }), [page]);

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
    let watchdogTimer: ReturnType<typeof setInterval> | undefined;
    let lastTransportAt = 0;
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
        lastTransportAt = Date.now();
        retryMs = 1000;
        setStatus("LIVE");
        socket.send(JSON.stringify({ action: "subscribe", page }));
      };
      socket.onmessage = (event) => {
        if (disposed) return;
        let message: Record<string, unknown>;
        try { message = JSON.parse(String(event.data)) as Record<string, unknown>; }
        catch { return; }
        // Heartbeats prove transport liveness, not market-data freshness.
        // Only timestamp actual page updates for the UI's "Last refresh" label.
        lastTransportAt = Date.now();
        if (message.type === "heartbeat") return;
        if (message.type === "command_ack") {
          const requestId = String(message.request_id || "");
          const pending = pendingCommandsRef.current.get(requestId);
          if (pending) {
            clearTimeout(pending.timeout);
            pendingCommandsRef.current.delete(requestId);
            if (message.ok === true) pending.resolve(message.data && typeof message.data === "object" ? message.data as Record<string, unknown> : {});
            else pending.reject(new Error(String(message.error || "Scanner command rejected")));
          }
          return;
        }
        if (message.type !== "page_update" || message.page !== page) return;
        setLastMessageAt(Date.now());
        const sequence = Number(message.sequence);
        const eventType = String(message.event || "");
        if (!Number.isSafeInteger(sequence) || sequence <= 0) return;
        if (eventType === "SNAPSHOT") {
          sequenceRef.current = sequence;
          resyncPendingRef.current = false;
          setData({ ...(message.data && typeof message.data === "object" ? message.data as Record<string, unknown> : {}), __event: "SNAPSHOT" });
          return;
        }
        if (sequenceRef.current && sequence !== sequenceRef.current + 1) {
          resync();
          return;
        }
        sequenceRef.current = sequence;
        if (eventType === "PATCH" && message.data && typeof message.data === "object") {
          setData((previous) => ({ ...(previous || {}), ...(message.data as Record<string, unknown>), __event: "PATCH" }));
        } else if (eventType === "REMOVE" && message.data && typeof message.data === "object") {
          setData((previous) => ({ ...(previous || {}), ...(message.data as Record<string, unknown>), __event: "REMOVE" }));
        }
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        for (const [requestId, pending] of pendingCommandsRef.current) {
          clearTimeout(pending.timeout);
          pending.reject(new Error("Scanner WebSocket disconnected before acknowledgement"));
          pendingCommandsRef.current.delete(requestId);
        }
        if (disposed) return;
        setStatus("RECONNECTING");
        retryTimer = setTimeout(connect, retryMs);
        retryMs = Math.min(15000, retryMs * 2);
      };
    };

    watchdogTimer = setInterval(() => {
      if (lastTransportAt && Date.now() - lastTransportAt > 45000) {
        setStatus("RECONNECTING");
        socketRef.current?.close();
      }
    }, 10000);
    sequenceRef.current = 0;
    resyncPendingRef.current = false;
    setData(null);
    connect();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (watchdogTimer) clearInterval(watchdogTimer);
      socketRef.current?.close();
      socketRef.current = null;
      for (const [requestId, pending] of pendingCommandsRef.current) {
        clearTimeout(pending.timeout);
        pending.reject(new Error("Scanner page unmounted"));
        pendingCommandsRef.current.delete(requestId);
      }
    };
  }, [page, resync]);

  return (
    <PagePushContext.Provider value={{ page, status, data, lastMessageAt, resync, command }}>
      {children}
    </PagePushContext.Provider>
  );
}

export function usePagePush(expectedPage: string) {
  const value = useContext(PagePushContext);
  if (value.page !== expectedPage) {
    return { status: "DISCONNECTED" as const, data: null, lastMessageAt: null, resync: () => undefined, command: async () => { throw new Error("This page has no active scanner WebSocket"); } };
  }
  return value;
}
