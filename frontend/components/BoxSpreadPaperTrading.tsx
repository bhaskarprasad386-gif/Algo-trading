"use client";

import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { appConfig } from "@/lib/config";

type LiveOpportunity = {
  symbol: string; instrument_class: string; expiry: string; low_strike: number;
  high_strike: number; direction: string; lot_size: number; executable_edge: number;
  low_call_price: number; high_call_price: number; high_put_price: number; low_put_price: number;
};
type PaperTrade = {
  trade_id: number; underlying: string; instrument_class: string; expiry: string;
  low_strike: number; high_strike: number; direction: string; lots: number;
  quantity: number; lot_size: number; status: string; created_at: string;
};

const roles = ["LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"] as const;
type Role = typeof roles[number];

export default function BoxSpreadPaperTrading() {
  const api = appConfig.apiBaseUrl.replace(/\/$/, "") + "/api/v1/scanner/box-spread-paper";
  const [account, setAccount] = useState("default");
  const [balance, setBalance] = useState<string | null>(null);
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [opportunities, setOpportunities] = useState<LiveOpportunity[]>([]);
  const [underlying, setUnderlying] = useState("NIFTY");
  const [instrumentClass, setInstrumentClass] = useState("INDEX");
  const [expiry, setExpiry] = useState("");
  const [lowStrike, setLowStrike] = useState("25000");
  const [highStrike, setHighStrike] = useState("25100");
  const [direction, setDirection] = useState("LONG");
  const [lots, setLots] = useState("1");
  const [prices, setPrices] = useState<Record<Role, string>>({ LOW_CE: "", HIGH_CE: "", HIGH_PE: "", LOW_PE: "" });
  const [selectedTrade, setSelectedTrade] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [ledger, setLedger] = useState<any | null>(null);

  const request = useCallback(async (path: string, init?: RequestInit) => {
    const response = await fetch(api + path, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      signal: AbortSignal.timeout(12000),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(String(body?.detail ?? "Paper API request failed (" + response.status + ")"));
    return body;
  }, [api]);

  const refresh = useCallback(async () => {
    try {
      const [accountBody, tradesBody] = await Promise.all([
        request("/account?account_key=" + encodeURIComponent(account)),
        request("/trades?account_key=" + encodeURIComponent(account)),
      ]);
      setBalance(String(accountBody.virtual_balance));
      setTrades(Array.isArray(tradesBody.data) ? tradesBody.data : []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load paper ledger");
    }
  }, [account, request]);

  const loadOpportunities = useCallback(async () => {
    try {
      const response = await fetch(appConfig.apiBaseUrl.replace(/\\/$/, "") + "/api/v1/scanner/box-spread/live?limit=30&max_age_seconds=5", {
        cache: "no-store", signal: AbortSignal.timeout(5000),
      });
      if (!response.ok) throw new Error("live Box Spread scanner HTTP " + response.status);
      const body = await response.json();
      setOpportunities(Array.isArray(body.data) ? body.data.filter((row: LiveOpportunity) =>
        [row.low_call_price, row.high_call_price, row.high_put_price, row.low_put_price].every(value => Number.isFinite(Number(value)) && Number(value) > 0)
      ) : []);
    } catch {
      // Keep the live scanner's own status independent; never fabricate rows on API failure.
      setOpportunities([]);
    }
  }, []);

  useEffect(() => { void refresh(); void loadOpportunities(); }, [refresh, loadOpportunities]);

  async function createTrade(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(null); setMessage(null); setLedger(null);
    try {
      const result = await request("/trades", {
        method: "POST",
        body: JSON.stringify({
          underlying, instrument_class: instrumentClass, expiry,
          low_strike: Number(lowStrike), high_strike: Number(highStrike),
          direction, lots: Number(lots), account_key: account,
          idempotency_key: "box-paper-" + crypto.randomUUID(),
        }),
      });
      setSelectedTrade(Number(result.trade_id));
      setMessage("Paper trade #" + result.trade_id + " created. Exact four contracts resolved; no broker order sent.");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to create paper trade");
    } finally { setBusy(false); }
  }

  async function simulateEntry(event: React.FormEvent) {
    event.preventDefault();
    if (selectedTrade == null) return;
    setBusy(true); setError(null); setMessage(null);
    try {
      const reference_prices = Object.fromEntries(roles.map(role => [role, Number(prices[role])]));
      const result = await request("/trades/" + selectedTrade + "/simulate-entry", {
        method: "POST", body: JSON.stringify({ account_key: account, reference_prices }),
      });
      setLedger(result);
      setMessage("Paper fills recorded with 200 ms simulated latency and adverse slippage. Broker orders remain disabled.");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Paper execution failed");
    } finally { setBusy(false); }
  }

  async function paperTradeOpportunity(opportunity: LiveOpportunity) {
    setBusy(true); setError(null); setMessage(null); setLedger(null);
    try {
      const create = await request("/trades", {
        method: "POST",
        body: JSON.stringify({
          underlying: opportunity.symbol, instrument_class: opportunity.instrument_class,
          expiry: opportunity.expiry, low_strike: opportunity.low_strike,
          high_strike: opportunity.high_strike, direction: opportunity.direction,
          lots: 1, account_key: account,
          idempotency_key: "box-paper-" + crypto.randomUUID(),
        }),
      });
      const tradeId = Number(create.trade_id);
      setSelectedTrade(tradeId);
      const result = await request("/trades/" + tradeId + "/simulate-entry", {
        method: "POST",
        body: JSON.stringify({
          account_key: account,
          reference_prices: {
            LOW_CE: opportunity.low_call_price,
            HIGH_CE: opportunity.high_call_price,
            HIGH_PE: opportunity.high_put_price,
            LOW_PE: opportunity.low_put_price,
          },
        }),
      });
      setLedger(result);
      setMessage("Live scanner opportunity paper-traded as #" + tradeId + ". Quotes were taken from the backend row; simulated fills include latency, slippage and charges. No broker order was sent.");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to paper-trade opportunity");
      await refresh();
    } finally { setBusy(false); }
  }

  async function loadLedger(tradeId: number) {
    setSelectedTrade(tradeId); setError(null);
    try { setLedger(await request("/trades/" + tradeId + "/ledger?account_key=" + encodeURIComponent(account))); }
    catch (e) { setError(e instanceof Error ? e.message : "Unable to load ledger"); }
  }

  return (
    <section className="space-y-4 rounded-2xl border theme-border theme-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] theme-accent">Separate paper environment</p>
          <h2 className="mt-1 text-xl font-semibold theme-text">Box Spread Paper Trading</h2>
          <p className="mt-1 text-sm theme-muted">Virtual balance and persistent fills only. No broker orders, no live scanner changes.</p>
        </div>
        <div className="rounded-xl border theme-border px-3 py-2">
          <div className="text-xs theme-muted">Virtual balance</div>
          <div className="text-lg font-semibold theme-text">{balance == null ? "—" : "₹" + Number(balance).toLocaleString("en-IN", { maximumFractionDigits: 2 })}</div>
          <div className="text-xs text-emerald-500">PAPER ONLY · ORDERS OFF</div>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm theme-muted">Paper account key
          <input value={account} onChange={e => setAccount(e.target.value)} maxLength={128} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 theme-text" />
        </label>
        <div className="flex items-end"><button type="button" onClick={() => void refresh()} disabled={busy} className="rounded-lg border theme-border px-4 py-2 text-sm font-semibold theme-text disabled:opacity-50">Refresh ledger</button></div>
      </div>

      <div className="space-y-2 rounded-xl border theme-border p-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="font-semibold theme-text">Live scanner opportunities</h3><p className="text-xs theme-muted">Backend opportunities only · executable four-leg quotes · rows older than five seconds excluded.</p></div><button type="button" onClick={() => void loadOpportunities()} disabled={busy} className="rounded-lg border theme-border px-3 py-2 text-xs font-semibold theme-text">Refresh opportunities</button></div>
        <div className="overflow-x-auto"><table className="w-full min-w-[800px] text-left text-xs"><thead className="theme-surface-2 theme-muted"><tr>{["Underlying","Expiry","Strikes","Direction","Edge","4 leg ref prices","Paper trade"].map(label=><th key={label} className="px-2 py-2">{label}</th>)}</tr></thead><tbody>
          {opportunities.map((op, index)=><tr key={op.symbol + op.expiry + op.low_strike + op.high_strike + index} className="border-t theme-border">
            <td className="px-2 py-2 theme-text">{op.symbol}</td><td className="px-2 py-2 theme-text">{op.expiry}</td><td className="px-2 py-2 theme-text">{op.low_strike} / {op.high_strike}</td><td className="px-2 py-2 theme-text">{op.direction}</td><td className="px-2 py-2 theme-text">{Number(op.executable_edge).toFixed(4)}</td><td className="px-2 py-2 theme-muted">LC {op.low_call_price} · HC {op.high_call_price}<br/>HP {op.high_put_price} · LP {op.low_put_price}</td><td className="px-2 py-2"><button type="button" disabled={busy} onClick={() => void paperTradeOpportunity(op)} className="rounded-md bg-cyan-500 px-3 py-2 font-bold text-slate-950 disabled:opacity-50">Paper trade</button></td>
          </tr>)}
          {opportunities.length === 0 && <tr><td colSpan={7} className="px-3 py-5 text-center theme-muted">No fresh executable live opportunities returned by the backend. No sample rows are shown.</td></tr>}
        </tbody></table></div>
      </div>

      <form onSubmit={createTrade} className="grid gap-3 rounded-xl border theme-border p-3 sm:grid-cols-4">
        <div className="sm:col-span-4"><h3 className="font-semibold theme-text">Create validated paper trade</h3><p className="text-xs theme-muted">Contract tokens/symbols are resolved from the instrument master; the request is rejected if the four-leg chain is ambiguous.</p></div>
        <label className="text-xs theme-muted">Underlying
          <input required value={underlying} onChange={e => setUnderlying(e.target.value.toUpperCase())} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text" />
        </label>
        <label className="text-xs theme-muted">Class
          <select value={instrumentClass} onChange={e => setInstrumentClass(e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text"><option value="INDEX">INDEX</option><option value="STOCK">STOCK (Top 15 only)</option></select>
        </label>
        <label className="text-xs theme-muted">Expiry
          <input required value={expiry} onChange={e => setExpiry(e.target.value)} placeholder="YYYY-MM-DD or 29OCT2026" className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text" />
        </label>
        <label className="text-xs theme-muted">Direction
          <select value={direction} onChange={e => setDirection(e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text"><option>LONG</option><option>SHORT</option></select>
        </label>
        <label className="text-xs theme-muted">Low strike
          <input required type="number" min="0.01" step="any" value={lowStrike} onChange={e => setLowStrike(e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text" />
        </label>
        <label className="text-xs theme-muted">High strike
          <input required type="number" min="0.01" step="any" value={highStrike} onChange={e => setHighStrike(e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text" />
        </label>
        <label className="text-xs theme-muted">Lots
          <input required type="number" min="1" max="100" step="1" value={lots} onChange={e => setLots(e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text" />
        </label>
        <div className="flex items-end"><button disabled={busy} className="w-full rounded-lg bg-cyan-500 px-4 py-2 text-sm font-bold text-slate-950 disabled:opacity-50">{busy ? "Working…" : "Create paper trade"}</button></div>
      </form>

      {selectedTrade != null && <form onSubmit={simulateEntry} className="grid gap-3 rounded-xl border theme-border p-3 sm:grid-cols-5">
        <div className="sm:col-span-5"><h3 className="font-semibold theme-text">Simulate four-leg entry · Trade #{selectedTrade}</h3><p className="text-xs theme-muted">Enter the observed reference price for each exact option leg. Simulation applies 200 ms latency and 0.05% adverse slippage; prices are not fetched or invented by the UI.</p></div>
        {roles.map(role => <label key={role} className="text-xs theme-muted">{role} reference price
          <input required type="number" min="0.01" step="any" value={prices[role]} onChange={e => setPrices(old => ({ ...old, [role]: e.target.value }))} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text" />
        </label>)}
        <div className="flex items-end"><button disabled={busy} className="w-full rounded-lg bg-emerald-500 px-3 py-2 text-sm font-bold text-slate-950 disabled:opacity-50">{busy ? "Simulating…" : "Simulate paper fills"}</button></div>
      </form>}

      {error && <div role="alert" className="rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-400">{error}</div>}
      {message && <div role="status" className="rounded-lg border theme-border p-3 text-sm theme-text">{message}</div>}

      <div className="overflow-x-auto rounded-xl border theme-border">
        <table className="w-full min-w-[760px] text-left text-sm">
          <thead className="theme-surface-2 theme-muted"><tr>{["Trade","Underlying","Expiry","Strikes","Direction","Lots","Status","Ledger"].map(label => <th key={label} className="px-3 py-2 font-semibold">{label}</th>)}</tr></thead>
          <tbody>{trades.map(trade => <tr key={trade.trade_id} className="border-t theme-border">
            <td className="px-3 py-2 theme-text">#{trade.trade_id}</td><td className="px-3 py-2 theme-text">{trade.underlying}</td><td className="px-3 py-2 theme-text">{trade.expiry}</td><td className="px-3 py-2 theme-text">{trade.low_strike} / {trade.high_strike}</td><td className="px-3 py-2 theme-text">{trade.direction}</td><td className="px-3 py-2 theme-text">{trade.lots} × {trade.lot_size}</td><td className="px-3 py-2 theme-text">{trade.status}</td><td className="px-3 py-2"><button type="button" onClick={() => void loadLedger(trade.trade_id)} className="rounded-md border theme-border px-2 py-1 text-xs font-semibold theme-text">View</button></td>
          </tr>)}
          {trades.length === 0 && <tr><td colSpan={8} className="px-3 py-5 text-center theme-muted">No paper trades recorded for this account.</td></tr>}</tbody>
        </table>
      </div>

      {ledger && <div className="space-y-2 rounded-xl border theme-border p-3">
        <div className="flex items-center justify-between gap-2"><h3 className="font-semibold theme-text">Paper ledger details</h3><button type="button" onClick={() => setLedger(null)} className="rounded-md border theme-border px-2 py-1 text-xs theme-text">Close</button></div>
        <div className="grid gap-2 sm:grid-cols-4">{["status","quantity","total_charges","realized_pnl","unrealized_pnl","net_cash_flow","fee_schedule_version"].map(key => <div key={key} className="rounded-lg theme-surface-2 p-2"><div className="text-xs theme-muted">{key.replaceAll("_"," ")}</div><div className="break-words text-sm font-semibold theme-text">{String(ledger.summary?.[key] ?? "—")}</div></div>)}</div>
        <div className="overflow-x-auto"><table className="w-full min-w-[600px] text-left text-xs"><thead className="theme-surface-2 theme-muted"><tr>{["Time","Leg","Action","Qty","Fill price","Charges","Cash delta"].map(k=><th key={k} className="px-2 py-2">{k}</th>)}</tr></thead><tbody>{(ledger.entries ?? []).map((entry: any, i: number)=><tr key={entry.fill_id + i} className="border-t theme-border"><td className="px-2 py-2 theme-text">{entry.created_at}</td><td className="px-2 py-2 theme-text">{entry.role}</td><td className="px-2 py-2 theme-text">{entry.action}</td><td className="px-2 py-2 theme-text">{entry.quantity}</td><td className="px-2 py-2 theme-text">{entry.fill_price}</td><td className="px-2 py-2 theme-text">{entry.charges}</td><td className="px-2 py-2 theme-text">{entry.cash_delta}</td></tr>)}</tbody></table></div>
      </div>}
    </section>
  );
}
