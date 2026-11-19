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


type CollectionMutation = {
  collection: string;
  operation: "ADD" | "UPDATE" | "DELETE";
  key_field: string;
  key?: string | number;
  item?: Record<string, unknown>;
};


function applyCollectionMutations(
  state: Record<string, unknown>,
  mutations: unknown,
): Record<string, unknown> {
  if (!Array.isArray(mutations)) return state;
  const next = { ...state };
  for (const raw of mutations) {
    if (!raw || typeof raw !== "object") continue;
    const mutation = raw as CollectionMutation;
    if (!mutation.collection || !mutation.key_field || !["ADD", "UPDATE", "DELETE"].includes(mutation.operation)) continue;
    const current = next[mutation.collection];
    if (!Array.isArray(current)) continue;
    const item = mutation.item && typeof mutation.item === "object" ? mutation.item : undefined;
    const key = mutation.key ?? item?.[mutation.key_field];
    if (key === undefined || key === null) continue;
    const findIndex = current.findIndex((row) => row && typeof row === "object" && String((row as Record<string, unknown>)[mutation.key_field]) === String(key));
    const rows = [...current];
    if (mutation.operation === "DELETE") {
      if (findIndex >= 0) rows.splice(findIndex, 1);
    } else if (item) {
      if (findIndex >= 0) rows[findIndex] = mutation.operation === "UPDATE" ? { ...(rows[findIndex] as Record<string, unknown>), ...item } : item;
      else rows.push(item);
    }
    next[mutation.collection] = rows;
  }
  return next;
}

export function PagePushProvider({ page, children }: { page: string | null; children: ReactNode }) {
  const [status, setStatus] = useState<PagePushStatus>("DISCONNECTED");
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const sequenceRef = useRef(0);
  const resyncPendingRef = useRef(false);
  const hasSnapshotRef = useRef(false);

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
    let lastPageUpdateAt = 0;
    let retryMs = 1000;

    const connect = () => {
      if (disposed) return;
      setStatus("CONNECTING");
      let socket: WebSocket;
      try {
        socket = new WebSocket(getDashboardWsUrl() + "/ws/page-updates");
      } catch {
        hasSnapshotRef.current = false;
        resyncPendingRef.current = false;
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
        // A connected transport is not synchronized until its authoritative
        // page snapshot arrives. Keep REST recovery enabled in the meantime.
        setStatus("CONNECTING");
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
        if (message.type !== "page_update" || message.page !== page) return;
        const sequence = Number(message.sequence);
        const eventType = String(message.event || "");
        // A newly created page channel legitimately starts at sequence 0.
        // Accept that authoritative initial snapshot; later PATCH/REMOVE frames
        // must still carry a positive sequence.
        if (!Number.isSafeInteger(sequence) || sequence < 0) return;
        if (eventType === "SNAPSHOT") {
          // Never let a delayed recovery snapshot rewind an already-live
          // channel's sequence cursor or replace newer pushed market state.
          if (hasSnapshotRef.current && sequence < sequenceRef.current) return;
          sequenceRef.current = sequence;
          hasSnapshotRef.current = true;
          lastPageUpdateAt = Date.now();
          setLastMessageAt(lastPageUpdateAt);
          resyncPendingRef.current = false;
          setStatus("LIVE");
          setData({ ...(message.data && typeof message.data === "object" ? message.data as Record<string, unknown> : {}), __event: "SNAPSHOT" });
          return;
        }
        // Ignore unknown frame types without advancing the sequence cursor.
        if (eventType !== "PATCH" && eventType !== "REMOVE") return;
        // Do not accept deltas until a full snapshot has established the
        // baseline, and require every subsequent sequence to be contiguous.
        if (!hasSnapshotRef.current || sequence !== sequenceRef.current + 1) {
          setStatus("RECONNECTING");
          resync();
          return;
        }
        sequenceRef.current = sequence;
        lastPageUpdateAt = Date.now();
        setLastMessageAt(lastPageUpdateAt);
        if (eventType === "PATCH" && message.data && typeof message.data === "object") {
          setData((previous) => {
            const payload = message.data as Record<string, unknown>;
            const merged = { ...(previous || {}), ...payload, __event: "PATCH" };
            delete merged.collection_changes;
            return applyCollectionMutations(merged, payload.collection_changes);
          });
        } else if (eventType === "REMOVE" && message.data && typeof message.data === "object") {
          const payload = message.data as Record<string, unknown>;
          const removeKeys = Array.isArray(payload.remove_keys)
            ? payload.remove_keys.filter((key): key is string => typeof key === "string")
            : Array.isArray(payload.keys)
              ? payload.keys.filter((key): key is string => typeof key === "string")
              : [];
          setData((previous) => {
            const next: Record<string, unknown> = { ...(previous || {}), __event: "REMOVE" };
            for (const key of removeKeys) delete next[key];
            for (const [key, value] of Object.entries(payload)) {
              if (key !== "remove_keys" && key !== "keys") next[key] = value;
            }
            delete next.collection_changes;
            return applyCollectionMutations(next, payload.collection_changes);
          });
        }
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        if (disposed) return;
        hasSnapshotRef.current = false;
        resyncPendingRef.current = false;
        setStatus("RECONNECTING");
        retryTimer = setTimeout(connect, retryMs);
        retryMs = Math.min(15000, retryMs * 2);
      };
    };

    watchdogTimer = setInterval(() => {
      const now = Date.now();
      if (lastTransportAt && now - lastTransportAt > 45000) {
        setStatus("RECONNECTING");
        socketRef.current?.close();
        return;
      }
      // Heartbeats can keep transport alive while page data itself has stalled.
      if (hasSnapshotRef.current && lastPageUpdateAt && now - lastPageUpdateAt > 15000) {
        setStatus("RECONNECTING");
        if (now - lastPageUpdateAt > 30000) socketRef.current?.close();
        else resync();
      }
    }, 10000);
    sequenceRef.current = 0;
    hasSnapshotRef.current = false;
    resyncPendingRef.current = false;
    setData(null);
    connect();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (watchdogTimer) clearInterval(watchdogTimer);
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
