"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, ArrowDown, ArrowUp, RefreshCw, ShieldAlert, ShieldCheck, Target, Waves } from "lucide-react";
import { appConfig } from "@/lib/config";

type Engine = { id: string; name: string; weight: number; status: string };
type Status = {
  strategy?: string;
  status?: string;
  feed_connected?: boolean;
  scanner_active?: boolean;
  signal_count?: number;
  latest_signal?: unknown;
  market_regime?: string;
  order_routing_enabled?: boolean;
  iv_scanner_dependency?: boolean;
  engines?: Engine[];
  notes?: string[];
};

const engineDetails: Engine[] = [
  { id: "absorption", name: "Absorption Detection", weight: 30, status: "WAITING", role: "Entry-location engine", detail: "Executed pressure versus price progress, liquidity replenishment and rejection at a level." },
  { id: "synthetic_spot", name: "Synthetic Future vs Spot", weight: 20, status: "WAITING", role: "Early pressure", detail: "Synchronized same-strike CE/PE synthetic value compared with spot; spread and quote age gates." },
  { id: "failed_auction", name: "Failed Auction / Rejection", weight: 15, status: "WAITING", role: "Trap filter", detail: "Sweep, failed breakout, reclaim or rejection. Avoids chasing a move after price has extended." },
  { id: "market_impact", name: "Market Impact Efficiency", weight: 20, status: "WAITING", role: "Pressure quality", detail: "How much price progress is produced by executed flow; flags effort without progress and weak follow-through." },
  { id: "cross_instrument", name: "Cross-Instrument Confirmation", weight: 15, status: "WAITING", role: "Validation", detail: "Spot and futures context. Confirmation is a quality filter, not a late mandatory trigger." },
];

const fmtTime = (value?: string | null) => value ? new Date(value).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" }) : "—";

function Metric({ title, value, subtitle }: { title: string; value: string; subtitle?: string }) {
  return <div className="min-w-0 rounded-xl border theme-border theme-surface p-3 sm:p-4">
    <div className="text-[10px] font-semibold uppercase tracking-wider theme-muted">{title}</div>
    <div className="mt-1 truncate text-lg font-bold theme-text">{value}</div>
    {subtitle && <div className="mt-1 text-[11px] theme-muted">{subtitle}</div>}
  </div>;
}

