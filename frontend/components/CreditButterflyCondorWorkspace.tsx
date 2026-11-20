"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, RefreshCw, ShieldCheck } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";
import { usePagePush } from "@/components/page-push-provider";

type ExpiryType = "WEEKLY" | "MONTHLY";
type ShadowRow = {
  strategy: string;
  strategy_type: string;
  direction: string;
  option_type: "CE" | "PE";
  underlying: string;
  expiry: number | string;
  expiry_type: string;
  strikes: number[];
  lot_size: number;
  gross_credit_per_unit: number;
  gross_credit_per_lot: number;
  minimum_gross_expiry_pnl_per_unit: number;
  maximum_gross_expiry_loss_per_unit: number;
  l1_executable_lots: number;
  depth: { status: string; executable_lots?: number; reason?: string };
  timestamp_ns: number;
  legs: Array<{ strike: number; action: string; multiplier: number; entry_price: number }>;
};
type Payload = {
  runtime_state: string;
  execution_mode: "SHADOW_ONLY";
  order_routing_enabled: false;
  shared_snapshot: { quote_contract_count: number; depth_record_count: number; depth_enabled: boolean; depth_running: boolean };
  result_count: number;
  results: ShadowRow[];
  live_coverage: Array<{ underlying: string; expiry_type: string; expiry: number; option_type: string; valid_quote_count: number; result_count: number }>;
  rejected_counts: Record<string, number>;
};

const SECTIONS = [
  { key: "LONG_CREDIT_BUTTERFLY_CE", title: "Long Credit Butterfly · CE", strategy: "LONG_CREDIT_BUTTERFLY", side: "CE" },
  { key: "LONG_CREDIT_BUTTERFLY_PE", title: "Long Credit Butterfly · PE", strategy: "LONG_CREDIT_BUTTERFLY", side: "PE" },
  { key: "SHORT_CREDIT_BUTTERFLY_CE", title: "Short Credit Butterfly · CE", strategy: "SHORT_CREDIT_BUTTERFLY", side: "CE" },
  { key: "SHORT_CREDIT_BUTTERFLY_PE", title: "Short Credit Butterfly · PE", strategy: "SHORT_CREDIT_BUTTERFLY", side: "PE" },
  { key: "LONG_CREDIT_CONDOR_CE", title: "Long Credit Condor · CE", strategy: "LONG_CREDIT_CONDOR", side: "CE" },
  { key: "LONG_CREDIT_CONDOR_PE", title: "Long Credit Condor · PE", strategy: "LONG_CREDIT_CONDOR", side: "PE" },
  { key: "SHORT_CREDIT_CONDOR_CE", title: "Short Credit Condor · CE", strategy: "SHORT_CREDIT_CONDOR", side: "CE" },
  { key: "SHORT_CREDIT_CONDOR_PE", title: "Short Credit Condor · PE", strategy: "SHORT_CREDIT_CONDOR", side: "PE" },
] as const;

const fmt = (value: unknown) => {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—";
};
const expiryLabel = (value: number | string) => {
  const raw = String(value);
  const match = raw.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (!match) return raw;
  return new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
};

