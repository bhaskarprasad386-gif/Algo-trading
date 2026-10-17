"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Activity, ArrowLeft, Bell, Filter, RefreshCw, Search, ShieldCheck, Zap } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

export type WorkspaceConfig = {
  slug: string;
  title: string;
  description: string;
  universe: string;
  columns: string[];
  metrics: string[];
  controls: string[];
  pairTitle: string;
  pairColumns: string[];
  historyEndpoint: string;
  pairEndpoint?: string;
};

const configs: Record<string, WorkspaceConfig> = {
  "calendar-spread": {
    slug: "calendar-spread",
    title: "Calendar Spread",
    description: "Dedicated near-vs-far expiry spread scanner using the common live market-data feed.",
    universe: "F&O + INDEX + COMMODITIES",
    columns: ["Underlying", "Exchange", "Near", "Far", "Spread", "Gap / Edge", "Volume / OI", "Signal"],
    metrics: ["Eligible Contracts", "Spread Opportunities", "Signals", "Last Scan"],
    controls: ["All Eligible", "INDEX PRIORITY", "Near/Far", "Edge Threshold"],
    pairTitle: "Near / Far Pair Monitor",
    pairColumns: ["Time", "Underlying", "Exchange", "Near", "Far", "Spread", "Edge", "Liquidity", "State"],
    historyEndpoint: "/api/v1/scanner/calendar-spread/history?days=1&limit=100",
    pairEndpoint: "/api/v1/scanner/calendar-spread/pairs?limit=200",
  },
  "synthetic-arbitrage": {
    slug: "synthetic-arbitrage",
    title: "Synthetic Arbitrage",
    description: "Dedicated synthetic future/cash-carry scanner with the live option + future universe.",
    universe: "SUPPORTED INDEX + NIFTY TOP 15 LIQUID F&O STOCKS",
    columns: ["Underlying", "Expiry", "Strike", "Option", "Future", "Executable Edge", "Liquidity", "Signal"],
    metrics: ["Eligible Combos", "Executable Edges", "Signals", "Last Scan"],
    controls: ["ALL", "TOP15", "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"],
    pairTitle: "Option / Future Pair Monitor",
    pairColumns: ["Time", "Underlying", "Expiry", "Strike", "Option", "Future", "Edge", "Direction", "State"],
    historyEndpoint: "/api/v1/scanner/synthetic-cash-carry/alerts?days=1&limit=100",
    pairEndpoint: "/api/v1/scanner/synthetic-cash-carry/pairs?limit=200",
  },
  "box-spread": {
    slug: "box-spread",
    title: "Box Spread",
    description: "Dedicated four-leg box scanner with executable edge and liquidity ranking.",
    universe: "INDEX + ELIGIBLE OPTIONS",
    columns: ["Underlying", "Expiry", "Low Strike", "High Strike", "Box Edge", "Liquidity", "Direction", "Signal"],
    metrics: ["Eligible Boxes", "Executable Edges", "Signals", "Last Scan"],
    controls: ["All Eligible", "Expiry", "Strike Range", "Edge Threshold"],
    pairTitle: "Low / High Strike Pair Monitor",
    pairColumns: ["Time", "Underlying", "Expiry", "Low Strike", "High Strike", "Box Edge", "Lot Size", "Direction", "State"],
    historyEndpoint: "/api/v1/scanner/box-spread/alerts?days=1&limit=100",
  },
  "custom-strategy": {
    slug: "custom-strategy",
    title: "Custom Strategy",
    description: "Independent strategy workspace.",
    universe: "CONFIGURATION ONLY",
    columns: ["Symbol", "Segment", "Contract", "Side", "Live Price", "Condition", "Action"],
    metrics: ["Eligible Symbols", "Conditions Met", "Alerts", "Last Scan"],
    controls: ["F&O Stocks", "Cash / Future / Option", "BUY / SELL", "Condition"],
    pairTitle: "Pair Monitor",
    pairColumns: ["Time", "Symbol", "State"],
    historyEndpoint: "",
  },
};

type Row = Record<string, unknown>;

function value(row: Row, ...keys: string[]) {
  for (const key of keys) {
    if (row[key] !== undefined && row[key] !== null && row[key] !== "") return row[key];
  }
  return undefined;
}

function numberValue(row: Row, ...keys: string[]) {
  const raw = value(row, ...keys);
  const n = Number(raw);
  return Number.isFinite(n) ? n : 0;
}

function formatValue(raw: unknown) {
  if (raw === undefined || raw === null || raw === "") return "—";
  if (typeof raw === "boolean") return raw ? "YES" : "NO";
  if (typeof raw === "number") return Number.isFinite(raw) ? raw.toLocaleString("en-IN", { maximumFractionDigits: 4 }) : "—";
  return String(raw);
}

