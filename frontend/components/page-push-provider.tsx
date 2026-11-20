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
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
    const mutation = raw as CollectionMutation;
    const collection = mutation.collection;
    const keyField = mutation.key_field;
    if (
      typeof collection !== "string" || !collection.trim() ||
      typeof keyField !== "string" || !keyField.trim() ||
      ["__proto__", "prototype", "constructor"].includes(collection) ||
      ["__proto__", "prototype", "constructor"].includes(keyField) ||
      !["ADD", "UPDATE", "DELETE"].includes(mutation.operation)
    ) continue;
    const current = next[collection];
    if (!Array.isArray(current)) continue;
    const item = mutation.item && typeof mutation.item === "object" && !Array.isArray(mutation.item)
      ? mutation.item
      : undefined;
    const key = mutation.key ?? item?.[keyField];
    if ((typeof key !== "string" && typeof key !== "number") || !String(key).trim()) continue;
    if (mutation.operation !== "DELETE" && !item) continue;
    if (mutation.operation === "ADD" && item?.[keyField] === undefined) continue;
    if (item && item[keyField] !== undefined && String(item[keyField]) !== String(key)) continue;
    const matchesKey = (row: unknown) => {
      if (!row || typeof row !== "object" || Array.isArray(row)) return false;
      const rowKey = (row as Record<string, unknown>)[keyField];
      // Missing, blank, or boolean identities must never collide with a valid
      // string/number key (e.g. the literal string "undefined").
      if ((typeof rowKey !== "string" && typeof rowKey !== "number") || !String(rowKey).trim()) return false;
      return String(rowKey) === String(key);
    };
    const findIndex = current.findIndex(matchesKey);
    const rows = [...current];
    if (mutation.operation === "DELETE") {
      // Remove all matches so a malformed earlier ADD cannot leave duplicate
      // identities visible after a later DELETE.
      next[collection] = rows.filter((row) => !matchesKey(row));
      continue;
    } else if (mutation.operation === "ADD") {
      // ADD is idempotent by key: replace one existing identity and collapse
      // any duplicate keyed rows rather than accumulating repeated deliveries.
      if (findIndex >= 0) {
        rows[findIndex] = item!;
        next[collection] = rows.filter((row, index) => !matchesKey(row) || index === findIndex);
      } else {
        rows.push(item!);
        next[collection] = rows;
      }
      continue;
    } else if (mutation.operation === "UPDATE" && findIndex >= 0) {
      // UPDATE must not create a row when its keyed target is missing. A
      // delayed delta could otherwise resurrect an item that was already deleted.
      rows[findIndex] = { ...(rows[findIndex] as Record<string, unknown>), ...item };
      // Merge every duplicate match and collapse duplicates to one identity.
      next[collection] = rows.filter((row, index) => !matchesKey(row) || index === findIndex);
      continue;
    }
    next[collection] = rows;
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
  const resyncTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const resync = useCallback(() => {
    const socket = socketRef.current;
    if (!page || socket?.readyState !== WebSocket.OPEN || resyncPendingRef.current) return;
    resyncPendingRef.current = true;
    try {
      socket.send(JSON.stringify({ action: "resync", page }));
      if (resyncTimerRef.current) clearTimeout(resyncTimerRef.current);
      // If the authoritative recovery snapshot stalls, restart transport so
      // the page cannot remain indefinitely frozen in RECONNECTING.
      resyncTimerRef.current = setTimeout(() => {
        resyncTimerRef.current = undefined;
        if (resyncPendingRef.current) socket.close();
      }, 8000);
    } catch {
      resyncPendingRef.current = false;
      socket.close();
    }
  }, [page]);

  useEffect(() => {
    if (!page) {
      setStatus("DISCONNECTED");
      setData(null);
      setLastMessageAt(null);
      return;
    }
    // Page channels have independent sequence cursors. Reset transport state when
    // switching pages so a low-sequence snapshot from the new channel is not
    // rejected against the previous channel\u2019s sequence baseline.
    sequenceRef.current = 0;
    hasSnapshotRef.current = false;
    resyncPendingRef.current = false;
    setData(null);
    setLastMessageAt(null);
    let disposed = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let watchdogTimer: ReturnType<typeof setInterval> | undefined;
    let lastTransportAt = 0;
    let connectionStartedAt = 0;
    let initialSnapshotTimer: ReturnType<typeof setTimeout> | undefined;
    let retryMs = 1000;

    const connect = () => {
      if (disposed) return;
      setStatus("CONNECTING");
      connectionStartedAt = Date.now();
      let socket: WebSocket;
      try {
        socket = new WebSocket(getDashboardWsUrl() + "/ws/page-updates");
      } catch {
        hasSnapshotRef.current = false;
        resyncPendingRef.current = false;
        if (resyncTimerRef.current) clearTimeout(resyncTimerRef.current);
        resyncTimerRef.current = undefined;
        setStatus("RECONNECTING");
        retryTimer = setTimeout(connect, retryMs);
        retryMs = Math.min(15000, retryMs * 2);
        return;
      }
      socketRef.current = socket;
      socket.onopen = () => {
        // A previous transport may finish opening after reconnect replaced it.
        if (disposed || socketRef.current !== socket) { socket.close(); return; }
        lastTransportAt = Date.now();
        connectionStartedAt = 0;
        // Do not reset reconnect backoff merely because TCP/WebSocket opened.
        // Repeated UNAVAILABLE snapshots close the socket before synchronization
        // and must progressively back off rather than retry every second.
        // A connected transport is not synchronized until its authoritative
        // page snapshot arrives. Keep REST recovery enabled in the meantime.
        setStatus("CONNECTING");
        try {
          socket.send(JSON.stringify({ action: "subscribe", page }));
        } catch {
          // A send failure is not a synchronized connection; force the
          // existing bounded reconnect path instead of leaving it half-open.
          socket.close();
          return;
        }
        // Heartbeats can keep the transport healthy even when the initial
        // page snapshot never arrives. Bound the synchronization wait and
        // trigger authoritative resync instead of leaving this channel stuck.
        if (initialSnapshotTimer) clearTimeout(initialSnapshotTimer);
        initialSnapshotTimer = setTimeout(() => {
          initialSnapshotTimer = undefined;
          if (!hasSnapshotRef.current && socketRef.current === socket && !disposed) {
            setStatus("RECONNECTING");
            resync();
          }
        }, 8000);
      };
      socket.onmessage = (event) => {
        // Never allow a retired transport to deliver stale snapshots/deltas.
        if (disposed || socketRef.current !== socket) return;
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
          const snapshotData = message.data && typeof message.data === "object"
            ? message.data as Record<string, unknown>
            : {};
          if (snapshotData.snapshot_status === "UNAVAILABLE") {
            // A transport snapshot is not authoritative when its provider failed.
            // Keep REST recovery enabled and retry through bounded reconnect/resync.
            sequenceRef.current = sequence;
            hasSnapshotRef.current = false;
            setLastMessageAt(Date.now());
            resyncPendingRef.current = false;
            if (resyncTimerRef.current) clearTimeout(resyncTimerRef.current);
            resyncTimerRef.current = undefined;
            setStatus("RECONNECTING");
            // Avoid a tight resync loop when the snapshot provider is busy:
            // close this socket and let the existing exponential reconnect
            // backoff retry a fresh subscription snapshot.
            socket.close();
            return;
          }
          sequenceRef.current = sequence;
          hasSnapshotRef.current = true;
          // Reset exponential retry only after a valid authoritative snapshot,
          // not on a transport open that may immediately fail synchronization.
          retryMs = 1000;
          if (initialSnapshotTimer) clearTimeout(initialSnapshotTimer);
          initialSnapshotTimer = undefined;
          setLastMessageAt(Date.now());
          resyncPendingRef.current = false;
          if (resyncTimerRef.current) clearTimeout(resyncTimerRef.current);
          resyncTimerRef.current = undefined;
          setStatus("LIVE");
          setData({ ...snapshotData, __event: "SNAPSHOT" });
          return;
        }
        // A saturated server queue deliberately drops intermediate frames and
        // emits SNAPSHOT_REQUIRED. Recover the authoritative baseline rather
        // than silently ignoring the control frame and leaving stale UI data.
        if (eventType === "SNAPSHOT_REQUIRED") {
          setStatus("RECONNECTING");
          // Keep the last accepted sequence baseline while resync is pending.
          // Clearing it would allow a delayed, older snapshot to rewind state.
          resync();
          return;
        }
        // Ignore unknown frame types without advancing the sequence cursor.
        if (eventType !== "PATCH" && eventType !== "REMOVE") return;
        // Once a gap/overflow requires resync, ignore all deltas until the
        // authoritative snapshot arrives to avoid mutating an incomplete state.
        if (resyncPendingRef.current) return;
        // Do not accept deltas until a full snapshot has established the
        // baseline, and require every subsequent sequence to be contiguous.
        if (!hasSnapshotRef.current) {
          setStatus("RECONNECTING");
          resync();
          return;
        }
        // Duplicate/stale journal deliveries are harmless: ignore them without
        // moving the cursor. Only a forward gap requires authoritative resync.
        if (sequence <= sequenceRef.current) return;
        if (sequence !== sequenceRef.current + 1) {
          setStatus("RECONNECTING");
          resync();
          return;
        }
        sequenceRef.current = sequence;
        setLastMessageAt(Date.now());
        if (eventType === "PATCH" && message.data && typeof message.data === "object") {
          setData((previous) => {
            const payload = message.data as Record<string, unknown>;
            const merged: Record<string, unknown> = { ...(previous || {}), ...payload, __event: "PATCH" };
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
      socket.onerror = () => { if (socketRef.current === socket) socket.close(); };
      socket.onclose = () => {
        if (disposed) return;
        // Ignore stale socket-close callbacks and release recovery timers for this transport.
        if (socketRef.current !== socket) return;
        socketRef.current = null;
        connectionStartedAt = 0;
        if (resyncTimerRef.current) clearTimeout(resyncTimerRef.current);
        resyncTimerRef.current = undefined;
        hasSnapshotRef.current = false;
        resyncPendingRef.current = false;
        if (initialSnapshotTimer) clearTimeout(initialSnapshotTimer);
        initialSnapshotTimer = undefined;
        setStatus("RECONNECTING");
        retryTimer = setTimeout(connect, retryMs);
        retryMs = Math.min(15000, retryMs * 2);
      };
    };

    watchdogTimer = setInterval(() => {
      const now = Date.now();
      if (socketRef.current?.readyState === WebSocket.CONNECTING && connectionStartedAt && now - connectionStartedAt > 30000) {
        // Detect a transport that never reaches onopen; it cannot deliver
        // either snapshots or close notifications while stuck connecting.
        socketRef.current.close();
        return;
      }
      if (lastTransportAt && now - lastTransportAt > 45000) {
        setStatus("RECONNECTING");
        socketRef.current?.close();
        return;
      }
      // Heartbeats establish transport liveness, but page data may legitimately be quiet
      // (e.g. market closed or a scanner has no new observations). Market/feed freshness
      // is reported by the page payload; do not force resync/reconnect on silence.
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
      if (resyncTimerRef.current) clearTimeout(resyncTimerRef.current);
      resyncTimerRef.current = undefined;
      if (initialSnapshotTimer) clearTimeout(initialSnapshotTimer);
      initialSnapshotTimer = undefined;
      socketRef.current?.close();
      socketRef.current = null;
      connectionStartedAt = 0;
    };
  }, [page, resync]);

  return (
    <PagePushContext.Provider value={{ page, status, data, lastMessageAt, resync }}>
      {children}
    </PagePushContext.Provider>
  );
}

export function usePagePush(expectedPage: string): { status: PagePushStatus; data: Record<string, unknown> | null; lastMessageAt: number | null; resync: () => void } {
  const value = useContext(PagePushContext);
  if (value.page !== expectedPage) {
    return { status: "DISCONNECTED" as const, data: null, lastMessageAt: null, resync: () => undefined };
  }
  return value;
}
