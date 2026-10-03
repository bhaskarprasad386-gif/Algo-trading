"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Bell, Filter, RefreshCw, Search, ShieldCheck, Zap } from "lucide-react";
import { Card, PageTitle, StatusDot } from "@/components/ui";
import { appConfig } from "@/lib/config";

export type WorkspaceConfig = {
  slug: string;
  title: string;
  description: string;
  universe: string;
  columns: string[];
  metrics: string[];
  controls: string[];
};

const configs: Record<string, WorkspaceConfig> = {
  "cash-future": {
    slug: "cash-future", title: "Cash-Future", description: "Dedicated cash-vs-future scanner for eligible F&O stocks.", universe: "ALL F&O STOCKS",
    columns: ["Symbol","Expiry","Cash Bid/Ask","Future Bid/Ask","Executable Gap","Volume / OI","Signal"],
    metrics: ["Eligible F&O Stocks","Executable Gaps","Signals","Last Scan"],
    controls: ["All F&O Stocks","CURRENT","NEAR","Gap Threshold"],
  },
  "calendar-spread": {
    slug: "calendar-spread", title: "Calendar Spread", description: "Dedicated near-vs-far expiry spread scanner.", universe: "F&O + INDEX + COMMODITIES",
    columns: ["Underlying","Near","Far","Spread","Gap / Edge","Volume / OI","Signal"],
    metrics: ["Eligible Contracts","Spread Opportunities","Signals","Last Scan"],
    controls: ["All Eligible","INDEX PRIORITY","Near/Far","Edge Threshold"],
  },
  "synthetic-arbitrage": {
    slug: "synthetic-arbitrage", title: "Synthetic Arbitrage", description: "Dedicated synthetic future/cash-carry scanner with strategy-specific strike universe.", universe: "INDEX + NIFTY 50",
    columns: ["Underlying","Expiry","Strike","Option","Future","Executable Edge","Signal"],
    metrics: ["Eligible Combos","Executable Edges","Signals","Last Scan"],
    controls: ["All Eligible","ATM Range","Expiry","Edge Threshold"],
  },
  "box-spread": {
    slug: "box-spread", title: "Box Spread", description: "Dedicated four-leg box scanner with executable edge ranking.", universe: "INDEX + ELIGIBLE OPTIONS",
    columns: ["Underlying","Expiry","Low Strike","High Strike","Box Edge","Liquidity","Signal"],
    metrics: ["Eligible Boxes","Executable Edges","Signals","Last Scan"],
    controls: ["All Eligible","Expiry","Strike Range","Edge Threshold"],
  },
  "custom-strategy": {
    slug: "custom-strategy", title: "Custom Strategy", description: "Independent strategy workspace. It does not depend on built-in scanner signals.", universe: "LIVE ELIGIBLE F&O UNIVERSE",
    columns: ["Symbol","Segment","Contract","Side","Live Price","Condition","Action"],
    metrics: ["Eligible Symbols","Conditions Met","Alerts","Last Scan"],
    controls: ["F&O Stocks","Cash / Future / Option","BUY / SELL","Condition"],
  },
};

function formatCell(slug: string, column: string, row: Record<string, unknown>) {
  const keys: Record<string, Record<string, string>> = {
    "cash-future": [["Symbol","symbol"],["Expiry","contract_month"],["Cash Bid/Ask","cash_execution_price"],["Future Bid/Ask","future_execution_price"],["Executable Gap","executable_gap"],["Volume / OI","volume"],["Signal","executable"]].reduce((a,[k,v]) => (a[k]=v,a), {} as Record<string,string>),
    "calendar-spread": {Underlying:"underlying",Near:"near_contract_month",Far:"far_contract_month",Spread:"gap_points","Gap / Edge":"long_edge", "Volume / OI":"liquidity_qty",Signal:"qualifies"},
    "synthetic-arbitrage": {Underlying:"underlying",Expiry:"expiry",Strike:"strike",Option:"call_bid",Future:"future_bid","Executable Edge":"executable_edge",Signal:"direction"},
    "box-spread": {Underlying:"symbol",Expiry:"expiry","Low Strike":"low_strike","High Strike":"high_strike","Box Edge":"executable_edge",Liquidity:"liquidity_qty",Signal:"direction"},
  };
  const key = keys[slug]?.[column];
  const value = key ? row[key] : undefined;
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "boolean") return value ? "YES" : "NO";
  if (typeof value === "number") return Number.isFinite(value) ? value.toLocaleString("en-IN", { maximumFractionDigits: 4 }) : "—";
  return String(value);
}

