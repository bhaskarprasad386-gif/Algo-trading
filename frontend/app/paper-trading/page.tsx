"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, ArrowDownToLine, ArrowUpFromLine, ShieldCheck } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";

const strategies = ["Cash-Future", "Calendar Spread", "Synthetic Arbitrage", "Box Spread"];

const instrumentsByStrategy: Record<string, string[]> = {
  "Cash-Future": ["Stock / F&O Symbol"],
  "Calendar Spread": ["F&O Symbol"],
  "Synthetic Arbitrage": ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
  "Box Spread": ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
};

const optionStrategies = new Set(["Calendar Spread", "Synthetic Arbitrage", "Box Spread"]);

export default function PaperTradingPage() {
  const [strategy, setStrategy] = useState("Cash-Future");
  const [instrument, setInstrument] = useState(instrumentsByStrategy["Cash-Future"][0]);
  const [expiry, setExpiry] = useState("");
  const [strike, setStrike] = useState("");
  const [optionType, setOptionType] = useState("CE");
  const [side, setSide] = useState("BUY");
  const [quantity, setQuantity] = useState("");

  const instruments = instrumentsByStrategy[strategy];
  const requiresOption = optionStrategies.has(strategy);

  const actionLabel = useMemo(() => {
    if (strategy === "Cash-Future") return "Cash/Future leg";
    if (strategy === "Calendar Spread") return "Spread leg";
    if (strategy === "Synthetic Arbitrage") return "Synthetic leg";
    return "Box leg";
  }, [strategy]);

  function changeStrategy(value: string) {
    setStrategy(value);
    setInstrument(instrumentsByStrategy[value][0]);
    setExpiry("");
    setStrike("");
    setOptionType("CE");
    setSide("BUY");
    setQuantity("");
  }

  return (
    <div className="space-y-5">
      <PageTitle
        eyebrow="Phase 5 • Manual Paper Trading"
        title="Manual Paper Trading"
        description="Build a strategy-specific paper order and submit it to the paper-trading workflow. Real broker orders remain OFF."
      />

      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-2xl border border-algo-border bg-algo-card p-4">
          <p className="text-xs uppercase tracking-wider text-algo-muted">Execution</p>
          <p className="mt-2 text-sm font-semibold text-algo-profit">PAPER ONLY</p>
        </div>
        <div className="rounded-2xl border border-algo-border bg-algo-card p-4">
          <p className="text-xs uppercase tracking-wider text-algo-muted">Broker Orders</p>
          <p className="mt-2 text-sm font-semibold text-algo-warning">OFF</p>
        </div>
        <div className="rounded-2xl border border-algo-border bg-algo-card p-4">
          <p className="text-xs uppercase tracking-wider text-algo-muted">Auto Adjustment</p>
          <p className="mt-2 text-sm font-semibold text-algo-muted">HOLD</p>
        </div>
      </div>

      <Card className="p-4 sm:p-6">
        <div className="flex flex-col gap-2 border-b border-algo-border pb-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-base font-semibold text-white">Create Paper Trade</h2>
            <p className="mt-1 text-xs text-algo-muted">Strategy → instrument → contract → side → quantity → paper execute</p>
          </div>
          <span className="inline-flex w-fit items-center gap-2 rounded-xl border border-algo-border bg-algo-surface px-3 py-2 text-xs font-semibold text-algo-profit">
            <ShieldCheck className="h-4 w-4" /> LIVE ORDERS OFF
          </span>
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <label className="space-y-2 text-sm">
            <span className="text-algo-muted">Strategy</span>
            <select value={strategy} onChange={(e) => changeStrategy(e.target.value)} className="min-h-11 w-full px-3">
              {strategies.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>

          <label className="space-y-2 text-sm">
            <span className="text-algo-muted">Instrument</span>
            <select value={instrument} onChange={(e) => setInstrument(e.target.value)} className="min-h-11 w-full px-3">
              {instruments.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>

          <label className="space-y-2 text-sm">
            <span className="text-algo-muted">Expiry</span>
            <input value={expiry} onChange={(e) => setExpiry(e.target.value)} placeholder="Select expiry" className="min-h-11 w-full px-3" />
          </label>

          <label className="space-y-2 text-sm">
            <span className="text-algo-muted">Strike</span>
            <input value={strike} onChange={(e) => setStrike(e.target.value.replace(/[^0-9.]/g, ""))} placeholder={requiresOption ? "e.g. 25000" : "Not required"} disabled={!requiresOption} className="min-h-11 w-full px-3 disabled:cursor-not-allowed disabled:opacity-50" />
          </label>
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <label className="space-y-2 text-sm">
            <span className="text-algo-muted">Contract</span>
            <select value={optionType} onChange={(e) => setOptionType(e.target.value)} disabled={!requiresOption} className="min-h-11 w-full px-3 disabled:cursor-not-allowed disabled:opacity-50">
              <option>CE</option>
              <option>PE</option>
            </select>
          </label>

          <label className="space-y-2 text-sm">
            <span className="text-algo-muted">Side</span>
            <select value={side} onChange={(e) => setSide(e.target.value)} className="min-h-11 w-full px-3">
              <option>BUY</option>
              <option>SELL</option>
            </select>
          </label>

          <label className="space-y-2 text-sm">
            <span className="text-algo-muted">Quantity</span>
            <input value={quantity} onChange={(e) => setQuantity(e.target.value.replace(/[^0-9]/g, ""))} inputMode="numeric" placeholder="Enter quantity" className="min-h-11 w-full px-3" />
          </label>

          <div className="flex items-end">
            <button type="button" disabled className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-algo-primary px-4 text-sm font-semibold text-black opacity-60">
              <ShieldCheck className="h-4 w-4" /> Paper Execute
            </button>
          </div>
        </div>

        <div className="mt-5 rounded-xl border border-algo-border bg-algo-surface p-4 text-xs leading-5 text-algo-muted">
          <p className="font-semibold text-white">{actionLabel}</p>
          <p className="mt-1">Paper execution endpoint is not connected yet. This UI does not send Angel One or any live broker order.</p>
        </div>
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card className="p-5">
          <h2 className="text-base font-semibold text-white">Order Preview</h2>
          <div className="mt-4 space-y-3 text-sm">
            <div className="flex justify-between gap-4"><span className="text-algo-muted">Strategy</span><span className="font-medium text-white">{strategy}</span></div>
            <div className="flex justify-between gap-4"><span className="text-algo-muted">Instrument</span><span className="font-medium text-white">{instrument}</span></div>
            <div className="flex justify-between gap-4"><span className="text-algo-muted">Expiry / Strike</span><span className="font-medium text-white">{expiry || "—"} / {requiresOption ? strike || "—" : "—"}</span></div>
            <div className="flex justify-between gap-4"><span className="text-algo-muted">Contract / Side</span><span className="font-medium text-white">{requiresOption ? optionType : "Future"} / {side}</span></div>
            <div className="flex justify-between gap-4"><span className="text-algo-muted">Quantity</span><span className="font-medium text-white">{quantity || "—"}</span></div>
          </div>
        </Card>

        <Card className="p-5">
          <h2 className="text-base font-semibold text-white">Paper Safety Boundary</h2>
          <div className="mt-4 space-y-3 text-sm text-algo-muted">
            <p className="flex gap-3"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-algo-profit" /> Manual execution is paper-only.</p>
            <p className="flex gap-3"><ArrowUpFromLine className="mt-0.5 h-4 w-4 shrink-0 text-algo-primary" /> BUY/SELL is recorded only after backend paper execution is connected.</p>
            <p className="flex gap-3"><ArrowDownToLine className="mt-0.5 h-4 w-4 shrink-0 text-algo-warning" /> No Angel One order is sent from this page.</p>
            <p className="flex gap-3"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-algo-warning" /> Auto-adjustment remains HOLD.</p>
          </div>
        </Card>
      </div>
    </div>
  );
}
