"use client";

import { useCallback, useEffect, useRef, useState, useMemo } from "react";
import { Archive, RefreshCw } from "lucide-react";
import { Card } from "@/components/ui";
import { appConfig } from "@/lib/config";

type Snapshot = {
  symbol: string;
  instrument_class?: string;
  expiry: string | number;
  expiry_date?: string;
  expiry_type?: "WEEKLY" | "MONTHLY" | "UNKNOWN";
  low_strike: number;
  high_strike: number;
  direction: string;
  observed_at: string;
  timestamp_ns?: number | string | null;
  spot_price?: number | null;
  executable_edge?: number | null;
  edge_per_lot?: number | null;
  gross_pnl?: number | null;
  lot_size?: number | null;
  max_executable_lots?: number | null;
  freshness?: string;
  last_update_timestamp_ns?: number | string | null;
  timestamp_skew_ms?: number | null;
  low_call_ltp?: number | null; low_call_bid?: number | null; low_call_ask?: number | null; low_call_bid_qty?: number | null; low_call_ask_qty?: number | null;
  low_put_ltp?: number | null; low_put_bid?: number | null; low_put_ask?: number | null; low_put_bid_qty?: number | null; low_put_ask_qty?: number | null;
  high_call_ltp?: number | null; high_call_bid?: number | null; high_call_ask?: number | null; high_call_bid_qty?: number | null; high_call_ask_qty?: number | null;
  high_put_ltp?: number | null; high_put_bid?: number | null; high_put_ask?: number | null; high_put_bid_qty?: number | null; high_put_ask_qty?: number | null;
  trace_entry_sides?: Record<string, string>;
  trace_entry_prices?: Record<string, number | null>;
  trace_entry_depth?: Record<string, number | null> | Array<number | null>;
  depth_legs?: Array<{
    role: string;
    side: string;
    vwap?: number | null;
    available_quantity?: number | null;
    selected_quantity?: number | null;
    timestamp_ns?: number | string | null;
    depth_status?: string;
    bid_depth_levels?: Array<{ price: number; quantity: number }>;
    ask_depth_levels?: Array<{ price: number; quantity: number }>;
  }>;
};

type SnapshotResponse = {
  strategy: string;
  days: number;
  count: number;
  total_count: number;
  expiry_counts: { WEEKLY: number; MONTHLY: number; UNKNOWN: number };
  offset: number;
  limit: number;
  has_more: boolean;
  data: Snapshot[];
};

const number = (value: number | null | undefined) =>
  value == null || !Number.isFinite(Number(value))
    ? "—"
    : Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 });

const observedTime = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "short", timeStyle: "medium" });
};

const quoteField = (row: Snapshot, prefix: string, field: string) => number(row[(prefix + "_" + field) as keyof Snapshot] as number | null | undefined);

const traceIdentity = (row: Snapshot) => [row.symbol, row.expiry, row.timestamp_ns ?? row.last_update_timestamp_ns ?? "", row.low_strike, row.high_strike, row.direction].join("|");

const snapshotKey = (row: Snapshot) => [
  row.symbol, row.expiry, row.low_strike, row.high_strike, row.direction,
  row.last_update_timestamp_ns ?? row.observed_at,
].join("|");

const mergeSnapshots = (current: Snapshot[], incoming: Snapshot[]) => {
  const seen = new Set(current.map(snapshotKey));
  const merged = [...current];
  for (const row of incoming) {
    const key = snapshotKey(row);
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(row);
  }
  return merged;
};

const quoteTime = (value: number | string | null | undefined) => {
  if (value == null || value === "") return "—";
  const raw = String(value);
  if (!/^\d+$/.test(raw)) return "—";
  let milliseconds: number;
  try {
    const timestamp = BigInt(raw);
    if (timestamp <= BigInt("0")) return "—";
    // Snapshot timestamps are epoch nanoseconds. Convert before Number so
    // large integer precision is not lost while deriving the clock time.
    milliseconds = Number(timestamp / BigInt("1000000"));
  } catch {
    return "—";
  }
  const date = new Date(milliseconds);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false }) + " IST";
};

