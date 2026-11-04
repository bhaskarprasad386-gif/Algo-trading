"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, ArrowDownUp, Radio, Wifi, WifiOff } from "lucide-react";
import { appConfig } from "@/lib/config";

type Opportunity = Record<string, unknown> & {
  symbol?: string; expiry?: string | number; expiry_type?: string; low_strike?: number; high_strike?: number;
  spot_price?: number | null; spot_timestamp_ns?: number | string | null; spot_age_seconds?: number | null;
  direction?: string; executable_edge?: number; last_update_timestamp_ns?: number | string; timestamp_skew_ms?: number | null;
  edge_per_lot?: number; gross_pnl?: number; lot_size?: number; max_executable_lots?: number;
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
function timestampMilliseconds(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const raw = String(value);
  if (!/^\d+$/.test(raw)) return null;
  try {
    // Keep nanosecond epoch values exact until after conversion to milliseconds.
    const integer = BigInt(raw);
    if (integer >= BigInt("100000000000000000")) return Number(integer / BigInt("1000000"));
    if (integer >= BigInt("1000000000000")) return Number(integer);
    if (integer >= BigInt("1000000000")) return Number(integer * BigInt("1000"));
  } catch {
    return null;
  }
  return null;
}
function spotFeedStatus(row: Opportunity, receivedAt?: number) {
  const price = n(row, "spot_price");
  const timestampMs = timestampMilliseconds(row.spot_timestamp_ns);
  const fallbackAge = n(row, "spot_age_seconds");
  const ageSeconds = timestampMs === null
    ? fallbackAge === null
      ? null
      : fallbackAge + (receivedAt == null ? 0 : Math.max(0, Date.now() - receivedAt) / 1000)
    : (Date.now() - timestampMs) / 1000;
  if (price === null || ageSeconds === null || !Number.isFinite(ageSeconds) || ageSeconds < 0) {
    return { label: "SPOT WAITING", age: null, live: false };
  }
  return { label: ageSeconds <= 5 ? "SPOT LIVE" : "SPOT STALE", age: ageSeconds, live: ageSeconds <= 5 };
}
function quoteTime(value: unknown) {
  const milliseconds = timestampMilliseconds(value);
  if (milliseconds === null || !Number.isFinite(milliseconds) || milliseconds <= 0) return "—";
  return new Date(milliseconds).toLocaleTimeString("en-IN", {
    hour12: false, timeZone: "Asia/Kolkata",
  }) + " IST";
}
function fmt(value: unknown, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("en-IN", { maximumFractionDigits: digits }) : "—";
}
function quote(row: Opportunity, priceField: string, quantityField: string) {
  return { price: n(row, priceField), qty: n(row, quantityField) };
}
function makeWsUrl() {
  const base = typeof window === "undefined" ? appConfig.wsUrl : new URL(appConfig.wsUrl, window.location.href).toString();
  const url = new URL(base);
  url.protocol = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/ws/live-opportunities";
  url.search = "";
  return url.toString();
}
function LegQuote({ row, name, price, qty, side, depthKey }: { row: Opportunity; name: string; price: string; qty: string; side: "BUY" | "SELL"; depthKey: string }) {
  const q = quote(row, price, qty);
  const buy = side === "BUY";
  const depth = row.market_depth && typeof row.market_depth === "object"
    ? (row.market_depth as Record<string, unknown>)[depthKey] as { status?: string; bids?: Array<{price:number;quantity:number}>; asks?: Array<{price:number;quantity:number}> } | undefined
    : undefined;
  const ladder = (levels: Array<{price:number;quantity:number}> | undefined) => (levels || []).slice(0, 5);
  const bids = ladder(depth?.bids);
  const asks = ladder(depth?.asks);
  const live = depth?.status === "LIVE";
  return (
    <div className="flex min-w-[150px] flex-col gap-1 rounded-lg border theme-border theme-surface-2 px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-bold tracking-wide theme-text">{name}</span>
        <span className={"rounded px-1.5 py-0.5 text-[9px] font-extrabold " + (buy ? "bg-emerald-400/15 text-emerald-300" : "bg-rose-400/15 text-rose-300")}>{buy ? "BUY · ASK" : "SELL · BID"}</span>
      </div>
      <div className={"text-sm font-bold tabular-nums " + (buy ? "text-emerald-300" : "text-rose-300")}>{fmt(q.price)}</div>
      <div className="text-[10px] theme-muted">L1 Qty {fmt(q.qty, 0)}</div>
      <div className="mt-1 border-t theme-border pt-1">
        <div className="mb-1 flex justify-between text-[9px] font-bold uppercase tracking-wide theme-muted"><span>Bid · Qty</span><span>Ask · Qty</span></div>
        {live ? Array.from({length: Math.max(bids.length, asks.length, 5)}, (_, index) => (
          <div key={index} className="grid grid-cols-2 gap-2 text-[9px] tabular-nums">
            <span className="flex justify-between gap-1 text-rose-300"><span>{bids[index] ? fmt(bids[index].price) : "—"}</span><span className="theme-muted">{bids[index] ? fmt(bids[index].quantity, 0) : "—"}</span></span>
            <span className="flex justify-between gap-1 text-emerald-300"><span>{asks[index] ? fmt(asks[index].price) : "—"}</span><span className="theme-muted">{asks[index] ? fmt(asks[index].quantity, 0) : "—"}</span></span>
          </div>
        )) : <div className="text-[9px] theme-muted">Depth unavailable / stale</div>}
      </div>
    </div>
  );
}

export default function LiveOpportunityGrid() {
  const [rows, setRows] = useState<TrackedRow[]>([]);
  const [connection, setConnection] = useState<"CONNECTING" | "LIVE" | "RECONNECTING">("CONNECTING");
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);
  const [expiryView, setExpiryView] = useState<"MONTHLY" | "WEEKLY">("MONTHLY");
  const [sortByEdge, setSortByEdge] = useState(true);
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
        else if (age >= 1500 && tracked.status !== "STALE") rowsRef.current.set(key, { ...tracked, status: "STALE" });
      }
      setRows(Array.from(rowsRef.current.values()).sort((a, b) => b.lastSeen - a.lastSeen));
    };
    const scheduleReconnect = () => {
      if (disposed) return;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = setTimeout(connect, retryMs);
      retryMs = Math.min(15000, retryMs * 2);
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
            const incomingRawTimestamp = row.last_update_timestamp_ns;
            const incomingTimestamp = incomingRawTimestamp == null ? "" : String(incomingRawTimestamp);
            // Compare exchange nanosecond timestamps as integers, not JS Numbers.
            // Number conversion can collapse distinct large timestamps at 1e18.
            if (!/^\d+$/.test(incomingTimestamp)) continue;
            let incomingNs: bigint;
            try { incomingNs = BigInt(incomingTimestamp); } catch { continue; }
            if (incomingNs <= BigInt("0")) continue;
            const key = keyOf(row);
            const existing = rowsRef.current.get(key);
            const existingRawTimestamp = existing?.row.last_update_timestamp_ns;
            const existingTimestamp = existingRawTimestamp == null ? "" : String(existingRawTimestamp);
            // Ignore duplicate and out-of-order frames. Replayed snapshots must
            // not keep an old opportunity looking live by refreshing lastSeen.
            if (/^\d+$/.test(existingTimestamp) && incomingNs <= BigInt(existingTimestamp)) continue;
            rowsRef.current.set(key, { key, row, lastSeen: now, status: "LIVE" });
          }
          publishRows();
        } catch { /* Ignore malformed frames without breaking the stream. */ }
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => { if (!disposed) { setConnection("RECONNECTING"); scheduleReconnect(); } };
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
  const orderedRows = useMemo(() => [...expiryRows].sort((a, b) => sortByEdge
    ? (n(b.row, "executable_edge") ?? -Infinity) - (n(a.row, "executable_edge") ?? -Infinity)
    : b.lastSeen - a.lastSeen), [expiryRows, sortByEdge]);
  const liveCount = useMemo(() => expiryRows.filter((item) => item.status === "LIVE").length, [expiryRows]);
  // An open socket alone is not proof of a healthy stream; require a recent server frame.
  const streamLabel = connection !== "LIVE" ? connection : lastMessageAt === null ? "WAITING DATA" : Date.now() - lastMessageAt > 15000 ? "NO RECENT DATA" : "LIVE";
  const streamHealthy = streamLabel === "LIVE";

  return (
    <section className="box-live-opportunities overflow-hidden rounded-2xl border theme-border theme-surface theme-text shadow-[0_18px_55px_rgba(2,6,23,0.28)]">
      <div className="border-b theme-border bg-gradient-to-r from-[var(--app-surface-2)] via-[var(--app-surface)] to-[var(--app-surface-2)] p-4 sm:p-5">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex items-start gap-3">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-cyan-400/20 bg-cyan-400/10 text-cyan-300"><Activity size={19}/></div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-lg font-black tracking-wide text-slate-950 sm:text-xl">Live Box Opportunities</h2>
                <span className="rounded-md border border-slate-300 bg-slate-100 px-3 py-1 text-xs font-black uppercase tracking-wide text-slate-900">Four-leg basket</span>
              </div>
              <p className="mt-1 text-xs theme-muted">Live event stream · executable quote sides · weekly and monthly expiries stay separate</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            <span role="status" aria-live="polite" aria-atomic="true" className={"inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 font-bold " + (streamHealthy ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" : "border-amber-500/30 bg-amber-500/10 text-amber-200")}>
              {streamHealthy ? <Wifi size={13} aria-hidden="true"/> : <WifiOff size={13} aria-hidden="true"/>} {streamLabel} · {lastMessageAt === null ? "last frame —" : "last frame " + Math.max(0, (Date.now() - lastMessageAt) / 1000).toFixed(1) + "s ago"}
            </span>
            <span className="rounded-lg border theme-border theme-surface-2 px-3 py-2 theme-text">{liveCount} live <span className="theme-muted">/ {expiryRows.length} retained</span></span>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div className="inline-flex rounded-xl border theme-border theme-surface-2 p-1" role="group" aria-label="Opportunity expiry type">
            <button type="button" onClick={() => setExpiryView("MONTHLY")} aria-pressed={expiryView === "MONTHLY"} className={"rounded-lg px-4 py-2 text-[11px] font-bold transition " + (expiryView === "MONTHLY" ? "bg-violet-400/15 text-violet-200 shadow-sm" : "theme-muted hover:theme-text")}>Monthly</button>
            <button type="button" onClick={() => setExpiryView("WEEKLY")} aria-pressed={expiryView === "WEEKLY"} className={"rounded-lg px-4 py-2 text-[11px] font-bold transition " + (expiryView === "WEEKLY" ? "bg-cyan-400/15 text-cyan-200 shadow-sm" : "theme-muted hover:theme-text")}>Weekly</button>
          </div>
          <button type="button" onClick={() => setSortByEdge((value) => !value)} className="inline-flex min-h-9 items-center gap-2 rounded-lg border theme-border px-3 text-[11px] font-semibold theme-text hover:theme-border">
            <ArrowDownUp size={13}/> {sortByEdge ? "Highest edge first" : "Latest update first"}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-px bg-slate-800 sm:grid-cols-4">
        {[
          ["Expiry view", expiryView],
          ["Live rows", String(liveCount)],
          ["Displayed rows", String(expiryRows.length)],
          ["Stream update", lastMessageAt ? new Date(lastMessageAt).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" }) + " IST" : "Waiting"],
        ].map(([label, value]) => <div key={label} className="theme-surface-2 px-3 py-3 sm:px-4"><p className="text-[9px] font-bold uppercase tracking-wider theme-muted">{label}</p><p className="mt-1 truncate text-sm font-bold tabular-nums theme-text">{value}</p></div>)}
      </div>

      {orderedRows.length === 0 ? (
        <div className="px-5 py-12 text-center">
          <div className="mx-auto grid h-11 w-11 place-items-center rounded-full border theme-border theme-surface-2 theme-muted"><Radio size={19}/></div>
          <p className="mt-3 text-sm font-semibold theme-text">{!streamHealthy ? (streamLabel === "NO RECENT DATA" ? "Live stream has stopped sending updates" : "Connecting to the live scanner") : rows.length === 0 ? "Waiting for a qualifying opportunity" : "No " + expiryView.toLowerCase() + " opportunities in the current stream window"}</p>
          <p className="mx-auto mt-1 max-w-xl text-xs leading-5 theme-muted">{!streamHealthy ? "Waiting for a fresh server frame. No historical values are presented as live." : "Only backend events explicitly classified " + expiryView + " are shown here. This empty state does not create or estimate opportunities."}</p>
        </div>
      ) : (
        <div className="space-y-3 p-3 sm:p-4">
          {orderedRows.map(({ key, row, lastSeen, status }) => (
            <article key={key} className={"overflow-hidden rounded-xl border " + (status === "LIVE" ? "theme-border theme-surface-2" : "border-amber-500/20 theme-surface-2 opacity-75")}>
              <div className="flex flex-wrap items-center justify-between gap-3 border-b theme-border px-3 py-3 sm:px-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-extrabold tracking-wide theme-text">{String(row.symbol || "—")}</span>
                  <span className={"rounded-md px-2 py-1 text-[9px] font-extrabold " + (expiryView === "WEEKLY" ? "bg-cyan-400/10 text-cyan-200" : "bg-violet-400/10 text-violet-200")}>{expiryView}</span>
                  <span className="text-[11px] theme-muted">Expiry {String(row.expiry_date || row.expiry || "—")}</span>
                  <span className="inline-flex flex-wrap items-center gap-1.5 text-[11px]">
                    <span className="theme-text">Spot ₹{fmt(row.spot_price)}</span>
                    {(() => { const spot = spotFeedStatus(row, lastSeen); return <><span className={"rounded px-1.5 py-0.5 text-[9px] font-bold " + (spot.live ? "bg-emerald-400/10 text-emerald-300" : "bg-amber-400/10 text-amber-200")}>{spot.label}</span>{spot.age !== null && <span className="theme-muted">{fmt(spot.age, 2)}s</span>}</>; })()}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className={"rounded-md px-2 py-1 text-[9px] font-bold " + (status === "LIVE" ? "bg-emerald-400/10 text-emerald-300" : "bg-amber-400/10 text-amber-200")}>{status}</span>
                  <div className="flex flex-col items-end gap-0.5 text-[9px] tabular-nums theme-muted">
                    <span>Quote {quoteTime(row.last_update_timestamp_ns)}</span>
                    <span>4-leg skew {(() => { const skew = n(row, "timestamp_skew_ms"); return skew === null || skew < 0 ? "—" : fmt(skew, 3) + " ms"; })()}</span>
                    <span>Received {new Date(lastSeen).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" })} IST</span>
                    <span className={status === "LIVE" ? "text-emerald-400" : "text-amber-300"}>{status === "LIVE" ? "Updated" : "No update"} {Math.max(0, (Date.now() - lastSeen) / 1000).toFixed(1)}s ago</span>
                  </div>
                </div>
              </div>
              <div className="grid gap-3 p-3 sm:p-4 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-center">
                <div>
                  <div className="mb-2 flex flex-wrap items-center gap-2 text-[10px] theme-muted">
                    <span>Strikes <b className="theme-text">{fmt(row.low_strike, 0)} / {fmt(row.high_strike, 0)}</b></span>
                    <span className="theme-muted">•</span><span>Direction <b className="theme-text">{String(row.direction || "—")}</b></span>
                    <span className="theme-muted">•</span><span>Lot size <b className="theme-text">{fmt(row.lot_size, 0)}</b></span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
                    {String(row.direction || "LONG").toUpperCase() === "SHORT" ? (
                      <>
                        <LegQuote row={row} name="LOW CE" depthKey="low_call" price="low_call_bid" qty="low_call_bid_qty" side="SELL"/>
                        <LegQuote row={row} name="LOW PE" depthKey="low_put" price="low_put_ask" qty="low_put_ask_qty" side="BUY"/>
                        <LegQuote row={row} name="HIGH CE" depthKey="high_call" price="high_call_ask" qty="high_call_ask_qty" side="BUY"/>
                        <LegQuote row={row} name="HIGH PE" depthKey="high_put" price="high_put_bid" qty="high_put_bid_qty" side="SELL"/>
                      </>
                    ) : (
                      <>
                        <LegQuote row={row} name="LOW CE" depthKey="low_call" price="low_call_ask" qty="low_call_ask_qty" side="BUY"/>
                        <LegQuote row={row} name="LOW PE" depthKey="low_put" price="low_put_bid" qty="low_put_bid_qty" side="SELL"/>
                        <LegQuote row={row} name="HIGH CE" depthKey="high_call" price="high_call_bid" qty="high_call_bid_qty" side="SELL"/>
                        <LegQuote row={row} name="HIGH PE" depthKey="high_put" price="high_put_ask" qty="high_put_ask_qty" side="BUY"/>
                      </>
                    )}
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2 xl:min-w-[230px] xl:grid-cols-1">
                  <div className="rounded-xl border border-emerald-500/20 bg-emerald-500/[0.07] p-3">
                    <p className="text-[9px] font-bold uppercase tracking-wider text-emerald-200/70">Executable edge</p>
                    <p className="mt-1 text-xl font-extrabold tabular-nums text-emerald-300">₹{fmt(row.executable_edge)}</p>
                  </div>
                  <div className="rounded-xl border theme-border theme-surface-2 p-3">
                    <p className="text-[9px] font-bold uppercase tracking-wider theme-muted">Edge / lot · Gross P&amp;L</p>
                    <p className="mt-1 text-sm font-bold tabular-nums theme-text">₹{fmt(row.edge_per_lot)} <span className="theme-muted">·</span> ₹{fmt(row.gross_pnl)}</p>
                  </div>
                  <div className="rounded-xl border theme-border theme-surface-2 p-3">
                    <p className="text-[9px] font-bold uppercase tracking-wider theme-muted">Max Executable Lots</p>
                    <p className="mt-1 text-lg font-extrabold tabular-nums theme-text">{(() => { const lots = n(row, "max_executable_lots"); return lots === null || lots < 0 || !Number.isInteger(lots) ? "—" : lots.toLocaleString("en-IN") + " lots"; })()}</p>
                    <p className="mt-1 text-[9px] theme-muted">Common four-leg depth capacity · not a fill guarantee</p>
                  </div>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t theme-border theme-surface-2 px-4 py-3 text-[10px] theme-muted">
        <span>Executable edge uses synchronized VWAP across up to five available depth levels; each leg may contribute a different number of levels. Max Executable Lots is common four-leg depth capacity. Displayed ladders are live snapshots, not a fill guarantee.</span>
        <span>Four-leg quote sides follow LONG/SHORT direction; green = BUY/ASK, red = SELL/BID.</span>
        <span>Rows become STALE after 1.5s and expire after 60s without updates.</span>
      </div>
    </section>
  );
}