function formatCell(slug: string, column: string, row: Row) {
  const keys: Record<string, Record<string, string[]>> = {
    "calendar-spread": {
      Underlying: ["underlying"], Exchange: ["exchange"], Near: ["near_contract_month"],
      Far: ["far_contract_month"], Spread: ["gap_points"], "Gap / Edge": ["long_edge", "short_edge"],
      "Volume / OI": ["liquidity_qty"], Signal: ["direction"],
    },
    "synthetic-arbitrage": {
      Underlying: ["underlying"], Expiry: ["expiry"], Strike: ["strike"],
      Option: ["call_bid", "put_bid"], Future: ["future_bid", "future_ask"],
      "Executable Edge": ["executable_edge"], Liquidity: ["lot_size"], Signal: ["direction"],
    },
    "box-spread": {
      Underlying: ["symbol"], Expiry: ["expiry"], "Low Strike": ["low_strike"],
      "High Strike": ["high_strike"], "Box Edge": ["executable_edge"],
      Liquidity: ["liquidity_qty"], Direction: ["direction"], Signal: ["direction"],
    },
  };
  return formatValue(value(row, ...(keys[slug]?.[column] ?? [])));
}

function pairCell(slug: string, column: string, row: Row) {
  const maps: Record<string, Record<string, string[]>> = {
    "calendar-spread": {
      Time: ["timestamp_ns", "observed_at"], Underlying: ["underlying"], Exchange: ["exchange"],
      Near: ["near_contract_month"], Far: ["far_contract_month"], Spread: ["gap_points"],
      Edge: ["long_edge", "short_edge"], Liquidity: ["liquidity_qty"], State: ["direction", "qualifies"],
    },
    "synthetic-arbitrage": {
      Time: ["timestamp_ns", "observed_at"], Underlying: ["underlying"], Expiry: ["expiry"], Strike: ["strike"],
      Option: ["direction", "call_bid", "put_bid"], Future: ["future_bid", "future_ask"],
      Edge: ["executable_edge"], Direction: ["direction"], State: ["executable_edge"],
    },
    "box-spread": {
      Time: ["timestamp_ns", "observed_at"], Underlying: ["symbol", "underlying"], Expiry: ["expiry"],
      "Low Strike": ["low_strike"], "High Strike": ["high_strike"], "Box Edge": ["executable_edge"],
      "Lot Size": ["lot_size"], Direction: ["direction"], State: ["executable_edge"],
    },
  };
  const raw = value(row, ...(maps[slug]?.[column] ?? []));
  if (column === "Time") {
    if (raw === undefined || raw === null || raw === "") return "—";
    const n = Number(raw);
    if (Number.isFinite(n) && n > 1_000_000_000_000) return new Date(n / 1_000_000).toLocaleTimeString("en-IN", { hour12: false });
    const d = new Date(String(raw));
    return Number.isNaN(d.getTime()) ? String(raw) : d.toLocaleTimeString("en-IN", { hour12: false });
  }
  if (column === "State") {
    const edge = numberValue(row, "executable_edge", "long_edge", "short_edge");
    if (slug === "calendar-spread") return row.qualifies === true || edge > 0 ? "OPPORTUNITY" : "NO SIGNAL";
    if (slug === "synthetic-arbitrage") {
      const state = typeof row.state === "string" ? row.state.replaceAll("_", " ") : (edge > 0 ? "EDGE" : "NO EDGE");
      const reason = typeof row.reason === "string" && row.reason ? " · " + row.reason.replaceAll("_", " ") : "";
      return state + reason;
    }
    return edge > 0 ? "EDGE" : "NO EDGE";
  }
  return formatValue(raw);
}

function isSignal(slug: string, row: Row) {
  if (slug === "calendar-spread") return row.qualifies === true || numberValue(row, "long_edge", "short_edge") > 0;
  return numberValue(row, "executable_edge") > 0;
}

function runnerLabel(slug: string) {
  if (slug === "calendar-spread") return "Calendar Spread Runner";
  if (slug === "synthetic-arbitrage") return "Synthetic Future Runner";
  if (slug === "box-spread") return "Box Spread Runner";
  return "Strategy Runner";
}