export default function CreditButterflyCondorWorkspace({ expiryType }: { expiryType: ExpiryType }) {
  const symbols = useMemo(() => expiryType === "WEEKLY" ? ["NIFTY", "SENSEX"] : ["NIFTY", "SENSEX", "BANKNIFTY"], [expiryType]);
  const { status: connectionStatus, data: pageData, lastMessageAt, resync } = usePagePush(
    expiryType === "WEEKLY" ? "credit-butterfly-condor-weekly" : "credit-butterfly-condor-monthly"
  );
  const [payload, setPayload] = useState<Payload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState("");
  const [snapshotStale, setSnapshotStale] = useState(true);

  // PagePush is authoritative. When the runner/feed stops emitting, clearly
  // mark retained rows stale instead of presenting an old shadow snapshot LIVE.
  useEffect(() => {
    if (lastMessageAt === null) { setSnapshotStale(true); return; }
    setSnapshotStale(false);
    const timer = window.setTimeout(() => setSnapshotStale(true), 5000);
    return () => window.clearTimeout(timer);
  }, [lastMessageAt]);


  const refresh = useCallback(() => resync(), [resync]);

  useEffect(() => {
    if (!pageData) return;
    if (String(pageData.__event ?? "").toUpperCase() === "REMOVE") {
      const removedKeys = Array.isArray(pageData.remove_keys)
        ? pageData.remove_keys.filter((key): key is string => typeof key === "string")
        : Array.isArray(pageData.keys)
          ? pageData.keys.filter((key): key is string => typeof key === "string")
          : [];
      if (removedKeys.includes("payload")) {
        setPayload(null);
        setLoading(false);
        setError("");
        setLastUpdated("");
      }
    }
    const next = pageData.payload;
    if (next && typeof next === "object") {
      setPayload(next as Payload);
      setLoading(false);
      setError("");
    }
    if (lastMessageAt !== null) {
      setLastUpdated(new Date(lastMessageAt).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" }));
    }
  }, [pageData, lastMessageAt]);

  const eligibleRows = useMemo(() => (payload?.results ?? []).filter(row =>
    row.expiry_type === expiryType && symbols.includes(String(row.underlying).toUpperCase())
  ), [payload, expiryType, symbols]);

  const coverage = useMemo(() => (payload?.live_coverage ?? []).filter(row =>
    row.expiry_type === expiryType && symbols.includes(String(row.underlying).toUpperCase())
  ), [payload, expiryType, symbols.join("|")]);

  return (
    <main className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <PageTitle title={expiryType === "WEEKLY" ? "Weekly Credit Butterfly & Condor" : "Monthly Credit Butterfly & Condor"} description="Eight independent CE/PE strategy sections · existing Box Spread live market snapshot" />
          <div className="mt-2 flex flex-wrap gap-2">
            {symbols.map(symbol => <span key={symbol} className="rounded-full border theme-border px-3 py-1 text-xs font-bold">{symbol}</span>)}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-2 py-2 text-xs font-bold text-emerald-600"><ShieldCheck size={14} /> SHADOW ONLY · ORDERS OFF</span>
          <button onClick={() => void refresh()} className="inline-flex items-center gap-2 rounded-lg border theme-border px-3 py-2 text-xs font-bold"><RefreshCw size={14} /> Refresh</button>
        </div>
      </div>

      <nav aria-label="Expiry pages" className="flex gap-2">
        <Link href="/strategies/credit-butterfly-condor/weekly" aria-current={expiryType === "WEEKLY" ? "page" : undefined} className={`rounded-lg border px-4 py-2 text-sm font-bold ${expiryType === "WEEKLY" ? "border-cyan-500 bg-cyan-500/10 text-cyan-600" : "theme-border theme-muted"}`}>Weekly</Link>
        <Link href="/strategies/credit-butterfly-condor/monthly" aria-current={expiryType === "MONTHLY" ? "page" : undefined} className={`rounded-lg border px-4 py-2 text-sm font-bold ${expiryType === "MONTHLY" ? "border-cyan-500 bg-cyan-500/10 text-cyan-600" : "theme-border theme-muted"}`}>Monthly</Link>
      </nav>

      <Card className="space-y-2 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <div className="flex items-center gap-2 font-bold"><Activity size={15} /> Shared market feed <span className="rounded border theme-border px-2 py-0.5 text-[10px]">{connectionStatus}</span><span className={`rounded border px-2 py-0.5 text-[10px] ${snapshotStale ? "border-amber-500/40 text-amber-600" : "border-emerald-500/40 text-emerald-600"}`}>{snapshotStale ? "SNAPSHOT STALE" : "SNAPSHOT FRESH"}</span></div>
          <div>Runtime: <b>{payload?.runtime_state ?? (loading ? "LOADING" : "UNAVAILABLE")}</b></div>
          <div>Quote contracts: <b>{fmt(payload?.shared_snapshot.quote_contract_count)}</b></div>
          <div>Depth records: <b>{fmt(payload?.shared_snapshot.depth_record_count)}</b></div>
          <div>Last refresh: <b>{lastUpdated || "—"} IST</b></div>
        </div>
        <p className="text-xs theme-muted">Quote freshness ≤ 5 seconds · cross-leg skew ≤ 2 seconds · alert eligibility requires minimum expiry gross profit strictly greater than 5 points. Charges and net P&amp;L are not calculated.</p>{snapshotStale && <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-xs font-semibold text-amber-600">No fresh PagePush snapshot for 5 seconds. Retained rows are stale and must not be treated as current opportunities.</p>}
        {error && <p role="status" className="rounded-lg border border-amber-500/40 p-2 text-xs">{error}</p>}
      </Card>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        {SECTIONS.map(section => {
          const rows = eligibleRows.filter(row => row.strategy === section.strategy && row.option_type === section.side)
            .sort((a, b) => b.minimum_gross_expiry_pnl_per_unit - a.minimum_gross_expiry_pnl_per_unit);
          const coverageRows = coverage.filter(row => row.option_type === section.side);
          return (
            <Card key={section.key} className="min-w-0 space-y-3 p-3">
              <div className="flex items-start justify-between gap-2">
                <div><h2 className="text-sm font-extrabold">{section.title}</h2><p className="mt-1 text-[11px] theme-muted">{section.strategy.includes("BUTTERFLY") ? "3-leg · equal wing spacing" : "4-leg · equal strike spacing"} · {section.side}</p></div>
                <span className="rounded-md border theme-border px-2 py-1 text-xs font-bold">{rows.length} qualified</span>
              </div>
              <div className="flex flex-wrap gap-2 text-[11px] theme-muted">
                {symbols.map(symbol => {
                  const count = rows.filter(row => row.underlying === symbol).length;
                  const listed = coverageRows.filter(row => row.underlying === symbol).reduce((sum, row) => sum + Number(row.valid_quote_count || 0), 0);
                  return <span key={symbol} className="rounded-md theme-surface-2 px-2 py-1">{symbol}: {count} signals · {listed} fresh quotes</span>;
                })}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[690px] text-left text-[11px]">
                  <thead><tr className="border-b theme-border theme-muted"><th className="p-2">Index / Expiry</th><th className="p-2">Strikes</th><th className="p-2">Gross credit / unit</th><th className="p-2">Min expiry gross P&amp;L</th><th className="p-2">L1 lots</th><th className="p-2">Depth</th></tr></thead>
                  <tbody>
                    {rows.map((row, index) => <tr key={section.key + row.underlying + row.expiry + row.strikes.join("-") + index} className="border-b theme-border/60">
                      <td className="p-2 font-bold">{row.underlying}<div className="font-normal theme-muted">{expiryLabel(row.expiry)}</div></td>
                      <td className="p-2">{row.strikes.join(" / ")}</td>
                      <td className="p-2">{fmt(row.gross_credit_per_unit)}<div className="theme-muted">/ lot {fmt(row.gross_credit_per_lot)}</div></td>
                      <td className="p-2 font-bold">{fmt(row.minimum_gross_expiry_pnl_per_unit)}</td>
                      <td className="p-2">{fmt(row.l1_executable_lots)}</td>
                      <td className="p-2">{row.depth.status === "READY" ? <span className="text-emerald-600">READY · {fmt(row.depth.executable_lots)} lots</span> : <span className="theme-muted" title={row.depth.reason}>{row.depth.status}</span>}</td>
                    </tr>)}
                    {rows.length === 0 && <tr><td colSpan={6} className="p-4 text-center theme-muted">{loading ? "Loading live candidates…" : "No qualified candidates from the current fresh shared snapshot."}</td></tr>}
                  </tbody>
                </table>
              </div>
            </Card>
          );
        })}
      </div>
      <p className="text-[11px] theme-muted">Read-only shadow scanner. Reuses the Box Spread runner snapshot; it does not create subscriptions, send alerts externally, place paper trades, or route broker orders.</p>
    </main>
  );
}
