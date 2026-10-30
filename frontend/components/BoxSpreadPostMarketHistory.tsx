"use client";

import { useCallback, useEffect, useState } from "react";
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
  spot_price?: number | null;
  executable_edge?: number | null;
  edge_per_lot?: number | null;
  gross_pnl?: number | null;
  lot_size?: number | null;
  freshness?: string;
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

export default function BoxSpreadPostMarketHistory() {
  const [days, setDays] = useState(7);
  const [expiryType, setExpiryType] = useState("ALL");
  const [rows, setRows] = useState<Snapshot[]>([]);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState({ WEEKLY: 0, MONTHLY: 0, UNKNOWN: 0 });
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);

  const load = useCallback(async (append = false) => {
    if (append) setLoadingMore(true);
    else setLoading(true);
    try {
      const base = appConfig.apiBaseUrl.replace(/\\/$/, "");
      const nextOffset = append ? offset : 0;
      const params = new URLSearchParams({
        days: String(days),
        limit: "500",
        offset: String(nextOffset),
      });
      if (expiryType !== "ALL") params.set("expiry_type", expiryType);
      const response = await fetch(base + "/api/v1/scanner/box-spread/snapshots?" + params.toString(), {
        cache: "no-store",
        signal: AbortSignal.timeout(12000),
      });
      if (!response.ok) throw new Error("Snapshot history HTTP " + response.status);
      const body = (await response.json()) as SnapshotResponse;
      if (body?.strategy !== "box-spread" || !Array.isArray(body.data) || !body.expiry_counts) {
        throw new Error("Unexpected snapshot history response");
      }
      setRows(current => append ? [...current, ...body.data] : body.data);
      setTotal(body.total_count);
      setCounts(body.expiry_counts);
      setOffset(nextOffset + body.data.length);
      setHasMore(body.has_more === true);
      setError(null);
      setCheckedAt(new Date().toISOString());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load saved Box Spread history");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [days, expiryType, offset]);

  useEffect(() => {
    setOffset(0);
    void load(false);
  }, [days, expiryType]); // Filter changes intentionally reset pagination.

  const refresh = () => {
    setOffset(0);
    void load(false);
  };

  return (
    <Card className="theme-border theme-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Archive className="mt-0.5 h-5 w-5 theme-accent" />
          <div>
            <h2 className="text-base font-semibold theme-text">Post-Market Box Spread Pair History</h2>
            <p className="mt-1 text-xs theme-muted">Persisted qualifying observations · saved snapshot prices and edge · read-only history, not live quotes</p>
          </div>
        </div>
        <button type="button" onClick={refresh} disabled={loading || loadingMore} className="inline-flex min-h-9 items-center gap-2 rounded-lg border theme-border px-3 text-xs font-semibold theme-text disabled:opacity-50">
          <RefreshCw className={"h-3.5 w-3.5 " + (loading ? "animate-spin" : "")} /> Refresh
        </button>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <label className="text-xs theme-muted">History window
          <select value={days} onChange={event => setDays(Number(event.target.value))} className="ml-2 min-h-9 rounded-lg border theme-border theme-surface px-2 text-xs theme-text">
            <option value={1}>1 day</option><option value={7}>7 days</option><option value={30}>30 days</option>
          </select>
        </label>
        <label className="text-xs theme-muted">Expiry
          <select value={expiryType} onChange={event => setExpiryType(event.target.value)} className="ml-2 min-h-9 rounded-lg border theme-border theme-surface px-2 text-xs theme-text">
            <option value="ALL">All expiries</option><option value="WEEKLY">Weekly</option><option value="MONTHLY">Monthly</option><option value="UNKNOWN">Unknown</option>
          </select>
        </label>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ["Matching snapshots", total],
          ["Weekly", counts.WEEKLY],
          ["Monthly", counts.MONTHLY],
          ["Unknown expiry", counts.UNKNOWN],
        ].map(([label, value]) => <div key={String(label)} className="rounded-xl border theme-border theme-surface-2 p-3">
          <p className="text-xs theme-muted">{label}</p><p className="mt-1 text-base font-semibold theme-text">{Number(value).toLocaleString("en-IN")}</p>
        </div>)}
      </div>

      {error && <div role="status" className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs theme-text">
        History request failed ({error}). {rows.length ? "Previously loaded rows are retained." : "No verified saved history is available."}
      </div>}
      {!rows.length && loading && <p className="mt-4 text-sm theme-muted">Loading saved Box Spread snapshots…</p>}
      {!rows.length && !loading && !error && <p className="mt-4 rounded-lg border theme-border p-3 text-sm theme-muted">No qualifying snapshots were saved in this window. No synthetic or sample rows are displayed.</p>}

      {rows.length > 0 && <div className="mt-4 overflow-x-auto rounded-xl border theme-border">
        <table className="w-full min-w-[980px] text-left text-xs">
          <thead className="theme-surface-2 theme-muted"><tr>
            <th className="px-3 py-2">Observed (IST)</th><th className="px-3 py-2">Underlying</th><th className="px-3 py-2">Expiry</th><th className="px-3 py-2">Type</th><th className="px-3 py-2">Low strike</th><th className="px-3 py-2">High strike</th><th className="px-3 py-2">Direction</th><th className="px-3 py-2">Spot</th><th className="px-3 py-2">Edge</th><th className="px-3 py-2">Edge / lot</th><th className="px-3 py-2">Gross P&L</th>
          </tr></thead>
          <tbody>{rows.map((row, index) => <tr key={row.symbol + "-" + row.expiry + "-" + row.low_strike + "-" + row.high_strike + "-" + row.direction + "-" + row.observed_at + "-" + index} className="border-t theme-border">
            <td className="whitespace-nowrap px-3 py-2 theme-muted">{observedTime(row.observed_at)}</td>
            <td className="px-3 py-2 font-semibold theme-text">{row.symbol}</td>
            <td className="whitespace-nowrap px-3 py-2 theme-muted">{row.expiry_date || row.expiry}</td>
            <td className="px-3 py-2 theme-muted">{row.expiry_type || "UNKNOWN"}</td>
            <td className="px-3 py-2 theme-text">{number(row.low_strike)}</td>
            <td className="px-3 py-2 theme-text">{number(row.high_strike)}</td>
            <td className="px-3 py-2 font-semibold theme-text">{row.direction}</td>
            <td className="px-3 py-2 theme-muted">{number(row.spot_price)}</td>
            <td className="px-3 py-2 theme-text">{number(row.executable_edge)}</td>
            <td className="px-3 py-2 theme-text">{number(row.edge_per_lot)}</td>
            <td className="px-3 py-2 theme-text">{number(row.gross_pnl)}</td>
          </tr>)}</tbody>
        </table>
      </div>}

      {rows.length > 0 && <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs theme-muted">Showing {rows.length.toLocaleString("en-IN")} of {total.toLocaleString("en-IN")} matching saved snapshots · SAVED SNAPSHOT, not LIVE</p>
        {hasMore && <button type="button" onClick={() => void load(true)} disabled={loadingMore} className="min-h-9 rounded-lg border theme-border px-3 text-xs font-semibold theme-text disabled:opacity-50">
          {loadingMore ? "Loading…" : "Load more"}
        </button>}
      </div>}
      {checkedAt && <p className="mt-2 text-[11px] theme-muted">Last checked: {new Date(checkedAt).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" })} IST</p>}
    </Card>
  );
}
