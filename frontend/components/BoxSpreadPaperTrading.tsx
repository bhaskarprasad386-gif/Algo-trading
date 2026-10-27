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
  const [selectedTrade, setSelectedTrade] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [ledger, setLedger] = useState<any | null>(null);
  const [exitRole, setExitRole] = useState<Role>("LOW_CE");
  const [exitQuantity, setExitQuantity] = useState("1");
  const [exitPrice, setExitPrice] = useState("");
  const [settlementDate, setSettlementDate] = useState("");
  const [settlementPrices, setSettlementPrices] = useState<Record<Role, string>>({ LOW_CE: "", HIGH_CE: "", HIGH_PE: "", LOW_PE: "" });

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
      const response = await fetch(appConfig.apiBaseUrl.replace(/\/$/, "") + "/api/v1/scanner/box-spread/live?limit=30&max_age_seconds=5", {
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

  async function exitSelectedLeg(event: FormEvent) {
    event.preventDefault();
    if (selectedTrade == null) return;
    setBusy(true); setError(null); setMessage(null);
    try {
      const result = await request("/trades/" + selectedTrade + "/exit", {
        method: "POST",
        body: JSON.stringify({
          account_key: account, role: exitRole, quantity: Number(exitQuantity),
          fill_price: Number(exitPrice), fill_id: "exit-" + crypto.randomUUID(),
        }),
      });
      setLedger(await request("/trades/" + selectedTrade + "/ledger?account_key=" + encodeURIComponent(account)));
      setMessage("Paper exit fill recorded for " + exitRole + ". Remaining legs stay visible until fully closed.");
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Paper exit failed"); }
    finally { setBusy(false); }
  }

  async function settleSelectedExpiry(event: FormEvent) {
    event.preventDefault();
    if (selectedTrade == null) return;
    setBusy(true); setError(null); setMessage(null);
    try {
      const settlement_prices = Object.fromEntries(roles.map(role => [role, Number(settlementPrices[role])]));
      const result = await request("/trades/" + selectedTrade + "/settle-expiry", {
        method: "POST",
        body: JSON.stringify({ account_key: account, settlement_date: settlementDate, settlement_prices }),
      });
      setLedger(await request("/trades/" + selectedTrade + "/ledger?account_key=" + encodeURIComponent(account)));
      setMessage("Expiry settlement recorded using the explicitly entered official settlement prices. Status: " + result.status);
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Expiry settlement failed"); }
    finally { setBusy(false); }
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

      {selectedTrade != null && <div className="grid gap-3 md:grid-cols-2">
        <form onSubmit={exitSelectedLeg} className="space-y-3 rounded-xl border theme-border p-3">
          <h3 className="font-semibold theme-text">Close one paper leg</h3>
          <p className="text-xs theme-muted">Enter the actual observed exit price. Closing is not blocked by low virtual balance.</p>
          <label className="block text-xs theme-muted">Leg
            <select value={exitRole} onChange={e => setExitRole(e.target.value as Role)} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text">{roles.map(role=><option key={role}>{role}</option>)}</select>
          </label>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs theme-muted">Quantity
              <input required type="number" min="1" step="1" value={exitQuantity} onChange={e => setExitQuantity(e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text"/>
            </label>
            <label className="text-xs theme-muted">Exit fill price
              <input required type="number" min="0.01" step="any" value={exitPrice} onChange={e => setExitPrice(e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text"/>
            </label>
          </div>
          <button disabled={busy} className="rounded-lg border theme-border px-4 py-2 text-sm font-semibold theme-text disabled:opacity-50">Record paper exit</button>
        </form>
        <form onSubmit={settleSelectedExpiry} className="space-y-3 rounded-xl border theme-border p-3">
          <h3 className="font-semibold theme-text">Expiry settlement</h3>
          <p className="text-xs theme-muted">Use official settlement values only, on or after contract expiry. Zero is allowed for worthless options.</p>
          <label className="block text-xs theme-muted">Settlement date
            <input required type="date" value={settlementDate} onChange={e => setSettlementDate(e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text"/>
          </label>
          <div className="grid grid-cols-2 gap-2">{roles.map(role=><label key={role} className="text-xs theme-muted">{role}
            <input required type="number" min="0" step="any" value={settlementPrices[role]} onChange={e => setSettlementPrices(old => ({ ...old, [role]: e.target.value }))} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text"/>
          </label>)}</div>
          <button disabled={busy || !settlementDate || roles.some(role => settlementPrices[role] === "")} className="rounded-lg border border-amber-500/50 px-4 py-2 text-sm font-semibold text-amber-500 disabled:opacity-50">Settle expiry</button>
        </form>
      </div>}

      {ledger && <div className="space-y-2 rounded-xl border theme-border p-3">
        <div className="flex items-center justify-between gap-2"><h3 className="font-semibold theme-text">Paper ledger details</h3><button type="button" onClick={() => setLedger(null)} className="rounded-md border theme-border px-2 py-1 text-xs theme-text">Close</button></div>
        <div className="grid gap-2 sm:grid-cols-4">{["status","quantity","total_charges","realized_pnl","unrealized_pnl","net_cash_flow","fee_schedule_version"].map(key => <div key={key} className="rounded-lg theme-surface-2 p-2"><div className="text-xs theme-muted">{key.replaceAll("_"," ")}</div><div className="break-words text-sm font-semibold theme-text">{String(ledger.summary?.[key] ?? "—")}</div></div>)}</div>
        <div className="overflow-x-auto"><table className="w-full min-w-[600px] text-left text-xs"><thead className="theme-surface-2 theme-muted"><tr>{["Time","Leg","Action","Qty","Fill price","Charges","Cash delta"].map(k=><th key={k} className="px-2 py-2">{k}</th>)}</tr></thead><tbody>{(ledger.entries ?? []).map((entry: any, i: number)=><tr key={entry.fill_id + i} className="border-t theme-border"><td className="px-2 py-2 theme-text">{entry.created_at}</td><td className="px-2 py-2 theme-text">{entry.role}</td><td className="px-2 py-2 theme-text">{entry.action}</td><td className="px-2 py-2 theme-text">{entry.quantity}</td><td className="px-2 py-2 theme-text">{entry.fill_price}</td><td className="px-2 py-2 theme-text">{entry.charges}</td><td className="px-2 py-2 theme-text">{entry.cash_delta}</td></tr>)}</tbody></table></div>
      </div>}
    </section>
  );
}
