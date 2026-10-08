"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Activity, Bell, Filter, RefreshCw, Search, ShieldCheck, SlidersHorizontal, Zap } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

const markets = ["ALL F&O STOCKS", "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"];
const columns = ["Symbol", "Expiry", "Cash Bid/Ask", "Future Bid/Ask", "Executable Gap", "Volume / OI", "Signal"];
const pairColumns = ["Time", "Symbol", "Contract", "Pair State", "Cash Ask", "Future Bid", "Gap %", "Why / Diagnostics"];

type Row = Record<string, unknown>;

const numberValue = (row: Row, ...keys: string[]) => {
  for (const key of keys) {
    const value = Number(row[key]);
    if (Number.isFinite(value)) return value;
  }
  return 0;
};

const cell = (row: Row, ...keys: string[]) => {
  for (const key of keys) {
    const value = row[key];
    if (value !== undefined && value !== null && value !== "") {
      if (typeof value === "number") return value.toLocaleString("en-IN", { maximumFractionDigits: 4 });
      return String(value);
    }
  }
  return "—";
};

function ScannerContent() {
  const searchParams = useSearchParams();
  const requestedMarket = searchParams.get("market")?.toUpperCase() ?? "ALL F&O STOCKS";
  const [market, setMarket] = useState(requestedMarket);
  const [search, setSearch] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pairRows, setPairRows] = useState<Row[]>([]);
  const [pairHistory, setPairHistory] = useState<Row[]>([]);
  const [marketSession, setMarketSession] = useState<"OPEN" | "CLOSED" | "UNKNOWN">("UNKNOWN");

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(`${base}/api/v1/scanner/cash-future/live/fast?limit=500`, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail || `Scanner HTTP ${response.status}`);
      const data = Array.isArray(body?.data) ? body.data : [];
      setRows(data.filter((item: unknown): item is Row => !!item && typeof item === "object"));
    } catch (e) {
      setRows([]);
      setError(e instanceof Error ? e.message : "Backend unavailable");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setMarket(markets.includes(requestedMarket) ? requestedMarket : "ALL F&O STOCKS");
  }, [requestedMarket]);

  const loadPairLive = async () => {
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(`${base}/api/v1/scanner/cash-future/live/pairs?max_age_seconds=30&limit=150`, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      setPairRows(Array.isArray(body?.data) ? body.data : []);
    } catch {
      setPairRows([]);
    }
  };

  const loadPairHistory = async () => {
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(`${base}/api/v1/scanner/cash-future/live/history?days=1&limit=250`, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      setPairHistory(Array.isArray(body?.data) ? body.data : []);
    } catch {
      setPairHistory([]);
    }
  };

  useEffect(() => {
    void load();
    void loadPairLive();
    void loadPairHistory();
    const liveTimer = window.setInterval(() => { void load(); void loadPairLive(); }, 1500);
    const historyTimer = window.setInterval(() => { void loadPairHistory(); }, 10000);
    return () => {
      window.clearInterval(liveTimer);
      window.clearInterval(historyTimer);
    };
  }, []);

  const filteredRows = useMemo(() => {
    const q = search.trim().toUpperCase();
    return rows.filter((row) => {
      const symbol = String(row.symbol ?? row.underlying ?? "").toUpperCase();
      const normalizedSymbol = symbol.replace(/\s+/g, "");
      const normalizedMarket = market.replace(/\s+/g, "");
      const marketMatch = market === "ALL F&O STOCKS" || normalizedSymbol === normalizedMarket || (market === "NIFTY" && normalizedSymbol === "NIFTY50");
      const searchMatch = !q || Object.values(row).some((value) => String(value ?? "").toUpperCase().includes(q));
      return marketMatch && searchMatch;
    });
  }, [rows, market, search]);

  const signals = filteredRows.filter((row) =>
    row.executable === true ||
    numberValue(row, "executable_gap", "gap", "net_gap") > 0
  ).length;

  const gaps = filteredRows.filter((row) => numberValue(row, "executable_gap", "gap", "net_gap") > 0).length;
  const filteredPairs = useMemo(() => {
    const q = search.trim().toUpperCase();
    return pairRows.filter((row) => {
      const symbol = String(row.symbol ?? "").toUpperCase();
      const normalizedSymbol = symbol.replace(/\s+/g, "");
      const normalizedMarket = market.replace(/\s+/g, "");
      const marketMatch = market === "ALL F&O STOCKS" || normalizedSymbol === normalizedMarket || (market === "NIFTY" && normalizedSymbol === "NIFTY50");
      const searchMatch = !q || Object.values(row).some((value) => String(value ?? "").toUpperCase().includes(q));
      return marketMatch && searchMatch;
    });
  }, [pairRows, market, search]);

  const filteredPairHistory = useMemo(() => {
    const q = search.trim().toUpperCase();
    return pairHistory.filter((row) => {
      const symbol = String(row.symbol ?? "").toUpperCase();
      const normalizedSymbol = symbol.replace(/\s+/g, "");
      const normalizedMarket = market.replace(/\s+/g, "");
      const marketMatch = market === "ALL F&O STOCKS" || normalizedSymbol === normalizedMarket || (market === "NIFTY" && normalizedSymbol === "NIFTY50");
      const searchMatch = !q || Object.values(row).some((value) => String(value ?? "").toUpperCase().includes(q));
      return marketMatch && searchMatch;
    });
  }, [pairHistory, market, search]);

  const pairStats = useMemo(() => ({
    pairs: filteredPairs.length,
    missing: filteredPairs.filter((row) => row.status === "MISSING_EXECUTION_QUOTE").length,
    noSignal: filteredPairs.filter((row) => row.status === "NO_SIGNAL").length,
    signals: filteredPairs.filter((row) => row.status === "SIGNAL").length,
  }), [filteredPairs]);

  const formatTime = (value: unknown) => {
    if (!value) return "—";
    const date = new Date(String(value));
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleTimeString("en-IN", { hour12: false });
  };

  const reasonText = (row: Row) => {
    const reasons = Array.isArray(row.reason_codes) ? row.reason_codes : String(row.reason_codes ?? "").split(",").filter(Boolean);
    return reasons.join(" • ") || "PAIR_CREATED";
  };

  const refresh = async () => {
    setRefreshing(true);
    await Promise.all([load(), loadPairLive(), loadPairHistory()]);
    setRefreshing(false);
  };

  return (
    <div className="min-h-screen space-y-5 theme-bg p-4 theme-text">
      <PageTitle eyebrow="Phase 3 • Live Scanner" title="Cash-Future Live Scanner" description="Live Cash-Future opportunities across the subscribed F&O stock-futures universe, with executable bid/ask gap, volume/OI and strategy signals." />

      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`inline-flex min-h-10 items-center gap-2 rounded-xl border px-3 text-xs font-semibold ${loading ? "theme-border theme-warning-bg theme-warning" : error ? "theme-border theme-danger-bg theme-danger" : "theme-border theme-success-bg theme-success"}`}>
            <span className="h-2 w-2 rounded-full bg-current" />
            {loading ? "CONNECTING" : error ? "BACKEND ERROR" : rows.length ? "LIVE TICKS" : "API CONNECTED • NO LIVE DATA"}
          </span>
          <span className="inline-flex min-h-10 items-center gap-2 rounded-xl border theme-border theme-surface px-3 text-xs font-semibold theme-muted"><Zap className="h-4 w-4" /> {marketSession === "CLOSED" ? "Polling paused • Market closed" : "1.5s polling"}</span>
          <span className="inline-flex min-h-10 items-center gap-2 rounded-xl border theme-border theme-surface px-3 text-xs font-semibold theme-muted"><Bell className="h-4 w-4" /> Alerts: —</span>
        </div>
        <div className="flex gap-2">
          <Link href="/custom-alert" className="inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border theme-border theme-surface px-4 text-sm font-semibold theme-text hover:theme-border"><Bell className="h-4 w-4" /> Custom Alert</Link>
          <button type="button" onClick={refresh} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border theme-border theme-surface px-4 text-sm font-semibold theme-text hover:theme-border"><RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} /> Refresh</button>
        </div>
      </div>

      <Card className="theme-border theme-surface p-4">
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-2 text-sm font-semibold theme-text"><Activity className="h-4 w-4 theme-accent" /><SlidersHorizontal className="h-4 w-4 theme-accent" /> Scanner Controls</div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-[1.2fr_1fr_auto]">
            <label className="relative block"><span className="sr-only">Search symbol</span><Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 theme-muted" /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search symbol" className="min-h-11 w-full rounded-xl border theme-border theme-surface pl-9 pr-3 text-sm theme-text" /></label>
            <select value={market} onChange={(e) => setMarket(e.target.value)} className="min-h-11 w-full rounded-xl border theme-border theme-surface px-3 text-sm theme-text">{markets.map((item) => <option key={item}>{item}</option>)}</select>
            <button type="button" disabled className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border theme-border theme-surface px-4 text-sm font-semibold theme-muted opacity-70"><Filter className="h-4 w-4" /> Filters</button>
          </div>
          <div className="flex flex-wrap gap-2">{markets.map((item) => <button key={item} type="button" onClick={() => setMarket(item)} className={`min-h-10 rounded-xl border px-3 text-xs font-semibold ${market === item ? "theme-accent theme-accent-bg" : "theme-border theme-surface theme-muted"}`}>{item}</button>)}</div>
        </div>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ["Live F&O Rows", loading ? "…" : filteredRows.length.toLocaleString("en-IN")],
          ["Signals Detected", loading ? "…" : signals.toLocaleString("en-IN")],
          ["Executable Gaps", loading ? "…" : gaps.toLocaleString("en-IN")],
          ["Last Scan", loading ? "…" : rows.length ? "LIVE" : "—"],
        ].map(([label, value]) => <Card key={label} className="theme-border theme-surface p-4"><p className="text-xs theme-muted">{label}</p><p className="mt-2 text-xl font-semibold theme-text">{value}</p></Card>)}
      </div>

      {error && <Card className="theme-border theme-danger-bg p-4 text-sm theme-danger">{error}</Card>}

      <Card className="overflow-hidden theme-border theme-surface">
        <div className="flex flex-col gap-2 border-b theme-border p-4 sm:flex-row sm:items-center sm:justify-between">
          <div><h2 className="font-semibold theme-text">Live Opportunities</h2><p className="mt-1 text-xs theme-muted">Live rows from the FastAPI scanner. Broker orders remain OFF.</p></div>
          <span className="text-xs theme-muted">Market: {market}{search ? " • Search: " + search : ""}</span>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-[1000px] w-full text-left text-sm">
            <thead className="theme-surface-2 text-xs uppercase tracking-wider theme-muted"><tr>{columns.map((column) => <th key={column} className="px-4 py-3 font-semibold">{column}</th>)}</tr></thead>
            <tbody>
              {loading ? <tr><td colSpan={columns.length} className="px-4 py-14 text-center text-sm theme-muted">Loading live data…</td></tr>
                : filteredRows.length === 0 ? <tr><td colSpan={columns.length} className="px-4 py-14 text-center text-sm theme-muted">No live data</td></tr>
                : filteredRows.map((row, index) => <tr key={String(row.id ?? row.event_id ?? `scanner-${index}`)} className="border-b theme-border last:border-0">
                  <td className="px-4 py-3 font-semibold theme-text">{cell(row, "symbol", "underlying")}</td>
                  <td className="px-4 py-3 theme-muted">{cell(row, "contract_month", "expiry")}</td>
                  <td className="px-4 py-3 theme-muted">{cell(row, "cash_execution_price", "cash_bid", "cash_ask")}</td>
                  <td className="px-4 py-3 theme-muted">{cell(row, "future_execution_price", "future_bid", "future_ask")}</td>
                  <td className="px-4 py-3 theme-muted">{cell(row, "executable_gap", "net_gap", "gap")}</td>
                  <td className="px-4 py-3 theme-muted">{cell(row, "volume", "open_interest", "oi")}</td>
                  <td className="px-4 py-3 font-semibold theme-text">{row.executable === true ? "SIGNAL" : numberValue(row, "executable_gap", "gap", "net_gap") > 0 ? "OPPORTUNITY" : "—"}</td>
                </tr>)}
            </tbody>
          </table>
        </div>
      </Card>

      <Card className="theme-border theme-surface p-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div><h2 className="flex items-center gap-2 font-semibold theme-text"><Activity className="h-4 w-4 theme-accent" /> Live Pair Monitor & Diagnostics</h2><p className="mt-1 text-xs theme-muted">Every matched cash + futures pair is shown here even when it cannot become a signal. Diagnostics are persisted for post-market review.</p></div>
          <span className="text-xs font-semibold theme-muted">{pairStats.pairs} recent pairs • {marketSession === "CLOSED" ? "refresh paused" : "1.5s refresh"}</span>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-4">{[["Pairs matched", pairStats.pairs], ["Missing quote", pairStats.missing], ["No signal", pairStats.noSignal], ["Signals", pairStats.signals]].map(([label, value]) => <div key={String(label)} className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">{label}</p><p className="mt-1 text-lg font-semibold theme-text">{value}</p></div>)}</div>
        <div className="mt-4 overflow-x-auto"><table className="min-w-[1100px] w-full text-left text-sm">
          <thead className="theme-surface-2 text-xs uppercase tracking-wider theme-muted"><tr>{pairColumns.map((column) => <th key={column} className="px-4 py-3 font-semibold">{column}</th>)}</tr></thead>
          <tbody>{filteredPairs.length === 0 ? <tr><td colSpan={pairColumns.length} className="px-4 py-10 text-center text-sm theme-muted">No pair diagnostics in the last 30 seconds.</td></tr> : filteredPairs.map((row, index) => {
            const status = String(row.status ?? "PAIR_CREATED");
            const statusLabel = status === "SIGNAL" ? "SIGNAL" : status === "NO_SIGNAL" ? "NO SIGNAL" : status === "MISSING_EXECUTION_QUOTE" ? "BLOCKED • QUOTE" : status;
            return <tr key={String(row.observation_ref ?? "pair-" + index)} className="border-b theme-border last:border-0">
              <td className="px-4 py-3 theme-muted">{formatTime(row.observed_at)}</td><td className="px-4 py-3 font-semibold theme-text">{cell(row, "symbol")}</td>
              <td className="px-4 py-3 theme-muted">{cell(row, "contract_month")}</td><td className="px-4 py-3 font-semibold theme-text">{statusLabel}</td>
              <td className="px-4 py-3 theme-muted">{cell(row, "cash_ask")}</td><td className="px-4 py-3 theme-muted">{cell(row, "future_bid")}</td>
              <td className="px-4 py-3 theme-muted">{numberValue(row, "gap_pct").toFixed(4)}%</td><td className="max-w-[430px] px-4 py-3 text-xs leading-5 theme-muted">{reasonText(row)}</td>
            </tr>;
          })}</tbody>
        </table></div>
      </Card>
      <Card className="theme-border theme-surface p-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"><div><h2 className="font-semibold theme-text">Post-Market Pair History</h2><p className="mt-1 text-xs theme-muted">Persisted pair diagnostics from the last trading day. This remains available after the live feed stops.</p></div><span className="text-xs theme-muted">{filteredPairHistory.length.toLocaleString("en-IN")} shown • {pairHistory.length.toLocaleString("en-IN")} saved</span></div>
        <div className="mt-3 flex flex-wrap gap-2 text-xs theme-muted"><span className="rounded-lg border theme-border px-3 py-2">Saved automatically</span><span className="rounded-lg border theme-border px-3 py-2">Retention: scanner result setting</span><span className="rounded-lg border theme-border px-3 py-2">Includes quote failures + no-signal reasons</span></div>
        <div className="mt-4 overflow-x-auto"><table className="min-w-[1100px] w-full text-left text-sm">
          <thead className="theme-surface-2 text-xs uppercase tracking-wider theme-muted"><tr>{pairColumns.map((column) => <th key={column} className="px-4 py-3 font-semibold">{column}</th>)}</tr></thead>
          <tbody>{filteredPairHistory.length === 0 ? <tr><td colSpan={pairColumns.length} className="px-4 py-10 text-center text-sm theme-muted">No saved pair diagnostics for this selection.</td></tr> : filteredPairHistory.slice(0, 100).map((row, index) => {
            const status = String(row.lifecycle ?? "PAIR_CREATED");
            const statusLabel = status === "SIGNAL" ? "SIGNAL" : status === "NO_SIGNAL" ? "NO SIGNAL" : status === "MISSING_EXECUTION_QUOTE" ? "BLOCKED • QUOTE" : status === "LIQUIDITY_BLOCKED" ? "BLOCKED • LIQUIDITY" : status;
            return <tr key={String(row.observation_ref ?? "history-" + index)} className="border-b theme-border last:border-0">
              <td className="px-4 py-3 theme-muted">{formatTime(row.observed_at)}</td><td className="px-4 py-3 font-semibold theme-text">{cell(row, "symbol")}</td>
              <td className="px-4 py-3 theme-muted">{cell(row, "contract_month")}</td><td className="px-4 py-3 font-semibold theme-text">{statusLabel}</td>
              <td className="px-4 py-3 theme-muted">{cell(row, "cash_ask")}</td><td className="px-4 py-3 theme-muted">{cell(row, "future_bid")}</td>
              <td className="px-4 py-3 theme-muted">{numberValue(row, "gap_pct").toFixed(4)}%</td><td className="max-w-[430px] px-4 py-3 text-xs leading-5 theme-muted">{reasonText(row)}</td>
            </tr>;
          })}</tbody>
        </table></div>
      </Card>
      <Card className="theme-border theme-surface p-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"><div><p className="flex items-center gap-2 text-sm font-semibold theme-text"><ShieldCheck className="h-4 w-4 theme-success" /> Scanner safety boundary</p><p className="mt-1 text-xs leading-5 theme-muted">Scanner output is paper-safe. Broker orders remain OFF.</p></div><span className="shrink-0 rounded-lg border theme-border theme-danger-bg px-3 py-2 text-xs font-semibold theme-danger">BROKER ORDERS OFF</span></div>
      </Card>
    </div>
  );
}

export default function ScannerPage() {
  return (
    <Suspense fallback={<div className="min-h-screen theme-bg p-4 theme-text">Loading scanner…</div>}>
      <ScannerContent />
    </Suspense>
  );
}