export default function BoxSpreadPostMarketHistory() {
  const [days, setDays] = useState(1);
  const [expiryType, setExpiryType] = useState("ALL");
  const [selectedIndex, setSelectedIndex] = useState("ALL");
  const [rows, setRows] = useState<Snapshot[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState({ WEEKLY: 0, MONTHLY: 0, UNKNOWN: 0 });
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const [sortingAll, setSortingAll] = useState(false);
  const [sortKey, setSortKey] = useState<"profit" | "edge" | "edgePerLot" | "time" | "gap" | "lots">("time");
  const [sortDirection, setSortDirection] = useState<"desc" | "asc">("desc");
  const [traces, setTraces] = useState<Record<string, { id: number; quantity_lots: number }>>({});
  const [traceBusy, setTraceBusy] = useState<string | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [depthSnapshot, setDepthSnapshot] = useState<Snapshot | null>(null);
  const strikeGap = (row: Snapshot) => Number(row.high_strike) - Number(row.low_strike);
  const executableLots = (row: Snapshot) => {
    const savedLots = Number(row.max_executable_lots);
    if (row.max_executable_lots != null && Number.isFinite(savedLots) && savedLots >= 0) return Math.floor(savedLots);
    const lotSize = Number(row.lot_size);
    if (!Number.isFinite(lotSize) || lotSize <= 0 || !Array.isArray(row.depth_legs) || row.depth_legs.length !== 4) return null;
    const capacities = row.depth_legs.map(leg => Number(leg.available_quantity));
    if (capacities.some(quantity => !Number.isFinite(quantity) || quantity < 0)) return null;
    return Math.floor(Math.min(...capacities) / lotSize);
  };
  const sortedRows = useMemo(() => [...rows].sort((a, b) => {
    const metric = (row: Snapshot) => sortKey === "profit" ? row.gross_pnl : sortKey === "edge" ? row.executable_edge : sortKey === "edgePerLot" ? row.edge_per_lot : sortKey === "gap" ? strikeGap(row) : sortKey === "lots" ? executableLots(row) : new Date(row.observed_at).getTime();
    const av = metric(a), bv = metric(b);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return (sortDirection === "desc" ? -1 : 1) * (Number(av) - Number(bv));
  }), [rows, sortKey, sortDirection]);
  const selectSort = async (key: "profit" | "edge" | "edgePerLot" | "time" | "gap" | "lots") => {
    const nextDirection = sortKey === key ? (sortDirection === "desc" ? "asc" : "desc") : "desc";
    setSortKey(key);
    setSortDirection(nextDirection);
    if (!hasMore || sortingAll) return;
    setSortingAll(true);
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const allRows = [...rows];
      let nextOffset = offset;
      let more = true;
      while (more) {
        const params = new URLSearchParams({ days: String(days), limit: "5000", offset: String(nextOffset) });
        if (expiryType !== "ALL") params.set("expiry_type", expiryType);
        if (selectedIndex !== "ALL") params.set("symbol", selectedIndex);
        const response = await fetch(base + "/api/v1/scanner/box-spread/snapshots?" + params.toString(), { cache: "no-store", signal: AbortSignal.timeout(20000) });
        if (!response.ok) throw new Error("Snapshot history HTTP " + response.status);
        const body = (await response.json()) as SnapshotResponse;
        if (body?.strategy !== "box-spread" || !Array.isArray(body.data)) throw new Error("Unexpected snapshot history response");
        allRows.push(...body.data);
        nextOffset += body.data.length;
        more = body.has_more === true && body.data.length > 0;
      }
      setRows(mergeSnapshots([], allRows));
      setOffset(nextOffset);
      setHasMore(false);
      setTotal(allRows.length);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load complete history for sorting");
    } finally { setSortingAll(false); }
  };
  const sortMark = (key: "profit" | "edge" | "edgePerLot" | "time" | "gap" | "lots") => sortKey === key ? (sortDirection === "desc" ? " ↓" : " ↑") : " ↕";
  const traceApi = appConfig.apiBaseUrl.replace(/\/$/, "") + "/api/v1/scanner/box-spread/trace";
  useEffect(() => {
    let active = true;
    fetch(traceApi, { cache: "no-store" }).then(r => r.ok ? r.json() : Promise.reject(new Error("Trace API unavailable"))).then(body => {
      if (!active || !Array.isArray(body.data)) return;
      const next: Record<string, { id: number; quantity_lots: number }> = {};
      for (const item of body.data) next[String(item.source_identity)] = { id: Number(item.id), quantity_lots: Number(item.quantity_lots) };
      setTraces(next);
    }).catch(() => {});
    return () => { active = false; };
  }, [traceApi]);
  const addTrace = async (row: Snapshot) => {
    const key = traceIdentity(row); setTraceBusy(key); setTraceError(null);
    try {
      const snapshot = { ...row, timestamp_ns: row.timestamp_ns ?? row.last_update_timestamp_ns };
      const response = await fetch(traceApi, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ snapshot }) });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : body.detail?.message || "Unable to add trace");
      setTraces(old => ({ ...old, [String(body.data.source_identity)]: { id: Number(body.data.id), quantity_lots: Number(body.data.quantity_lots) } }));
      window.dispatchEvent(new CustomEvent("box-spread-trace-updated", { detail: body.data }));
    } catch (e) { setTraceError(e instanceof Error ? e.message : "Unable to add trace"); }
    finally { setTraceBusy(null); }
  };
  const requestSequence = useRef(0);
  const appendInFlight = useRef(false);

  const load = useCallback(async (append = false, pageOffset = 0) => {
    if (append && appendInFlight.current) return;
    const requestId = ++requestSequence.current;
    if (append) {
      appendInFlight.current = true;
      setLoadingMore(true);
    }
    else setLoading(true);
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const nextOffset = append ? pageOffset : 0;
      const params = new URLSearchParams({
        days: String(days),
        limit: "500",
        offset: String(nextOffset),
      });
      if (expiryType !== "ALL") params.set("expiry_type", expiryType);
      if (selectedIndex !== "ALL") params.set("symbol", selectedIndex);
      const response = await fetch(base + "/api/v1/scanner/box-spread/snapshots?" + params.toString(), {
        cache: "no-store",
        signal: AbortSignal.timeout(12000),
      });
      if (!response.ok) throw new Error("Snapshot history HTTP " + response.status);
      const body = (await response.json()) as SnapshotResponse;
      if (requestId !== requestSequence.current) return;
      if (body?.strategy !== "box-spread" || !Array.isArray(body.data) || !body.expiry_counts) {
        throw new Error("Unexpected snapshot history response");
      }
      setRows(current => append ? mergeSnapshots(current, body.data) : mergeSnapshots([], body.data));
      setTotal(body.total_count);
      setCounts(body.expiry_counts);
      setOffset(nextOffset + body.data.length);
      setHasMore(body.has_more === true);
      setError(null);
      setCheckedAt(new Date().toISOString());
    } catch (e) {
      if (requestId === requestSequence.current) {
        setError(e instanceof Error ? e.message : "Unable to load saved Box Spread history");
      }
    } finally {
      if (requestId === requestSequence.current) {
        setLoading(false);
        setLoadingMore(false);
        appendInFlight.current = false;
      }
    }
  }, [days, expiryType, selectedIndex]);

  useEffect(() => {
    // Do not display rows from the previous expiry/window under the new filter
    // while its request is in flight or after that request fails.
    appendInFlight.current = false;
    setRows([]);
    setTotal(0);
    setCounts({ WEEKLY: 0, MONTHLY: 0, UNKNOWN: 0 });
    setOffset(0);
    setHasMore(false);
    void load(false);
  }, [load]); // Filter changes intentionally reset pagination.

  const refresh = () => {
    setOffset(0);
    void load(false);
  };

  return (
    <Card className="scroll-mt-24 theme-border theme-surface theme-text box-history-contrast p-4 shadow-xl sm:p-5 ring-1 ring-slate-400/70 dark:ring-slate-500/70" id="box-history">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Archive className="mt-0.5 h-5 w-5 theme-accent" />
          <div>
            <h2 className="text-base font-extrabold theme-text">Opportunity History · Last 24 Hours</h2>
            <p className="mt-1 text-sm theme-muted">Historical qualifying scanner observations · saved snapshot prices and edge · selected history window · read-only, not live quotes or today’s alert list</p>
          </div>
        </div>
        <button type="button" onClick={refresh} disabled={loading || loadingMore} className="inline-flex min-h-9 items-center gap-2 rounded-lg border-2 border-slate-400 dark:border-slate-500 theme-border px-3 text-base font-bold theme-text disabled:opacity-50">
          <RefreshCw className={"h-3.5 w-3.5 " + (loading ? "animate-spin" : "")} /> Refresh
        </button>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <label className="text-sm theme-muted">History window
          <select value={days} onChange={event => setDays(Number(event.target.value))} className="ml-2 min-h-9 rounded-lg border-2 border-slate-400 dark:border-slate-500 theme-border theme-surface px-2 text-sm theme-text">
            <option value={1}>1 day</option><option value={7}>7 days</option><option value={30}>30 days</option>
          </select>
        </label>
        <label className="text-sm theme-muted">Expiry
          <select value={expiryType} onChange={event => setExpiryType(event.target.value)} className="ml-2 min-h-9 rounded-lg border-2 border-slate-400 dark:border-slate-500 theme-border theme-surface px-2 text-sm theme-text">
            <option value="ALL">All expiries</option><option value="WEEKLY">Weekly</option><option value="MONTHLY">Monthly</option><option value="UNKNOWN">Unknown</option>
          </select>
        </label>
        <label className="text-sm theme-muted">Index
          <select value={selectedIndex} onChange={event => setSelectedIndex(event.target.value)} className="ml-2 min-h-9 rounded-lg border-2 border-slate-400 dark:border-slate-500 theme-border theme-surface px-3 text-base font-bold theme-text" aria-label="Filter history by index">
            <option value="ALL">All indices</option><option value="NIFTY">NIFTY</option><option value="BANKNIFTY">BANKNIFTY</option><option value="FINNIFTY">FINNIFTY</option><option value="MIDCPNIFTY">MIDCPNIFTY</option><option value="SENSEX">SENSEX</option><option value="BANKEX">BANKEX</option>
          </select>
        </label>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ["Matching snapshots", total],
          ["Weekly", counts.WEEKLY],
          ["Monthly", counts.MONTHLY],
          ["Unknown expiry", counts.UNKNOWN],
        ].map(([label, value]) => <div key={String(label)} className="rounded-xl border-2 border-slate-400 dark:border-slate-500 theme-border theme-surface-2 p-3">
          <p className="text-sm theme-muted">{label}</p><p className="mt-1 text-base font-bold theme-text">{Number(value).toLocaleString("en-IN")}</p>
        </div>)}
      </div>

      {traceError && <div role="status" className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm theme-text">{traceError}</div>}
      {error && <div role="status" className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm theme-text">
        History request failed ({error}). {rows.length ? "Previously loaded rows are retained." : "No verified saved history is available."}
      </div>}
      {!rows.length && loading && <p className="mt-4 text-sm theme-muted">Loading saved Box Spread snapshots…</p>}
      {!rows.length && !loading && !error && <p className="mt-4 rounded-lg border-2 border-slate-400 dark:border-slate-500 theme-border p-3 text-sm theme-muted">No qualifying snapshots were saved in this window. No synthetic or sample rows are displayed.</p>}

      {rows.length > 0 && <div className="mt-4 space-y-2">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5" aria-label="Sort saved history">
          {(["profit","edge","edgePerLot","gap","lots","time"] as const).map(key => <button key={key} type="button" onClick={() => void selectSort(key)} disabled={sortingAll} aria-pressed={sortKey === key} className={"min-h-14 rounded-xl border px-3 text-base font-extrabold shadow-sm disabled:opacity-50 " + (sortKey === key ? "theme-accent theme-accent-bg theme-border" : "theme-border theme-surface theme-text")}>{key === "profit" ? "Gross Profit" : key === "edge" ? "Edge" : key === "edgePerLot" ? "Edge / Lot" : key === "gap" ? "Strike Gap" : key === "lots" ? "Max Lots" : "Timing"}<span className="block mt-1 text-sm font-medium">{sortKey === key ? (sortDirection === "desc" ? "Highest first ↓" : "Lowest first ↑") : "Tap to sort ↕"}</span></button>)}
        </div>
        {sortingAll && <p role="status" className="text-sm theme-muted">Loading complete history for sorting…</p>}
      </div>}
      {rows.length > 0 && <div className="mt-4 grid gap-3 md:hidden">
        {sortedRows.map(row => <article key={snapshotKey(row)} className="rounded-xl border-2 border-slate-400 dark:border-slate-500 theme-border theme-surface-2 p-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div><div className="flex items-center gap-2"><span className="text-base font-extrabold theme-text">{row.symbol}</span><span className={"rounded-md px-2 py-0.5 text-base font-extrabold " + (row.expiry_type === "WEEKLY" ? "bg-cyan-500/10 text-cyan-500" : row.expiry_type === "MONTHLY" ? "bg-violet-500/10 text-violet-500" : "bg-slate-500/10 theme-muted")}>{row.expiry_type || "UNKNOWN"}</span></div><p className="mt-1 text-sm theme-muted">Expiry {row.expiry_date || row.expiry} · {row.direction}</p></div>
            <div className="text-right"><p className="text-sm uppercase tracking-wide theme-muted">Executable edge</p><p className="text-lg font-extrabold tabular-nums theme-text">₹{number(row.executable_edge)}</p></div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <div className="rounded-lg border-2 border-slate-400 dark:border-slate-500 theme-border theme-surface p-2"><p className="text-sm theme-muted">Strike range</p><p className="mt-1 text-base font-bold theme-text">{number(row.low_strike)} – {number(row.high_strike)}</p><p className="mt-1 text-sm theme-muted">Strike gap: {number(strikeGap(row))}</p></div>
            <div className="rounded-lg border-2 border-slate-400 dark:border-slate-500 theme-border theme-surface p-2"><p className="text-sm theme-muted">Spot price</p><p className="mt-1 text-base font-bold theme-text">{row.spot_price == null ? "—" : "₹" + number(row.spot_price)}</p></div>
            <div className="rounded-lg border-2 border-slate-400 dark:border-slate-500 theme-border theme-surface p-2"><p className="text-sm theme-muted">Edge / lot</p><p className="mt-1 text-base font-bold theme-text">₹{number(row.edge_per_lot)}</p></div>
            <div className="rounded-lg border-2 border-slate-400 dark:border-slate-500 theme-border theme-surface p-2"><p className="text-sm theme-muted">Gross P&L</p><p className="mt-1 text-base font-bold theme-text">₹{number(row.gross_pnl)}</p><p className="mt-1 text-sm theme-muted">Max lots: {executableLots(row) == null ? "—" : executableLots(row)}</p></div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">{([["Low CE","low_call"],["Low PE","low_put"],["High CE","high_call"],["High PE","high_put"]] as const).map(([label,prefix]) => { const buy = row.direction?.toUpperCase().includes("LONG") ? (prefix === "low_call" || prefix === "high_put") : (prefix === "low_put" || prefix === "high_call"); return <div key={prefix} className="rounded-lg border-2 border-slate-400 dark:border-slate-500 theme-border theme-surface p-2"><p className={"text-base font-extrabold " + (buy ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400")}>{label} · {buy ? "BUY / ASK" : "SELL / BID"}</p><p className="mt-1 text-sm theme-muted">LTP {quoteField(row,prefix,"ltp")}</p><p className="mt-1 text-sm"><span className={buy ? "text-emerald-600 dark:text-emerald-400 font-bold" : "text-rose-600 dark:text-rose-400"}>{buy ? "ASK BUY" : "BID SELL"} {quoteField(row,prefix,buy ? "ask" : "bid")}</span></p><p className="mt-1 text-sm theme-muted">Bid {quoteField(row,prefix,"bid")} ({quoteField(row,prefix,"bid_qty")}) · Ask {quoteField(row,prefix,"ask")} ({quoteField(row,prefix,"ask_qty")})</p></div>;})}</div><div className="mt-3 flex flex-wrap justify-between gap-2 border-t-2 border-slate-400 dark:border-slate-500 theme-border pt-2 text-sm theme-muted"><span>Saved {observedTime(row.observed_at)}</span><span>Quote {quoteTime(row.last_update_timestamp_ns)}</span></div>
