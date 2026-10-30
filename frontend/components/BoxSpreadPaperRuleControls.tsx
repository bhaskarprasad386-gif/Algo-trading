"use client";
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";

type Config = {
  entry: { symbols: string[]; direction: "ALL"|"LONG"|"SHORT"; instrument_class: "ALL"|"INDEX"|"STOCK"; expiry_type: "ALL"|"WEEKLY"|"MONTHLY"|"UNKNOWN"; metric: "executable_edge"|"edge_per_lot"|"gross_pnl"; operator: ">="|">"|"<="|"<"|"=="; threshold: number; range_enabled?: boolean; range_min?: number; range_max?: number; lots: number; max_open_positions: number };
  exit: { metric: "executable_edge"|"edge_per_lot"|"gross_pnl"|"position_net_pnl"; operator: "<="|"<"|">="|">"|"=="; threshold: number; hold_to_expiry: boolean; order_type: "LIMIT"|"MARKET" };
  monitor: { interval_seconds: number };
};
type Rule = { rule_id: number; name: string; enabled: boolean; config: Config };
type Evaluation = { evaluation_id: number; event: string; outcome: string; reason: string; evaluated_at: string; metric: string|null; observed_value: string|null; threshold: string|null; trade_id: number|null };
type EntryAudit = { candidate?: { symbol?: string; expiry?: string; low_strike?: number|string; high_strike?: number|string; direction?: string; lots?: number; metric?: string; observed?: number|null; range_min?: number; range_max?: number }; order_type?: string; elapsed_s?: number; engine_latency_s?: number; legs?: Array<{role?:string;action?:string;quote_side?:string;qty?:number;reference?:string;limit?:string;fill?:string}> };
function parseEntryAudit(reason: string): EntryAudit | null {
  try {
    const value = JSON.parse(reason) as EntryAudit;
    return value && typeof value === "object" && value.candidate && Array.isArray(value.legs) ? value : null;
  } catch { return null; }
}
const defaults: Config = {
  entry: { symbols: ["NIFTY","SENSEX"], direction: "LONG", instrument_class: "INDEX", expiry_type: "WEEKLY", metric: "executable_edge", operator: ">=", threshold: 20, range_enabled: true, range_min: 20, range_max: 100000, lots: 5, max_open_positions: 1 },
  exit: { metric: "executable_edge", operator: "<=", threshold: 1, hold_to_expiry: false, order_type: "LIMIT" },
  monitor: { interval_seconds: 20 },
};

