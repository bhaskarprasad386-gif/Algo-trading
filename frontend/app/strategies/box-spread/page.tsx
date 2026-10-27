"use client";

import { StrategyWorkspace } from "@/components/strategy-workspace";
import BoxSpreadEdgeTimingAnalysis from "@/components/BoxSpreadEdgeTimingAnalysis";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, ShieldCheck, Play, Plus } from "lucide-react";
import { Card } from "@/components/ui";
import { appConfig } from "@/lib/config";

type PaperTrade = { trade_id: number; underlying: string; expiry: string; low_strike: number; high_strike: number; direction: string; lots: number; status: string; created_at: string };
type PaperApi = { detail?: string; virtual_balance?: string; data?: PaperTrade[]; trade_id?: number };
const paperEndpoint = appConfig.apiBaseUrl.replace(/\/$/, "") + "/api/v1/scanner/box-spread-paper";
const paperInput = "w-full rounded-lg border theme-border theme-surface px-3 py-2 text-sm theme-text";
const paperButton = "inline-flex items-center gap-2 rounded-lg border theme-border px-3 py-2 text-sm font-medium theme-text disabled:opacity-50";

function BoxSpreadPaperTrading() {
  const [account, setAccount] = useState("default");
  const [balance, setBalance] = useState("—");
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [underlying, setUnderlying] = useState("NIFTY");
  const [expiry, setExpiry] = useState("");
  const [low, setLow] = useState("25000");
  const [high, setHigh] = useState("25100");
  const [direction, setDirection] = useState("LONG");
  const [lots, setLots] = useState("1");
  const [tradeId, setTradeId] = useState<number | null>(null);
  const [prices, setPrices] = useState({ LOW_CE: "100", HIGH_CE: "50", HIGH_PE: "55", LOW_PE: "105" });
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const call = useCallback(async (path: string, init?: RequestInit): Promise<PaperApi> => {
    const response = await fetch(paperEndpoint + path, { ...init, cache: "no-store", headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
    const data = await response.json().catch(() => ({})) as PaperApi;
    if (!response.ok) throw new Error(data.detail ?? "Paper API request failed");
    return data;
  }, []);
  const refresh = useCallback(async () => {
    try {
      const [a, t] = await Promise.all([call("/account?account_key=" + encodeURIComponent(account)), call("/trades?account_key=" + encodeURIComponent(account))]);
      setBalance(a.virtual_balance ?? "—"); setTrades(t.data ?? []); setMessage("");
    } catch (e) { setMessage(e instanceof Error ? e.message : "Paper API unavailable"); }
  }, [account, call]);
  useEffect(() => { void refresh(); }, [refresh]);
  async function create() {
    setBusy(true);
    try {
      const r = await call("/trades", { method: "POST", body: JSON.stringify({
        underlying, instrument_class: ["SENSEX","BANKEX","NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"].includes(underlying) ? "INDEX" : "STOCK",
        expiry, low_strike: Number(low), high_strike: Number(high), direction, lots: Number(lots),
        idempotency_key: "ui-" + crypto.randomUUID(), account_key: account,
      }) });
      setTradeId(r.trade_id ?? null); setMessage("Paper trade created; exact contracts resolved. No broker order was sent."); await refresh();
    } catch (e) { setMessage(e instanceof Error ? e.message : "Unable to create paper trade"); }
    finally { setBusy(false); }
  }
  async function simulate() {
    if (tradeId === null) return;
    setBusy(true);
    try {
      await call("/trades/" + tradeId + "/simulate-entry", { method: "POST", body: JSON.stringify({ reference_prices: Object.fromEntries(Object.entries(prices).map(([k,v]) => [k, Number(v)])), account_key: account }) });
      setMessage("Four-leg paper execution recorded; no broker order was sent."); await refresh();
    } catch (e) { setMessage(e instanceof Error ? e.message : "Paper execution failed"); }
    finally { setBusy(false); }
  }
  const indices = ["NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX","BANKEX"];
  const stocks = ["RELIANCE","HDFCBANK","ICICIBANK","SBIN","INFY","TCS","AXISBANK","LT","BHARTIARTL","KOTAKBANK","TMPV","BAJFINANCE","M&M","HINDALCO","TATASTEEL"];
  return <Card className="p-4 sm:p-5">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2 text-sm font-semibold theme-text"><ShieldCheck size={17}/>Box Spread Paper Trading</div><p className="mt-1 text-xs theme-muted">Dedicated virtual ledger · broker orders disabled · exact subscribed contracts only.</p></div><button className={paperButton} onClick={() => void refresh()} disabled={busy}><RefreshCw size={14}/>Refresh</button></div>
    <div className="mt-4 grid gap-3 sm:grid-cols-3"><div className="rounded-xl border theme-border p-3"><p className="text-xs theme-muted">Virtual balance (₹)</p><p className="mt-1 text-lg font-semibold theme-text">{Number(balance).toLocaleString("en-IN",{maximumFractionDigits:2})}</p></div><div className="rounded-xl border theme-border p-3"><p className="text-xs theme-muted">Paper trades</p><p className="mt-1 text-lg font-semibold theme-text">{trades.length}</p></div><div className="rounded-xl border theme-border p-3"><p className="text-xs theme-muted">Paper execution</p><p className="mt-1 text-lg font-semibold theme-text">200 ms · 0.05% slippage</p></div></div>
    <h3 className="mb-3 mt-5 text-sm font-semibold theme-text">Create paper trade</h3>
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <label className="text-xs theme-muted">Underlying<select className={paperInput} value={underlying} onChange={e => setUnderlying(e.target.value)}><optgroup label="Indices">{indices.map(x=><option key={x}>{x}</option>)}</optgroup><optgroup label="Top 15 F&O stocks">{stocks.map(x=><option key={x}>{x}</option>)}</optgroup></select></label>
      <label className="text-xs theme-muted">Expiry<input className={paperInput} placeholder="29OCT2026" value={expiry} onChange={e=>setExpiry(e.target.value.toUpperCase())}/></label>
      <label className="text-xs theme-muted">Low strike<input className={paperInput} type="number" min="0.01" value={low} onChange={e=>setLow(e.target.value)}/></label>
      <label className="text-xs theme-muted">High strike<input className={paperInput} type="number" min="0.01" value={high} onChange={e=>setHigh(e.target.value)}/></label>
      <label className="text-xs theme-muted">Direction<select className={paperInput} value={direction} onChange={e=>setDirection(e.target.value)}><option>LONG</option><option>SHORT</option></select></label>
      <label className="text-xs theme-muted">Lots<input className={paperInput} type="number" min="1" max="100" value={lots} onChange={e=>setLots(e.target.value)}/></label>
      <label className="text-xs theme-muted">Account key<input className={paperInput} value={account} onChange={e=>setAccount(e.target.value)}/></label>
      <div className="flex items-end"><button className={paperButton+" w-full justify-center"} disabled={busy||!expiry||Number(low)>=Number(high)||!account.trim()} onClick={()=>void create()}><Plus size={14}/>Create paper trade</button></div>
    </div>
    {tradeId !== null && <div className="mt-5 rounded-xl border theme-border p-4"><h3 className="text-sm font-semibold theme-text">Simulate four-leg entry · trade #{tradeId}</h3><p className="mt-1 text-xs theme-muted">Enter reference prices; the paper engine applies adverse slippage and ledger charges.</p><div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">{(Object.keys(prices) as Array<keyof typeof prices>).map(role=><label key={role} className="text-xs theme-muted">{role.replace("_"," ")} price<input className={paperInput} type="number" min="0.01" step="0.01" value={prices[role]} onChange={e=>setPrices(p=>({...p,[role]:e.target.value}))}/></label>)}</div><button className={paperButton+" mt-3"} disabled={busy} onClick={()=>void simulate()}><Play size={14}/>Simulate paper fills</button></div>}
    {message && <p role="status" className="mt-4 rounded-lg border theme-border p-3 text-sm theme-text">{message}</p>}
    <div className="mt-5 overflow-x-auto"><h3 className="mb-2 text-sm font-semibold theme-text">Recent paper trades</h3><table className="w-full min-w-[700px] text-left text-xs"><thead className="theme-muted"><tr>{["ID","Underlying","Expiry","Box strikes","Direction","Lots","Status","Created"].map(x=><th key={x} className="px-2 py-2 font-medium">{x}</th>)}</tr></thead><tbody>{trades.map(t=><tr key={t.trade_id} className="border-t theme-border"><td className="px-2 py-2 theme-text">{t.trade_id}</td><td className="px-2 py-2 theme-text">{t.underlying}</td><td className="px-2 py-2 theme-text">{t.expiry}</td><td className="px-2 py-2 theme-text">{t.low_strike} / {t.high_strike}</td><td className="px-2 py-2 theme-text">{t.direction}</td><td className="px-2 py-2 theme-text">{t.lots}</td><td className="px-2 py-2 theme-text">{t.status}</td><td className="px-2 py-2 theme-muted">{new Date(t.created_at).toLocaleString("en-IN")}</td></tr>)}</tbody></table>{!trades.length&&<p className="py-3 text-xs theme-muted">No paper trades recorded for this account yet.</p>}</div>
  </Card>;
}


export default function BoxSpreadWorkspacePage() {
  return (
    <div className="space-y-4">
      <BoxSpreadEdgeTimingAnalysis />
      <BoxSpreadPaperTrading />
      <StrategyWorkspace slug="box-spread" />
    </div>
  );
}
