"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { appConfig } from "@/lib/config";

type TraceRow = Record<string, unknown> & {
  trace_id: string;
  traced_at: string;
  trace_key: string;
};

const STORAGE_KEY = "algo:synthetic-arbitrage:trace-opportunities:v1";
const DELETED_KEY = "algo:synthetic-arbitrage:trace-deleted:v1";
const LIVE_ENDPOINT = "/api/v1/scanner/synthetic-cash-carry/live?limit=50";
const INDEX_ORDER = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"];

function clean(raw: unknown) {
  return String(raw ?? "").trim();
}

function keyPart(raw: unknown) {
  return clean(raw).toUpperCase();
}

function traceKey(row: Record<string, unknown>) {
  return [
    keyPart(row.underlying),
    keyPart(row.expiry),
    keyPart(row.strike),
    keyPart(row.future_symbol ?? row.future_trading_symbol ?? row.future_name ?? row.future_token),
    keyPart(row.call_symbol ?? row.call_trading_symbol ?? row.call_name ?? row.call_token),
    keyPart(row.put_symbol ?? row.put_trading_symbol ?? row.put_name ?? row.put_token),
    keyPart(row.direction),
  ].join("|");
}

function formatNumber(raw: unknown) {
  const n = Number(raw);
  return Number.isFinite(n) ? n.toLocaleString("en-IN", { maximumFractionDigits: 4 }) : "—";
}

function formatExpiry(raw: unknown) {
  const text = clean(raw);
  if (!text) return "—";
  if (/^\d{8}(?:\.0+)?$/.test(text)) {
    const digits = text.split(".")[0];
    const date = new Date(Date.UTC(Number(digits.slice(0, 4)), Number(digits.slice(4, 6)) - 1, Number(digits.slice(6, 8))));
    if (!Number.isNaN(date.getTime())) return date.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
  }
  return text;
}

function formatAddedAt(raw: string) {
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? raw : date.toLocaleString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" });
}

function readStored(): TraceRow[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.filter((item): item is TraceRow => !!item && typeof item === "object" && typeof item.trace_id === "string") : [];
  } catch {
    return [];
  }
}

function writeStored(rows: TraceRow[]) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(rows));
  } catch {
    // Keep the in-memory trace list usable if browser storage is unavailable.
  }
}