export default function BoxSpreadPaperRuleControls({ api, token, account, selection }: { api: string; token: string | null; account: string; selection: { expiryType: "WEEKLY"|"MONTHLY"; symbols: string[]; lots: number } }) {
  const [rules,setRules]=useState<Rule[]>([]);
  const [config,setConfig]=useState<Config>(defaults);
  const [name,setName]=useState("NIFTY/SENSEX weekly edge rule");
  const [enabled,setEnabled]=useState(false);
  const [editingId,setEditingId]=useState<number|null>(null);
  const [evaluations,setEvaluations]=useState<Evaluation[]>([]);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<string|null>(null);
  const [notice,setNotice]=useState<string|null>(null);

  const request=useCallback(async(path:string,init?:RequestInit)=>{
    const response=await fetch(api+path,{...init,cache:"no-store",headers:{"Content-Type":"application/json",...(token ? { Authorization:"Bearer "+token } : {}),...(init?.headers??{})},signal:AbortSignal.timeout(12000)});
    const body=await response.json().catch(()=>({}));
    if(!response.ok) throw new Error(String(body?.detail??"Rule API request failed"));
    return body;
  },[api,token]);

  const load=useCallback(async()=>{
    if(!token)return;
    try{const body=await request("/rules?account_key="+encodeURIComponent(account));setRules(Array.isArray(body.data)?body.data:[]);setError(null);}
    catch(e){setError(e instanceof Error?e.message:"Unable to load rules");}
  },[account,request,token]);
  useEffect(()=>{void load();},[load]);
  useEffect(()=>{setConfig(current=>({...current,entry:{...current.entry,expiry_type:selection.expiryType,symbols:selection.symbols,lots:selection.lots}}));},[selection.expiryType,selection.symbols.join(","),selection.lots]);

  async function save(event:FormEvent){
    event.preventDefault();setBusy(true);setError(null);setNotice(null);
    try{
      const payload={account_key:account,name,enabled,config:{...config,entry:{...config.entry,symbols:config.entry.symbols.map(x=>x.trim().toUpperCase()).filter(Boolean)}}};
      const body=await request(editingId===null?"/rules":"/rules/"+editingId,{method:editingId===null?"POST":"PUT",body:JSON.stringify(payload)});
      setEditingId(body.rule_id);setNotice((body.enabled?"Rule enabled. ":"Rule saved disabled. ")+"New entries also require the main Paper Execution switch ON.");await load();
    }catch(e){setError(e instanceof Error?e.message:"Unable to save rule");}
    finally{setBusy(false);}
  }
  function edit(rule:Rule){setEditingId(rule.rule_id);setName(rule.name);setEnabled(rule.enabled);setConfig(rule.config);setNotice("Editing rule #"+rule.rule_id);}
  function newRule(){setEditingId(null);setName("Custom Box Spread rule");setEnabled(false);setConfig(defaults);setEvaluations([]);setNotice("New disabled rule draft.");}
  async function history(id:number){
    try{const body=await request("/rules/"+id+"/evaluations?account_key="+encodeURIComponent(account)+"&limit=30");setEvaluations(Array.isArray(body.data)?body.data:[]);setNotice("Recent evaluations for rule #"+id);}
    catch(e){setError(e instanceof Error?e.message:"Unable to load evaluation history");}
  }
  const entry=(key:keyof Config["entry"],value:unknown)=>setConfig(c=>({...c,entry:{...c.entry,[key]:value} as Config["entry"]}));
  const exit=(key:keyof Config["exit"],value:unknown)=>setConfig(c=>({...c,exit:{...c.exit,[key]:value} as Config["exit"]}));

  return <section className="space-y-4 rounded-xl border theme-border p-3">
    <div><p className="text-xs font-semibold uppercase tracking-wider theme-accent">Custom automation</p><h3 className="text-lg font-semibold theme-text">Entry & Exit Rule Builder</h3><p className="text-xs theme-muted">Rules evaluate fresh backend four-leg quotes. Paper-only; no broker orders.</p></div>
    <div className="space-y-2">
      <div className="flex items-center justify-between"><h4 className="font-semibold theme-text">Saved rules</h4><button type="button" onClick={newRule} className="rounded-lg border theme-border px-3 py-2 text-xs">New rule</button></div>
      {rules.length?rules.map(rule=><div key={rule.rule_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border theme-border theme-surface-2 p-3">
        <div><div className="text-sm font-semibold theme-text"># {rule.rule_id} · {rule.name}</div><div className="text-xs theme-muted">Entry {rule.config.entry.metric} {rule.config.entry.operator} {rule.config.entry.threshold} · Exit {rule.config.exit.hold_to_expiry ? "Hold to expiry" : rule.config.exit.metric + " " + rule.config.exit.operator + " " + rule.config.exit.threshold} · Exit order: {rule.config.exit.order_type} · {rule.config.monitor.interval_seconds}s monitor</div><div className={"mt-1 text-[11px] font-bold "+(rule.enabled?"text-emerald-600":"text-amber-600")}>{rule.enabled?"RULE ENABLED":"RULE DISABLED"}</div></div>
        <div className="flex gap-2"><button type="button" onClick={()=>edit(rule)} className="rounded-md border theme-border px-3 py-2 text-xs">Edit</button><button type="button" onClick={()=>void history(rule.rule_id)} className="rounded-md border theme-border px-3 py-2 text-xs">History</button></div>
      </div>):<p className="rounded-lg border theme-border p-3 text-xs theme-muted">No custom rules saved.</p>}
    </div>
    <form onSubmit={save} className="space-y-3 rounded-lg border theme-border p-3">
      <div className="flex flex-wrap items-center justify-between"><h4 className="font-semibold theme-text">{editingId===null?"Create rule":"Edit rule #"+editingId}</h4><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>Enable rule</label></div>
      <label className="block text-xs theme-muted">Rule name<input required maxLength={128} value={name} onChange={e=>setName(e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text"/></label>
      <div className="rounded-lg theme-surface-2 p-3"><h5 className="text-sm font-semibold theme-text">Entry conditions</h5><div className="mt-2 grid gap-2 sm:grid-cols-2">
        <div className="rounded-lg border theme-border p-2 text-xs theme-muted sm:col-span-2">Shared selection: <strong className="theme-text">{config.entry.expiry_type} · {config.entry.symbols.length ? config.entry.symbols.join(", ") : "No indices selected"} · {config.entry.lots} lots per leg</strong>. Change these in the Entry configuration above; Manual and Algo use the same selection.</div>
        <label className="text-xs theme-muted">Direction<select value={config.entry.direction} onChange={e=>entry("direction",e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface px-2 py-2 text-sm theme-text"><option>ALL</option><option>LONG</option><option>SHORT</option></select></label>
        <label className="text-xs theme-muted">Instrument<select value={config.entry.instrument_class} onChange={e=>entry("instrument_class",e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface px-2 py-2 text-sm theme-text"><option>ALL</option><option>INDEX</option><option>STOCK</option></select></label>
        <div className="text-xs theme-muted">Expiry type: <strong className="theme-text">{config.entry.expiry_type}</strong></div>
        <label className="text-xs theme-muted">Entry metric<select value={config.entry.metric} onChange={e=>entry("metric",e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface px-2 py-2 text-sm theme-text"><option value="executable_edge">Executable edge (points)</option><option value="edge_per_lot">Edge per lot (₹)</option><option value="gross_pnl">Scanner gross profit (₹)</option></select></label>
        <label className="flex items-center gap-2 text-xs theme-muted sm:col-span-2"><input type="checkbox" checked={config.entry.range_enabled===true} onChange={e=>entry("range_enabled",e.target.checked)}/>Use a minimum-to-maximum range</label>
        {config.entry.range_enabled ? <><label className="text-xs theme-muted">Minimum {config.entry.metric==="executable_edge"?"edge":config.entry.metric==="gross_pnl"?"gross profit":"edge per lot"}<input type="number" step="any" value={config.entry.range_min ?? 0} onChange={e=>entry("range_min",Number(e.target.value))} className="mt-1 w-full rounded-lg border theme-border theme-surface px-2 py-2 text-sm theme-text"/></label><label className="text-xs theme-muted">Maximum {config.entry.metric==="executable_edge"?"edge":config.entry.metric==="gross_pnl"?"gross profit":"edge per lot"}<input type="number" step="any" value={config.entry.range_max ?? 100000} onChange={e=>entry("range_max",Number(e.target.value))} className="mt-1 w-full rounded-lg border theme-border theme-surface px-2 py-2 text-sm theme-text"/></label></> : <><label className="text-xs theme-muted">Entry comparison<select value={config.entry.operator} onChange={e=>entry("operator",e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface px-2 py-2 text-sm theme-text"><option value=">=">≥</option><option value=">">&gt;</option><option value="<=">≤</option><option value="<">&lt;</option><option value="==">=</option></select></label><label className="text-xs theme-muted">Entry threshold<input type="number" step="any" value={config.entry.threshold} onChange={e=>entry("threshold",Number(e.target.value))} className="mt-1 w-full rounded-lg border theme-border theme-surface px-2 py-2 text-sm theme-text"/></label></>}
        <div className="text-xs theme-muted">Quantity: <strong className="theme-text">{config.entry.lots} lots × all four legs</strong></div>
        <label className="text-xs theme-muted">Maximum open positions per rule (1–20)<input type="number" min={1} max={20} value={config.entry.max_open_positions} onChange={e=>entry("max_open_positions",Number(e.target.value))} className="mt-1 w-full rounded-lg border theme-border theme-surface px-2 py-2 text-sm theme-text"/></label>
      </div></div>
      <div className="rounded-lg theme-surface-2 p-3"><h5 className="text-sm font-semibold theme-text">Exit conditions</h5><label className="mt-2 flex items-center gap-2 text-xs"><input type="checkbox" checked={config.exit.hold_to_expiry} onChange={e=>exit("hold_to_expiry",e.target.checked)}/>Hold until expiry (no early exit)</label>
        {!config.exit.hold_to_expiry&&<div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-xs theme-muted">Exit metric<select value={config.exit.metric} onChange={e=>exit("metric",e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface px-2 py-2 text-sm theme-text"><option value="executable_edge">Executable edge (points)</option><option value="edge_per_lot">Edge per lot (₹)</option><option value="gross_pnl">Gross P&amp;L (₹)</option></select></label>
          <label className="text-xs theme-muted">Exit comparison<select value={config.exit.operator} onChange={e=>exit("operator",e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface px-2 py-2 text-sm theme-text"><option value="<=">≤</option><option value="<">&lt;</option><option value=">=">≥</option><option value=">">&gt;</option><option value="==">=</option></select></label>
          <label className="text-xs theme-muted">Exit threshold<input type="number" step="any" value={config.exit.threshold} onChange={e=>exit("threshold",Number(e.target.value))} className="mt-1 w-full rounded-lg border theme-border theme-surface px-2 py-2 text-sm theme-text"/></label>
          <label className="text-xs theme-muted">Exit order type<select value={config.exit.order_type} onChange={e=>exit("order_type",e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface px-2 py-2 text-sm theme-text"><option value="LIMIT">Limit (default)</option><option value="MARKET">Market</option></select></label>
        </div>}
      </div>
      <label className="block text-xs theme-muted">Position monitor interval (seconds; 5–3600)<input type="number" min={5} max={3600} value={config.monitor.interval_seconds} onChange={e=>setConfig(c=>({...c,monitor:{interval_seconds:Number(e.target.value)}}))} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text"/></label>
      <p className="text-[11px] theme-muted">Algo entry scans only the selected expiry and indices. Use an inclusive minimum-to-maximum edge or gross-profit range; the backend evaluates live scanner rows and validates all four executable quotes before paper entry. Exit can use edge, gross P&amp;L, net position P&amp;L, or hold-to-expiry. Expiry-day settlement remains manual until official server-verified settlement data is available.</p>
      <div className="flex flex-wrap gap-2"><button type="submit" disabled={busy || config.entry.symbols.length===0 || (config.entry.range_enabled===true && Number(config.entry.range_min)>Number(config.entry.range_max))} className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-bold text-slate-950 disabled:opacity-50">{busy?"Saving…":"Save rule"}</button>{editingId!==null&&<button type="button" onClick={newRule} className="rounded-lg border theme-border px-4 py-2 text-sm">Cancel edit</button>}</div>
    </form>
    {notice&&<p className="text-xs text-emerald-600">{notice}</p>}{error&&<p role="alert" className="text-xs text-red-500">{error}</p>}
    {!!evaluations.length&&<div className="space-y-2"><h4 className="font-semibold theme-text">Recent evaluation history</h4>{evaluations.map(item=>{const audit=parseEntryAudit(item.reason);return <div key={item.evaluation_id} className="rounded-lg border theme-border p-2 text-xs"><div className="flex flex-wrap justify-between gap-2"><strong className="theme-text">{item.event} · {item.outcome}</strong><span className="theme-muted">{new Date(item.evaluated_at).toLocaleString("en-IN",{timeZone:"Asia/Kolkata"})} IST</span></div>{audit?.candidate?<><p className="mt-1 theme-muted">{audit.candidate.symbol} · {audit.candidate.expiry} · {audit.candidate.direction} · {audit.candidate.low_strike}/{audit.candidate.high_strike} · {audit.candidate.lots} lots · {audit.candidate.metric}: {audit.candidate.observed??"—"} · {audit.order_type} · {audit.elapsed_s??"—"}s</p><div className="mt-2 grid gap-1 sm:grid-cols-2">{audit.legs?.map(leg=><div key={leg.role} className="rounded border theme-border px-2 py-1 theme-muted">{leg.role} · {leg.action} @ {leg.quote_side??(leg.action==="BUY"?"ASK":"BID")} · Qty {leg.qty} · Quote ₹{leg.reference} · Limit ₹{leg.limit} · Fill ₹{leg.fill}</div>)}</div><p className="mt-1 theme-muted">Engine latency: {audit.engine_latency_s??"—"}s</p></>:<p className="mt-1 theme-muted break-words">{item.reason}</p>}{item.metric?<p className="mt-1 theme-muted">{item.metric}: {item.observed_value??"—"} / {item.threshold??"—"}</p>:null}{item.trade_id?<p className="mt-1 theme-muted">Trade #{item.trade_id}</p>:null}</div>})}</div>}
  </section>;
}
