"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Activity, ArrowLeft, RefreshCw, Search, ShieldCheck, Zap } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

type Row = Record<string, unknown>;
type WorkspaceConfig = { slug: string; title: string; description: string; universe: string; columns: string[]; controls: string[]; pairColumns: string[]; historyEndpoint: string; pairEndpoint?: string };

const configs: Record<string, WorkspaceConfig> = {
  "calendar-spread": { slug: "calendar-spread", title: "Calendar Spread", description: "Dedicated near-vs-far expiry spread scanner using the common live market-data feed.", universe: "F&O + INDEX + COMMODITIES", columns: ["Time","Underlying","Exchange","Near","Far","Spread","Gap / Edge","Volume / OI","Signal"], controls: ["All Eligible","INDEX PRIORITY","Near/Far","Edge Threshold"], pairColumns: ["Time","Underlying","Exchange","Near","Far","Spread","Edge","Liquidity","State"], historyEndpoint: "/api/v1/scanner/calendar-spread/history?days=1&limit=100", pairEndpoint: "/api/v1/scanner/calendar-spread/pairs?limit=200" },
  "synthetic-arbitrage": { slug: "synthetic-arbitrage", title: "Synthetic Arbitrage", description: "Executable synthetic future vs future scanner using raw bid/ask prices only — no carry or fees.", universe: "SUPPORTED INDEX + NIFTY TOP 15 LIQUID F&O STOCKS", columns: ["Time","Underlying","Expiry","Strike","Future","Call (CE)","Put (PE)","Synthetic Future","Edge","Signal"], controls: ["ALL","TOP15","NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY","SENSEX","BANKEX"], pairColumns: ["Time","Underlying","Expiry","Strike","Future","Call (CE)","Put (PE)","Synthetic Future","Edge","Direction","State"], historyEndpoint: "/api/v1/scanner/synthetic-cash-carry/pair-history?days=2&limit=5000", pairEndpoint: "/api/v1/scanner/synthetic-cash-carry/pairs?limit=200" },
  "box-spread": { slug: "box-spread", title: "Box Spread", description: "Dedicated four-leg box scanner with executable edge and liquidity ranking.", universe: "INDEX + ELIGIBLE OPTIONS", columns: ["Underlying","Expiry","Low Strike","High Strike","Box Edge","Liquidity","Direction","Signal"], controls: ["All Eligible","Expiry","Strike Range","Edge Threshold"], pairColumns: ["Time","Underlying","Expiry","Low Strike","High Strike","Box Edge","Lot Size","Direction","State"], historyEndpoint: "/api/v1/scanner/box-spread/alerts?days=1&limit=100" },
  "custom-strategy": { slug: "custom-strategy", title: "Custom Strategy", description: "Independent strategy workspace.", universe: "CONFIGURATION ONLY", columns: ["Symbol","Segment","Contract","Side","Live Price","Condition","Action"], controls: ["F&O Stocks","Cash / Future / Option","BUY / SELL","Condition"], pairColumns: ["Time","Symbol","State"], historyEndpoint: "" }
};

