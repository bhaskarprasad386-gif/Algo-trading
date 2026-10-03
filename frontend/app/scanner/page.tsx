"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Bell, Filter, RefreshCw, Search, SlidersHorizontal, Zap } from "lucide-react";
import { Card, PageTitle, StatusDot } from "@/components/ui";

const markets = ["ALL F&O", "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"];
const columns = ["Symbol", "Expiry", "Cash", "Future", "Gap", "Volume / OI", "Signal"];

export default function ScannerPage() {
  const [market, setMarket] = useState("ALL F&O");
  const [search, setSearch] = useState("");

  useEffect(() => {
    const requestedMarket = new URLSearchParams(window.location.search).get("market");
    if (requestedMarket && markets.includes(requestedMarket)) setMarket(requestedMarket);
  }, []);

  return (
    <div className="space-y-5">
      <PageTitle eyebrow="Phase 3 • Live Scanner" title="Live Scanner" description="All F&O instruments with live 1-second scanner output, executable bid/ask gap, volume/OI and strategy signals." />
      <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-algo-border bg-algo-card px-3 text-xs font-semibold text-algo-warning"><StatusDot /> LIVE FEED NOT CONNECTED</span>
          <span className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-algo-border bg-algo-card px-3 text-xs font-semibold text-algo-muted"><Zap className="h-4 w-4" /> 1s scanner</span>
          <span className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-algo-border bg-algo-card px-3 text-xs font-semibold text-algo-muted"><Bell className="h-4 w-4" /> Alerts: —</span>
        </div>
        <div className="flex gap-2">
          <Link href="/custom-alert" className="inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border border-algo-border bg-algo-card px-4 text-sm font-semibold text-white transition hover:border-algo-primary/60"><Bell className="h-4 w-4" /> Custom Alert</Link>
          <button type="button" disabled className="inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border border-algo-border bg-algo-card px-4 text-sm font-semibold text-algo-muted opacity-70"><RefreshCw className="h-4 w-4" /> Refresh</button>
        </div>
      </div>
      <Card className="p-4">
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-white"><SlidersHorizontal className="h-4 w-4 text-algo-primary" /> Scanner Controls</div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-[1.2fr_1fr_1fr_auto]">
            <label className="relative block"><span className="sr-only">Search symbol</span><Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-algo-muted" /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search symbol" className="min-h-11 w-full pl-9 pr-3 text-sm" /></label>
            <label className="block"><span className="sr-only">Market</span><select value={market} onChange={(e) => setMarket(e.target.value)} className="min-h-11 w-full px-3 text-sm">{markets.map((item) => <option key={item}>{item}</option>)}</select></label>
            <select disabled className="min-h-11 w-full px-3 text-sm opacity-70" defaultValue="All Signals" aria-label="Signal filter"><option>All Signals</option></select>
            <button type="button" disabled className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-algo-border px-4 text-sm font-semibold text-algo-muted opacity-70"><Filter className="h-4 w-4" /> Filters</button>
          </div>
          <div className="flex flex-wrap gap-2">{markets.map((item) => <button key={item} type="button" onClick={() => setMarket(item)} className={"min-h-10 rounded-xl border px-3 text-xs font-semibold transition " + (market === item ? "border-algo-primary/70 bg-algo-primary/10 text-algo-primary" : "border-algo-border bg-algo-card text-algo-muted hover:text-white")}>{item}</button>)}</div>
        </div>
      </Card>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[["Eligible Instruments", "—"], ["Signals Detected", "—"], ["Executable Gaps", "—"], ["Last Scan", "—"]].map(([label, value]) => <Card key={label} className="p-4"><p className="text-xs text-algo-muted">{label}</p><p className="mt-2 text-xl font-semibold text-white">{value}</p></Card>)}
      </div>
      <Card className="overflow-hidden">
        <div className="flex flex-col gap-2 border-b border-algo-border p-4 sm:flex-row sm:items-center sm:justify-between">
          <div><h2 className="font-semibold text-white">Live Opportunities</h2><p className="mt-1 text-xs text-algo-muted">No live rows are displayed until the backend/WebSocket feed is connected.</p></div>
          <span className="text-xs text-algo-muted">Market: {market}{search ? " • Search: " + search : ""}</span>
        </div>
        <div className="overflow-x-auto"><table className="min-w-[900px] w-full text-left text-sm"><thead className="bg-algo-surface text-xs uppercase tracking-wider text-algo-muted"><tr>{columns.map((column) => <th key={column} className="px-4 py-3 font-semibold">{column}</th>)}</tr></thead><tbody><tr><td colSpan={columns.length} className="px-4 py-14 text-center text-sm text-algo-muted">No live data</td></tr></tbody></table></div>
      </Card>
      <Card className="p-4"><div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"><div><p className="text-sm font-semibold text-white">Scanner safety boundary</p><p className="mt-1 text-xs leading-5 text-algo-muted">Scanner output is paper-safe. Broker orders remain OFF. Phase 10 will connect the real FastAPI/WebSocket data source.</p></div><span className="shrink-0 rounded-lg border border-algo-border px-3 py-2 text-xs font-semibold text-algo-loss">BROKER ORDERS OFF</span></div></Card>
    </div>
  );
}
