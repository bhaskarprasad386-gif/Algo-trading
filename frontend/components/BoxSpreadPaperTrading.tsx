"use client";

import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { appConfig } from "@/lib/config";

type LiveOpportunity = {
  symbol: string; instrument_class: string; expiry: string; expiry_type?: string; low_strike: number;
  high_strike: number; spot_price?: number | null; spot_age_seconds?: number | null;
  direction: string; lot_size: number; executable_edge: number;
  low_call_price: number; high_call_price: number; high_put_price: number; low_put_price: number;
  low_call_bid?: number | null; low_call_ask?: number | null; low_put_bid?: number | null; low_put_ask?: number | null;
  high_call_bid?: number | null; high_call_ask?: number | null; high_put_bid?: number | null; high_put_ask?: number | null;
  low_call_bid_qty?: number | null; low_call_ask_qty?: number | null; low_put_bid_qty?: number | null; low_put_ask_qty?: number | null;
  high_call_bid_qty?: number | null; high_call_ask_qty?: number | null; high_put_bid_qty?: number | null; high_put_ask_qty?: number | null;
};
type ScannerDiagnostics = {
  status?: string;
  active_buckets?: number;
  counts?: Record<string, number>;
  last_scan?: {
    symbol?: string; expiry?: string | number; outcome?: string;
    grouped_strikes?: number; result_count?: number; min_executable_edge?: number;
  };
};
type OpportunityStatus = "CHECKING" | "LIVE" | "EMPTY" | "ERROR";

type PaperLegPosition = {
  role: Role; entry_action: string | null; entry_quantity: number;
  entry_average_price: string | null; open_quantity: number; open_action: string;
  mark_price: string | null; exit_quantity: number; exit_average_price: string | null;
};
type PaperLedgerEntry = {
  fill_id: string; role: string; action: string; quantity: number;
  fill_price: string; notional: string; charges: string; cash_delta: string; created_at: string;
};
type PaperTrade = {
  trade_id: number; underlying: string; instrument_class: string; expiry: string;
  low_strike: number; high_strike: number; direction: string; lots: number;
  quantity: number; lot_size: number; status: string; created_at: string;
  entry_spot_price?: number | null; entry_at?: string | null; closed_at?: string | null;
  mark_status?: string; summary?: {
    status?: string; realized_pnl?: string | null; unrealized_pnl?: string | null;
    total_charges?: string | null; net_cash_flow?: string | null; fill_count?: number;
  };
  legs?: PaperLegPosition[]; entries?: PaperLedgerEntry[];
};
type RecoveryLeg = {
  role: Role; expected_quantity: number; entry_filled_quantity: number;
  entry_remaining_quantity: number; exited_quantity: number;
  open_position_quantity: number; signed_open_position_quantity: number;
  open_position_action: "BUY" | "SELL" | "FLAT";
  fills: Array<{ fill_id: string; action: string; quantity: number; fill_price: string; created_at: string }>;
};
type RecoveryTrade = {
  trade_id: number; underlying: string; expiry: string; status: string;
  quantity: number; entry_claim_status: string | null; entry_claimed_at: string | null;
  entry_claim_completed_at: string | null; manual_review_required: boolean;
  automatic_replay: boolean; legs: RecoveryLeg[];
};

const roles = ["LOW_CE", "HIGH_CE", "HIGH_PE", "LOW_PE"] as const;
type Role = typeof roles[number];

