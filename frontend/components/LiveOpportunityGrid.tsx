"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { appConfig } from "@/lib/config";

type Opportunity = Record<string, unknown> & {
  symbol?: string; expiry?: string | number; low_strike?: number; high_strike?: number;
  direction?: string; executable_edge?: number; last_update_timestamp_ns?: number;
};
type TrackedRow = { key: string; row: Opportunity; lastSeen: number; status: "LIVE" | "STALE" };

function keyOf(row: Opportunity) {
  return [row.symbol, row.expiry, row.low_strike, row.high_strike, row.direction].map(String).join("|");
}
function n(row: Opportunity, field: string) {
  const value = Number(row[field]);
  return Number.isFinite(value) ? value : null;
}
function fmt(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—";
}
function makeWsUrl() {
  const base = typeof window === "undefined" ? appConfig.wsUrl : new URL(appConfig.wsUrl, window.location.href).toString();
  const url = new URL(base);
  url.protocol = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/ws/live-opportunities";
  url.search = "";
  return url.toString();
}

export default function LiveOpportunityGrid() {
  const [rows, setRows] = useState<TrackedRow[]>([]);
  const [connection, setConnection] = useState<"CONNECTING" | "LIVE" | "RECONNECTING">("CONNECTING");
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);
  const rowsRef = useRef<Map<string, TrackedRow>>(new Map());
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let disposed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let staleTimer: ReturnType<typeof setInterval> | undefined;
    let retryMs = 1000;

    const publishRows = () => {
      const now = Date.now();
      for (const [key, tracked] of rowsRef.current) {
        const age = now - tracked.lastSeen;
        if (age >= 5000) rowsRef.current.delete(key);
        else if (age >= 1500 && tracked.status !== "STALE") {
          rowsRef.current.set(key, { ...tracked, status: "STALE" });
        }
      }
      setRows(Array.from(rowsRef.current.values()).sort((a, b) => b.lastSeen - a.lastSeen));
    };

    const connect = () => {
      if (disposed) return;
      setConnection(rowsRef.current.size ? "RECONNECTING" : "CONNECTING");
      let socket: WebSocket;
      try { socket = new WebSocket(makeWsUrl()); }
      catch { setConnection("RECONNECTING"); scheduleReconnect(); return; }
      socketRef.current = socket;
      socket.onopen = () => { retryMs = 1000; setConnection("LIVE"); };
      socket.onmessage = (event) => {
        if (disposed) return;
        setLastMessageAt(Date.now());
        try {
          const message = JSON.parse(String(event.data));
          if (message.type !== "opportunities" || !Array.isArray(message.data)) return;
          const now = Date.now();
          for (const raw of message.data) {
            if (!raw || typeof raw !== "object") continue;
            const row = raw as Opportunity;
            const edge = n(row, "executable_edge");
            const symbol = String(row.symbol || "").toUpperCase();
            const threshold = symbol === "NIFTY" ? 5 : 10;
            if (edge === null || edge <= threshold) continue;
            const key = keyOf(row);
            rowsRef.current.set(key, { key, row, lastSeen: now, status: "LIVE" });
          }
          publishRows();
        } catch { /* Ignore malformed frames without breaking the stream. */ }
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        if (!disposed) { setConnection("RECONNECTING"); scheduleReconnect(); }
      };
    };
    const scheduleReconnect = () => {
      if (disposed) return;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, retryMs);
      retryMs = Math.min(15000, retryMs * 2);
    };

    staleTimer = setInterval(publishRows, 200);
    connect();
    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (staleTimer) clearInterval(staleTimer);
      socketRef.current?.close();
      socketRef.current = null;
      rowsRef.current.clear();
    };
  }, []);

  const liveCount = useMemo(() => rows.filter((item) => item.status === "LIVE").length, [rows]);
  return (
    <section className="rounded-2xl border border-slate-700 bg-slate-950 p-4 text-slate-100 shadow-xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">Live Box Spread Opportunities</h2>
          <p className="mt-1 text-xs text-slate-400">Event stream · threshold-filtered · rows retained for 5 seconds</p>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className={connection === "LIVE" ? "rounded-full bg-emerald-950 px-3 py-1 font-bold text-emerald-300" : "rounded-full bg-amber-950 px-3 py-1 font-bold text-amber-300"}>{connection}</span>
          <span className="text-slate-400">{liveCount} LIVE / {rows.length} visible</span>
        </div>
      </div>
      {rows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-700 p-8 text-center text-sm text-slate-400">
          {connection === "LIVE" ? "WebSocket connected — no qualifying opportunity received yet." : "Connecting to the live scanner…"}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse text-left text-xs">
            <thead><tr className="border-b border-slate-700 text-slate-400">
              {["Status","Time (IST)","Underlying","Expiry","Low / High Strike","Direction","Box Edge","Edge / Lot","Gross P&L"].map((label) => <th key={label} className="px-3 py-3 font-semibold">{label}</th>)}
            </tr></thead>
            <tbody>{rows.map(({ key, row, lastSeen, status }) => (
              <tr key={key} className={"border-b border-slate-800 transition-opacity " + (status === "STALE" ? "opacity-40" : "opacity-100")}>
                <td className="px-3 py-3"><span className={status === "LIVE" ? "rounded-full bg-emerald-950 px-2 py-1 font-bold text-emerald-300" : "rounded-full bg-amber-950 px-2 py-1 font-bold text-amber-300"}>{status}</span></td>
                <td className="whitespace-nowrap px-3 py-3 tabular-nums">{new Date(lastSeen).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" })}</td>
                <td className="px-3 py-3 font-bold">{String(row.symbol || "—")}</td>
                <td className="px-3 py-3">{String(row.expiry_date || row.expiry || "—")}</td>
                <td className="px-3 py-3 tabular-nums">{fmt(row.low_strike)} / {fmt(row.high_strike)}</td>
                <td className="px-3 py-3">{String(row.direction || "—")}</td>
                <td className="px-3 py-3 font-bold text-emerald-300 tabular-nums">{fmt(row.executable_edge)}</td>
                <td className="px-3 py-3 tabular-nums">{fmt(row.edge_per_lot)}</td>
                <td className="px-3 py-3 tabular-nums">{fmt(row.gross_pnl)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-[11px] text-slate-500">Last WebSocket message: {lastMessageAt ? new Date(lastMessageAt).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" }) : "waiting"} · Rows become STALE after 1.5s and are removed after 5s without updates.</p>
    </section>
  );
}
