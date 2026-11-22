"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, RefreshCw, ShieldAlert, ArrowUpRight, ArrowDownRight, Clock3 } from "lucide-react";
import { appConfig } from "@/lib/config";

type Signal = { signal_id?: string; underlying?: string; pattern?: string; direction?: string; score?: number; strength?: string; status?: string; trigger_ready?: boolean; invalidation_price?: number; last_price?: number; updated_at?: string; created_at?: string; evidence?: string[]; components?: Record<string, number>; scanner_only?: boolean };
type Status = { scanner?: string; status?: string; feed_connected?: boolean; scanner_active?: boolean; signal_count?: number; feed_integration?: string; order_routing_enabled?: boolean };
const patterns = [["SWING_EXHAUSTION", "Swing exhaustion"], ["COMPRESSION_REVERSAL", "Impulse + compression"], ["V_REVERSAL", "Immediate V reversal"]];
const fmt = (value?: string) => value ? new Date(value).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "—";

export default function VptXPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [symbol, setSymbol] = useState("NIFTY");
  const [snapshot, setSnapshot] = useState("");
  const requestLock = useRef(false);
  const base = appConfig.apiBaseUrl.replace(/\/$/, "");

  const refresh = useCallback(async () => {
    if (requestLock.current) return;
    requestLock.current = true;
    setBusy(true);
    try {
      const [s, h] = await Promise.all([
        fetch(base + "/api/v1/scanner/vpt-x/status", { cache: "no-store" }),
        fetch(base + "/api/v1/scanner/vpt-x/signals?limit=100", { cache: "no-store" }),
      ]);
      if (!s.ok || !h.ok) throw new Error("API HTTP " + (!s.ok ? s.status : h.status));
      setStatus(await s.json());
      setSignals((await h.json()).signals ?? []);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "API request failed");
    } finally {
      setBusy(false);
      requestLock.current = false;
    }
  }, [base]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 15000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const evaluate = async () => {
    if (requestLock.current) return;
    requestLock.current = true;
    setBusy(true);
    try {
      const parsed = JSON.parse(snapshot);
      const res = await fetch(base + "/api/v1/scanner/vpt-x/evaluate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...parsed, underlying: symbol }),
      });
      if (!res.ok) throw new Error("Evaluate HTTP " + res.status);
      const result = await res.json();
      if (result.status === "EVALUATED") setSignals(result.candidates ?? []);
      setErr(result.status === "EVALUATED" ? null : (result.reason ?? result.status));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Invalid snapshot JSON");
    } finally {
      setBusy(false);
      requestLock.current = false;
    }
  };

  const loadSample = () => setSnapshot(JSON.stringify({
    underlying: symbol,
    ticks: Array.from({ length: 30 }, (_, i) => ({
      timestamp_ms: Date.now() - 2900 + i * 100, price: 25000 + i * 0.2,
      buy_volume: 40 + i, sell_volume: 20, bid_qty: 90, ask_qty: 55,
      bid: 24999.95 + i * 0.2, ask: 25000.05 + i * 0.2,
    })),
  }, null, 2));

  return (
    <main className="space-y-4">
      <section className="rounded-2xl border theme-border theme-surface overflow-hidden">
        <div className="p-4 sm:p-5 border-b theme-border">
          <div className="text-xs uppercase font-bold tracking-widest theme-accent flex items-center gap-2"><Activity size={15} /> Isolated Reversal Engine</div>
          <h1 className="text-2xl sm:text-3xl font-black mt-1">VPT-X REVERSAL SCANNER</h1>
          <p className="text-sm theme-muted mt-1">3 pattern families • BUY & SELL • scanner-only</p>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 p-3 sm:p-4">
          <Metric label="Feed" value={status?.feed_connected ? "CONNECTED" : "NOT CONNECTED"} />
          <Metric label="Runner" value={status?.scanner_active ? "ACTIVE" : "OFF"} />
          <Metric label="Signals in memory" value={String(status?.signal_count ?? 0)} />
          <Metric label="Order routing" value="OFF" />
        </div>
      </section>

      {err && <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm"><strong>Notice:</strong> {err}</div>}
      <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-xs flex gap-2">
        <ShieldAlert size={16} className="shrink-0" />
        <div><b>Live feed integration is not enabled.</b> Scanner evaluates timestamped snapshots only. No live subscription, broker execution or paper trading is activated by this page.</div>
      </div>

      <section className="rounded-2xl border theme-border theme-surface p-3 sm:p-4 space-y-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <h2 className="font-bold text-base">Evaluate snapshot</h2>
          <button onClick={() => void refresh()} disabled={busy} className="inline-flex gap-2 items-center border theme-border theme-surface-2 rounded-lg px-3 py-2 text-xs font-semibold"><RefreshCw size={14} className={busy ? "animate-spin" : ""} /> Refresh</button>
        </div>
        <div className="flex gap-2 flex-wrap">
          <select value={symbol} onChange={e => setSymbol(e.target.value)} className="rounded-lg border theme-border theme-surface-2 p-2 text-sm"><option>NIFTY</option><option>BANKNIFTY</option></select>
          <button onClick={loadSample} className="rounded-lg border theme-border theme-surface-2 px-3 py-2 text-xs font-semibold">Load sample data</button>
          <button onClick={() => void evaluate()} disabled={busy || !snapshot.trim()} className="rounded-lg theme-accent-bg theme-accent px-3 py-2 text-xs font-bold disabled:opacity-50">Evaluate snapshot</button>
        </div>
        <textarea value={snapshot} onChange={e => setSnapshot(e.target.value)} placeholder="Paste a snapshot JSON with timestamped ticks (max 500)..." className="w-full min-h-44 rounded-xl border theme-border theme-surface-2 p-3 font-mono text-[11px] theme-text" />
        <p className="text-[11px] theme-muted">Sample is synthetic for API testing only; it is not a market signal or live data.</p>
      </section>

      <section className="rounded-2xl border theme-border theme-surface overflow-hidden">
        <div className="p-3 sm:p-4 border-b theme-border"><h2 className="font-bold">Pattern coverage</h2><p className="text-xs theme-muted mt-1">Each family evaluates BUY and SELL candidates symmetrically.</p></div>
        <div className="grid sm:grid-cols-3 gap-2 p-3">
          {patterns.map(([id, label]) => <div key={id} className="rounded-xl border theme-border theme-surface-2 p-3">
            <div className="text-xs font-bold">{label}</div><div className="mt-1 text-[10px] theme-muted">{id}</div>
            <div className="mt-2 flex gap-1 text-[10px]"><span className="rounded bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 px-2 py-1">BUY</span><span className="rounded bg-rose-500/10 text-rose-700 dark:text-rose-300 px-2 py-1">SELL</span></div>
          </div>)}
        </div>
      </section>

      <section className="rounded-2xl border theme-border theme-surface overflow-hidden">
        <div className="p-3 sm:p-4 border-b theme-border"><h2 className="font-bold">Candidates & signal lifecycle</h2><p className="text-xs theme-muted mt-1">WATCH / TRIGGERED / INVALIDATED. In-memory, bounded history.</p></div>
        {signals.length === 0 ? <div className="p-8 text-center theme-muted text-sm">No evaluated candidates yet. No synthetic alerts are shown.</div> : <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-2 p-3">
          {signals.map((s, i) => <article key={s.signal_id ?? i} className="rounded-xl border theme-border theme-surface-2 p-3">
            <div className="flex items-center justify-between gap-2"><b className="text-sm">{s.underlying} · {s.pattern?.replaceAll("_", " ")}</b><span className={"rounded px-2 py-1 text-[10px] font-bold " + (s.direction === "BUY" ? "text-emerald-700 dark:text-emerald-300" : "text-rose-700 dark:text-rose-300")}>{s.direction === "BUY" ? <ArrowUpRight size={13} className="inline" /> : <ArrowDownRight size={13} className="inline" />} {s.direction}</span></div>
            <div className="mt-3 flex items-end justify-between"><div><div className="text-2xl font-black">{s.score ?? "—"}<span className="text-xs theme-muted">/100</span></div><div className="text-[10px] theme-muted">{s.strength ?? "Score"}</div></div><span className="rounded-md border theme-border px-2 py-1 text-[10px] font-bold">{s.status ?? (s.trigger_ready ? "TRIGGERED" : "WATCH")}</span></div>
            <div className="grid grid-cols-2 gap-2 mt-3 text-xs"><div><div className="theme-muted">Last price</div><b>{s.last_price ?? "—"}</b></div><div><div className="theme-muted">Invalidation</div><b>{s.invalidation_price ?? "—"}</b></div></div>
            <div className="mt-3 flex items-center gap-1 text-[10px] theme-muted"><Clock3 size={12} /> {fmt(s.updated_at)}</div>
            {s.evidence?.length ? <div className="mt-2 text-[10px] theme-muted">{s.evidence.join(" · ")}</div> : null}
          </article>)}
        </div>}
      </section>
      <p className="text-[10px] theme-muted">Scanner-only • No orders • Feed/subscription integration pending approval</p>
    </main>
  );
}
function Metric({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl border theme-border theme-surface-2 p-3"><div className="text-[10px] theme-muted uppercase font-bold">{label}</div><div className="text-sm font-bold mt-1 truncate">{value}</div></div>;
}