<div className="mt-3 flex flex-wrap items-center gap-2 border-t-2 border-slate-400 dark:border-slate-500 theme-border pt-3">
<button type="button" onClick={() => setDepthSnapshot(row)} className="min-h-9 rounded-lg border-2 border-slate-400 dark:border-slate-500 theme-border px-3 text-base font-extrabold theme-text disabled:opacity-40">View Saved Depth</button>
{traces[traceIdentity(row)] ? <span className="rounded-md bg-emerald-500/10 px-2 py-1 text-base font-extrabold text-emerald-700 dark:text-emerald-300">ADDED TO TRACE · {traces[traceIdentity(row)].quantity_lots} LOTS</span> : <button type="button" onClick={() => void addTrace(row)} disabled={traceBusy === traceIdentity(row)} className="min-h-9 rounded-lg border border-cyan-500/50 px-3 text-base font-extrabold theme-text disabled:opacity-50">{traceBusy === traceIdentity(row) ? "Adding…" : "＋ Add to Trace"}</button>}
</div>
        </article>)}
      </div>}

      {rows.length > 0 && <div className="mt-4 hidden overflow-x-auto rounded-xl border-2 border-slate-400 dark:border-slate-500 theme-border md:block">
        {sortingAll && <p role="status" className="px-3 py-2 text-sm theme-muted">Loading complete history for sorting…</p>}
        <table className="w-full min-w-[2760px] text-left text-sm">
          <thead className="theme-surface-2 theme-muted"><tr>
            <th className="px-3 py-2"><button type="button" onClick={() => void selectSort("time")} disabled={sortingAll}>Saved (IST){sortMark("time")}</button></th><th className="px-3 py-2">Quote time (IST)</th><th className="px-3 py-2">Quote skew</th><th className="px-3 py-2">Underlying</th><th className="px-3 py-2">Expiry</th><th className="px-3 py-2">Type</th><th className="px-3 py-2">Low strike</th><th className="px-3 py-2">High strike</th><th className="px-3 py-2">Strike gap</th><th className="px-3 py-2">Max lots</th><th className="px-3 py-2">Direction</th><th className="px-3 py-2">Spot</th><th className="px-3 py-2"><button type="button" onClick={() => void selectSort("edge")} disabled={sortingAll}>Edge{sortMark("edge")}</button></th><th className="px-3 py-2"><button type="button" onClick={() => void selectSort("edgePerLot")} disabled={sortingAll}>Edge / lot{sortMark("edgePerLot")}</button></th><th className="px-3 py-2"><button type="button" onClick={() => void selectSort("profit")} disabled={sortingAll}>Gross P&L{sortMark("profit")}</button></th>{Array.from({length:20},(_,i)=><th key={"quotehead"+i}></th>)}<th className="px-3 py-2" rowSpan={3}>Add to Trace / Exit</th>
          </tr><tr>{Array.from({length:15},(_,i)=><th key={"blank"+i}></th>)}<th className="px-2 py-1" colSpan={5}>Low CE</th><th className="px-2 py-1" colSpan={5}>Low PE</th><th className="px-2 py-1" colSpan={5}>High CE</th><th className="px-2 py-1" colSpan={5}>High PE</th></tr><tr>{Array.from({length:15},(_,i)=><th key={"subblank"+i}></th>)}{Array.from({length:4},(_,i)=>["LTP","Bid","Bid Qty","Ask","Ask Qty"].map(label=><th key={i+label} className="px-2 py-1">{label}</th>))}</tr></thead>
          <tbody>{sortedRows.map(row => <tr key={snapshotKey(row)} className="border-t-2 border-slate-400 dark:border-slate-500 theme-border">
            <td className="whitespace-nowrap px-3 py-2 theme-muted">{observedTime(row.observed_at)}</td>
            <td className="whitespace-nowrap px-3 py-2 theme-muted">{quoteTime(row.last_update_timestamp_ns)}</td>
            <td className="whitespace-nowrap px-3 py-2 theme-muted">{row.timestamp_skew_ms == null ? "—" : number(row.timestamp_skew_ms) + " ms"}</td>
            <td className="px-3 py-2 font-bold theme-text">{row.symbol}</td>
            <td className="whitespace-nowrap px-3 py-2 theme-muted">{row.expiry_date || row.expiry}</td>
            <td className="px-3 py-2 theme-muted">{row.expiry_type || "UNKNOWN"}</td>
            <td className="px-3 py-2 theme-text">{number(row.low_strike)}</td>
            <td className="px-3 py-2 theme-text">{number(row.high_strike)}</td>
            <td className="px-3 py-2 theme-text">{number(strikeGap(row))}</td>
            <td className="px-3 py-2 theme-text">{executableLots(row) == null ? "—" : executableLots(row)}</td>
            <td className="px-3 py-2 font-bold theme-text">{row.direction}</td>
            <td className="px-3 py-2 theme-muted">{number(row.spot_price)}</td>
            <td className="px-3 py-2 theme-text">{number(row.executable_edge)}</td>
            <td className="px-3 py-2 theme-text">{number(row.edge_per_lot)}</td>
            <td className="px-3 py-2 theme-text">{number(row.gross_pnl)}</td>


            {(["low_call","low_put","high_call","high_put"] as const).flatMap(prefix => { const isLong = row.direction?.toUpperCase().includes("LONG"); const buy = isLong ? (prefix === "low_call" || prefix === "high_put") : (prefix === "low_put" || prefix === "high_call"); const buyTone = "text-emerald-600 dark:text-emerald-400 font-bold"; const sellTone = "text-rose-600 dark:text-rose-400 font-bold"; return [<td key={prefix+"ltp"} className="px-2 py-2 theme-muted">{quoteField(row,prefix,"ltp")}</td>,<td key={prefix+"bid"} className={"px-2 py-2 " + (!buy ? sellTone : "theme-muted")}>{quoteField(row,prefix,"bid")}</td>,<td key={prefix+"bq"} className={"px-2 py-2 " + (!buy ? sellTone : "theme-muted")}>{quoteField(row,prefix,"bid_qty")}</td>,<td key={prefix+"ask"} className={"px-2 py-2 " + (buy ? buyTone : "theme-muted")}>{quoteField(row,prefix,"ask")}</td>,<td key={prefix+"aq"} className={"px-2 py-2 " + (buy ? buyTone : "theme-muted")}>{quoteField(row,prefix,"ask_qty")}</td>];})}
            <td className="min-w-[280px] px-3 py-2">
<button type="button" onClick={() => setDepthSnapshot(row)} className="mb-2 min-h-8 rounded-md border-2 border-slate-400 dark:border-slate-500 theme-border px-2 text-sm font-extrabold theme-text disabled:opacity-40">{row.depth_legs?.length ? "View Saved Depth" : "Depth Not Recorded"}</button>
{traces[traceIdentity(row)] ? <span className="text-base font-extrabold text-emerald-700 dark:text-emerald-300">ADDED TO TRACE · {traces[traceIdentity(row)].quantity_lots} LOTS</span> : <button type="button" onClick={() => void addTrace(row)} disabled={traceBusy === traceIdentity(row)} className="min-h-8 rounded-md border border-cyan-500/50 px-2 text-base font-extrabold theme-text disabled:opacity-50">{traceBusy === traceIdentity(row) ? "Adding…" : "＋ Add to Trace"}</button>}</td>
          </tr>)}</tbody>
        </table>
      </div>}

      {rows.length > 0 && <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm theme-muted">Showing {rows.length.toLocaleString("en-IN")} of {total.toLocaleString("en-IN")} matching saved snapshots · SAVED SNAPSHOT, not LIVE</p>
        {hasMore && <button type="button" onClick={() => void load(true, offset)} disabled={loadingMore} className="min-h-9 rounded-lg border-2 border-slate-400 dark:border-slate-500 theme-border px-3 text-base font-bold theme-text disabled:opacity-50">
          {loadingMore ? "Loading…" : "Load more"}
        </button>}
      </div>}
      {checkedAt && <p className="mt-2 text-sm theme-muted">Last checked: {new Date(checkedAt).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" })} IST</p>}
      {depthSnapshot && <div className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-900/35 p-3 sm:p-6" role="dialog" aria-modal="true" aria-label="Saved market depth">
        <div className="max-h-[90vh] w-full max-w-5xl overflow-y-auto rounded-2xl border-2 border-slate-300 bg-white p-4 text-slate-900 shadow-2xl sm:p-6">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 pb-3">
            <div><h3 className="text-lg font-extrabold">Saved Market Depth · {depthSnapshot.symbol}</h3><p className="mt-1 text-sm text-slate-600">{depthSnapshot.expiry} · {number(depthSnapshot.low_strike)}–{number(depthSnapshot.high_strike)} · {depthSnapshot.direction}</p><p className="mt-1 text-xs text-slate-600">Original quote time: {quoteTime(depthSnapshot.last_update_timestamp_ns)} · Saved: {observedTime(depthSnapshot.observed_at)} IST</p></div>
            <button type="button" onClick={() => setDepthSnapshot(null)} className="min-h-10 rounded-lg border-2 border-slate-400 px-4 text-sm font-bold text-slate-900">Close</button>
          </div>
          <p className="mt-3 rounded-lg border border-cyan-200 bg-cyan-50 p-3 text-xs text-cyan-950">Historical broker depth captured with this exact opportunity. These levels are immutable saved data, not current quotes.</p>
          {!depthSnapshot.depth_legs?.some(leg => (leg.bid_depth_levels?.length || 0) + (leg.ask_depth_levels?.length || 0) > 0) && <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-950">This older snapshot has no saved five-level bid/ask ladder. Its saved L1 values remain available; no current depth is substituted.</p>}
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {(depthSnapshot.depth_legs || []).map(leg => <section key={leg.role} className="rounded-xl border border-slate-300 bg-slate-50 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-extrabold">{leg.role.replaceAll("_", " ")} · EXECUTABLE {leg.side}</h4><span className="text-xs font-bold text-cyan-800">{leg.depth_status || "DEPTH"}</span></div>
              <p className="mt-1 text-xs text-slate-600">Depth tick: {quoteTime(leg.timestamp_ns)} · VWAP: ₹{number(leg.vwap)} · Capacity: {number(leg.available_quantity)} · Sized qty: {number(leg.selected_quantity)}</p>
              <div className="mt-3 grid grid-cols-2 gap-2">
                {([[ "BID", leg.bid_depth_levels || [] ], [ "ASK", leg.ask_depth_levels || [] ]] as const).map(([side, levels]) => <div key={side} className="rounded-lg border border-slate-300 bg-white p-2">
                  <h5 className="mb-2 text-xs font-extrabold">{side} DEPTH</h5>
                  <div className="grid grid-cols-[1fr_1fr] gap-2 text-[10px] font-bold text-slate-600"><span>PRICE</span><span className="text-right">QTY</span></div>
                  {levels.length ? levels.map((level, index) => <div key={side + index} className="grid grid-cols-[1fr_1fr] gap-2 border-t border-slate-200 py-1 text-xs"><span>₹{number(level.price)}</span><span className="text-right tabular-nums">{number(level.quantity)}</span></div>) : <p className="py-2 text-xs text-slate-500">No saved levels</p>}
                </div>)}
              </div>
            </section>)}
          </div>
          {!depthSnapshot.depth_legs?.length && <p className="mt-4 text-sm text-amber-800">Depth was not recorded for this older snapshot. Current market data is not substituted.</p>}
        </div>
      </div>}
    </Card>
  );
}
