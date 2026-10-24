"use client";

import { useState } from "react";
import { appConfig } from "@/lib/config";
import { Activity, AlertCircle, CheckCircle2, LoaderCircle, Search } from "lucide-react";

type Match = {
  symbol: string;
  strength: string;
  indicators_confirmed: string[];
  lows: { week: string; low: number; close: number }[];
  intervening_highs: { week: string; high: number }[];
  third_low_lower_band: number;
  indicator_values_at_third_low: Record<string, number | null>;
  checked_at_week: string;
};
type Result = { status: string; symbol: string; weekly_bars?: number; count?: number; matches?: Match[]; message?: string; data_source?: string; orders_placed?: boolean };

const money = (v: number) => v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
export default function WeeklySwingScannerPage() {
  const [symbol, setSymbol] = useState("WIPRO");
  const [exchange, setExchange] = useState("NSE");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Result | null>(null);

  async function scan() {
    setLoading(true); setError(""); setResult(null);
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(`${base}/api/v1/scanner/weekly-swing/scan?symbol=${encodeURIComponent(symbol.trim().toUpperCase())}&exchange=${exchange}`, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || `Scanner request failed (${response.status})`);
      setResult(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Scanner data unavailable");
    } finally { setLoading(false); }
  }

  return <div className="space-y-5">
    <header className="space-y-2">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] theme-accent"><Activity size={15}/> Weekly Pattern Scanner</div>
      <h1 className="text-2xl font-bold sm:text-3xl">Three-Low Swing + Indicator Breakout</h1>
      <p className="max-w-3xl text-sm theme-muted">Weekly candles only. Finds three progressively lower swing lows with shallow breakdown/recovery, Bollinger Band context, and CMF / OBV / RSI confirmation. No orders are placed.</p>
    </header>
    <section className="rounded-2xl border theme-border theme-surface p-4 sm:p-5">
      <div className="grid gap-3 sm:grid-cols-[1fr_140px_auto] sm:items-end">
        <label className="space-y-1 text-xs font-semibold theme-muted">Stock symbol
          <input value={symbol} onChange={e=>setSymbol(e.target.value.toUpperCase())} placeholder="e.g. WIPRO" className="w-full rounded-xl border theme-border theme-surface-2 px-3 py-3 text-sm theme-text outline-none focus:ring-2 focus:ring-cyan-500"/>
        </label>
        <label className="space-y-1 text-xs font-semibold theme-muted">Exchange
          <select value={exchange} onChange={e=>setExchange(e.target.value)} className="w-full rounded-xl border theme-border theme-surface-2 px-3 py-3 text-sm theme-text"><option value="NSE">NSE</option><option value="BSE">BSE</option></select>
        </label>
        <button onClick={scan} disabled={loading || !symbol.trim()} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-cyan-500 px-5 py-3 text-sm font-bold text-slate-950 disabled:opacity-60">{loading?<LoaderCircle size={16} className="animate-spin"/>:<Search size={16}/>} {loading?"Scanning…":"Scan Weekly"}</button>
      </div>
      <div className="mt-4 grid gap-2 text-xs theme-muted sm:grid-cols-2">
        <p>• 5 years of provider-sourced daily history aggregated into weekly candles.</p>
        <p>• Adaptive swing turns; no fixed gap between the lows.</p>
      </div>
    </section>
    {error && <section role="alert" className="flex gap-3 rounded-xl border border-rose-500/40 bg-rose-500/10 p-4 text-sm"><AlertCircle className="shrink-0 text-rose-400" size={19}/><div><strong>Real data request failed</strong><p className="mt-1 break-words">{error}</p><p className="mt-1 theme-muted">No sample or fabricated opportunities are shown. Check backend availability and the connected historical-data session.</p></div></section>}
    {result && <section className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border theme-border theme-surface p-4">
        <div><div className="text-xs theme-muted">Scan result</div><div className="mt-1 text-xl font-bold">{result.symbol}</div><div className="mt-1 text-xs theme-muted">{result.weekly_bars ?? 0} weekly bars · {result.data_source ?? "Historical source"}</div></div>
        <div className="text-right"><div className="text-2xl font-bold">{result.count ?? 0}</div><div className="text-xs theme-muted">qualifying setups</div></div>
      </div>
      {result.status === "insufficient_data" && <p className="rounded-xl border theme-border p-4 text-sm theme-muted">{result.message || "Insufficient weekly history."}</p>}
      {(!result.matches || result.matches.length===0) && result.status !== "insufficient_data" && <div className="rounded-xl border theme-border p-5 text-sm"><CheckCircle2 size={18} className="mb-2 text-emerald-400"/><strong>No qualifying setup found in returned history.</strong><p className="mt-1 theme-muted">This is a valid scan result, not a generated opportunity.</p></div>}
      {(result.matches || []).map((m,idx)=><article key={m.lows.map(x=>x.week).join("-")} className="space-y-4 rounded-2xl border theme-border theme-surface p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-lg font-bold">{m.symbol} · Three-Low setup</h2><p className="text-xs theme-muted">Checked through week {m.checked_at_week}</p></div><span className="rounded-full border border-emerald-500/40 px-3 py-1 text-xs font-bold text-emerald-400">{m.strength}</span></div>
        <div className="grid gap-3 sm:grid-cols-3">{m.lows.map((low,i)=><div key={low.week} className="rounded-xl border theme-border theme-surface-2 p-3"><div className="text-xs theme-muted">LOW {i+1} · {low.week}</div><div className="mt-1 text-lg font-bold">{money(low.low)}</div><div className="text-xs theme-muted">Close {money(low.close)}</div></div>)}</div>
        <div className="grid gap-3 sm:grid-cols-2"><div className="rounded-xl border theme-border p-3"><div className="text-xs theme-muted">Indicators confirmed at Low 3</div><div className="mt-2 flex flex-wrap gap-2">{m.indicators_confirmed.map(n=><span key={n} className="rounded-lg bg-emerald-500/10 px-2 py-1 text-xs font-semibold text-emerald-400">{n}</span>)}</div></div><div className="rounded-xl border theme-border p-3"><div className="text-xs theme-muted">Lower Bollinger Band at Low 3</div><div className="mt-2 font-semibold">{money(m.third_low_lower_band)}</div><div className="mt-1 text-xs theme-muted">{Object.entries(m.indicator_values_at_third_low).map(([k,v])=>`${k}: ${v==null?"—":Number(v).toFixed(2)}`).join(" · ")}</div></div></div>
        <p className="text-xs theme-muted">Intervening highs: {m.intervening_highs.map(h=>`${h.week} · ${money(h.high)}`).join(" | ")}</p>
      </article>)}
    </section>}
  </div>;
}