export function StrategyWorkspace({ slug }: { slug: keyof typeof configs }) {
  const c = configs[slug];
  const [search, setSearch] = useState("");
  const [control, setControl] = useState(c.controls[0]);
  const [refreshing, setRefreshing] = useState(false);
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const endpoint = slug === "cash-future" ? "/api/v1/scanner/cash-future/live/fast?limit=50"
    : slug === "calendar-spread" ? "/api/v1/scanner/calendar-spread/live?limit=50"
    : slug === "synthetic-arbitrage" ? "/api/v1/scanner/synthetic-cash-carry/live?limit=50"
    : slug === "box-spread" ? "/api/v1/scanner/box-spread/live?limit=50"
    : null;

  const load = async () => {
    if (!endpoint) { setRows([]); setLoading(false); return; }
    setLoading(true); setError(null);
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(`${base}${endpoint}`, { cache: "no-store" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.detail || `${c.title} HTTP ${response.status}`);
      const data = Array.isArray(body?.data) ? body.data : [];
      setRows(data.filter((item: unknown): item is Record<string, unknown> => !!item && typeof item === "object"));
    } catch (e) {
      setRows([]);
      setError(e instanceof Error ? e.message : "Backend unavailable");
    } finally { setLoading(false); }
  };

  useEffect(() => { void load(); }, [endpoint]);

  const rowsMessage = useMemo(() => search ? `No live data for “${search}”.` : "No live data", [search]);

  async function refresh() {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }

  const filteredRows = useMemo(() => {
    const q = search.trim().toUpperCase();
    if (!q) return rows;
    return rows.filter(row => Object.values(row).some(value => String(value ?? "").toUpperCase().includes(q)));
  }, [rows, search]);

  const signalCount = rows.filter(row => row.qualifies === true || Number(row.executable_edge ?? row.executable_gap ?? 0) > 0 || Number(row.gap_points ?? 0) > 0).length;
  const lastScan = rows.reduce<string | null>((latest, row) => {
    const raw = row.timestamp_ns ?? row.timestamp ?? null;
    if (raw == null) return latest;
    return latest == null || String(raw) > latest ? String(raw) : latest;
  }, null);

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <Link href="/scanner" className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-algo-border bg-algo-card px-3 text-xs font-semibold text-algo-muted hover:text-white">
          <ArrowLeft className="h-4 w-4" /> Overall Scanner
        </Link>
        <span className={`inline-flex min-h-10 items-center gap-2 rounded-xl border border-algo-border bg-algo-card px-3 text-xs font-semibold ${loading ? "text-algo-warning" : error ? "text-algo-loss" : "text-algo-profit"}`}>
          <StatusDot live={!loading && !error} /> {loading ? "CONNECTING" : error ? "BACKEND ERROR" : "LIVE DATA CONNECTED"}
        </span>
      </div>

      <PageTitle eyebrow="Phase 11 • Strategy Workspace" title={c.title + " Dedicated Scanner"} description={c.description} />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Card className="p-4"><p className="text-xs text-algo-muted">Universe</p><p className="mt-2 text-sm font-semibold text-white">{c.universe}</p></Card>
        <Card className="p-4"><p className="text-xs text-algo-muted">Scanner</p><p className="mt-2 flex items-center gap-2 text-sm font-semibold text-algo-primary"><Zap className="h-4 w-4" /> 1-second</p></Card>
        <Card className="p-4"><p className="text-xs text-algo-muted">Paper Trading</p><p className="mt-2 text-sm font-semibold text-algo-profit">AVAILABLE</p></Card>
        <Card className="p-4"><p className="text-xs text-algo-muted">Broker Orders</p><p className="mt-2 text-sm font-semibold text-algo-warning">OFF</p></Card>
      </div>

      <Card className="p-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-white"><Filter className="h-4 w-4 text-algo-primary" /> Dedicated Scanner Controls</div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-[1.2fr_1fr_auto]">
          <label className="relative block"><Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-algo-muted" /><span className="sr-only">Search</span><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search symbol / underlying" className="min-h-11 w-full pl-9 pr-3 text-sm" /></label>
          <select value={control} onChange={e=>setControl(e.target.value)} className="min-h-11 w-full px-3 text-sm">{c.controls.map(x=><option key={x}>{x}</option>)}</select>
          <button type="button" onClick={refresh} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-algo-border bg-algo-card px-4 text-sm font-semibold text-white hover:border-algo-primary/60"><RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} /> Refresh</button>
        </div>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {c.metrics.map(x=><Card key={x} className="p-4"><p className="text-xs text-algo-muted">{x}</p><p className="mt-2 text-xl font-semibold text-white">{loading ? "…" : x === "Signals" ? signalCount : x === "Executable Gaps" || x === "Executable Edges" || x === "Spread Opportunities" ? signalCount : x === "Eligible F&O Stocks" || x === "Eligible Contracts" || x === "Eligible Combos" || x === "Eligible Boxes" ? rows.length : lastScan ? "Live" : "—"}</p></Card>)}
      </div>

      {error && <Card className="border-algo-loss/30 bg-algo-loss/5 p-4 text-sm text-algo-loss">{error}</Card>}

      <Card className="overflow-hidden">
        <div className="flex flex-col gap-2 border-b border-algo-border p-4 sm:flex-row sm:items-center sm:justify-between">
          <div><h2 className="font-semibold text-white">{c.title} Opportunities</h2><p className="mt-1 text-xs text-algo-muted">Dedicated strategy scanner. Rows appear only when the real backend/live feed provides data.</p></div>
          <span className="text-xs text-algo-muted">{control}{search ? ` • ${search}` : ""}</span>
        </div>
        <div className="overflow-x-auto"><table className="min-w-[1000px] w-full text-left text-sm"><thead className="bg-algo-surface text-xs uppercase tracking-wider text-algo-muted"><tr>{c.columns.map(x=><th key={x} className="px-4 py-3 font-semibold">{x}</th>)}</tr></thead><tbody>{loading ? <tr><td colSpan={c.columns.length} className="px-4 py-14 text-center text-sm text-algo-muted">Loading live data…</td></tr> : filteredRows.length === 0 ? <tr><td colSpan={c.columns.length} className="px-4 py-14 text-center text-sm text-algo-muted">{rowsMessage}</td></tr> : filteredRows.map((row, index) => <tr key={String(row.id ?? row.event_id ?? `${slug}-${index}`)} className="border-b border-algo-border/70 last:border-0">{c.columns.map((column, i) => <td key={column} className={`px-4 py-3 ${i === 0 ? "font-semibold text-white" : "text-algo-muted"}`}>{formatCell(slug, column, row)}</td>)}</tr>)}</tbody></table></div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="p-4"><div className="flex items-center gap-2 text-sm font-semibold text-white"><Bell className="h-4 w-4 text-algo-primary" /> Strategy Alerts</div><p className="mt-2 text-xs leading-5 text-algo-muted">Configure scanner-wide alerts from the Custom Alert workspace.</p><Link href="/custom-alert" className="mt-3 inline-flex text-xs font-semibold text-algo-primary">Open alerts →</Link></Card>
        <Card className="p-4"><div className="flex items-center gap-2 text-sm font-semibold text-white"><ShieldCheck className="h-4 w-4 text-algo-profit" /> Paper Execution</div><p className="mt-2 text-xs leading-5 text-algo-muted">Paper-only execution remains separate from broker orders.</p><Link href="/paper-trading" className="mt-3 inline-flex text-xs font-semibold text-algo-primary">Open paper trading →</Link></Card>
        <Card className="p-4"><div className="text-sm font-semibold text-white">Workspace Boundary</div><p className="mt-2 text-xs leading-5 text-algo-muted">No fake values, no historical-download work, and no replay controls are added here.</p></Card>
      </div>
    </div>
  );
}