function readDeleted(): string[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(DELETED_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function writeDeleted(ids: string[]) {
  try {
    window.localStorage.setItem(DELETED_KEY, JSON.stringify([...new Set(ids)]));
  } catch {
    // Deletion still works in memory if browser storage is unavailable.
  }
}


export function SyntheticTracePanel() {
  const [liveRows, setLiveRows] = useState<Record<string, unknown>[]>([]);
  const [traces, setTraces] = useState<TraceRow[]>([]);
  const [liveLoading, setLiveLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [statusByTrace, setStatusByTrace] = useState<Record<string, Record<string, unknown>>>({});
  const [checkingTraceIds, setCheckingTraceIds] = useState<string[]>([]);

  useEffect(() => {
    const localRows = readStored();
    const deletedIds = readDeleted();
    setTraces(localRows.filter((row) => !deletedIds.includes(row.trace_id)));
    const base = appConfig.apiBaseUrl.endsWith("/") ? appConfig.apiBaseUrl.slice(0, -1) : appConfig.apiBaseUrl;
    void (async () => {
      try {
        const response = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/trace/opportunities", { cache: "no-store" });
        const body = await response.json().catch(() => ({}));
        if (!response.ok || !Array.isArray(body?.data)) throw new Error("Trace restore unavailable");
        const serverRows: TraceRow[] = (body.data as unknown[]).filter((item: unknown): item is TraceRow => !!item && typeof item === "object" && typeof (item as Record<string, unknown>).trace_id === "string");
        const serverIds = new Set(serverRows.map((row: TraceRow) => row.trace_id));
        const localOnly = localRows.filter((row: TraceRow) => !serverIds.has(row.trace_id) && !deletedIds.includes(row.trace_id));
        const merged = [...serverRows.filter((row: TraceRow) => !deletedIds.includes(row.trace_id)), ...localOnly]
          .sort((a: TraceRow, b: TraceRow) => new Date(b.traced_at).getTime() - new Date(a.traced_at).getTime());
        setTraces(merged);
        writeStored(merged);
        await Promise.allSettled(localOnly.map((row) => fetch(base + "/api/v1/scanner/synthetic-cash-carry/trace/opportunities", {
          method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
          body: JSON.stringify({ trace_id: row.trace_id, trace_key: row.trace_key, traced_at: row.traced_at, snapshot: row }),
        })));
        const deleteResults = await Promise.all(deletedIds.map(async (id) => {
          const result = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/trace/opportunities/" + encodeURIComponent(id), { method: "DELETE", cache: "no-store" });
          return result.ok;
        }));
        if (deleteResults.every(Boolean)) writeDeleted([]);
      } catch {
        // The local copy remains available when the API/database is offline.
      }
    })();
  }, []);

  const loadLive = useCallback(async () => {
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(base + LIVE_ENDPOINT, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail || "Live scanner unavailable");
      const rows: Record<string, unknown>[] = Array.isArray(body?.data)
        ? body.data.filter((item: unknown): item is Record<string, unknown> => !!item && typeof item === "object")
        : [];
      setLiveRows(rows.filter((row) => {
        const instrumentClass = keyPart(row.instrument_class);
        const underlying = keyPart(row.underlying);
        return instrumentClass === "INDEX" && INDEX_ORDER.includes(underlying);
      }));
    } catch {
      // The main scanner already owns live-feed error rendering. Trace capture
      // simply keeps the last successful opportunity list during a transient error.
    } finally {
      setLiveLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadLive();
    const timer = window.setInterval(() => void loadLive(), 1500);
    return () => window.clearInterval(timer);
  }, [loadLive]);

  const tracedKeys = useMemo(() => new Set(traces.map((item) => item.trace_key)), [traces]);

  const addToTrace = async (row: Record<string, unknown>) => {
    const key = traceKey(row);
    if (!key.replaceAll("|", "")) {
      setMessage("This opportunity has no stable contract identity and cannot be traced yet.");
      return;
    }
    if (tracedKeys.has(key)) {
      setMessage("This exact opportunity is already in Trace.");
      return;
    }
    const now = new Date().toISOString();
    const snapshot: TraceRow = {
      ...JSON.parse(JSON.stringify(row)),
      trace_id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      traced_at: now,
      trace_key: key,
    };
    const next = [snapshot, ...traces];
    setTraces(next);
    writeStored(next);
    try {
      const base = appConfig.apiBaseUrl.endsWith("/") ? appConfig.apiBaseUrl.slice(0, -1) : appConfig.apiBaseUrl;
      const response = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/trace/opportunities", {
        method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trace_id: snapshot.trace_id, trace_key: snapshot.trace_key, traced_at: snapshot.traced_at, snapshot }),
      });
      if (!response.ok) throw new Error("server rejected snapshot");
      setMessage(`${keyPart(row.underlying) || "Opportunity"} added to Trace and saved.`);
    } catch {
      setMessage(`${keyPart(row.underlying) || "Opportunity"} saved in this browser only; server persistence is unavailable.`);
    }
  };

  const checkLiveStatus = async (row: TraceRow) => {
    if (checkingTraceIds.includes(row.trace_id)) return;
    const direction = keyPart(row.direction);
    const rawEntryEdge = row.executable_edge ?? (direction === "LONG" ? row.long_edge : row.short_edge);
    const entryEdge = Number(rawEntryEdge);
    if (!["LONG", "SHORT"].includes(direction) || !Number.isFinite(entryEdge)) {
      setMessage("Trace row is missing a valid LONG/SHORT direction or entry edge.");
      return;
    }
    setCheckingTraceIds((current) => [...current, row.trace_id]);
    setMessage("");
    try {
      const base = appConfig.apiBaseUrl.endsWith("/") ? appConfig.apiBaseUrl.slice(0, -1) : appConfig.apiBaseUrl;
      const response = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/trace/live-status", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          trace_id: row.trace_id,
          underlying: row.underlying,
          expiry: row.expiry,
          strike: Number(row.strike),
          direction,
          entry_edge: entryEdge,
          lot_size: Number(row.lot_size) > 0 ? Number(row.lot_size) : null,
          entry_future_bid: row.future_bid ?? null,
          entry_future_ask: row.future_ask ?? null,
          entry_call_bid: row.call_bid ?? null,
          entry_call_ask: row.call_ask ?? null,
          entry_put_bid: row.put_bid ?? null,
          entry_put_ask: row.put_ask ?? null,
        }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : "Exact live status unavailable");
      setStatusByTrace((current) => ({ ...current, [row.trace_id]: body }));
      setMessage(body.history_persistence === "UNAVAILABLE" ? "Live quotes checked, but database history could not be saved." : keyPart(row.underlying) + " exact-contract status checked and history saved.");
    } catch (error) {
      const detail = error instanceof Error ? error.message : "Exact live status unavailable";
      let history: Record<string, unknown>[] = [];
      try {
        const base = appConfig.apiBaseUrl.endsWith("/") ? appConfig.apiBaseUrl.slice(0, -1) : appConfig.apiBaseUrl;
        const historyResponse = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/trace/history?trace_id=" + encodeURIComponent(row.trace_id), { cache: "no-store" });
        const historyBody = await historyResponse.json().catch(() => ({}));
        if (historyResponse.ok && Array.isArray(historyBody?.data)) history = historyBody.data;
      } catch {
        // Preserve the quote error if history retrieval is also unavailable.
      }
      setStatusByTrace((current) => ({
        ...current,
        [row.trace_id]: { status: "UNAVAILABLE", detail, checked_at: new Date().toISOString(), history },
      }));
      setMessage("Live status unavailable: " + detail);
    } finally {
      setCheckingTraceIds((current) => current.filter((id) => id !== row.trace_id));
    }
  };

  const removeTrace = async (traceId: string) => {
    const next = traces.filter((item) => item.trace_id !== traceId);
    setTraces(next);
    writeStored(next);
    try {
      const base = appConfig.apiBaseUrl.endsWith("/") ? appConfig.apiBaseUrl.slice(0, -1) : appConfig.apiBaseUrl;
      const response = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/trace/opportunities/" + encodeURIComponent(traceId), { method: "DELETE", cache: "no-store" });
      if (!response.ok) throw new Error("server delete failed");
      setMessage("Trace removed from saved list.");
    } catch {
      writeDeleted([...readDeleted(), traceId]);
      setMessage("Trace removed on this device; server deletion will retry when the API is available.");
    }
  };

  const loadTraceHistory = async (row: TraceRow) => {
    try {
      const base = appConfig.apiBaseUrl.endsWith("/") ? appConfig.apiBaseUrl.slice(0, -1) : appConfig.apiBaseUrl;
      const response = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/trace/history?trace_id=" + encodeURIComponent(row.trace_id), { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || !Array.isArray(body?.data)) throw new Error("Saved trace history unavailable");
      setStatusByTrace((current) => ({
        ...current,
        [row.trace_id]: { ...(current[row.trace_id] ?? {}), status: current[row.trace_id]?.status ?? "HISTORY", history: body.data },
      }));
      setMessage(body.data.length ? `Loaded ${body.data.length} saved checks.` : "No saved checks for this Trace yet.");
    } catch {
      setMessage("Could not load saved Trace history. Check backend/database availability.");
    }
  };

  const clearMessage = () => setMessage("");

  return (
    <div className="mx-4 mb-5 space-y-4 sm:mx-5">
      <section className="overflow-hidden rounded-2xl border theme-border theme-surface theme-shadow">
        <div className="flex flex-col gap-2 border-b theme-border p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-sm font-semibold theme-text">Trace Capture</h2>
            <p className="mt-1 text-xs theme-muted">Opportunity list refreshes automatically; saved Trace status is checked only when you press CHECK LIVE STATUS.</p>
          </div>
          <span className="text-xs font-semibold theme-muted">{liveRows.length} live opportunities</span>
        </div>
        {message && <button type="button" onClick={clearMessage} className="mx-4 mt-3 rounded-lg border theme-border theme-surface-2 px-3 py-2 text-left text-xs theme-text">{message} <span className="theme-muted">(tap to dismiss)</span></button>}
        <div className="grid gap-2 p-3 md:hidden">
          {liveLoading ? <div className="rounded-xl border theme-border p-4 text-center text-xs theme-muted">Loading live opportunities…</div> : liveRows.length === 0 ? <div className="rounded-xl border theme-border p-4 text-center text-xs theme-muted">No index opportunity available to trace.</div> : liveRows.map((row, index) => {
            const key = traceKey(row);
            const direction = keyPart(row.direction) || "—";
            const edge = row.executable_edge ?? (direction === "LONG" ? row.long_edge : row.short_edge);
            return <div key={key || String(index)} className="rounded-xl border theme-border theme-surface-2 p-3">
              <div className="flex items-center justify-between gap-2"><span className="text-sm font-bold theme-accent">{keyPart(row.underlying) || "INDEX"}</span><span className="text-[10px] theme-muted">INDEX OPPORTUNITY</span></div>
              <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
                <div><div className="theme-subtle">Expiry</div><div className="mt-0.5 theme-muted">{formatExpiry(row.expiry)}</div></div>
                <div><div className="theme-subtle">Strike</div><div className="mt-0.5 theme-muted">{formatNumber(row.strike)}</div></div>
                <div><div className="theme-subtle">Direction</div><div className="mt-0.5 font-semibold theme-text">{direction}</div></div>
                <div><div className="theme-subtle">Entry Edge</div><div className="mt-0.5 font-semibold theme-success">{formatNumber(edge)} pts</div></div>
              </div>
              <button type="button" onClick={() => addToTrace(row)} disabled={tracedKeys.has(key)} className="mt-3 min-h-10 w-full rounded-lg border theme-border px-3 text-xs font-bold theme-text disabled:opacity-50">{tracedKeys.has(key) ? "ADDED TO TRACE" : "ADD TO TRACE"}</button>
            </div>;
          })}
        </div>
        <div className="hidden overflow-x-auto md:block">
          <table className="w-full min-w-[850px] text-left text-sm">
            <thead className="theme-surface-2 text-xs uppercase tracking-wider theme-muted"><tr>{["Index", "Expiry", "Strike", "Direction", "Entry Edge", "Action"].map((label) => <th key={label} className="px-4 py-3 font-semibold">{label}</th>)}</tr></thead>
            <tbody>{liveRows.map((row, index) => {
              const key = traceKey(row);
              const direction = keyPart(row.direction) || "—";
              const edge = row.executable_edge ?? (direction === "LONG" ? row.long_edge : row.short_edge);
              return <tr key={key || String(index)} className="border-b theme-border last:border-0">
                <td className="px-4 py-3 font-semibold theme-text">{keyPart(row.underlying) || "INDEX"}</td>
                <td className="px-4 py-3 theme-muted">{formatExpiry(row.expiry)}</td>
                <td className="px-4 py-3 theme-muted">{formatNumber(row.strike)}</td>
                <td className="px-4 py-3 font-semibold theme-text">{direction}</td>
                <td className="px-4 py-3 font-semibold theme-success">{formatNumber(edge)} pts</td>
                <td className="px-4 py-3"><button type="button" onClick={() => addToTrace(row)} disabled={tracedKeys.has(key)} className="min-h-9 rounded-lg border theme-border px-3 text-xs font-bold theme-text disabled:opacity-50">{tracedKeys.has(key) ? "ADDED TO TRACE" : "ADD TO TRACE"}</button></td>
              </tr>;
            })}</tbody>
          </table>
        </div>
      </section>

      <section className="overflow-hidden rounded-2xl border theme-border theme-surface theme-shadow">
        <div className="flex flex-col gap-2 border-b theme-border p-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-sm font-semibold theme-text">Trace Opportunities</h2>
            <p className="mt-1 text-xs theme-muted">Exact entry snapshots. Live quotes are fetched only when you press CHECK LIVE STATUS; no trace status polling runs in the background.</p>
          </div>
          <span className="text-xs font-semibold theme-muted">{traces.length} traced</span>
        </div>
        {traces.length === 0 ? <div className="p-4 text-sm theme-muted">No opportunities added to Trace yet.</div> : <>
          <div className="grid gap-2 p-3 md:hidden">{traces.map((row) => <div key={row.trace_id} className="rounded-xl border theme-border theme-surface-2 p-3">
            <div className="flex items-center justify-between gap-2"><span className="text-sm font-bold theme-accent">{keyPart(row.underlying) || "INDEX"}</span><span className="text-[10px] theme-muted">{formatAddedAt(row.traced_at)}</span></div>
            <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
              <div><div className="theme-subtle">Expiry</div><div className="mt-0.5 theme-muted">{formatExpiry(row.expiry)}</div></div>
              <div><div className="theme-subtle">Strike</div><div className="mt-0.5 theme-muted">{formatNumber(row.strike)}</div></div>
              <div><div className="theme-subtle">Direction</div><div className="mt-0.5 font-semibold theme-text">{keyPart(row.direction) || "—"}</div></div>
              <div><div className="theme-subtle">Entry Edge</div><div className="mt-0.5 font-semibold theme-success">{formatNumber(row.executable_edge ?? (keyPart(row.direction) === "LONG" ? row.long_edge : row.short_edge))} pts</div></div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2"><button type="button" onClick={() => void checkLiveStatus(row)} disabled={checkingTraceIds.includes(row.trace_id)} className="min-h-10 flex-1 rounded-lg border theme-border px-3 text-xs font-bold theme-text disabled:opacity-50">{checkingTraceIds.includes(row.trace_id) ? "CHECKING…" : "CHECK LIVE STATUS"}</button><button type="button" onClick={() => void loadTraceHistory(row)} className="min-h-10 rounded-lg border theme-border px-3 text-xs font-semibold theme-text">VIEW HISTORY</button><button type="button" onClick={() => void removeTrace(row.trace_id)} className="min-h-10 rounded-lg border theme-border px-3 text-xs font-semibold theme-text">Remove</button></div>
            {statusByTrace[row.trace_id] && <div className="mt-3 rounded-lg border theme-border p-3 text-xs"><div className="font-semibold theme-text">{String(statusByTrace[row.trace_id].status ?? "STATUS") .replaceAll("_", " ")}</div>{statusByTrace[row.trace_id].history_detail != null && <p className="mt-1 break-words theme-warning">{String(statusByTrace[row.trace_id].history_detail)}</p>}{statusByTrace[row.trace_id].detail ? <p className="mt-1 break-words theme-warning">{String(statusByTrace[row.trace_id].detail)}</p> : <div className="mt-2 grid grid-cols-2 gap-2"><div><div className="theme-subtle">Entry Edge</div><div className="theme-text">{formatNumber(statusByTrace[row.trace_id].entry_edge)} pts</div></div><div><div className="theme-subtle">Current Edge</div><div className="theme-text">{formatNumber(statusByTrace[row.trace_id].current_edge)} pts</div></div><div><div className="theme-subtle">Edge Change</div><div className={Number(statusByTrace[row.trace_id].edge_change) >= 0 ? "theme-success" : "theme-warning"}>{Number(statusByTrace[row.trace_id].edge_change) > 0 ? "+" : ""}{formatNumber(statusByTrace[row.trace_id].edge_change)} pts</div></div><div><div className="theme-subtle">Checked At</div><div className="theme-muted">{formatAddedAt(String(statusByTrace[row.trace_id].checked_at ?? "—"))}</div></div><div><div className="theme-subtle">Max Quote Age</div><div className="theme-muted">{statusByTrace[row.trace_id].max_quote_age_seconds == null ? "—" : formatNumber(statusByTrace[row.trace_id].max_quote_age_seconds) + "s"}</div></div><div className="col-span-2"><div className="theme-subtle">Close P&amp;L (1 lot)</div><div className={Number(statusByTrace[row.trace_id].pnl_per_lot) >= 0 ? "theme-success" : "theme-warning"}>{statusByTrace[row.trace_id].pnl_per_lot == null ? String(statusByTrace[row.trace_id].pnl_detail ?? "Unavailable") : "₹" + formatNumber(statusByTrace[row.trace_id].pnl_per_lot)}</div><div className="theme-muted">{String(statusByTrace[row.trace_id].pnl_detail ?? "")}</div></div></div>}{Array.isArray(statusByTrace[row.trace_id].history) && (statusByTrace[row.trace_id].history as Record<string, unknown>[]).length > 0 && <div className="mt-3 border-t theme-border pt-2"><div className="mb-1 font-semibold theme-text">Check History</div>{(statusByTrace[row.trace_id].history as Record<string, unknown>[]).slice(0, 5).map((item) => <div key={String(item.id)} className="flex justify-between gap-2 py-1 theme-muted"><span>{formatAddedAt(String(item.checked_at ?? "—"))}</span><span>{item.pnl_per_lot == null ? "P&amp;L —" : "₹" + formatNumber(item.pnl_per_lot)}</span></div>)}</div>}</div>}
          </div>)}</div>
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead className="theme-surface-2 text-xs uppercase tracking-wider theme-muted"><tr>{["Index", "Expiry", "Strike", "Direction", "Entry Edge", "Added At", "Current Edge", "Edge Change", "Close P&L / Lot", "Checked At", "Action"].map((label) => <th key={label} className="px-4 py-3 font-semibold">{label}</th>)}</tr></thead>
              <tbody>{traces.map((row) => <tr key={row.trace_id} className="border-b theme-border last:border-0">
                <td className="px-4 py-3 font-semibold theme-text">{keyPart(row.underlying) || "INDEX"}</td>
                <td className="px-4 py-3 theme-muted">{formatExpiry(row.expiry)}</td>
                <td className="px-4 py-3 theme-muted">{formatNumber(row.strike)}</td>
                <td className="px-4 py-3 font-semibold theme-text">{keyPart(row.direction) || "—"}</td>
                <td className="px-4 py-3 font-semibold theme-success">{formatNumber(row.executable_edge ?? (keyPart(row.direction) === "LONG" ? row.long_edge : row.short_edge))} pts</td>
                <td className="px-4 py-3 theme-muted">{formatAddedAt(row.traced_at)}</td>
                <td className="px-4 py-3 theme-muted">{statusByTrace[row.trace_id]?.current_edge == null ? "—" : formatNumber(statusByTrace[row.trace_id].current_edge) + " pts"}</td>
                <td className={"px-4 py-3 " + (Number(statusByTrace[row.trace_id]?.edge_change) >= 0 ? "theme-success" : "theme-warning")}>{statusByTrace[row.trace_id]?.edge_change == null ? "—" : (Number(statusByTrace[row.trace_id].edge_change) > 0 ? "+" : "") + formatNumber(statusByTrace[row.trace_id].edge_change) + " pts"}</td>
                <td className={"px-4 py-3 " + (Number(statusByTrace[row.trace_id]?.pnl_per_lot) >= 0 ? "theme-success" : "theme-warning")}>{statusByTrace[row.trace_id]?.pnl_per_lot == null ? "—" : "₹" + formatNumber(statusByTrace[row.trace_id].pnl_per_lot)}</td>
                <td className="px-4 py-3 theme-muted">{statusByTrace[row.trace_id]?.checked_at ? formatAddedAt(String(statusByTrace[row.trace_id].checked_at)) : statusByTrace[row.trace_id]?.detail ? String(statusByTrace[row.trace_id].detail) : "Not checked"}</td>
                <td className="px-4 py-3"><div className="flex flex-wrap gap-2"><button type="button" onClick={() => void checkLiveStatus(row)} disabled={checkingTraceIds.includes(row.trace_id)} className="min-h-9 rounded-lg border theme-border px-3 text-xs font-bold theme-text disabled:opacity-50">{checkingTraceIds.includes(row.trace_id) ? "CHECKING…" : "CHECK LIVE STATUS"}</button><button type="button" onClick={() => void loadTraceHistory(row)} className="min-h-9 rounded-lg border theme-border px-3 text-xs font-semibold theme-text">VIEW HISTORY</button><button type="button" onClick={() => void removeTrace(row.trace_id)} className="min-h-9 rounded-lg border theme-border px-3 text-xs font-semibold theme-text">Remove</button></div>{Array.isArray(statusByTrace[row.trace_id]?.history) && (statusByTrace[row.trace_id]?.history as Record<string, unknown>[]).length > 0 && <div className="mt-2 min-w-52 rounded-lg border theme-border p-2 text-xs"><div className="mb-1 font-semibold theme-text">Recent checks</div>{(statusByTrace[row.trace_id]?.history as Record<string, unknown>[]).slice(0, 5).map((item) => <div key={String(item.id)} className="flex justify-between gap-2 py-1 theme-muted"><span>{formatAddedAt(String(item.checked_at ?? "—"))}</span><span>{item.pnl_per_lot == null ? "P&amp;L —" : "₹" + formatNumber(item.pnl_per_lot)}</span></div>)}</div>}</td>
              </tr>)}</tbody>
            </table>
          </div>
        </>}
      </section>
    </div>
  );
}