function value(row: Row, ...keys: string[]) { for (const k of keys) if (row[k] !== undefined && row[k] !== null && row[k] !== "") return row[k]; return undefined; }
function num(x: unknown) { if (x === undefined || x === null || x === "") return null; const n = Number(x); return Number.isFinite(n) ? n : null; }
function fmt(x: unknown) { if (x === undefined || x === null || x === "") return "—"; if (typeof x === "number") return Number.isFinite(x) ? x.toLocaleString("en-IN", { maximumFractionDigits: 4 }) : "—"; return String(x); }
function time(x: unknown) { if (x === undefined || x === null || x === "") return "—"; const n = Number(x); const d = Number.isFinite(n) ? new Date(n >= 1e17 ? n / 1e6 : n >= 1e12 ? n : n >= 1e9 ? n * 1000 : NaN) : new Date(String(x)); return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" }); }
function expiry(x: unknown) { if (x === undefined || x === null || x === "") return "—"; const s = String(x).replace(/\.0+$/, ""); if (/^\d{8}$/.test(s)) { const d = new Date(Date.UTC(Number(s.slice(0,4)),Number(s.slice(4,6))-1,Number(s.slice(6,8)))); if (!Number.isNaN(d.getTime())) return d.toLocaleDateString("en-IN", {day:"2-digit",month:"short",year:"numeric",timeZone:"UTC"}); } return s; }

function syntheticEdges(row: Row) {
  const k=num(row.strike), fb=num(row.future_bid), fa=num(row.future_ask), cb=num(row.call_bid), ca=num(row.call_ask), pb=num(row.put_bid), pa=num(row.put_ask);
  if ([k,fb,fa,cb,ca,pb,pa].some(v=>v===null)) return null;
  const syntheticBid=k!+cb!-pa!, syntheticAsk=k!+ca!-pb!;
  return { long: syntheticBid-fa!, short: fb!-syntheticAsk, syntheticBid, syntheticAsk };
}
function syntheticDirection(row: Row) {
  const e=syntheticEdges(row); if (!e) return String(value(row,"direction")??"").toUpperCase() || "NOT EVALUATED";
  const backend=String(value(row,"direction")??"").toUpperCase();
  if (backend==="LONG" && e.long>0) return "LONG";
  if (backend==="SHORT" && e.short>0) return "SHORT";
  if (e.long>0 && e.short<=0) return "LONG";
  if (e.short>0 && e.long<=0) return "SHORT";
  if (e.long>0 && e.short>0) return e.long>=e.short ? "LONG" : "SHORT";
  return backend || "NO EDGE";
}
function syntheticEdge(row: Row) { const e=syntheticEdges(row), d=syntheticDirection(row); if (!e) return num(row.executable_edge); return d==="SHORT"?e.short:d==="LONG"?e.long:num(row.executable_edge); }

function cell(slug:string,column:string,row:Row) {
  if (slug==="synthetic-arbitrage") {
    if(column==="Time") return time(value(row,"timestamp_ns","observed_at","timestamp"));
    if(column==="Underlying") return fmt(value(row,"underlying","symbol"));
    if(column==="Expiry") return expiry(row.expiry);
    if(column==="Future") return `Bid ${fmt(row.future_bid)} / Ask ${fmt(row.future_ask)}`;
    if(column==="Call (CE)") return `Bid ${fmt(row.call_bid)} / Ask ${fmt(row.call_ask)}`;
    if(column==="Put (PE)") return `Bid ${fmt(row.put_bid)} / Ask ${fmt(row.put_ask)}`;
    if(column==="Synthetic Future"){const e=syntheticEdges(row);return e?`Bid ${fmt(e.syntheticBid)} / Ask ${fmt(e.syntheticAsk)}`:"Bid — / Ask —";}
    if(column==="Edge"){const e=syntheticEdge(row);const d=syntheticDirection(row);return e===null?"—":`${fmt(e)} pts — ${d}`;}
    if(column==="Signal"||column==="Direction") return syntheticDirection(row);
    if(column==="State"){const e=syntheticEdge(row);return e!==null&&e>0?"EDGE":"NO EDGE";}
  }
  if(column==="Time") return time(value(row,"timestamp_ns","observed_at","timestamp"));
  if(column==="Expiry") return expiry(row.expiry);
  if(column==="Underlying") return fmt(value(row,"underlying","symbol"));
  const map:Record<string,string[]>={"Gap / Edge":["long_edge","short_edge"],Spread:["gap_points"],Signal:["direction"],Direction:["direction"],State:["state","direction"],"Box Edge":["executable_edge"],"Low Strike":["low_strike"],"High Strike":["high_strike"],Liquidity:["liquidity_qty","lot_size"]};
  return fmt(value(row,...(map[column]??[column.toLowerCase().replaceAll(" ","_")]));
}

export function StrategyWorkspace({ slug }: { slug: keyof typeof configs }) {
  const c=configs[slug]; const [rows,setRows]=useState<Row[]>([]); const [pairs,setPairs]=useState<Row[]>([]); const [history,setHistory]=useState<Row[]>([]); const [search,setSearch]=useState(""); const [control,setControl]=useState(c.controls[0]); const [loading,setLoading]=useState(true); const [error,setError]=useState<string|null>(null); const [refreshing,setRefreshing]=useState(false); const base=appConfig.apiBaseUrl.replace(/\/$/,"");
  const endpoint=slug==="calendar-spread"?"/api/v1/scanner/calendar-spread/live?limit=50":slug==="synthetic-arbitrage"?"/api/v1/scanner/synthetic-cash-carry/live?limit=50":slug==="box-spread"?"/api/v1/scanner/box-spread/live?limit=50&max_age_seconds=5":null;
  const load=async()=>{ if(!endpoint){setLoading(false);return;} try { const [lr,pr,hr]=await Promise.all([fetch(base+endpoint,{cache:"no-store"}),c.pairEndpoint?fetch(base+c.pairEndpoint,{cache:"no-store"}):Promise.resolve(null),c.historyEndpoint?fetch(base+c.historyEndpoint,{cache:"no-store"}):Promise.resolve(null)]); const lb=await lr.json().catch(()=>({})); if(!lr.ok) throw new Error(lb?.detail||`Backend HTTP ${lr.status}`); setRows(Array.isArray(lb?.data)?lb.data:[]); if(pr){const b=await pr.json().catch(()=>({}));if(pr.ok)setPairs(Array.isArray(b?.data)?b.data:[]);} if(hr){const b=await hr.json().catch(()=>({}));if(hr.ok)setHistory(Array.isArray(b?.data)?b.data:[]);} setError(null);} catch(e){setError(e instanceof Error?e.message:"Backend unavailable");} finally{setLoading(false);} };
  useEffect(()=>{void load();const t=window.setInterval(()=>void load(),1500);return()=>window.clearInterval(t);},[endpoint,c.pairEndpoint,c.historyEndpoint]);
  const filtered=useMemo(()=>{const q=search.trim().toUpperCase();return q?rows.filter(r=>Object.values(r).some(v=>String(v??"").toUpperCase().includes(q))):rows;},[rows,search]);
  const monitor=useMemo(()=>{const src=c.pairEndpoint?pairs:rows,q=search.trim().toUpperCase();return q?src.filter(r=>Object.values(r).some(v=>String(v??"").toUpperCase().includes(q))):src;},[pairs,rows,search,c.pairEndpoint]);
  const signals=rows.filter(r=>slug==="synthetic-arbitrage"?(syntheticEdge(r)??0)>0:Number(r.executable_edge??r.long_edge??0)>0).length;
  const refresh=async()=>{setRefreshing(true);await load();setRefreshing(false);};
  return <div className="min-h-screen space-y-5 theme-bg p-4 theme-text">
    <div className="flex items-center justify-between gap-3"><Link href="/scanner" className="inline-flex min-h-10 items-center gap-2 rounded-xl border theme-border theme-surface px-3 text-xs font-semibold theme-muted"><ArrowLeft className="h-4 w-4"/> Scanner</Link><span className="rounded-xl border theme-border theme-success-bg px-3 py-2 text-xs font-semibold theme-success">{loading?"CONNECTING":error?"BACKEND ERROR":"LIVE FEED"}</span></div>
    <PageTitle eyebrow="Live Strategy Scanner" title={c.title} description={c.description}/>
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Card className="theme-border theme-surface p-4"><p className="text-xs theme-muted">Universe</p><p className="mt-2 text-sm font-semibold">{c.universe}</p></Card><Card className="theme-border theme-surface p-4"><p className="text-xs theme-muted">Rows</p><p className="mt-2 text-xl font-semibold">{rows.length}</p></Card><Card className="theme-border theme-surface p-4"><p className="text-xs theme-muted">Positive Signals</p><p className="mt-2 text-xl font-semibold theme-success">{signals}</p></Card><Card className="theme-border theme-surface p-4"><p className="text-xs theme-muted">Broker Orders</p><p className="mt-2 text-sm font-semibold theme-warning">OFF</p></Card></div>
    <Card className="theme-border theme-surface p-4"><div className="flex flex-col gap-3 sm:flex-row"><label className="relative flex-1"><Search className="absolute left-3 top-3 h-4 w-4 theme-muted"/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search symbol / underlying" className="min-h-11 w-full rounded-xl border theme-border bg-transparent pl-9 pr-3 text-sm"/></label><select value={control} onChange={e=>setControl(e.target.value)} className="min-h-11 rounded-xl border theme-border bg-transparent px-3 text-sm">{c.controls.map(x=><option key={x}>{x}</option>)}</select><button type="button" onClick={()=>void refresh()} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border theme-border px-4 text-sm font-semibold"><RefreshCw className={refreshing?"animate-spin":""}/> Refresh</button></div></Card>
    {error&&<Card className="theme-border theme-danger-bg p-4 text-sm theme-danger">{error}</Card>}
    <Card className="overflow-hidden theme-border theme-surface"><div className="flex items-center gap-2 border-b theme-border p-4"><Zap className="h-4 w-4 theme-accent"/><div><h2 className="font-semibold">Live Opportunities</h2><p className="text-xs theme-muted">Raw executable bid/ask calculations. No carry or fees.</p></div></div><div className="overflow-x-auto"><table className="min-w-[1100px] w-full text-left text-sm"><thead className="theme-surface-2 text-xs uppercase theme-muted"><tr>{c.columns.map(x=><th key={x} className="px-4 py-3">{x}</th>)}</tr></thead><tbody>{loading?<tr><td colSpan={c.columns.length} className="p-10 text-center theme-muted">Loading…</td></tr>:filtered.length===0?<tr><td colSpan={c.columns.length} className="p-10 text-center theme-muted">No live opportunity data</td></tr>:filtered.map((r,i)=><tr key={String(r.id??r.event_id??r.timestamp_ns??i)} className="border-b theme-border">{c.columns.map(x=><td key={x} className="px-4 py-3">{cell(slug,x,r)}</td>)}</tr>)}</tbody></table></div></Card>
    <Card className="overflow-hidden theme-border theme-surface"><div className="p-4"><div className="flex items-center gap-2"><Activity className="h-4 w-4 theme-accent"/><h2 className="font-semibold">{c.title} Pair Monitor</h2></div></div><div className="overflow-x-auto"><table className="min-w-[1100px] w-full text-left text-sm"><thead className="theme-surface-2 text-xs uppercase theme-muted"><tr>{c.pairColumns.map(x=><th key={x} className="px-4 py-3">{x}</th>)}</tr></thead><tbody>{monitor.length===0?<tr><td colSpan={c.pairColumns.length} className="p-10 text-center theme-muted">No pair observations</td></tr>:monitor.slice(0,200).map((r,i)=><tr key={String(r.id??r.timestamp_ns??i)} className="border-b theme-border">{c.pairColumns.map(x=><td key={x} className="px-4 py-3">{cell(slug,x,r)}</td>)}</tr>)}</tbody></table></div></Card>
    <Card className="theme-border theme-surface p-4"><div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 theme-success"/><h2 className="font-semibold">Post-Market History</h2></div><p className="mt-1 text-xs theme-muted">{history.length} saved backend rows. No UI-generated rows.</p></Card>
  </div>;
}
