"use client";

import { useEffect, useState } from "react";
import { Activity, Bot, CircleStop, ShieldCheck, WalletCards, Zap } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

type PaperConfig = {
  enabled?: boolean;
  paper_amount?: number;
  emergency_stop?: boolean;
};

type Position = {
  id?: number | string;
  strategy?: string;
  symbol?: string;
  entry_price?: number;
  current_price?: number;
  pnl?: number;
  status?: string;
};

const money = (value: number | null) =>
  value === null ? "—" : `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

export default function AutoPaperPage() {
  const [paper, setPaper] = useState<PaperConfig>({});
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const base = appConfig.apiBaseUrl.replace(/\/$/, "");

  const load = async () => {
    setLoading(true);
    try {
      const [configResponse, positionsResponse] = await Promise.all([
        fetch(`${base}/api/v1/alerts/config`, { cache: "no-store" }),
        fetch(`${base}/api/v1/auto/live-paper/ongoing`, { cache: "no-store" }),
      ]);
      if (!configResponse.ok) throw new Error(`Config HTTP ${configResponse.status}`);
      const config = await configResponse.json();
      setPaper(config?.paper ?? {});
      if (positionsResponse.ok) {
        const data = await positionsResponse.json();
        const rows = Array.isArray(data) ? data : (data?.positions ?? data?.items ?? []);
        setPositions(Array.isArray(rows) ? rows : []);
      } else {
        setPositions([]);
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Backend unavailable");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const toggle = async () => {
    if (saving || loading) return;
    const next = !Boolean(paper.enabled);
    setSaving(true);
    setError(null);
    try {
      const response = await fetch(`${base}/api/v1/alerts/paper`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: next,
          paper_amount: Number(paper.paper_amount ?? 10000000),
          emergency_stop: Boolean(paper.emergency_stop),
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || `HTTP ${response.status}`);
      setPaper(data?.paper ?? {});
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to update paper auto-execute");
    } finally {
      setSaving(false);
    }
  };

  const totalCapital = Number.isFinite(Number(paper.paper_amount)) ? Number(paper.paper_amount) : null;
  const usedCapital = positions.length ? null : 0;
  const availableCapital = totalCapital === null || usedCapital === null ? null : totalCapital - usedCapital;

  return (
    <div className="space-y-5">
      <PageTitle
        eyebrow="Phase 6 • Automation"
        title="Auto Paper Trading"
        description="Automatically convert qualifying scanner alerts into paper positions. Broker orders remain locked OFF."
      />

      <div className="grid gap-3 sm:grid-cols-3">
        <Card className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-600">Auto Paper</p>
          <p className={paper.enabled ? "mt-2 text-xl font-bold text-emerald-700" : "mt-2 text-xl font-bold text-slate-600"}>
            {loading ? "Checking…" : paper.enabled ? "ON" : "OFF"}
          </p>
          <p className="mt-1 text-xs text-slate-600">Backend global setting</p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-600">Paper Mode</p>
          <p className="mt-2 text-xl font-bold text-emerald-700">ACTIVE</p>
          <p className="mt-1 text-xs text-slate-600">No broker order path</p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-600">Live Broker</p>
          <p className="mt-2 text-xl font-bold text-red-700">OFF</p>
          <p className="mt-1 text-xs text-slate-600">Safety gate locked</p>
        </Card>
      </div>

      <Card className="p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
          <div className="flex flex-1 items-start gap-3">
            <Bot className="mt-0.5 h-6 w-6 text-sky-700" />
            <div>
              <h2 className="text-base font-semibold text-slate-900">Global Auto Execute</h2>
              <p className="mt-1 text-sm text-slate-600">
                ON means qualifying scanner alerts can create paper trades. OFF means alerts remain alerts only.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void toggle()}
            disabled={loading || saving}
            className={paper.enabled
              ? "min-h-11 rounded-xl border border-emerald-300 bg-emerald-50 px-5 text-sm font-bold text-emerald-700 disabled:opacity-60"
              : "min-h-11 rounded-xl border border-slate-200 bg-white px-5 text-sm font-bold text-slate-900 disabled:opacity-60"}
          >
            {saving ? "Saving…" : paper.enabled ? "Turn OFF" : "Turn ON"}
          </button>
        </div>
        {paper.emergency_stop && (
          <div className="mt-4 flex items-center gap-2 rounded-xl border border-red-300 bg-red-50 p-3 text-sm text-red-700">
            <CircleStop className="h-4 w-4" /> Emergency stop is active.
          </div>
        )}
        {error && <p className="mt-3 text-xs text-red-700">Auto paper: {error}</p>}
      </Card>

      <Card className="p-5">
        <div className="mb-4 flex items-center gap-2">
          <WalletCards className="h-5 w-5 text-sky-700" />
          <div>
            <h2 className="text-base font-semibold text-slate-900">Paper Capital</h2>
            <p className="text-sm text-slate-600">Uses the backend-configured paper amount.</p>
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs uppercase tracking-wider text-slate-600">Total Capital</p>
            <p className="mt-2 text-lg font-bold text-slate-900">{money(totalCapital)}</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs uppercase tracking-wider text-slate-600">Used Capital</p>
            <p className="mt-2 text-lg font-bold text-slate-900">{money(usedCapital)}</p>
            <p className="mt-1 text-xs text-slate-600">{positions.length ? "Position allocation endpoint pending" : "No active positions reported"}</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <p className="text-xs uppercase tracking-wider text-slate-600">Available</p>
            <p className="mt-2 text-lg font-bold text-slate-900">{money(availableCapital)}</p>
          </div>
        </div>
      </Card>

      <Card className="p-5">
        <div className="mb-4 flex items-center gap-2">
          <Zap className="h-5 w-5 text-amber-700" />
          <div>
            <h2 className="text-base font-semibold text-slate-900">Auto Strategies</h2>
            <p className="text-sm text-slate-600">Scanner-driven paper automation. Custom Strategy remains independent.</p>
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {["Cash-Future", "Calendar Spread", "Synthetic Arbitrage", "Box Spread"].map((name) => (
            <div key={name} className="rounded-xl border border-slate-200 bg-white p-4">
              <p className="text-sm font-semibold text-slate-900">{name}</p>
              <p className="mt-2 text-xs text-slate-600">{paper.enabled ? "Global auto setting applies" : "Waiting for Auto Paper ON"}</p>
            </div>
          ))}
        </div>
      </Card>

      <Card className="p-5">
        <div className="mb-4 flex items-center gap-2">
          <Activity className="h-5 w-5 text-sky-700" />
          <div>
            <h2 className="text-base font-semibold text-slate-900">Ongoing Auto Paper Positions</h2>
            <p className="text-sm text-slate-600">Only backend-reported positions are shown.</p>
          </div>
        </div>
        {positions.length === 0 ? (
          <div className="rounded-xl border border-slate-200 bg-white p-6 text-center text-sm text-slate-600">
            {loading ? "Loading positions…" : "No ongoing auto paper positions reported."}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-sm">
              <thead className="text-xs uppercase tracking-wider text-slate-600">
                <tr><th className="px-3 py-3">Strategy</th><th className="px-3 py-3">Symbol</th><th className="px-3 py-3">Entry</th><th className="px-3 py-3">Current</th><th className="px-3 py-3">P&L</th><th className="px-3 py-3">Status</th></tr>
              </thead>
              <tbody>
                {positions.map((position, index) => (
                  <tr key={position.id ?? index} className="border-t border-slate-200">
                    <td className="px-3 py-3 text-slate-900">{position.strategy ?? "—"}</td>
                    <td className="px-3 py-3 text-slate-900">{position.symbol ?? "—"}</td>
                    <td className="px-3 py-3">{money(Number.isFinite(Number(position.entry_price)) ? Number(position.entry_price) : null)}</td>
                    <td className="px-3 py-3">{money(Number.isFinite(Number(position.current_price)) ? Number(position.current_price) : null)}</td>
                    <td className="px-3 py-3">{money(Number.isFinite(Number(position.pnl)) ? Number(position.pnl) : null)}</td>
                    <td className="px-3 py-3 text-slate-600">{position.status ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card className="p-5">
        <div className="mb-4 flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-emerald-700" />
          <h2 className="text-base font-semibold text-slate-900">Execution Safety</h2>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Scanner alert", "Qualifying alert"],
            ["Auto gate", "Backend setting"],
            ["Execution", "Paper only"],
            ["Broker", "Orders OFF"],
          ].map(([label, value]) => (
            <div key={label} className="rounded-xl border border-slate-200 bg-white p-4">
              <p className="text-xs uppercase tracking-wider text-slate-600">{label}</p>
              <p className="mt-2 text-sm font-semibold text-slate-900">{value}</p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