export function StrategyWorkspace({ slug }: { slug: keyof typeof configs }) {
  const c = configs[slug];
  const isCustom = slug === "custom-strategy";
  const endpoint =
    slug === "calendar-spread" ? "/api/v1/scanner/calendar-spread/live?limit=50" :
    slug === "synthetic-arbitrage" ? "/api/v1/scanner/synthetic-cash-carry/live?limit=50" :
    slug === "box-spread" ? "/api/v1/scanner/box-spread/live?limit=50&max_age_seconds=5" : null;

  const [search, setSearch] = useState("");
  const [control, setControl] = useState(c.controls[0]);
  const [top15StockSymbols, setTop15StockSymbols] = useState<string[]>([
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "INFY",
    "TCS", "AXISBANK", "LT", "BHARTIARTL", "KOTAKBANK",
    "TMPV", "BAJFINANCE", "M&M", "HINDALCO", "TATASTEEL",
  ]);
  const [universeAvailability, setUniverseAvailability] = useState<{
    eligible_future_symbols?: string[]; missing_future_symbols?: string[];
    eligible_target_symbols?: string[]; missing_option_expiry_symbols?: string[]; checked_at?: string | null;
  } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [historyRows, setHistoryRows] = useState<Row[]>([]);
  const [pairRows, setPairRows] = useState<Row[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedTicks, setFeedTicks] = useState<number | null>(null);
  const [feedAge, setFeedAge] = useState<number | null>(null);
  const [feedStatus, setFeedStatus] = useState("CHECKING");
  const [feedInstruments, setFeedInstruments] = useState<number | null>(null);
  const [runner, setRunner] = useState<{ running: boolean; detail: string } | null>(null);
  const [feedErrors, setFeedErrors] = useState<{ delivery: number; normalizer: number } | null>(null);
  const [scannerDiagnostics, setScannerDiagnostics] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    if (slug !== "synthetic-arbitrage") return;
    let cancelled = false;
    const base = appConfig.apiBaseUrl.replace(/\/$/, "");
    void fetch(base + "/api/v1/scanner/synthetic-cash-carry/universe", { cache: "no-store" })
      .then(async (response) => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body?.detail || "Unable to load synthetic universe");
        if (!cancelled && typeof body?.selection === "string") setControl(body.selection);
        if (!cancelled && Array.isArray(body?.top15_stock_symbols)) {
          setTop15StockSymbols(body.top15_stock_symbols.filter((symbol: unknown): symbol is string => typeof symbol === "string"));
        }
        if (!cancelled && body?.availability && typeof body.availability === "object") setUniverseAvailability(body.availability);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Unable to load synthetic universe");
      });
    return () => { cancelled = true; };
  }, [slug]);

  const changeControl = async (selection: string) => {
    if (slug !== "synthetic-arbitrage") {
      setControl(selection);
      return;
    }
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/universe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selection }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail?.message || body?.detail || "Universe update failed");
      setControl(String(body?.selection ?? selection));
      if (Array.isArray(body?.top15_stock_symbols)) {
        setTop15StockSymbols(body.top15_stock_symbols.filter((symbol: unknown): symbol is string => typeof symbol === "string"));
      }
      if (body?.availability && typeof body.availability === "object") setUniverseAvailability(body.availability);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Universe update failed");
    }
  };

  const load = async (showLoading = false) => {
    if (!endpoint) {
      setRows([]);
      setLoading(false);
      return;
    }
    if (showLoading) setLoading(true);
    setError(null);
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(base + endpoint, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail || c.title + " HTTP " + response.status);
      const data = Array.isArray(body?.data) ? body.data : [];
      setRows(data.filter((item: unknown): item is Row => !!item && typeof item === "object"));
    } catch (e) {
      setRows([]);
      setError(e instanceof Error ? e.message : "Backend unavailable");
    } finally {
      setLoading(false);
    }
  };

  const loadPairs = async () => {
    if (isCustom || !c.pairEndpoint) {
      setPairRows(rows);
      return;
    }
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(base + c.pairEndpoint, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(c.title + " pair monitor HTTP " + response.status);
      const data = Array.isArray(body?.data) ? body.data : [];
      setPairRows(data.filter((item: unknown): item is Row => !!item && typeof item === "object"));
    } catch {
      // Keep the last pair snapshot during a transient backend failure.
    }
  };

  const loadHistory = async () => {
    if (isCustom || !c.historyEndpoint) return;
    setHistoryLoading(true);
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(base + c.historyEndpoint, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(c.title + " history HTTP " + response.status);
      const data = Array.isArray(body?.data) ? body.data : [];
      setHistoryRows(data.filter((item: unknown): item is Row => !!item && typeof item === "object"));
    } catch {
      // Keep last good history snapshot during transient backend failures.
    } finally {
      setHistoryLoading(false);
    }
  };

  useEffect(() => {
    if (isCustom) return;
    void loadHistory();
    const timer = window.setInterval(() => void loadHistory(), 10000);
    return () => window.clearInterval(timer);
  }, [isCustom, c.historyEndpoint]);

  useEffect(() => {
    if (isCustom) return;
    if (!c.pairEndpoint) {
      setPairRows(rows);
      return;
    }
    void loadPairs();
    const timer = window.setInterval(() => void loadPairs(), 1500);
    return () => window.clearInterval(timer);
  }, [isCustom, c.pairEndpoint, rows]);

  useEffect(() => {
    if (slug !== "synthetic-arbitrage") {
      setScannerDiagnostics(null);
      return;
    }
    let cancelled = false;
    const loadScannerDiagnostics = async () => {
      try {
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/diagnostics", { cache: "no-store" });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error("scanner diagnostics HTTP " + response.status);
        if (!cancelled) setScannerDiagnostics(body && typeof body === "object" ? body as Record<string, unknown> : null);
      } catch {
        if (!cancelled) setScannerDiagnostics({ status: "DIAGNOSTICS_UNAVAILABLE" });
      }
    };
    void loadScannerDiagnostics();
    const timer = window.setInterval(loadScannerDiagnostics, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [slug]);

  useEffect(() => {
    if (isCustom) return;
    let cancelled = false;
    const loadRuntime = async () => {
      try {
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(base + "/api/v1/market-data/runtime/health", { cache: "no-store" });
        if (!response.ok) throw new Error("runtime health");
        const body = await response.json();
        if (cancelled) return;
        const feed = body?.common_feed ?? {};
        setFeedTicks(Number.isFinite(Number(feed.ticks_received)) ? Number(feed.ticks_received) : null);
        setFeedAge(Number.isFinite(Number(feed.age_seconds)) ? Number(feed.age_seconds) : null);
        setFeedInstruments(Number.isFinite(Number(feed.active_instruments)) ? Number(feed.active_instruments) : null);
        setFeedStatus(String(feed.status ?? body?.feed_status ?? "UNKNOWN").toUpperCase());
        setFeedErrors({
          delivery: Number(feed.delivery_errors ?? 0) || 0,
          normalizer: Number(feed.normalizer_errors ?? 0) || 0,
        });
        const rawRunner = body?.runners?.[slug === "synthetic-arbitrage" ? "synthetic" : slug.replace("-", "_")];
        const alternate = slug === "synthetic-arbitrage" ? body?.runners?.synthetic_arbitrage : slug === "box-spread" ? body?.runners?.box_spread : body?.runners?.calendar_spread;
        const selected = rawRunner ?? alternate;
        setRunner(selected ? { running: Boolean(selected.running), detail: String(selected.detail ?? (selected.running ? "RUNNING" : "STOPPED")) } : null);
      } catch {
        if (!cancelled) setFeedStatus("UNAVAILABLE");
      }
    };
    void loadRuntime();
    const timer = window.setInterval(loadRuntime, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [isCustom, slug]);

  useEffect(() => {
    if (isCustom) {
      setRows([]);
      setLoading(false);
      return;
    }
    void load(true);
    const timer = window.setInterval(() => void load(), 1500);
    return () => window.clearInterval(timer);
  }, [endpoint, isCustom]);

  const filteredRows = useMemo(() => {
    const q = search.trim().toUpperCase();
    if (!q) return rows;
    return rows.filter((row) => Object.values(row).some((v) => String(v ?? "").toUpperCase().includes(q)));
  }, [rows, search]);

  const signals = rows.filter((row) => isSignal(slug, row)).length;
  const monitorRows = c.pairEndpoint ? pairRows : rows;
  const lastScan = rows.reduce<string | null>((latest, row) => {
    const raw = value(row, "timestamp_ns", "timestamp");
    if (raw == null) return latest;
    const current = String(raw);
    return latest == null || current > latest ? current : latest;
  }, null);

  const refresh = async () => {
    setRefreshing(true);
    await Promise.all([load(), loadHistory(), loadPairs()]);
    setRefreshing(false);
  };

  const metricValue = (metric: string) => {
    if (loading) return "…";
    if (metric === "Eligible Contracts") return feedInstruments == null ? "—" : feedInstruments.toLocaleString("en-IN");
    if (metric === "Spread Opportunities" || metric === "Executable Edges" || metric === "Executable Gaps") return rows.length.toLocaleString("en-IN");
    if (metric === "Signals") return signals.toLocaleString("en-IN");
    if (metric === "Eligible Combos" || metric === "Eligible Boxes") return rows.length.toLocaleString("en-IN");
    if (metric === "Last Scan") return lastScan ? "LIVE" : "—";
    return "—";
  };

  return (
    <div className="min-h-screen space-y-5 theme-bg p-4 theme-text">
      <div className="flex items-center justify-between gap-3">
        <Link href="/scanner" className="inline-flex min-h-10 items-center gap-2 rounded-xl border theme-border theme-surface px-3 text-xs font-semibold theme-muted hover:theme-border">
          <ArrowLeft className="h-4 w-4" /> Cash Future Scanner
        </Link>
        <span className={"inline-flex min-h-10 items-center gap-2 rounded-xl border px-3 text-xs font-semibold " +
          (loading ? "theme-border theme-warning-bg theme-warning" : error ? "theme-border theme-danger-bg theme-danger" :
          feedStatus === "LIVE" ? "theme-border theme-success-bg theme-success" : "theme-border theme-warning-bg theme-warning")}>
          {loading ? "CONNECTING" : error ? "BACKEND ERROR" : feedStatus === "LIVE" ? "LIVE FEED" : feedStatus === "UNAVAILABLE" ? "FEED UNAVAILABLE" : "FEED CONNECTED"}
        </span>
      </div>

      <PageTitle eyebrow="Live Strategy Scanner" title={c.title} description={c.description} />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="theme-border theme-surface p-4"><p className="text-xs theme-muted">Universe</p><p className="mt-2 text-sm font-semibold theme-text">{c.universe}</p></Card>
        <Card className="theme-border theme-surface p-4"><p className="text-xs theme-muted">Common Market Data Feed</p><p className="mt-2 flex items-center gap-2 text-sm font-semibold theme-accent"><Activity className="h-4 w-4" />{feedTicks == null ? "CHECKING" : feedTicks.toLocaleString("en-IN") + " ticks"}</p></Card>
        <Card className="theme-border theme-surface p-4"><p className="text-xs theme-muted">{runnerLabel(slug)}</p><p className={"mt-2 text-sm font-semibold " + (runner?.running ? "theme-success" : "theme-warning")}>{runner?.running ? "RUNNING" : "STOPPED / UNAVAILABLE"}</p></Card>
        <Card className="theme-border theme-surface p-4"><p className="text-xs theme-muted">Broker Orders</p><p className="mt-2 text-sm font-semibold theme-warning">OFF</p></Card>
      </div>

      <Card className="theme-border theme-surface p-4">
        <div className="flex items-center gap-2 text-sm font-semibold theme-text"><Filter className="h-4 w-4 theme-accent" /> Live Scanner Controls</div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-[1.2fr_1fr_auto]">
          <label className="relative block">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 theme-muted" />
            <span className="sr-only">Search</span>
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search symbol / underlying" className="min-h-11 w-full rounded-xl border theme-border theme-surface px-3 pl-9 text-sm theme-text" />
          </label>
          <select value={control} onChange={(e) => void changeControl(e.target.value)} className="min-h-11 w-full rounded-xl border theme-border theme-surface px-3 text-sm theme-text">
            {c.controls.map((x) => <option key={x} value={x}>{slug === "synthetic-arbitrage" && x === "TOP15" ? "NIFTY TOP 15 LIQUID STOCKS (15)" : x}</option>)}
          </select>
          <button type="button" onClick={refresh} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border theme-border theme-surface px-4 text-sm font-semibold theme-text hover:theme-border">
            <RefreshCw className={"h-4 w-4 " + (refreshing ? "animate-spin" : "")} /> Refresh
          </button>
        </div>
        {slug === "synthetic-arbitrage" && (
          <div className="mt-3 rounded-xl border theme-border theme-surface-2 p-3 text-xs">
            <p className="font-semibold theme-text">
              {control === "TOP15" ? "NIFTY TOP 15 LIQUID STOCKS — stock-only scan" :
               control === "ALL" ? "ALL — supported indices + these 15 liquid F&O stocks" :
               "INDEX-ONLY SCAN — " + control}
            </p>
            {control === "TOP15" || control === "ALL" ? (
              <>
                <p className="mt-2 leading-6 theme-muted">{top15StockSymbols.join(" • ")}</p>
                <p className="mt-1 theme-muted">These names define the basket only; stocks are not separate dropdown choices.</p>
              </>
            ) : (
              <p className="mt-1 theme-muted">Only the selected index is scanned. No fallback to stocks or other indices.</p>
            )}
            {control === "TOP15" && <p className="mt-1 theme-success">TOP15 scans the configured stock basket automatically. No individual stock selection is required; only stocks with eligible current futures and matching option expiry can be scanned.</p>}
            {(control === "TOP15" || control === "ALL") && universeAvailability && (
              <div className="mt-2 space-y-1 border-t theme-border pt-2">
                <p className="theme-text">Eligible futures ({universeAvailability.eligible_future_symbols?.length ?? 0}/15): {(universeAvailability.eligible_future_symbols ?? []).join(" • ") || "Not yet checked"}</p>
                <p className="theme-muted">Missing futures ({universeAvailability.missing_future_symbols?.length ?? 0}): {(universeAvailability.missing_future_symbols ?? []).join(" • ") || "None reported"}</p>
                <p className="theme-text">Scan-ready targets ({universeAvailability.eligible_target_symbols?.filter((symbol) => top15StockSymbols.includes(symbol)).length ?? 0}/15): {(universeAvailability.eligible_target_symbols ?? []).filter((symbol) => top15StockSymbols.includes(symbol)).join(" • ") || "Not yet checked"}</p>
                <p className="theme-muted">Missing matching option expiry: {(universeAvailability.missing_option_expiry_symbols ?? []).filter((symbol) => top15StockSymbols.includes(symbol)).join(" • ") || "None reported"}</p>
                <p className="theme-muted">Availability is from the last backend universe check, not a guarantee of live quotes.</p>
              </div>
            )}
          </div>
        )}
      </Card>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {c.metrics.map((metric) => (
          <Card key={metric} className="theme-border theme-surface p-4">
            <p className="text-xs theme-muted">{metric}</p>
            <p className="mt-2 text-xl font-semibold theme-text">{metricValue(metric)}</p>
          </Card>
        ))}
      </div>

      <Card className="theme-border theme-surface p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
          <div><p className="text-xs theme-muted">Common Feed Ticks</p><p className="mt-1 text-lg font-semibold theme-text">{feedTicks == null ? "—" : feedTicks.toLocaleString("en-IN")}</p><p className="text-[11px] theme-muted">Actual cumulative feed counter</p></div>
          <div><p className="text-xs theme-muted">Feed Age</p><p className="mt-1 text-lg font-semibold theme-text">{feedAge == null ? "—" : feedAge.toFixed(1) + "s"}</p></div>
          <div><p className="text-xs theme-muted">Active Instruments</p><p className="mt-1 text-lg font-semibold theme-text">{feedInstruments == null ? "—" : feedInstruments.toLocaleString("en-IN")}</p></div>
          <div><p className="text-xs theme-muted">Runner</p><p className={"mt-1 text-lg font-semibold " + (runner?.running ? "theme-success" : "theme-warning")}>{runner?.running ? "RUNNING" : "STOPPED"}</p></div>
          <div><p className="text-xs theme-muted">Feed Delivery Errors</p><p className="mt-1 text-lg font-semibold theme-text">{feedErrors?.delivery ?? "—"}</p></div>
          <div><p className="text-xs theme-muted">Normalizer Errors</p><p className="mt-1 text-lg font-semibold theme-text">{feedErrors?.normalizer ?? "—"}</p></div>
        </div>
      </Card>

      {error && <Card className="theme-border theme-danger-bg p-4 text-sm theme-danger">{error}</Card>}

      <Card className="overflow-hidden theme-border theme-surface">
        <div className="flex flex-col gap-2 border-b theme-border p-4 sm:flex-row sm:items-center sm:justify-between">
          <div><h2 className="flex items-center gap-2 font-semibold theme-text"><Zap className="h-4 w-4 theme-accent" /> Live Opportunities</h2><p className="mt-1 text-xs theme-muted">Real backend rows only. No fake scanner values are generated in the UI.</p></div>
          <span className="text-xs theme-muted">{control}{search ? " • " + search : ""} • 1.5s refresh</span>
        </div>
        <div className="grid gap-2 p-3 md:hidden">
          {loading ? <div className="rounded-xl border theme-border p-4 text-center text-sm theme-muted">Loading live data…</div> : filteredRows.length === 0 ? <div className="rounded-xl border theme-border p-4 text-center text-sm theme-muted">No live opportunity data</div> : filteredRows.map((row, index) => <div key={String(row.id ?? row.event_id ?? row.timestamp_ns ?? slug + "-mobile-live-" + index)} className="rounded-xl border theme-border theme-surface-2 p-3"><div className="mb-2 text-sm font-bold theme-text">{formatCell(slug, c.columns[0], row)}</div><div className="grid grid-cols-2 gap-x-3 gap-y-2">{c.columns.slice(1).map((column) => <div key={column} className="min-w-0"><div className="text-[10px] uppercase tracking-wide theme-subtle">{column}</div><div className="mt-0.5 break-words text-xs font-medium theme-muted">{formatCell(slug, column, row)}</div></div>)}</div></div>)}
        </div>
        <div className="hidden overflow-x-auto md:block">
          <table className="min-w-[1100px] w-full text-left text-sm">
            <thead className="theme-surface-2 text-xs uppercase tracking-wider theme-muted"><tr>{c.columns.map((column) => <th key={column} className="px-4 py-3 font-semibold">{column}</th>)}</tr></thead>
            <tbody>
              {loading ? <tr><td colSpan={c.columns.length} className="px-4 py-14 text-center text-sm theme-muted">Loading live data…</td></tr> :
              filteredRows.length === 0 ? <tr><td colSpan={c.columns.length} className="px-4 py-14 text-center text-sm theme-muted">No live opportunity data</td></tr> :
              filteredRows.map((row, index) => <tr key={String(row.id ?? row.event_id ?? row.timestamp_ns ?? slug + "-" + index)} className="border-b theme-border last:border-0">
                {c.columns.map((column, i) => <td key={column} className={"px-4 py-3 " + (i === 0 ? "font-semibold theme-text" : "theme-muted")}>{formatCell(slug, column, row)}</td>)}
              </tr>)}
            </tbody>
          </table>
        </div>
      </Card>

      <Card className="theme-border theme-surface p-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div><h2 className="flex items-center gap-2 font-semibold theme-text"><Activity className="h-4 w-4 theme-accent" /> Live {c.pairTitle}</h2><p className="mt-1 text-xs theme-muted">Every current pair/combo from the real backend snapshot is shown, including rows without a signal.</p></div>
          <span className="text-xs font-semibold theme-muted">{monitorRows.length.toLocaleString("en-IN")} live pairs • 1.5s refresh</span>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Live pairs</p><p className="mt-1 text-lg font-semibold theme-text">{monitorRows.length.toLocaleString("en-IN")}</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Signals / edges</p><p className="mt-1 text-lg font-semibold theme-text">{signals.toLocaleString("en-IN")}</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Feed status</p><p className="mt-1 text-lg font-semibold theme-text">{feedStatus}</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Feed age</p><p className="mt-1 text-lg font-semibold theme-text">{feedAge == null ? "—" : feedAge.toFixed(1) + "s"}</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Last scan</p><p className="mt-1 text-lg font-semibold theme-text">{lastScan ? "LIVE" : "—"}</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Broker execution</p><p className="mt-1 text-lg font-semibold theme-warning">OFF</p></div>
        </div>
        <div className="mt-4 grid gap-2 md:hidden">
          {loading ? <div className="rounded-xl border theme-border p-4 text-center text-sm theme-muted">Loading live pair monitor…</div> : filteredRows.length === 0 ? <div className="rounded-xl border theme-border p-4 text-center text-sm theme-muted">No live pair/combo observations</div> : filteredRows.map((row, index) => <div key={String(row.id ?? row.timestamp_ns ?? slug + "-mobile-pair-" + index)} className="rounded-xl border theme-border theme-surface-2 p-3"><div className="mb-2 text-sm font-bold theme-text">{pairCell(slug, c.pairColumns[1], row)}</div><div className="grid grid-cols-2 gap-x-3 gap-y-2">{c.pairColumns.filter((column) => column !== c.pairColumns[1]).map((column) => <div key={column} className="min-w-0"><div className="text-[10px] uppercase tracking-wide theme-subtle">{column}</div><div className="mt-0.5 break-words text-xs font-medium theme-muted">{pairCell(slug, column, row)}</div></div>)}</div></div>)}
        </div>
        <div className="mt-4 hidden overflow-x-auto md:block">
          <table className="min-w-[1100px] w-full text-left text-sm">
            <thead className="theme-surface-2 text-xs uppercase tracking-wider theme-muted"><tr>{c.pairColumns.map((column) => <th key={column} className="px-4 py-3 font-semibold">{column}</th>)}</tr></thead>
            <tbody>
              {loading ? <tr><td colSpan={c.pairColumns.length} className="px-4 py-10 text-center theme-muted">Loading live pair monitor…</td></tr> :
              filteredRows.length === 0 ? <tr><td colSpan={c.pairColumns.length} className="px-4 py-10 text-center theme-muted">No live pair/combo observations</td></tr> :
              filteredRows.map((row, index) => <tr key={String(row.id ?? row.timestamp_ns ?? slug + "-pair-" + index)} className="border-b theme-border last:border-0">
                {c.pairColumns.map((column, i) => <td key={column} className={"px-4 py-3 " + (i === 1 ? "font-semibold theme-text" : "theme-muted")}>{pairCell(slug, column, row)}</td>)}
              </tr>)}
            </tbody>
          </table>
        </div>
        <div className="mt-3 rounded-xl border theme-border theme-surface-2 p-3 text-xs theme-muted"><span className="font-semibold theme-text">Common feed:</span> {feedTicks == null ? "—" : feedTicks.toLocaleString("en-IN") + " ticks"} • {feedInstruments == null ? "—" : feedInstruments.toLocaleString("en-IN") + " instruments"} • runner {runner?.running ? "RUNNING" : "STOPPED"}.</div>
      </Card>

      <Card className="theme-border theme-surface p-4">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div><h2 className="flex items-center gap-2 font-semibold theme-text"><Activity className="h-4 w-4 theme-accent" /> Post-Market {c.title} Pair History</h2><p className="mt-1 text-xs theme-muted">Real backend history/alert records for today's session. No synthetic rows are created in the UI.</p></div>
          <span className="text-xs font-semibold theme-muted">{historyLoading ? "Loading…" : historyRows.length.toLocaleString("en-IN") + " saved"}</span>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Saved observations</p><p className="mt-1 text-lg font-semibold theme-text">{historyRows.length.toLocaleString("en-IN")}</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">Live monitor</p><p className="mt-1 text-lg font-semibold theme-success">CONNECTED</p></div>
          <div className="rounded-xl border theme-border theme-surface-2 p-3"><p className="text-xs theme-muted">History source</p><p className="mt-1 text-sm font-semibold theme-text">{slug === "calendar-spread" ? "Scanner history" : "Alert history"}</p></div>
        </div>
        <div className="mt-4 grid gap-2 md:hidden">
          {historyRows.length === 0 ? <div className="rounded-xl border theme-border p-4 text-center text-sm theme-muted">No saved pair history for this session.</div> : historyRows.map((row, index) => <div key={String(row.id ?? row.timestamp_ns ?? slug + "-mobile-history-" + index)} className="rounded-xl border theme-border theme-surface-2 p-3"><div className="mb-2 text-sm font-bold theme-text">{pairCell(slug, c.pairColumns[1], row)}</div><div className="grid grid-cols-2 gap-x-3 gap-y-2">{c.pairColumns.filter((column) => column !== c.pairColumns[1]).map((column) => <div key={column} className="min-w-0"><div className="text-[10px] uppercase tracking-wide theme-subtle">{column}</div><div className="mt-0.5 break-words text-xs font-medium theme-muted">{pairCell(slug, column, row)}</div></div>)}</div></div>)}
        </div>
        <div className="mt-4 hidden overflow-x-auto md:block">
          <table className="min-w-[1100px] w-full text-left text-sm">
            <thead className="theme-surface-2 text-xs uppercase tracking-wider theme-muted"><tr>{c.pairColumns.map((column) => <th key={column} className="px-4 py-3 font-semibold">{column}</th>)}</tr></thead>
            <tbody>
              {historyRows.length === 0 ? <tr><td colSpan={c.pairColumns.length} className="px-4 py-10 text-center theme-muted">No saved pair history for this session.</td></tr> :
              historyRows.map((row, index) => <tr key={String(row.id ?? row.timestamp_ns ?? slug + "-history-" + index)} className="border-b theme-border last:border-0">
                {c.pairColumns.map((column, i) => <td key={column} className={"px-4 py-3 " + (i === 1 ? "font-semibold theme-text" : "theme-muted")}>{pairCell(slug, column, row)}</td>)}
              </tr>)}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="theme-border theme-surface p-4"><div className="flex items-center gap-2 text-sm font-semibold theme-text"><Bell className="h-4 w-4 theme-accent" /> Strategy Alerts</div><p className="mt-2 text-xs leading-5 theme-muted">Scanner-wide alerts remain separate from live opportunity rendering.</p><Link href="/custom-alert" className="mt-3 inline-flex text-xs font-semibold theme-accent">Open alerts →</Link></Card>
        <Card className="theme-border theme-surface p-4"><div className="flex items-center gap-2 text-sm font-semibold theme-text"><ShieldCheck className="h-4 w-4 theme-success" /> Scanner Safety Boundary</div><p className="mt-2 text-xs leading-5 theme-muted">This workspace reads live scanner output only. Broker orders remain OFF and no paper execution logic is changed.</p></Card>
        <Card className="theme-border theme-surface p-4"><div className="text-sm font-semibold theme-text">Session Diagnostics</div><p className="mt-2 text-xs leading-5 theme-muted">MCX can remain live after NSE closes. Calendar/commodity output therefore follows the common feed and actual exchange session rather than the NSE-only status.</p></Card>
      </div>
    </div>
  );
}
