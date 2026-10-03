"use client";

import { useEffect, useMemo, useState } from "react";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

type Trade = {
  id: number;
  strategy?: string;
  symbol?: string;
  direction?: string;
  entry_edge?: number;
  current_edge?: number;
  realized_pnl?: number;
  capital_used?: number;
  closed_at?: string | null;
  exit_reason?: string | null;
  status?: string;
};

const money = (value: number | undefined) =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(value ?? 0);

const dateOnly = (value: string | null | undefined) => (value ? value.slice(0, 10) : "");

export default function HistoryPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [strategy, setStrategy] = useState("ALL");
  const [query, setQuery] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [selected, setSelected] = useState<Trade | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(`${base}/api/v1/live-paper/status`, { cache: "no-store" });
      if (!response.ok) throw new Error("History data unavailable");
      const body = await response.json();
      setTrades(Array.isArray(body.completed) ? body.completed : []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load history");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return trades.filter((trade) => {
      const d = dateOnly(trade.closed_at);
      return (
        (strategy === "ALL" || trade.strategy === strategy) &&
        (!q || [trade.symbol, trade.strategy, trade.exit_reason, trade.direction].some((v) => String(v ?? "").toLowerCase().includes(q))) &&
        (!from || !d || d >= from) &&
        (!to || !d || d <= to)
      );
    });
  }, [trades, strategy, query, from, to]);

  const realized = filtered.reduce((sum, trade) => sum + Number(trade.realized_pnl || 0), 0);
  const profitable = filtered.filter((trade) => Number(trade.realized_pnl || 0) > 0).length;

  return (
    <>
      <PageTitle eyebrow="Workspace" title="History" description="Completed paper-trade history with filters and details. Replay is permanently excluded." />

      <div className="grid gap-3 sm:grid-cols-3">
        <Card className="p-4"><p className="text-xs text-[#64748B]">Completed</p><p className="mt-1 text-2xl font-semibold text-[#0F172A]">{filtered.length}</p></Card>
        <Card className="p-4"><p className="text-xs text-[#64748B]">Realized P&L</p><p className="mt-1 text-2xl font-semibold text-emerald-600">{money(realized)}</p></Card>
        <Card className="p-4"><p className="text-xs text-[#64748B]">Profitable</p><p className="mt-1 text-2xl font-semibold text-[#0F172A]">{profitable}</p></Card>
      </div>

      <Card className="mt-4 p-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)} className="rounded-xl border border-[#D7E0E8] bg-white px-3 py-2 text-sm text-[#0F172A]">
            <option value="ALL">All strategies</option>
            <option value="cash-future">Cash-Future</option>
            <option value="calendar-spread">Calendar Spread</option>
            <option value="synthetic-future-cash-carry">Synthetic Arbitrage</option>
            <option value="box-spread">Box Spread</option>
          </select>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search symbol, strategy, exit..." className="rounded-xl border border-[#D7E0E8] bg-white px-3 py-2 text-sm text-[#0F172A] placeholder:text-[#64748B]" />
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="rounded-xl border border-[#D7E0E8] bg-white px-3 py-2 text-sm text-[#0F172A]" />
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="rounded-xl border border-[#D7E0E8] bg-white px-3 py-2 text-sm text-[#0F172A]" />
          <button onClick={() => void load()} className="rounded-xl border border-[#D7E0E8] bg-white px-3 py-2 text-sm font-medium text-[#0F172A]">↻ Refresh</button>
        </div>
      </Card>

      {error ? <Card className="mt-4 p-5 text-sm text-red-600">{error}</Card> : null}
      <Card className="mt-4 overflow-hidden">
        {loading ? <div className="p-8 text-sm text-[#64748B]">Loading history…</div> : filtered.length === 0 ? (
          <div className="p-8 text-sm text-[#64748B]">No completed paper trades match the selected filters.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-[900px] w-full text-left text-sm">
              <thead className="border-b border-[#D7E0E8] text-xs uppercase tracking-wide text-[#64748B]">
                <tr>{["Strategy","Symbol","Direction","Entry","Exit","Realized P&L","Exit reason","Closed",""].map((h) => <th key={h} className="px-4 py-3 font-medium">{h}</th>)}</tr>
              </thead>
              <tbody>
                {filtered.map((trade) => (
                  <tr key={trade.id} className="border-b border-[#D7E0E8]/70 last:border-0">
                    <td className="px-4 py-3 text-[#0F172A]">{trade.strategy || "—"}</td>
                    <td className="px-4 py-3 font-medium text-[#0F172A]">{trade.symbol || "—"}</td>
                    <td className="px-4 py-3 text-[#64748B]">{trade.direction || "—"}</td>
                    <td className="px-4 py-3">{trade.entry_edge ?? "—"}</td>
                    <td className="px-4 py-3">{trade.current_edge ?? "—"}</td>
                    <td className={`px-4 py-3 font-semibold ${Number(trade.realized_pnl || 0) >= 0 ? "text-emerald-600" : "text-red-600"}`}>{money(trade.realized_pnl)}</td>
                    <td className="px-4 py-3 text-[#64748B]">{trade.exit_reason || "—"}</td>
                    <td className="px-4 py-3 text-[#64748B]">{trade.closed_at ? new Date(trade.closed_at).toLocaleString("en-IN") : "—"}</td>
                    <td className="px-4 py-3"><button onClick={() => setSelected(trade)} className="rounded-lg border border-[#D7E0E8] px-3 py-1.5 text-xs text-[#0F172A]">View</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {selected ? (
        <Card className="mt-4 p-5">
          <div className="flex items-center justify-between gap-3">
            <div><p className="text-xs uppercase tracking-wide text-sky-700">Trade Detail</p><h2 className="mt-1 text-lg font-semibold text-[#0F172A]">{selected.symbol || "Trade"} · #{selected.id}</h2></div>
            <button onClick={() => setSelected(null)} className="rounded-lg border border-[#D7E0E8] px-3 py-1.5 text-xs text-[#0F172A]">Close</button>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
            <div><span className="text-[#64748B]">Strategy</span><p className="text-[#0F172A]">{selected.strategy || "—"}</p></div>
            <div><span className="text-[#64748B]">Direction</span><p className="text-[#0F172A]">{selected.direction || "—"}</p></div>
            <div><span className="text-[#64748B]">Entry Edge</span><p className="text-[#0F172A]">{selected.entry_edge ?? "—"}</p></div>
            <div><span className="text-[#64748B]">Exit Edge</span><p className="text-[#0F172A]">{selected.current_edge ?? "—"}</p></div>
            <div><span className="text-[#64748B]">Capital Used</span><p className="text-[#0F172A]">{money(selected.capital_used)}</p></div>
            <div><span className="text-[#64748B]">Realized P&L</span><p className="text-emerald-600">{money(selected.realized_pnl)}</p></div>
            <div><span className="text-[#64748B]">Exit Reason</span><p className="text-[#0F172A]">{selected.exit_reason || "—"}</p></div>
            <div><span className="text-[#64748B]">Closed</span><p className="text-[#0F172A]">{selected.closed_at ? new Date(selected.closed_at).toLocaleString("en-IN") : "—"}</p></div>
          </div>
          <p className="mt-5 text-xs text-[#64748B]">Paper-only history. Broker/live orders remain OFF. Replay controls are intentionally not available.</p>
        </Card>
      ) : null}
    </>
  );
}
