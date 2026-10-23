"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, AlertTriangle, Clock3, RefreshCw, Search, ShieldCheck, Wifi, WifiOff } from "lucide-react";
import { appConfig } from "@/lib/config";

type Row = Record<string, unknown>;
type Health = "checking" | "online" | "error";

const INDEXES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"];
const STOCKS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY", "TCS", "AXISBANK", "LT", "BHARTIARTL", "KOTAKBANK", "TMPV", "BAJFINANCE", "M&M", "HINDALCO", "TATASTEEL"];
const fmt = (v: unknown) => {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString("en-IN", { maximumFractionDigits: 3 }) : String(v);
};
const text = (v: unknown) => String(v ?? "").trim();
const key = (v: unknown) => text(v).toUpperCase();

export function SyntheticAdvancedOverview() {
  const [rows, setRows] = useState<Row[]>([]);
  const [health, setHealth] = useState<Health>("checking");
  const [feedDetail, setFeedDetail] = useState("Checking live API");
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [direction, setDirection] = useState("ALL");
  const [instrument, setInstrument] = useState("ALL");

  const refresh = useCallback(async () => {
    setLoading(true);
    const base = appConfig.apiBaseUrl.replace(/\/$/, "");
    try {
      const response = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/live?limit=200", { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || !Array.isArray(body?.data)) {
        throw new Error(typeof body?.detail === "string" ? body.detail : "Live endpoint returned no usable data");
      }
      const eligible = (body.data as Row[]).filter((row) => {
        const underlying = key(row.underlying ?? row.symbol);
        const cls = key(row.instrument_class);
        return INDEXES.includes(underlying) || (STOCKS.includes(underlying) && (!cls || cls === "STOCK" || cls === "EQUITY" || cls === "INDEX"));
      });
      setRows(eligible);
      setHealth("online");
      setFeedDetail("Live scanner endpoint responding");
      setLastUpdated(new Date().toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" }));
    } catch (error) {
      setHealth("error");
      setFeedDetail(error instanceof Error ? error.message : "Live scanner request failed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const filtered = useMemo(() => rows.filter((row) => {
    const underlying = key(row.underlying ?? row.symbol);
    const kind = INDEXES.includes(underlying) ? "INDEX" : "STOCK";
    const dir = key(row.direction);
    const matchesSearch = !search || [row.underlying, row.symbol, row.expiry, row.strike].some((v) => text(v).toUpperCase().includes(search.toUpperCase()));
    return matchesSearch && (instrument === "ALL" || instrument === kind) && (direction === "ALL" || dir === direction);
  }), [rows, search, direction, instrument]);

  const edgeCount = rows.filter((row) => Number(row.executable_edge ?? row.edge_per_unit) > 0 && ["LONG", "SHORT"].includes(key(row.direction))).length;
  const indexCount = new Set(rows.map((r) => key(r.underlying ?? r.symbol)).filter((s) => INDEXES.includes(s))).size;
  const stockCount = new Set(rows.map((r) => key(r.underlying ?? r.symbol)).filter((s) => STOCKS.includes(s))).size;

  return (
    <section className="mb-6 space-y-4">
      <div className="overflow-hidden rounded-2xl border theme-border theme-surface theme-shadow">
        <div className="flex flex-col gap-4 p-4 sm:p-5 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] theme-accent">
              <Activity className="h-4 w-4" /> Advanced workspace · Synthetic only
            </div>
            <h2 className="text-xl font-bold tracking-tight theme-text sm:text-2xl">Synthetic Arbitrage Control Center</h2>
            <p className="mt-1 max-w-3xl text-sm theme-muted">Live option/future parity monitor, universe coverage and real API status. Broker orders remain OFF; this panel never places orders.</p>
          </div>
          <button type="button" onClick={() => void refresh()} disabled={loading} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border theme-border theme-surface-2 px-4 py-2 text-sm font-semibold theme-text disabled:opacity-60">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh live data
          </button>
        </div>
        <div className="grid grid-cols-2 gap-px border-t theme-border bg-[var(--app-border)] lg:grid-cols-4">
          <div className="theme-surface p-4">
            <div className="mb-2 flex items-center gap-2 text-xs theme-muted"><span className={`h-2 w-2 rounded-full ${health === "online" ? "bg-emerald-500" : health === "error" ? "bg-red-500" : "bg-amber-500"}`} /> Live API</div>
            <div className="text-lg font-bold theme-text">{health === "online" ? "RESPONDING" : health === "error" ? "UNAVAILABLE" : "CHECKING"}</div>
            <div className="mt-1 text-xs theme-muted">{feedDetail}</div>
          </div>
          <div className="theme-surface p-4">
            <div className="mb-2 flex items-center gap-2 text-xs theme-muted"><Wifi className="h-4 w-4" /> Eligible live rows</div>
            <div className="text-2xl font-bold theme-text">{health === "online" ? rows.length : "—"}</div>
            <div className="mt-1 text-xs theme-muted">Only rows returned by backend</div>
          </div>
          <div className="theme-surface p-4">
            <div className="mb-2 flex items-center gap-2 text-xs theme-muted"><ShieldCheck className="h-4 w-4" /> Universe coverage</div>
            <div className="text-lg font-bold theme-text">{health === "online" ? `${indexCount}/6 indices · ${stockCount}/15 stocks` : "—"}</div>
            <div className="mt-1 text-xs theme-muted">Counts unique underlyings present in live rows</div>
          </div>
          <div className="theme-surface p-4">
            <div className="mb-2 flex items-center gap-2 text-xs theme-muted"><Clock3 className="h-4 w-4" /> Last successful read</div>
            <div className="text-lg font-bold theme-text">{lastUpdated ?? "—"}</div>
            <div className="mt-1 text-xs theme-muted">Local time · Asia/Kolkata</div>
          </div>
        </div>
      </div>

      <div className="rounded-2xl border theme-border theme-surface p-4 theme-shadow">
        <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h3 className="font-semibold theme-text">Live opportunity quick filter</h3>
            <p className="mt-1 text-xs theme-muted">This is a compact view of actual API rows, not generated sample opportunities.</p>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            <span className="rounded-lg border theme-border theme-surface-2 px-3 py-2 theme-muted">Positive executable edges: <strong className="theme-text">{health === "online" ? edgeCount : "—"}</strong></span>
          </div>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <label className="relative block">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 theme-subtle" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search symbol / expiry / strike" className="min-h-11 w-full pl-9 pr-3 text-sm" />
          </label>
          <select value={instrument} onChange={(e) => setInstrument(e.target.value)} className="min-h-11 px-3 text-sm">
            <option value="ALL">All eligible underlyings</option><option value="INDEX">Indices</option><option value="STOCK">Top 15 stocks</option>
          </select>
          <select value={direction} onChange={(e) => setDirection(e.target.value)} className="min-h-11 px-3 text-sm">
            <option value="ALL">All directions / states</option><option value="LONG">LONG</option><option value="SHORT">SHORT</option>
          </select>
        </div>
        <div className="mt-4 overflow-x-auto rounded-xl border theme-border">
          <table className="w-full min-w-[720px] border-collapse text-left text-xs">
            <thead className="theme-surface-2 theme-muted"><tr>{["Underlying","Expiry","Strike","Direction","Executable edge","Updated"].map((h) => <th key={h} className="whitespace-nowrap px-3 py-3 font-semibold">{h}</th>)}</tr></thead>
            <tbody>
              {health === "error" ? <tr><td colSpan={6} className="px-3 py-6 text-center theme-danger"><WifiOff className="mr-2 inline h-4 w-4" /> Live rows unavailable. Existing scanner below may retain its own last successful state.</td></tr>
              : filtered.length === 0 ? <tr><td colSpan={6} className="px-3 py-6 text-center theme-muted">{health === "checking" ? "Loading live rows…" : "No matching live rows returned by backend."}</td></tr>
              : filtered.slice(0, 12).map((row, i) => {
                const edge = row.executable_edge ?? row.edge_per_unit;
                const dir = key(row.direction);
                const timestamp = row.observed_at ?? row.timestamp ?? row.timestamp_ns;
                return <tr key={`${key(row.underlying ?? row.symbol)}-${text(row.expiry)}-${text(row.strike)}-${i}`} className="border-t theme-border">
                  <td className="whitespace-nowrap px-3 py-3 font-semibold theme-text">{text(row.underlying ?? row.symbol) || "—"}</td>
                  <td className="whitespace-nowrap px-3 py-3 theme-muted">{text(row.expiry) || "—"}</td>
                  <td className="whitespace-nowrap px-3 py-3 theme-text">{fmt(row.strike)}</td>
                  <td className="whitespace-nowrap px-3 py-3"><span className={`rounded-md px-2 py-1 font-semibold ${dir === "LONG" || dir === "SHORT" ? "theme-success-bg theme-success" : "theme-surface-2 theme-muted"}`}>{dir || "NOT EVALUATED"}</span></td>
                  <td className="whitespace-nowrap px-3 py-3 font-semibold theme-text">{edge === undefined || edge === null ? "—" : `${fmt(edge)} pts`}</td>
                  <td className="whitespace-nowrap px-3 py-3 theme-muted">{timestamp ? text(timestamp) : "—"}</td>
                </tr>;
              })}
            </tbody>
          </table>
        </div>
        <div className="mt-3 flex items-start gap-2 text-xs theme-muted"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> Displayed edge is copied from backend fields; it is not a profit guarantee. Verify synchronized executable quotes, fees and carry before interpreting any signal.</div>
      </div>
    </section>
  );
}
