"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, RefreshCw, ShieldCheck } from "lucide-react";
import { Card } from "@/components/ui";
import { appConfig } from "@/lib/config";
import { usePagePush } from "@/components/page-push-provider";

type ShadowResult = {
  strategy: string;
  runtime_state: string;
  source: string;
  read_only: boolean;
  execution_mode: string;
  order_routing_enabled: boolean;
  result_count: number;
  shared_snapshot: { source: string; read_only: boolean; quote_contract_count: number; quote_group_count: number; freshness_limit_seconds: number; depth_enabled: boolean; depth_running: boolean; depth_record_count: number; depth_readiness_count: number };
  rejected_counts: Record<string, number>;
  diagnostics: Record<string, number | Record<string, number>>;
  depth_status: { complete: number; incomplete: number; incomplete_reasons: Record<string, number> };
  live_coverage: Array<Record<string, string | number>>;
  results: Array<Record<string, unknown>>;
};

const n = (v: unknown) => Number.isFinite(Number(v)) ? Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—";
const title = (v: string) => v.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, c => c.toUpperCase());

export default function BoxSpreadShadowPanel() {
  const [data, setData] = useState<ShadowResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { data: pagePushData, status: pagePushStatus } = usePagePush("box-spread");
  const requestSequence = useRef(0);
  const refresh = useCallback(async () => {
    const requestId = ++requestSequence.current;
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(base + "/api/v1/scanner/box-spread/shadow/butterfly-condor", { cache: "no-store", signal: AbortSignal.timeout(12000) });
      if (!response.ok) throw new Error("Shadow diagnostics HTTP " + response.status);
      const body = await response.json() as ShadowResult;
      if (requestId !== requestSequence.current) return;
      if (body?.strategy !== "box-spread-shadow" || body?.execution_mode !== "SHADOW_ONLY" || body?.order_routing_enabled !== false) throw new Error("Unexpected or unsafe shadow response");
      setData(body); setError(null);
    } catch (e) { if (requestId === requestSequence.current) setError(e instanceof Error ? e.message : "Unable to load shadow diagnostics"); }
    finally { if (requestId === requestSequence.current) setLoading(false); }
  }, []);
  useEffect(() => {
    if (pagePushStatus === "LIVE") return;
    void refresh();
    const id = window.setInterval(() => void refresh(), 15000);
    return () => { window.clearInterval(id); requestSequence.current += 1; };
  }, [refresh, pagePushStatus]);
  useEffect(() => {
    const pushed = pagePushData?.shadow as ShadowResult | undefined;
    if (!pushed || pushed.strategy !== "box-spread-shadow" || pushed.execution_mode !== "SHADOW_ONLY" || pushed.order_routing_enabled !== false) return;
    requestSequence.current += 1;
    setData(pushed);
    setError(null);
    setLoading(false);
  }, [pagePushData]);

  const diagnostics = data?.diagnostics ?? {};
  const rejected = data?.rejected_counts ?? {};
  const metricKeys = ["valid_quotes", "candidate_structures", "depth_ready", "depth_incomplete", "lot_size_mismatch", "cross_leg_quote_timestamp_skew", "non_positive_credit", "below_minimum_expiry_profit"];
  return <Card id="box-shadow-monitor" className="space-y-4 border border-slate-300 dark:border-slate-700 p-4" aria-label="Butterfly and Condor shadow diagnostics">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><div className="flex items-center gap-2 text-base font-extrabold"><Activity className="h-5 w-5" /> Butterfly / Condor Shadow Monitor</div><p className="mt-1 text-sm text-slate-500">Existing Box Spread quotes and depth only · no additional broker subscriptions</p></div>
      <div className="flex items-center gap-2"><span className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-xs font-bold text-emerald-600"><ShieldCheck className="mr-1 inline h-3.5 w-3.5" /> SHADOW ONLY · ORDERS OFF</span><button onClick={() => void refresh()} className="rounded-lg border px-3 py-2 text-sm font-bold"><RefreshCw className="mr-1 inline h-4 w-4" /> Refresh</button></div>
    </div>
    {error && <p role="status" className="rounded-lg border border-amber-500/40 p-3 text-sm">{error}</p>}
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      <div className="rounded-lg bg-slate-100 p-3 dark:bg-slate-800"><div className="text-xs text-slate-500">Runtime</div><div className="mt-1 font-extrabold">{data?.runtime_state ? title(data.runtime_state) : loading ? "Loading" : "Unavailable"}</div></div>
      <div className="rounded-lg bg-slate-100 p-3 dark:bg-slate-800"><div className="text-xs text-slate-500">Shadow Results</div><div className="mt-1 text-xl font-extrabold">{n(data?.result_count)}</div></div>
      <div className="rounded-lg bg-slate-100 p-3 dark:bg-slate-800"><div className="text-xs text-slate-500">Depth Ready / Incomplete</div><div className="mt-1 font-extrabold">{n(data?.depth_status.complete)} / {n(data?.depth_status.incomplete)}</div></div>
      <div className="rounded-lg bg-slate-100 p-3 dark:bg-slate-800"><div className="text-xs text-slate-500">Live Coverage Groups</div><div className="mt-1 text-xl font-extrabold">{n(data?.live_coverage.length)}</div></div>
      <div className="rounded-lg bg-slate-100 p-3 dark:bg-slate-800"><div className="text-xs text-slate-500">Shared Quote Contracts</div><div className="mt-1 text-xl font-extrabold">{n(data?.shared_snapshot.quote_contract_count)}</div></div>
      <div className="rounded-lg bg-slate-100 p-3 dark:bg-slate-800"><div className="text-xs text-slate-500">Shared Depth Records</div><div className="mt-1 text-xl font-extrabold">{n(data?.shared_snapshot.depth_record_count)}</div><div className="text-xs text-slate-500">{data?.shared_snapshot.depth_enabled ? (data.shared_snapshot.depth_running ? "Depth running" : "Depth stopped") : "Depth disabled"}</div></div>
    </div>
    <div><h3 className="mb-2 text-sm font-extrabold">Calculation diagnostics</h3><div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{metricKeys.map(key => <div key={key} className="rounded-lg border p-2"><div className="text-xs text-slate-500">{title(key)}</div><div className="font-bold">{n(diagnostics[key] as number)}</div></div>)}</div></div>
    <div><h3 className="mb-2 text-sm font-extrabold">Rejected quote groups</h3><div className="flex flex-wrap gap-2">{Object.entries(rejected).map(([key, count]) => <span key={key} className="rounded-md border px-2 py-1 text-xs">{title(key)}: <b>{n(count)}</b></span>)}</div></div>\n    <div className="overflow-x-auto"><h3 className="mb-2 text-sm font-extrabold">Live coverage by expiry and option side</h3><table className="w-full min-w-[720px] text-left text-xs"><thead><tr className="border-b"><th className="p-2">Underlying</th><th className="p-2">Expiry</th><th className="p-2">Type</th><th className="p-2">Listed / Valid</th><th className="p-2">Candidates</th><th className="p-2">Results</th><th className="p-2">Depth Ready / Incomplete</th></tr></thead><tbody>{(data?.live_coverage ?? []).map((row, i) => <tr key={String(row.underlying)+String(row.expiry)+String(row.option_type)+i} className="border-b"><td className="p-2 font-bold">{String(row.underlying)}</td><td className="p-2">{String(row.expiry)}</td><td className="p-2">{String(row.option_type)}</td><td className="p-2">{n(row.listed_quote_rows)} / {n(row.valid_quote_count)}</td><td className="p-2">{n(row.candidate_structure_count)}</td><td className="p-2">{n(row.result_count)}</td><td className="p-2">{n(row.depth_ready_count)} / {n(row.depth_incomplete_count)}</td></tr>)}{(data?.live_coverage.length ?? 0) === 0 && <tr><td colSpan={7} className="p-3 text-center text-slate-500">No eligible live coverage groups in the shared snapshot.</td></tr>}</tbody></table></div>
    <div className="overflow-x-auto"><h3 className="mb-2 text-sm font-extrabold">Shadow opportunities</h3><table className="w-full min-w-[900px] text-left text-xs"><thead><tr className="border-b"><th className="p-2">Underlying / Expiry</th><th className="p-2">Structure</th><th className="p-2">Option</th><th className="p-2">Strikes</th><th className="p-2">Gross / Unit</th><th className="p-2">Gross / Lot</th><th className="p-2">L1 Lots</th><th className="p-2">Depth</th><th className="p-2">L1 − Depth VWAP</th></tr></thead><tbody>{(data?.results ?? []).slice(0, 100).map((row, i) => { const cmp = row.l1_vs_depth_vwap as Record<string, unknown> | undefined; const depth = row.depth as Record<string, unknown> | undefined; return <tr key={String(row.underlying)+String(row.expiry)+String(row.strategy)+String(row.option_type)+i} className="border-b"><td className="p-2 font-bold">{String(row.underlying)}<div className="font-normal text-slate-500">{String(row.expiry_type)} · {String(row.expiry)}</div></td><td className="p-2">{String(row.strategy)}</td><td className="p-2">{String(row.option_type)}</td><td className="p-2">{Array.isArray(row.strikes) ? row.strikes.join(" / ") : "—"}</td><td className="p-2">{n(row.gross_credit_per_unit)}</td><td className="p-2">{n(row.gross_credit_per_lot)}</td><td className="p-2">{n(row.l1_executable_lots)}</td><td className="p-2">{String(depth?.status ?? "—")}</td><td className="p-2">{n(cmp?.l1_minus_depth_vwap_credit_per_unit)}</td></tr>; })}{!loading && (data?.results.length ?? 0) === 0 && <tr><td colSpan={9} className="p-4 text-center text-slate-500">No eligible shadow structures from the current fresh shared snapshot. This is expected off-market or while quotes/depth are waiting.</td></tr>}</tbody></table></div>
    <div className="text-xs text-slate-500">WebSocket page push updates the panel from the Box runner's existing shared snapshot. REST refresh runs every 15 seconds only while WebSocket is unavailable; manual refresh remains available. No orders or additional broker subscriptions.</div>
  </Card>;
}
