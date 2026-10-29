"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { appConfig } from "@/lib/config";

type Opportunity = Record<string, unknown> & {
  symbol?: string; expiry?: string | number; expiry_type?: string; low_strike?: number; high_strike?: number;
  spot_price?: number | null; spot_age_seconds?: number | null;
  direction?: string; executable_edge?: number; last_update_timestamp_ns?: number;
};
type TrackedRow = { key: string; row: Opportunity; lastSeen: number; status: "LIVE" | "STALE" };

function keyOf(row: Opportunity) {
  return [row.symbol, row.expiry, row.low_strike, row.high_strike, row.direction].map(String).join("|");
}
function n(row: Opportunity, field: string) {
  const raw = row[field];
  if (raw === null || raw === undefined || raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}
function quote(row: Opportunity, priceField: string, quantityField: string) {
  const price = n(row, priceField);
  const quantity = n(row, quantityField);
  return `${price === null ? "—" : fmt(price)} (${quantity === null ? "—" : fmt(quantity)})`;
}
function fmt(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
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
  const [expiryView, setExpiryView] = useState<"MONTHLY" | "WEEKLY">("MONTHLY");
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
        if (age >= 60000) rowsRef.current.delete(key);
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

  const expiryRows = useMemo(() => rows.filter((item) => String(item.row.expiry_type || "").toUpperCase() === expiryView), [rows, expiryView]);
  const liveCount = useMemo(() => expiryRows.filter((item) => item.status === "LIVE").length, [expiryRows]);
  return (
    <section className="rounded-2xl border border-slate-700 bg-slate-950 p-4 text-slate-100 shadow-xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">Opportunity ({expiryView === "MONTHLY" ? "Monthly" : "Weekly"})</h2>
          <p className="mt-1 text-xs text-slate-400">Live backend event stream · expiry types kept separate · rows retained for 60 seconds</p>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className={connection === "LIVE" ? "rounded-full bg-emerald-950 px-3 py-1 font-bold text-emerald-300" : "rounded-full bg-amber-950 px-3 py-1 font-bold text-amber-300"}>{connection}</span>
          <span className="text-slate-400">{liveCount} LIVE / {expiryRows.length} {expiryView.toLowerCase()} rows · {rows.length} total</span>
        </div>
      </div>
      <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Opportunity expiry type">
        <button type="button" onClick={() => setExpiryView("MONTHLY")} aria-pressed={expiryView === "MONTHLY"} className={"rounded-lg border px-4 py-2 text-xs font-bold " + (expiryView === "MONTHLY" ? "border-violet-500 bg-violet-950 text-violet-200" : "border-slate-700 text-slate-400")}>Opportunity (Monthly)</button>
        <button type="button" onClick={() => setExpiryView("WEEKLY")} aria-pressed={expiryView === "WEEKLY"} className={"rounded-lg border px-4 py-2 text-xs font-bold " + (expiryView === "WEEKLY" ? "border-cyan-500 bg-cyan-950 text-cyan-200" : "border-slate-700 text-slate-400")}>Opportunity (Weekly)</button>
      </div>
      {expiryRows.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-700 p-8 text-center text-sm text-slate-400">
          {connection !== "LIVE" ? "Connecting to the live scanner…" : rows.length === 0 ? "WebSocket connected — no qualifying opportunity received yet." : `No ${expiryView.toLowerCase()} opportunities received. Only backend rows explicitly classified ${expiryView} are shown.`}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1350px] border-collapse text-left text-xs">
            <thead><tr className="border-b border-slate-700 text-slate-400">
              {["Status","Time (IST)","Underlying","Spot Price","Expiry","Expiry Type","Weekly Opportunity","Low / High Strike","Direction","Box Edge","Bid (qty) / Ask (qty) — 4 legs","Edge / Lot","Gross P&L"].map((label) => <th key={label} className="px-3 py-3 font-semibold">{label}</th>)}
            </tr></thead>
            <tbody>{expiryRows.map(({ key, row, lastSeen, status }) => (
              <tr key={key} className={"border-b border-slate-800 transition-opacity " + (status === "STALE" ? "opacity-40" : "opacity-100")}>
                <td className="px-3 py-3"><span className={status === "LIVE" ? "rounded-full bg-emerald-950 px-2 py-1 font-bold text-emerald-300" : "rounded-full bg-amber-950 px-2 py-1 font-bold text-amber-300"}>{status}</span></td>
                <td className="whitespace-nowrap px-3 py-3 tabular-nums">{new Date(lastSeen).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" })}</td>
                <td className="px-3 py-3 font-bold">{String(row.symbol || "—")}</td>
                <td className="px-3 py-3 tabular-nums">{fmt(row.spot_price)}{n(row, "spot_age_seconds") !== null ? <span className="ml-1 text-[10px] text-slate-400">({fmt(row.spot_age_seconds)}s)</span> : null}</td>
                <td className="px-3 py-3">{String(row.expiry_date || row.expiry || "—")}</td>
                <td className="px-3 py-3"><span className={"rounded-full px-2 py-1 text-[10px] font-bold " + (String(row.expiry_type || "").toUpperCase() === "WEEKLY" ? "bg-cyan-950 text-cyan-300" : String(row.expiry_type || "").toUpperCase() === "MONTHLY" ? "bg-violet-950 text-violet-300" : "bg-slate-800 text-slate-300")}>{String(row.expiry_type || "UNKNOWN").toUpperCase()}</span></td>
                <td className="px-3 py-3"><span className={"rounded-full px-2 py-1 text-[10px] font-bold " + (String(row.expiry_type || "").toUpperCase() === "WEEKLY" ? "bg-cyan-950 text-cyan-300" : String(row.expiry_type || "").toUpperCase() === "MONTHLY" ? "bg-slate-800 text-slate-400" : "bg-slate-800 text-slate-300")}>{String(row.expiry_type || "").toUpperCase() === "WEEKLY" ? "YES" : String(row.expiry_type || "").toUpperCase() === "MONTHLY" ? "NO" : "UNKNOWN"}</span></td>
                <td className="px-3 py-3 tabular-nums">{fmt(row.low_strike)} / {fmt(row.high_strike)}</td>
                <td className="px-3 py-3">{String(row.direction || "—")}</td>
                <td className="px-3 py-3 font-bold text-emerald-300 tabular-nums">{fmt(row.executable_edge)}</td>
                <td className="px-3 py-3 whitespace-nowrap tabular-nums"><span className="font-semibold text-emerald-300">LC · BUY / ASK</span> · Bid {quote(row, "low_call_bid", "low_call_bid_qty")} · <span className="rounded bg-emerald-950 px-1.5 py-0.5 font-semibold text-emerald-300">Ask {quote(row, "low_call_ask", "low_call_ask_qty")}</span><br/><span className="font-semibold text-rose-300">LP · SELL / BID</span> · <span className="rounded bg-rose-950 px-1.5 py-0.5 font-semibold text-rose-300">Bid {quote(row, "low_put_bid", "low_put_bid_qty")}</span> · Ask {quote(row, "low_put_ask", "low_put_ask_qty")}<br/><span className="font-semibold text-rose-300">HC · SELL / BID</span> · <span className="rounded bg-rose-950 px-1.5 py-0.5 font-semibold text-rose-300">Bid {quote(row, "high_call_bid", "high_call_bid_qty")}</span> · Ask {quote(row, "high_call_ask", "high_call_ask_qty")}<br/><span className="font-semibold text-emerald-300">HP · BUY / ASK</span> · Bid {quote(row, "high_put_bid", "high_put_bid_qty")} · <span className="rounded bg-emerald-950 px-1.5 py-0.5 font-semibold text-emerald-300">Ask {quote(row, "high_put_ask", "high_put_ask_qty")}</span></td>
                <td className="px-3 py-3 tabular-nums">{fmt(row.edge_per_lot)}</td>
                <td className="px-3 py-3 tabular-nums">{fmt(row.gross_pnl)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-[11px] text-slate-500">Last WebSocket message: {lastMessageAt ? new Date(lastMessageAt).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" }) : "waiting"} · Rows become STALE after 1.5s and are removed after 60s without updates.</p>
    </section>
  );
}