export default function BoxSpreadPaperTrading() {
  const api = appConfig.apiBaseUrl.replace(/\/$/, "") + "/api/v1/scanner/box-spread-paper";
  const [account, setAccount] = useState("default");
  const [token, setToken] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [loginBusy, setLoginBusy] = useState(false);
  const [balance, setBalance] = useState<string | null>(null);
  const [executionEnabled, setExecutionEnabled] = useState(false);
  const [controlBusy, setControlBusy] = useState(false);
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [recoveryTrades, setRecoveryTrades] = useState<RecoveryTrade[]>([]);
  const [opportunities, setOpportunities] = useState<LiveOpportunity[]>([]);
  const [opportunityStatus, setOpportunityStatus] = useState<OpportunityStatus>("CHECKING");
  const [opportunityError, setOpportunityError] = useState<string | null>(null);
  const [scannerDiagnostics, setScannerDiagnostics] = useState<ScannerDiagnostics | null>(null);
  const [lastOpportunityCheck, setLastOpportunityCheck] = useState<number | null>(null);
  const [selectedTrade, setSelectedTrade] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [ledger, setLedger] = useState<any | null>(null);
  const [exitRole, setExitRole] = useState<Role>("LOW_CE");
  const [exitQuantity, setExitQuantity] = useState("1");
  const [lots, setLots] = useState(1);
  const [positionSearch, setPositionSearch] = useState("");
  const [positionDirection, setPositionDirection] = useState("ALL");
  const [positionSort, setPositionSort] = useState("LATEST");

  const request = useCallback(async (path: string, init?: RequestInit) => {
    const response = await fetch(api + path, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}), ...(init?.headers ?? {}) },
      signal: AbortSignal.timeout(12000),
    });
    const body = await response.json().catch(() => ({}));
    if (response.status === 401 && token) {
      window.sessionStorage.removeItem("box-spread-paper-token");
      setToken(null);
    }
    if (!response.ok) throw new Error(String(body?.detail ?? "Paper API request failed (" + response.status + ")"));
    return body;
  }, [api, token]);

  async function login(event: FormEvent) {
    event.preventDefault(); setLoginBusy(true); setError(null);
    try {
      const response = await fetch(api + "/auth/login", { method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password }), signal: AbortSignal.timeout(12000) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || typeof body.access_token !== "string") throw new Error(String(body?.detail ?? "Paper-trading login failed"));
      window.sessionStorage.setItem("box-spread-paper-token", body.access_token);
      setToken(body.access_token); setPassword(""); setMessage("Private paper-trading session started.");
    } catch (e) { setError(e instanceof Error ? e.message : "Paper-trading login failed"); }
    finally { setLoginBusy(false); }
  }

  const refresh = useCallback(async () => {
    try {
      const [accountBody, tradesBody, controlBody] = await Promise.all([
        request("/account?account_key=" + encodeURIComponent(account)),
        request("/trades?account_key=" + encodeURIComponent(account) + "&limit=500&include_ledger=true"),
        request("/execution-control"),
      ]);
      setBalance(String(accountBody.virtual_balance));
      setExecutionEnabled(controlBody.enabled === true);
      setTrades(Array.isArray(tradesBody.data) ? tradesBody.data : []);
      setError(null);
      // Recovery is a separate read-only request so its failure never blocks the normal ledger.
      void request("/recovery?account_key=" + encodeURIComponent(account))
        .then(body => setRecoveryTrades(Array.isArray(body.data) ? body.data : []))
        .catch(() => { /* Preserve the last durable recovery snapshot on transient API errors. */ });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load paper ledger");
    }
  }, [account, request]);

  const loadOpportunities = useCallback(async () => {
    setOpportunityStatus("CHECKING");
    const base = appConfig.apiBaseUrl.replace(/\/$/, "") + "/api/v1/scanner/box-spread";
    const [liveResult, diagnosticsResult] = await Promise.allSettled([
      fetch(base + "/live?limit=30&max_age_seconds=5", {
        cache: "no-store", signal: AbortSignal.timeout(5000),
      }).then(async response => {
        if (!response.ok) throw new Error("Live scanner HTTP " + response.status);
        return response.json();
      }),
      fetch(base + "/diagnostics", {
        cache: "no-store", signal: AbortSignal.timeout(5000),
      }).then(async response => {
        if (!response.ok) throw new Error("Scanner diagnostics HTTP " + response.status);
        return response.json();
      }),
    ]);

    setLastOpportunityCheck(Date.now());
    if (diagnosticsResult.status === "fulfilled") {
      setScannerDiagnostics(diagnosticsResult.value as ScannerDiagnostics);
    } else {
      setScannerDiagnostics(null);
    }

    if (liveResult.status === "rejected") {
      setOpportunities([]);
      setOpportunityStatus("ERROR");
      setOpportunityError(liveResult.reason instanceof Error
        ? liveResult.reason.message : "Unable to reach the live Box Spread scanner");
      return;
    }

    const body = liveResult.value;
    if (!Array.isArray(body?.data)) {
      setOpportunities([]);
      setOpportunityStatus("ERROR");
      setOpportunityError("Live scanner returned an invalid response; expected a data array");
      return;
    }

    const rows = body.data.filter((row: LiveOpportunity) =>
      [row.low_call_price, row.high_call_price, row.high_put_price, row.low_put_price].every(value =>
        Number.isFinite(Number(value)) && Number(value) > 0
      )
    );
    setOpportunities(rows);
    setOpportunityError(null);
    setOpportunityStatus(rows.length > 0 ? "LIVE" : "EMPTY");
  }, []);

  useEffect(() => { setToken(window.sessionStorage.getItem("box-spread-paper-token")); }, []);

  useEffect(() => {
    if (!token) return;
    void refresh();
    void loadOpportunities();
    // Keep the control and ledger in sync with backend state (including restart-default OFF),
    // independently of the faster live-opportunity refresh.
    const statusTimer = window.setInterval(() => { void refresh(); }, 7000);
    const opportunitiesTimer = window.setInterval(() => { void loadOpportunities(); }, 7000);
    return () => {
      window.clearInterval(statusTimer);
      window.clearInterval(opportunitiesTimer);
    };
  }, [token, refresh, loadOpportunities]);

  async function togglePaperExecution() {
    setControlBusy(true); setError(null); setMessage(null);
    try {
      const body = await request("/execution-control", {
        method: "POST",
        body: JSON.stringify({ enabled: !executionEnabled }),
      });
      setExecutionEnabled(body.enabled === true);
      setMessage(body.enabled === true
        ? "Paper execution ON. New four-leg entries are allowed only with fresh backend quotes and safety checks."
        : "Paper execution OFF. New entries are blocked; existing positions and safe exits remain available.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to change paper execution switch");
    } finally { setControlBusy(false); }
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
          lots, account_key: account, entry_spot_price: opportunity.spot_price ?? undefined,
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
          fill_id: "exit-" + crypto.randomUUID(),
        }),
      });
      setLedger(await request("/trades/" + selectedTrade + "/ledger?account_key=" + encodeURIComponent(account)));
      setMessage("Paper exit fill recorded for " + exitRole + ". Remaining legs stay visible until fully closed.");
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Paper exit failed"); }
    finally { setBusy(false); }
  }

  async function loadLedger(tradeId: number) {
    setSelectedTrade(tradeId); setError(null);
    try { setLedger(await request("/trades/" + tradeId + "/ledger?account_key=" + encodeURIComponent(account))); }
    catch (e) { setError(e instanceof Error ? e.message : "Unable to load ledger"); }
  }

  const searchTerm = positionSearch.trim().toUpperCase();
  const filteredTrades = trades.filter(trade =>
    (!searchTerm || trade.underlying.toUpperCase().includes(searchTerm) || String(trade.trade_id).includes(searchTerm)) &&
    (positionDirection === "ALL" || trade.direction === positionDirection)
  );
  const sortPositions = (items: PaperTrade[], closed: boolean) => [...items].sort((a, b) => {
    if (positionSort !== "LATEST") {
      const pnlA = Number(a.summary?.realized_pnl ?? 0) + Number(a.summary?.unrealized_pnl ?? 0);
      const pnlB = Number(b.summary?.realized_pnl ?? 0) + Number(b.summary?.unrealized_pnl ?? 0);
      if (pnlA !== pnlB) return positionSort === "PNL_HIGH" ? pnlB - pnlA : pnlA - pnlB;
    }
    const timeA = Date.parse((closed ? a.closed_at : a.entry_at) || a.created_at) || 0;
    const timeB = Date.parse((closed ? b.closed_at : b.entry_at) || b.created_at) || 0;
    return timeB - timeA;
  });
  const openTrades = sortPositions(filteredTrades.filter(trade =>
    !["CLOSED", "CANCELLED"].includes(trade.status) &&
    (!trade.legs?.some(leg => leg.entry_quantity > 0) || trade.legs.some(leg => leg.open_quantity > 0))
  ), false);
  const closedTrades = sortPositions(filteredTrades.filter(trade =>
    trade.status === "CLOSED" ||
    (!!trade.legs?.length && trade.legs.some(leg => leg.entry_quantity > 0) && trade.legs.every(leg => leg.open_quantity === 0))
  ), true);
  const allOpenTrades = trades.filter(trade =>
    !["CLOSED", "CANCELLED"].includes(trade.status) &&
    (!trade.legs?.some(leg => leg.entry_quantity > 0) || trade.legs.some(leg => leg.open_quantity > 0))
  );
  const totalRealizedPnl = trades.reduce((sum, trade) => sum + Number(trade.summary?.realized_pnl ?? 0), 0);
  const allOpenMarked = allOpenTrades.length === 0 || allOpenTrades.every(trade =>
    trade.legs?.every(leg => leg.open_quantity === 0 || leg.mark_price != null) &&
    trade.summary?.unrealized_pnl != null
  );
  const totalUnrealizedPnl = allOpenMarked
    ? allOpenTrades.reduce((sum, trade) => sum + Number(trade.summary?.unrealized_pnl ?? 0), 0)
    : null;
  const totalNetPnl = totalUnrealizedPnl == null ? null : totalRealizedPnl + totalUnrealizedPnl;
  const money = (value: string | number | null | undefined) =>
    value == null || !Number.isFinite(Number(value)) ? "—" :
      "₹" + Number(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const istDateTime = (value?: string | null) => {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value :
      date.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" }) + " IST";
  };
  const renderPositionCard = (trade: PaperTrade, closed: boolean) => (
    <article key={trade.trade_id} className="space-y-3 rounded-xl border theme-border theme-surface-2 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="font-semibold theme-text">#{trade.trade_id} · {trade.underlying} · {trade.direction} BOX</div>
          <div className="mt-1 text-xs theme-muted">{trade.instrument_class} · {trade.low_strike.toLocaleString("en-IN")} / {trade.high_strike.toLocaleString("en-IN")} · {trade.lots} lot(s) · Qty {trade.quantity}</div>
        </div>
        <span className={"rounded-full px-2 py-1 text-[10px] font-bold " + (closed ? "bg-slate-500/15 theme-muted" : "bg-emerald-500/15 text-emerald-600")}>{closed ? "CLOSED" : trade.status}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
        <div><div className="theme-muted">Entry date / time</div><div className="mt-1 theme-text">{trade.entry_at ? istDateTime(trade.entry_at) : "No fills yet · " + istDateTime(trade.created_at)}</div></div>
        <div><div className="theme-muted">Expiry</div><div className="mt-1 theme-text">{trade.expiry}</div></div>
        <div><div className="theme-muted">Spot at entry</div><div className="mt-1 theme-text">{money(trade.entry_spot_price)}</div></div>
        <div><div className="theme-muted">{closed ? "Closed at" : "Opened at"}</div><div className="mt-1 theme-text">{closed ? istDateTime(trade.closed_at) : trade.entry_at ? istDateTime(trade.entry_at) : "Entry pending / review"}</div></div>
      </div>
      <div className="overflow-x-auto rounded-lg border theme-border">
        <table className="w-full min-w-[610px] text-left text-xs">
          <thead className="theme-surface theme-muted"><tr>{["Leg","Entry side","Entry avg","Filled qty","Open qty","Mark / Exit avg"].map(label=><th key={label} className="px-2 py-2">{label}</th>)}</tr></thead>
          <tbody>{(trade.legs ?? []).map(leg=><tr key={leg.role} className="border-t theme-border">
            <td className="px-2 py-2 font-semibold theme-text">{leg.role}</td>
            <td className="px-2 py-2 theme-text">{leg.entry_action ?? "—"}</td>
            <td className="px-2 py-2 theme-text">{money(leg.entry_average_price)}</td>
            <td className="px-2 py-2 theme-text">{leg.entry_quantity.toLocaleString("en-IN")}</td>
            <td className="px-2 py-2 theme-text">{leg.open_quantity.toLocaleString("en-IN")}</td>
            <td className="px-2 py-2 theme-text">{closed ? money(leg.exit_average_price) : money(leg.mark_price)}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="rounded-lg border theme-border p-2"><div className="text-[11px] theme-muted">Realized P&L</div><div className={"mt-1 text-sm font-bold " + (Number(trade.summary?.realized_pnl ?? 0) >= 0 ? "text-emerald-600" : "text-red-500")}>{money(trade.summary?.realized_pnl)}</div></div>
        <div className="rounded-lg border theme-border p-2"><div className="text-[11px] theme-muted">{closed ? "Unrealized P&L" : "Unrealized P&L"}</div><div className="mt-1 text-sm font-bold theme-text">{closed ? money(0) : money(trade.summary?.unrealized_pnl)}</div></div>
        <div className="rounded-lg border theme-border p-2"><div className="text-[11px] theme-muted">Charges</div><div className="mt-1 text-sm font-semibold theme-text">{money(trade.summary?.total_charges)}</div></div>
        <div className="rounded-lg border theme-border p-2"><div className="text-[11px] theme-muted">Fill records</div><div className="mt-1 text-sm font-semibold theme-text">{trade.summary?.fill_count ?? trade.entries?.length ?? 0}</div></div>
      </div>
      {!closed && trade.mark_status !== "FRESH_BACKEND_QUOTES" && <p className="text-[11px] text-amber-600">Live MTM unavailable: backend quotes are stale or incomplete. Unrealized P&L is not estimated from stale prices.</p>}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] theme-muted">{closed ? "Realized result from persisted exit fills." : "Open quantity and marks are based on persisted fills and fresh backend quotes only."}</p>
        <button type="button" onClick={() => void loadLedger(trade.trade_id)} className="rounded-lg border theme-border px-3 py-2 text-xs font-semibold theme-text">Full fill ledger</button>
      </div>
    </article>
  );

  if (!token) return (
    <section className="mx-auto max-w-lg space-y-4 rounded-2xl border theme-border theme-surface p-4 sm:p-5">
      <div><h2 className="text-xl font-semibold theme-text">Private Box Spread Paper Trading</h2><p className="mt-1 text-sm theme-muted">Enter your server-configured private password. This session only accesses the paper ledger; broker orders remain OFF.</p></div>
      <form onSubmit={login} className="space-y-3">
        <label className="block text-sm theme-muted">Private access password<input required type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 theme-text" /></label>
        <button type="submit" disabled={loginBusy || !password} className="rounded-lg bg-cyan-500 px-4 py-2 text-sm font-bold text-slate-950 disabled:opacity-50">{loginBusy ? "Signing in…" : "Unlock paper trading"}</button>
      </form>
      {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
    </section>
  );

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

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border theme-border p-3">
        <div>
          <h3 className="font-semibold theme-text">Live Paper Trading Execution</h3>
          <p className="mt-1 text-xs theme-muted">Manual control for new Box Spread paper entries. OFF is the default after API restart. Market-data scanner remains read-only; broker orders stay OFF.</p>
          <p className={"mt-2 text-sm font-bold " + (executionEnabled ? "text-emerald-500" : "text-amber-500")}>{executionEnabled ? "PAPER EXECUTION ON" : "PAPER EXECUTION OFF"}</p>
        </div>
        <button type="button" onClick={() => void togglePaperExecution()} disabled={controlBusy || busy} className={"rounded-lg px-4 py-2 text-sm font-bold disabled:opacity-50 " + (executionEnabled ? "border border-amber-500 text-amber-500" : "bg-emerald-500 text-slate-950")}>
          {controlBusy ? "Updating…" : executionEnabled ? "Turn OFF" : "Manually Turn ON"}
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm theme-muted">Paper account key
          <input value={account} onChange={e => setAccount(e.target.value)} maxLength={128} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 theme-text" />
        </label>
        <label className="text-sm theme-muted">Lots per paper trade
          <select value={lots} onChange={e => setLots(Number(e.target.value))} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 theme-text">
            {[1, 2, 5].map(value => <option key={value} value={value}>{value} {value === 1 ? "lot" : "lots"}</option>)}
          </select>
        </label>
        <div className="flex items-end"><button type="button" onClick={() => void refresh()} disabled={busy} className="rounded-lg border theme-border px-4 py-2 text-sm font-semibold theme-text disabled:opacity-50">Refresh ledger</button></div>
      </div>

      <section className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div><p className="text-xs font-semibold uppercase tracking-wider theme-accent">Portfolio book</p><h3 className="text-lg font-semibold theme-text">Open & Closed Positions</h3><p className="text-xs theme-muted">Each executed trade, four leg fills, entry timestamp, expiry, entry spot, fees and P&L. All amounts are from the paper ledger.</p></div>
          <span className="rounded-lg border theme-border px-3 py-2 text-xs theme-muted">{openTrades.length} open · {closedTrades.length} closed</span>
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          <label className="text-xs theme-muted">Search underlying / trade ID
            <input value={positionSearch} onChange={event=>setPositionSearch(event.target.value)} placeholder="NIFTY, SENSEX, #trade…" className="mt-1 min-h-9 w-full rounded-lg border theme-border theme-surface-2 px-3 text-sm theme-text"/>
          </label>
          <label className="text-xs theme-muted">Direction
            <select value={positionDirection} onChange={event=>setPositionDirection(event.target.value)} className="mt-1 min-h-9 w-full rounded-lg border theme-border theme-surface-2 px-3 text-sm theme-text"><option value="ALL">All directions</option><option value="LONG">LONG</option><option value="SHORT">SHORT</option></select>
          </label>
          <label className="text-xs theme-muted">Sort positions
            <select value={positionSort} onChange={event=>setPositionSort(event.target.value)} className="mt-1 min-h-9 w-full rounded-lg border theme-border theme-surface-2 px-3 text-sm theme-text"><option value="LATEST">Newest first</option><option value="PNL_HIGH">Highest P&amp;L first</option><option value="PNL_LOW">Lowest P&amp;L first</option></select>
          </label>
        </div>
        <div className="grid gap-3 lg:grid-cols-2">
          <div className="min-w-0 space-y-3 rounded-xl border theme-border p-3">
            <div className="flex items-center justify-between gap-2"><h4 className="font-semibold theme-text">Open Positions</h4><span className="text-xs text-emerald-600">{openTrades.length} active</span></div>
            {openTrades.length ? openTrades.map(trade=>renderPositionCard(trade,false)) : <p className="rounded-lg border theme-border p-4 text-sm theme-muted">No open paper positions for this account.</p>}
          </div>
          <div className="min-w-0 space-y-3 rounded-xl border theme-border p-3">
            <div className="flex items-center justify-between gap-2"><h4 className="font-semibold theme-text">Closed Positions</h4><span className="text-xs theme-muted">{closedTrades.length} completed</span></div>
            {closedTrades.length ? closedTrades.map(trade=>renderPositionCard(trade,true)) : <p className="rounded-lg border theme-border p-4 text-sm theme-muted">No closed paper positions for this account.</p>}
          </div>
        </div>
        <div className="rounded-xl border theme-border p-3">
          <h4 className="font-semibold theme-text">Total Profit & Loss · All Positions</h4>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="rounded-lg theme-surface-2 p-3"><div className="text-xs theme-muted">Realized P&L</div><div className={"mt-1 text-lg font-bold " + (totalRealizedPnl >= 0 ? "text-emerald-600" : "text-red-500")}>{money(totalRealizedPnl)}</div></div>
            <div className="rounded-lg theme-surface-2 p-3"><div className="text-xs theme-muted">Open unrealized P&L</div><div className="mt-1 text-lg font-bold theme-text">{totalUnrealizedPnl == null ? "Unavailable" : money(totalUnrealizedPnl)}</div></div>
            <div className="rounded-lg theme-surface-2 p-3"><div className="text-xs theme-muted">Net P&L</div><div className="mt-1 text-lg font-bold theme-text">{totalNetPnl == null ? "—" : money(totalNetPnl)}</div></div>
            <div className="rounded-lg theme-surface-2 p-3"><div className="text-xs theme-muted">Total charges</div><div className="mt-1 text-lg font-bold theme-text">{money(trades.reduce((sum,trade)=>sum+Number(trade.summary?.total_charges??0),0))}</div></div>
          </div>
          <p className="mt-2 text-[11px] theme-muted">Net P&L is shown only when every open position has a fresh backend mark. If any leg quote is stale/missing, unrealized and combined P&L remain unavailable rather than using stale prices. Historical trades created before entry-spot capture will show —.</p>
        </div>
      </section>

      <div className="space-y-3 rounded-xl border theme-border p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div><h3 className="font-semibold theme-text">Restart recovery · read only</h3><p className="text-xs theme-muted">Persisted non-terminal trades and per-leg exposure. No automatic replay and no broker orders.</p></div>
          <span className="rounded-md border theme-border px-2 py-1 text-xs theme-muted">{recoveryTrades.length} open</span>
        </div>
        {recoveryTrades.length === 0
          ? <p className="py-2 text-sm theme-muted">No persisted non-terminal paper trades for this account.</p>
          : recoveryTrades.map(trade => <div key={trade.trade_id} className="space-y-2 rounded-lg border theme-border p-3">
            <div className="flex flex-wrap items-center justify-between gap-2"><div className="font-semibold theme-text">#{trade.trade_id} · {trade.underlying} · {trade.expiry}</div><div className="text-xs theme-muted">{trade.status} · expected qty {trade.quantity}</div></div>
            <div className="flex flex-wrap gap-2 text-xs"><span className="rounded-md border theme-border px-2 py-1 theme-muted">Entry claim: {trade.entry_claim_status ?? "MISSING"}</span><span className="rounded-md border theme-border px-2 py-1 theme-muted">Claimed: {trade.entry_claimed_at ?? "—"}</span>{trade.entry_claim_completed_at && <span className="rounded-md border theme-border px-2 py-1 theme-muted">Completed: {trade.entry_claim_completed_at}</span>}</div>
            {(trade.manual_review_required || trade.automatic_replay !== false) && <div role="alert" className="rounded-lg border border-amber-500/60 bg-amber-500/10 p-3 text-sm theme-text"><strong>Manual review required.</strong> This entry may be incomplete or ambiguous. Automatic replay is disabled; inspect all four legs and persisted fills before taking any action.</div>}
            <div className="overflow-x-auto"><table className="w-full min-w-[650px] text-left text-xs"><thead className="theme-surface-2 theme-muted"><tr>{["Leg","Entry filled","Entry remaining","Exited","Open qty","Exposure"].map(label=><th key={label} className="px-2 py-2">{label}</th>)}</tr></thead><tbody>
              {trade.legs.map(leg=><tr key={leg.role} className="border-t theme-border"><td className="px-2 py-2 theme-text">{leg.role}</td><td className="px-2 py-2 theme-text">{leg.entry_filled_quantity}</td><td className="px-2 py-2 theme-text">{leg.entry_remaining_quantity}</td><td className="px-2 py-2 theme-text">{leg.exited_quantity}</td><td className="px-2 py-2 theme-text">{leg.open_position_quantity}</td><td className="px-2 py-2 theme-text">{leg.open_position_action} ({leg.signed_open_position_quantity > 0 ? "+" : ""}{leg.signed_open_position_quantity})</td></tr>)}
            </tbody></table></div>
            {trade.legs.some(leg => leg.fills.length > 0) && <details className="text-xs"><summary className="cursor-pointer theme-text">Persisted fills</summary><div className="mt-2 overflow-x-auto"><table className="w-full min-w-[600px] text-left"><thead className="theme-surface-2 theme-muted"><tr>{["Leg","Fill ID","Action","Qty","Price","Time"].map(label=><th key={label} className="px-2 py-2">{label}</th>)}</tr></thead><tbody>{trade.legs.flatMap(leg => leg.fills.map((fill, index)=><tr key={leg.role + fill.fill_id + index} className="border-t theme-border"><td className="px-2 py-2 theme-text">{leg.role}</td><td className="px-2 py-2 theme-text">{fill.fill_id}</td><td className="px-2 py-2 theme-text">{fill.action}</td><td className="px-2 py-2 theme-text">{fill.quantity}</td><td className="px-2 py-2 theme-text">{fill.fill_price}</td><td className="px-2 py-2 theme-muted">{fill.created_at}</td></tr>))}</tbody></table></div></details>}
          </div>)}
      </div>

      <div className="space-y-2 rounded-xl border theme-border p-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="font-semibold theme-text">Live scanner opportunities</h3><p className="text-xs theme-muted">Backend opportunities only · executable four-leg quotes · rows older than five seconds excluded.</p></div><button type="button" onClick={() => void loadOpportunities()} disabled={busy} className="rounded-lg border theme-border px-3 py-2 text-xs font-semibold theme-text">Refresh opportunities</button></div>
        <div className="rounded-lg border theme-border theme-surface-2 p-3 text-xs" role="status">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <strong className={opportunityStatus === "LIVE" ? "text-emerald-500" : opportunityStatus === "ERROR" ? "text-red-500" : "theme-text"}>
              {opportunityStatus === "LIVE" ? "LIVE · FRESH OPPORTUNITIES FOUND" : opportunityStatus === "EMPTY" ? "API OK · NO CURRENT QUALIFYING OPPORTUNITY" : opportunityStatus === "ERROR" ? "LIVE SCANNER API ERROR" : "CHECKING LIVE SCANNER…"}
            </strong>
            <span className="theme-muted">{lastOpportunityCheck == null ? "Not checked yet" : "Checked " + new Date(lastOpportunityCheck).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" }) + " IST"}</span>
          </div>
          {opportunityError && <p className="mt-1 text-red-500">{opportunityError}</p>}
          {opportunityStatus === "EMPTY" && <p className="mt-1 theme-muted">The backend responded successfully, but no row passed the current five-second four-leg freshness and executable-price filters. No sample opportunities are generated.</p>}
          {scannerDiagnostics?.last_scan && <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <div className="theme-muted">Last scan: <strong className="theme-text">{scannerDiagnostics.last_scan.symbol ?? "—"} · {String(scannerDiagnostics.last_scan.expiry ?? "—")}</strong></div>
            <div className="theme-muted">Outcome: <strong className="theme-text">{scannerDiagnostics.last_scan.outcome ?? "—"}</strong></div>
            <div className="theme-muted">Grouped strikes: <strong className="theme-text">{scannerDiagnostics.last_scan.grouped_strikes ?? "—"}</strong></div>
            <div className="theme-muted">Active quote buckets: <strong className="theme-text">{scannerDiagnostics.active_buckets ?? "—"}</strong></div>
          </div>}
          {scannerDiagnostics?.counts && <details className="mt-2">
            <summary className="cursor-pointer font-semibold theme-text">Scanner rejection diagnostics</summary>
            <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {[
                ["Accepted ticks", "ticks_accepted_for_assembly"],
                ["Snapshots scanned", "snapshots_scanned"],
                ["Incomplete CE/PE pairs", "missing_or_non_executable_strike_pairs"],
                ["Stale ticks rejected", "stale_timestamp"],
                ["Timestamp-skew rejects", "scan_timestamp_skew_rejected_pairs"],
                ["Liquidity rejects", "scan_liquidity_rejected_pairs"],
                ["Edge rejects", "scan_edge_rejected_directions"],
                ["Strategy rejects", "scan_backtest_rejected_directions"],
                ["Qualified directions", "scan_qualified_directions"],
              ].map(([label, key]) => <div key={key} className="rounded-md border theme-border p-2">
                <div className="theme-muted">{label}</div>
                <strong className="theme-text">{(scannerDiagnostics.counts?.[key] ?? 0).toLocaleString("en-IN")}</strong>
              </div>)}
            </div>
          </details>}
          {!scannerDiagnostics && <p className="mt-1 theme-muted">Detailed scanner diagnostics are temporarily unavailable; opportunity API status is shown separately.</p>}
        </div>
        <div className="overflow-x-auto"><table className="w-full min-w-[1250px] text-left text-xs"><thead className="theme-surface-2 theme-muted"><tr>{["Underlying","Spot Price","Expiry","Expiry Type","Weekly Opportunity","Strikes","Direction","Edge","4 leg ref prices","Bid (qty) / Ask (qty) — 4 legs","Paper trade"].map(label=><th key={label} className="px-2 py-2">{label}</th>)}</tr></thead><tbody>
          {opportunities.map((op, index)=><tr key={op.symbol + op.expiry + op.low_strike + op.high_strike + index} className="border-t theme-border">
            <td className="px-2 py-2 theme-text">{op.symbol}</td><td className="px-2 py-2 theme-text">{op.spot_price == null ? "—" : Number(op.spot_price).toLocaleString("en-IN", { maximumFractionDigits: 2 })}{op.spot_age_seconds == null ? "" : " (" + Number(op.spot_age_seconds).toFixed(2) + "s)"}</td><td className="px-2 py-2 theme-text">{op.expiry}</td><td className="px-2 py-2 theme-text"><span className={"inline-flex rounded-full px-2 py-1 text-[10px] font-bold " + (String(op.expiry_type || "").toUpperCase() === "WEEKLY" ? "bg-cyan-500/15 text-cyan-600" : String(op.expiry_type || "").toUpperCase() === "MONTHLY" ? "bg-violet-500/15 text-violet-600" : "theme-surface-2 theme-muted")}>{String(op.expiry_type || "UNKNOWN").toUpperCase()}</span></td><td className="px-2 py-2 theme-text"><span className={"inline-flex rounded-full px-2 py-1 text-[10px] font-bold " + (String(op.expiry_type || "").toUpperCase() === "WEEKLY" ? "bg-cyan-500/15 text-cyan-600" : String(op.expiry_type || "").toUpperCase() === "MONTHLY" ? "theme-surface-2 theme-muted" : "theme-surface-2 theme-muted")}>{String(op.expiry_type || "").toUpperCase() === "WEEKLY" ? "YES" : String(op.expiry_type || "").toUpperCase() === "MONTHLY" ? "NO" : "UNKNOWN"}</span></td><td className="px-2 py-2 theme-text">{op.low_strike} / {op.high_strike}</td><td className="px-2 py-2 theme-text">{op.direction}</td><td className="px-2 py-2 theme-text">{Number(op.executable_edge).toFixed(4)}</td><td className="px-2 py-2 theme-muted">LC {op.low_call_price} · HC {op.high_call_price}<br/>HP {op.high_put_price} · LP {op.low_put_price}</td><td className="px-2 py-2 theme-muted whitespace-nowrap">LC Bid ₹{op.low_call_bid ?? "—"} ({op.low_call_bid_qty ?? "—"}) · Ask ₹{op.low_call_ask ?? "—"} ({op.low_call_ask_qty ?? "—"})<br/>LP Bid ₹{op.low_put_bid ?? "—"} ({op.low_put_bid_qty ?? "—"}) · Ask ₹{op.low_put_ask ?? "—"} ({op.low_put_ask_qty ?? "—"})<br/>HC Bid ₹{op.high_call_bid ?? "—"} ({op.high_call_bid_qty ?? "—"}) · Ask ₹{op.high_call_ask ?? "—"} ({op.high_call_ask_qty ?? "—"})<br/>HP Bid ₹{op.high_put_bid ?? "—"} ({op.high_put_bid_qty ?? "—"}) · Ask ₹{op.high_put_ask ?? "—"} ({op.high_put_ask_qty ?? "—"})</td><td className="px-2 py-2"><button type="button" disabled={busy} onClick={() => void paperTradeOpportunity(op)} className="rounded-md bg-cyan-500 px-3 py-2 font-bold text-slate-950 disabled:opacity-50">Paper trade</button></td>
          </tr>)}
          {opportunities.length === 0 && <tr><td colSpan={11} className="px-3 py-5 text-center theme-muted">{opportunityStatus === "ERROR" ? "Live scanner request failed. See the diagnostic message above; zero rows do not mean there are no opportunities." : opportunityStatus === "CHECKING" ? "Checking the backend for fresh executable four-leg quotes…" : "No fresh executable live opportunities returned by the backend. No sample rows are shown."}</td></tr>}
        </tbody></table></div>
      </div>

      {selectedTrade != null && <div className="grid gap-3 md:grid-cols-2">
        <form onSubmit={exitSelectedLeg} className="space-y-3 rounded-xl border theme-border p-3">
          <h3 className="font-semibold theme-text">Close one paper leg</h3>
          <p className="text-xs theme-muted">The backend records the fresh executable quote from the live scanner; no browser-entered price is used. Closing is not blocked by low virtual balance.</p>
          <label className="block text-xs theme-muted">Leg
            <select value={exitRole} onChange={e => setExitRole(e.target.value as Role)} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text">{roles.map(role=><option key={role}>{role}</option>)}</select>
          </label>
          <div className="grid grid-cols-1 gap-2">
            <label className="text-xs theme-muted">Quantity
              <input required type="number" min="1" step="1" value={exitQuantity} onChange={e => setExitQuantity(e.target.value)} className="mt-1 w-full rounded-lg border theme-border theme-surface-2 px-3 py-2 text-sm theme-text"/>
            </label>
          </div>
          <button disabled={busy} className="rounded-lg border theme-border px-4 py-2 text-sm font-semibold theme-text disabled:opacity-50">Record paper exit</button>
        </form>
        <div className="space-y-2 rounded-xl border border-amber-500/50 p-3" role="status">
          <h3 className="font-semibold theme-text">Expiry settlement disabled</h3>
          <p className="text-xs theme-muted">Disabled until the backend verifies official underlying settlement data and calculates each option leg's intrinsic value. User-entered leg prices are not accepted as official settlement values.</p>
        </div>
      </div>}

      {ledger && <div className="space-y-2 rounded-xl border theme-border p-3">
        <div className="flex items-center justify-between gap-2"><h3 className="font-semibold theme-text">Paper ledger details</h3><button type="button" onClick={() => setLedger(null)} className="rounded-md border theme-border px-2 py-1 text-xs theme-text">Close</button></div>
        <div className="grid gap-2 sm:grid-cols-4">{["status","quantity","total_charges","realized_pnl","unrealized_pnl","net_cash_flow","fee_schedule_version"].map(key => <div key={key} className="rounded-lg theme-surface-2 p-2"><div className="text-xs theme-muted">{key.replaceAll("_"," ")}</div><div className="break-words text-sm font-semibold theme-text">{String(ledger.summary?.[key] ?? "—")}</div></div>)}</div>
        <div className="overflow-x-auto"><table className="w-full min-w-[600px] text-left text-xs"><thead className="theme-surface-2 theme-muted"><tr>{["Time","Leg","Action","Qty","Fill price","Charges","Cash delta"].map(k=><th key={k} className="px-2 py-2">{k}</th>)}</tr></thead><tbody>{(ledger.entries ?? []).map((entry: any, i: number)=><tr key={entry.fill_id + i} className="border-t theme-border"><td className="px-2 py-2 theme-text">{entry.created_at}</td><td className="px-2 py-2 theme-text">{entry.role}</td><td className="px-2 py-2 theme-text">{entry.action}</td><td className="px-2 py-2 theme-text">{entry.quantity}</td><td className="px-2 py-2 theme-text">{entry.fill_price}</td><td className="px-2 py-2 theme-text">{entry.charges}</td><td className="px-2 py-2 theme-text">{entry.cash_delta}</td></tr>)}</tbody></table></div>
      </div>}
    </section>
  );
}