export default function EarlyImpulsePage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const requestLock = useRef(false);

  const refresh = useCallback(async () => {
    if (requestLock.current) return;
    requestLock.current = true;
    setLoading(true);
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(base + "/api/v1/early-impulse/status", {
        cache: "no-store",
        signal: AbortSignal.timeout(8000),
      });
      if (!response.ok) throw new Error("API returned HTTP " + response.status);
      const body = await response.json() as Status;
      setStatus(body);
      setError(null);
      setUpdatedAt(new Date().toISOString());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Status request failed");
    } finally {
      requestLock.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: number | undefined;
    const poll = async () => {
      if (disposed) return;
      await refresh();
      if (!disposed) timer = window.setTimeout(poll, 20000);
    };
    void poll();
    return () => { disposed = true; if (timer !== undefined) window.clearTimeout(timer); };
  }, [refresh]);

  const engines = status?.engines ?? engineDetails;
  const feedReady = status?.feed_connected === true;
  const liveSignals = status?.signal_count ?? 0;
  const backendUnavailable = !!error;

  return <main className="space-y-4">
    <section className="overflow-hidden rounded-2xl border theme-border theme-surface">
      <div className="border-b theme-border p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.12em] theme-accent"><Activity size={15}/> Independent Strategy</div>
            <h1 className="mt-1 text-2xl font-extrabold tracking-tight sm:text-3xl">EARLY IMPULSE PRO</h1>
            <p className="mt-1 max-w-3xl text-sm theme-muted">Pre-move pressure, location-aware entry and liquidity-trap protection. Existing IV Scanner is not used.</p>
          </div>
          <button onClick={() => void refresh()} disabled={loading} className="inline-flex min-h-10 items-center gap-2 rounded-lg border theme-border theme-surface-2 px-3 text-xs font-semibold theme-text disabled:opacity-60"><RefreshCw size={15} className={loading ? "animate-spin" : ""}/> Refresh</button>
        </div>
        <div className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-4 sm:p-4">
          <Metric title="Feed" value={feedReady ? "CONNECTED" : "WAITING"} subtitle={feedReady ? "Market stream available" : "Feed runner not wired yet"}/>
          <Metric title="Scanner" value={status?.scanner_active ? "ACTIVE" : "INACTIVE"} subtitle="No signals fabricated"/>
          <Metric title="Signals" value={String(liveSignals)} subtitle="Live confirmed signals"/>
          <Metric title="Market Regime" value={status?.market_regime ?? "UNKNOWN"} subtitle="Trend / range detection"/>
        </div>
      </div>
      {backendUnavailable && <div className="m-3 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300"><strong>Status unavailable:</strong> {error}. The page will not display simulated live signals.</div>}
      {!backendUnavailable && !feedReady && <div className="m-3 flex items-start gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300"><ShieldAlert size={16} className="mt-0.5 shrink-0"/><div><strong>Feed integration pending.</strong> Strategy scoring and API contracts are scaffolded, but live shared-feed subscription and scanner runner have not been enabled. No paper trades or broker orders are sent.</div></div>}
      <div className="px-3 pb-3 text-[10px] theme-muted sm:px-4">Last status refresh: {fmtTime(updatedAt)} IST • Auto refresh: 20s (no overlapping requests)</div>
    </section>

    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
      <div className="rounded-xl border theme-border theme-surface p-3 sm:p-4 xl:col-span-2">
        <div className="flex items-center gap-2 text-sm font-bold"><Target size={17}/> Strategy logic</div>
        <div className="mt-3 space-y-2 text-xs theme-muted">
          <div className="flex items-start gap-2"><ArrowUp className="mt-0.5 text-emerald-600" size={15}/><span><strong className="theme-text">BUY:</strong> early bullish pressure, selling absorbed, reclaim/acceptance, positive price impact and no hard opposite conflict.</span></div>
          <div className="flex items-start gap-2"><ArrowDown className="mt-0.5 text-rose-600" size={15}/><span><strong className="theme-text">SELL:</strong> early bearish pressure, buying absorbed, rejection/acceptance lower, negative price impact and no hard opposite conflict.</span></div>
          <div className="flex items-start gap-2"><Waves size={15} className="mt-0.5"/><span><strong className="theme-text">Range mode:</strong> seek setups at range edges; suppress mid-range entries. Switch to breakout mode only after price acceptance.</span></div>
        </div>
      </div>
      <div className="rounded-xl border theme-border theme-surface p-3 sm:p-4">
        <div className="flex items-center gap-2 text-sm font-bold"><ShieldCheck size={17}/> Safety gates</div>
        <ul className="mt-3 space-y-2 text-xs theme-muted">
          <li>• Fresh, synchronized data</li><li>• Valid spread / executable quotes</li><li>• Entry not extended</li><li>• Structure-based invalidation defined</li><li>• No strong opposite absorption</li><li>• Scanner-only, order routing OFF</li>
        </ul>
      </div>
      <div className="rounded-xl border theme-border theme-surface p-3 sm:p-4">
        <div className="text-sm font-bold">Signal score</div>
        <div className="mt-3 text-3xl font-extrabold">— / 100</div>
        <div className="mt-1 text-xs theme-muted">Waiting for live engine data</div>
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-300/50 dark:bg-slate-700"><div className="h-full w-0 rounded-full bg-emerald-500"/></div>
      </div>
    </section>

    <section className="rounded-2xl border theme-border theme-surface">
      <div className="border-b theme-border p-3 sm:p-4">
        <h2 className="text-base font-bold">5 Signal Engines</h2>
        <p className="mt-1 text-xs theme-muted">Separate modules, weighted score contract, no IV Scanner dependency.</p>
      </div>
      <div className="divide-y theme-border">
        {engineDetails.map((engine) => {
          const current = engines.find((item) => item.id === engine.id);
          return <div key={engine.id} className="grid gap-2 p-3 sm:grid-cols-[minmax(0,1fr)_100px] sm:items-center sm:p-4">
            <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="text-sm font-bold theme-text">{engine.name}</span><span className="rounded-md theme-surface-2 px-2 py-0.5 text-[10px] theme-muted">{engine.role}</span></div><p className="mt-1 text-xs theme-muted">{engine.detail}</p></div>
            <div className="flex items-center justify-between gap-2 sm:justify-end"><span className="text-xs font-semibold theme-muted">{engine.weight}% weight</span><span className="rounded-md border theme-border px-2 py-1 text-[10px] font-semibold theme-muted">{current?.status ?? "WAITING"}</span></div>
          </div>;
        })}
      </div>
    </section>

    <section className="rounded-2xl border theme-border theme-surface">
      <div className="border-b theme-border p-3 sm:p-4"><h2 className="text-base font-bold">Live Signals</h2><p className="mt-1 text-xs theme-muted">Only backend-generated, validated signals will appear here.</p></div>
      <div className="p-8 text-center">
        <Activity size={28} className="mx-auto theme-muted"/>
        <div className="mt-2 text-sm font-semibold theme-text">{feedReady && liveSignals > 0 ? "Signals ready" : "No live signals"}</div>
        <p className="mt-1 text-xs theme-muted">{feedReady ? "No validated opportunity at this moment." : "Live shared-feed integration is pending. This empty state is intentional—not demo data."}</p>
      </div>
    </section>

    <section className="rounded-xl border theme-border theme-surface p-3 text-xs theme-muted">
      <strong className="theme-text">Isolation:</strong> Existing IV Scanner, IV runner, settings and subscriptions remain untouched. Early Impulse Pro has no broker order routing and no paper execution enabled in this first integration.
    </section>
  </main>;
}
