"use client";

import { useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpFromLine,
  CircleDollarSign,
  FileText,
  ShieldCheck,
  SlidersHorizontal,
  WalletCards,
} from "lucide-react";
import { Card, PageTitle } from "@/components/ui";

const strategyModes = [
  "Cash-Future",
  "Calendar Spread",
  "Synthetic Arbitrage",
  "Box Spread",
  "Custom Strategy",
];

const strategyInstruments: Record<string, string[]> = {
  "Cash-Future": ["F&O Stock / Symbol"],
  "Calendar Spread": ["F&O Symbol"],
  "Synthetic Arbitrage": ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
  "Box Spread": ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"],
};

const optionStrategies = new Set([
  "Calendar Spread",
  "Synthetic Arbitrage",
  "Box Spread",
]);

const customSegments = ["F&O Stocks — Cash", "F&O Stocks — Future", "F&O Stocks — Option"];
const customActions = ["BUY", "SELL"];
const customContracts = ["CASH", "FUTURE", "CE", "PE"];

export default function PaperTradingPage() {
  const [strategy, setStrategy] = useState("Cash-Future");
  const [instrument, setInstrument] = useState(strategyInstruments["Cash-Future"][0]);
  const [expiry, setExpiry] = useState("");
  const [strike, setStrike] = useState("");
  const [optionType, setOptionType] = useState("CE");
  const [side, setSide] = useState("BUY");
  const [quantity, setQuantity] = useState("");

  const [customName, setCustomName] = useState("");
  const [customSegment, setCustomSegment] = useState("F&O Stocks — Cash");
  const [customSymbol, setCustomSymbol] = useState("");
  const [customExpiry, setCustomExpiry] = useState("");
  const [customStrike, setCustomStrike] = useState("");
  const [customContract, setCustomContract] = useState("CASH");
  const [customSide, setCustomSide] = useState("BUY");
  const [customQuantity, setCustomQuantity] = useState("");
  const [customOrderType, setCustomOrderType] = useState("MARKET");

  const isCustom = strategy === "Custom Strategy";
  const requiresOption = optionStrategies.has(strategy);
  const customNeedsDerivativeFields = customContract !== "CASH";

  const instruments = strategyInstruments[strategy] ?? [];

  const actionLabel = useMemo(() => {
    if (isCustom) return "Independent custom strategy";
    if (strategy === "Cash-Future") return "Cash/Future leg";
    if (strategy === "Calendar Spread") return "Spread leg";
    if (strategy === "Synthetic Arbitrage") return "Synthetic leg";
    return "Box leg";
  }, [isCustom, strategy]);

  function changeStrategy(value: string) {
    setStrategy(value);
    if (value !== "Custom Strategy") {
      setInstrument(strategyInstruments[value][0]);
      setExpiry("");
      setStrike("");
      setOptionType("CE");
      setSide("BUY");
      setQuantity("");
    }
  }

  function changeCustomSegment(value: string) {
    setCustomSegment(value);
    if (value.includes("Cash")) {
      setCustomContract("CASH");
      setCustomExpiry("");
      setCustomStrike("");
    } else if (value.includes("Future")) {
      setCustomContract("FUTURE");
      setCustomStrike("");
    } else {
      setCustomContract("CE");
    }
  }

  const previewStrategy = isCustom ? customName || "Custom Strategy" : strategy;
  const previewInstrument = isCustom ? customSymbol || "F&O stock — select from live eligible list" : instrument;
  const previewExpiry = isCustom ? customExpiry : expiry;
  const previewStrike = isCustom ? customStrike : requiresOption ? strike : "";
  const previewContract = isCustom ? customContract : requiresOption ? optionType : "FUTURE";
  const previewSide = isCustom ? customSide : side;
  const previewQuantity = isCustom ? customQuantity : quantity;

  return (
    <div className="space-y-5">
      <PageTitle
        eyebrow="Phase 5 • Manual Paper Trading"
        title="Manual Paper Trading"
        description="Trade any supported strategy independently in the paper ledger. Custom Strategy is not tied to the scanner and uses only live eligible instruments when connected."
      />

      <div className="grid gap-3 sm:grid-cols-4">
        <div className="rounded-2xl border theme-border theme-surface p-4">
          <p className="text-xs uppercase tracking-wider theme-muted">Execution</p>
          <p className="mt-2 text-sm font-semibold theme-success">PAPER ONLY</p>
        </div>
        <div className="rounded-2xl border theme-border theme-surface p-4">
          <p className="text-xs uppercase tracking-wider theme-muted">Live Feed</p>
          <p className="mt-2 text-sm font-semibold theme-muted">BACKEND PAPER ENDPOINT PENDING</p>
        </div>
        <div className="rounded-2xl border theme-border theme-surface p-4">
          <p className="text-xs uppercase tracking-wider theme-muted">Broker Orders</p>
          <p className="mt-2 text-sm font-semibold theme-warning">OFF</p>
        </div>
        <div className="rounded-2xl border theme-border theme-surface p-4">
          <p className="text-xs uppercase tracking-wider theme-muted">Auto Adjustment</p>
          <p className="mt-2 text-sm font-semibold theme-muted">HOLD</p>
        </div>
      </div>

      <Card className="p-4 sm:p-6">
        <div className="flex flex-col gap-2 border-b border-slate-200 pb-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="flex items-center gap-2 text-base font-semibold theme-text"><FileText className="h-4 w-4 theme-accent" /> Create Paper Trade</h2>
            <p className="mt-1 text-xs theme-muted">
              Choose a built-in strategy or run a completely independent Custom Strategy.
            </p>
          </div>
          <span className="inline-flex w-fit items-center gap-2 rounded-xl border theme-border theme-surface-2 px-3 py-2 text-xs font-semibold theme-success">
            <ShieldCheck className="h-4 w-4" /> LIVE ORDERS OFF
          </span>
        </div>

        <div className="mt-5 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <label className="space-y-2 text-sm">
            <span className="theme-muted">Strategy</span>
            <select value={strategy} onChange={(e) => changeStrategy(e.target.value)} className="min-h-11 w-full px-3">
              {strategyModes.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>

          {isCustom ? (
            <label className="space-y-2 text-sm">
              <span className="theme-muted">Custom Strategy Name</span>
              <input value={customName} onChange={(e) => setCustomName(e.target.value)} placeholder="e.g. My Cash Momentum" className="min-h-11 w-full px-3" />
            </label>
          ) : (
            <label className="space-y-2 text-sm">
              <span className="theme-muted">Instrument</span>
              <select value={instrument} onChange={(e) => setInstrument(e.target.value)} className="min-h-11 w-full px-3">
                {instruments.map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
          )}

          {isCustom ? (
            <label className="space-y-2 text-sm">
              <span className="theme-muted">Segment</span>
              <select value={customSegment} onChange={(e) => changeCustomSegment(e.target.value)} className="min-h-11 w-full px-3">
                {customSegments.map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
          ) : (
            <label className="space-y-2 text-sm">
              <span className="theme-muted">Expiry</span>
              <input value={expiry} onChange={(e) => setExpiry(e.target.value)} placeholder="Select expiry" className="min-h-11 w-full px-3" />
            </label>
          )}

          <label className="space-y-2 text-sm">
            <span className="theme-muted">{isCustom ? "Contract" : "Strike"}</span>
            {isCustom ? (
              <select value={customContract} onChange={(e) => setCustomContract(e.target.value)} className="min-h-11 w-full px-3">
                {customContracts.filter((item) => customSegment.includes("Cash") ? item === "CASH" : customSegment.includes("Future") ? item === "FUTURE" : item === "CE" || item === "PE").map((item) => (
                  <option key={item}>{item}</option>
                ))}
              </select>
            ) : (
              <input value={strike} onChange={(e) => setStrike(e.target.value.replace(/[^0-9.]/g, ""))} placeholder={requiresOption ? "e.g. 25000" : "Not required"} disabled={!requiresOption} className="min-h-11 w-full px-3 disabled:cursor-not-allowed disabled:opacity-50" />
            )}
          </label>
        </div>

        {isCustom ? (
          <>
            <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <label className="space-y-2 text-sm">
                <span className="theme-muted">F&O Stock Symbol</span>
                <input value={customSymbol} onChange={(e) => setCustomSymbol(e.target.value.toUpperCase())} placeholder="Live F&O eligible stock list" className="min-h-11 w-full px-3" />
              </label>
              <label className="space-y-2 text-sm">
                <span className="theme-muted">Expiry</span>
                <input value={customExpiry} onChange={(e) => setCustomExpiry(e.target.value)} placeholder={customContract === "CASH" ? "Not required" : "Select expiry"} disabled={!customNeedsDerivativeFields} className="min-h-11 w-full px-3 disabled:cursor-not-allowed disabled:opacity-50" />
              </label>
              <label className="space-y-2 text-sm">
                <span className="theme-muted">Strike</span>
                <input value={customStrike} onChange={(e) => setCustomStrike(e.target.value.replace(/[^0-9.]/g, ""))} placeholder={customContract === "CASH" || customContract === "FUTURE" ? "Not required" : "e.g. 25000"} disabled={customContract === "CASH" || customContract === "FUTURE"} className="min-h-11 w-full px-3 disabled:cursor-not-allowed disabled:opacity-50" />
              </label>
              <label className="space-y-2 text-sm">
                <span className="theme-muted">Side</span>
                <select value={customSide} onChange={(e) => setCustomSide(e.target.value)} className="min-h-11 w-full px-3">
                  {customActions.map((item) => <option key={item}>{item}</option>)}
                </select>
              </label>
            </div>

            <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              <label className="space-y-2 text-sm">
                <span className="theme-muted">Quantity</span>
                <input value={customQuantity} onChange={(e) => setCustomQuantity(e.target.value.replace(/[^0-9]/g, ""))} inputMode="numeric" placeholder="Enter quantity" className="min-h-11 w-full px-3" />
              </label>
              <label className="space-y-2 text-sm">
                <span className="theme-muted">Order Type</span>
                <select value={customOrderType} onChange={(e) => setCustomOrderType(e.target.value)} className="min-h-11 w-full px-3">
                  <option>MARKET</option>
                  <option>LIMIT</option>
                </select>
              </label>
              <div className="flex items-end">
                <button type="button" disabled className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-algo-primary px-4 text-sm font-semibold text-black opacity-60">
                  <ShieldCheck className="h-4 w-4" /> Paper Execute Custom Trade
                </button>
              </div>
            </div>

            <div className="mt-5 rounded-xl border theme-border theme-accent-bg p-4 text-xs leading-5 theme-muted">
              <p className="flex items-center gap-2 font-semibold theme-text"><CircleDollarSign className="h-4 w-4 theme-accent" /> Independent Custom Strategy</p>
              <p className="mt-1">
                This strategy has its own name, symbol, side, quantity and order settings. It does not depend on Cash-Future, Calendar, Synthetic or Box scanner signals. For Cash, the backend will populate only currently eligible F&O stocks from the live contract universe; it will not show arbitrary non-F&O stocks.
              </p>
            </div>
          </>
        ) : (
          <>
            <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <label className="space-y-2 text-sm">
                <span className="theme-muted">Contract</span>
                <select value={optionType} onChange={(e) => setOptionType(e.target.value)} disabled={!requiresOption} className="min-h-11 w-full px-3 disabled:cursor-not-allowed disabled:opacity-50">
                  <option>CE</option>
                  <option>PE</option>
                </select>
              </label>
              <label className="space-y-2 text-sm">
                <span className="theme-muted">Side</span>
                <select value={side} onChange={(e) => setSide(e.target.value)} className="min-h-11 w-full px-3">
                  <option>BUY</option>
                  <option>SELL</option>
                </select>
              </label>
              <label className="space-y-2 text-sm">
                <span className="theme-muted">Quantity</span>
                <input value={quantity} onChange={(e) => setQuantity(e.target.value.replace(/[^0-9]/g, ""))} inputMode="numeric" placeholder="Enter quantity" className="min-h-11 w-full px-3" />
              </label>
              <div className="flex items-end">
                <button type="button" disabled className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-xl bg-algo-primary px-4 text-sm font-semibold text-black opacity-60">
                  <ShieldCheck className="h-4 w-4" /> Paper Execute
                </button>
              </div>
            </div>
          </>
        )}

        <div className="mt-5 rounded-xl border theme-border theme-surface-2 p-4 text-xs leading-5 theme-muted">
          <p className="font-semibold theme-text">{actionLabel}</p>
          <p className="mt-1">Paper execution endpoint is pending. No Angel One or live broker order is sent from this UI.</p>
        </div>
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card className="p-5">
          <h2 className="flex items-center gap-2 text-base font-semibold theme-text"><WalletCards className="h-4 w-4 theme-accent" /> Order Preview</h2>
          <div className="mt-4 space-y-3 text-sm">
            <div className="flex justify-between gap-4"><span className="theme-muted">Strategy</span><span className="font-medium theme-text">{previewStrategy}</span></div>
            <div className="flex justify-between gap-4"><span className="theme-muted">Instrument</span><span className="max-w-[65%] text-right font-medium theme-text">{previewInstrument}</span></div>
            <div className="flex justify-between gap-4"><span className="theme-muted">Expiry / Strike</span><span className="font-medium theme-text">{previewExpiry || "—"} / {previewStrike || "—"}</span></div>
            <div className="flex justify-between gap-4"><span className="theme-muted">Contract / Side</span><span className="font-medium theme-text">{previewContract} / {previewSide}</span></div>
            <div className="flex justify-between gap-4"><span className="theme-muted">Quantity</span><span className="font-medium theme-text">{previewQuantity || "—"}</span></div>
            {isCustom && <div className="flex justify-between gap-4"><span className="theme-muted">Order Type</span><span className="font-medium theme-text">{customOrderType}</span></div>}
          </div>
        </Card>

        <Card className="p-5">
          <h2 className="flex items-center gap-2 text-base font-semibold theme-text"><ShieldCheck className="h-4 w-4 theme-success" /> Paper Safety Boundary</h2>
          <div className="mt-4 space-y-3 text-sm theme-muted">
            <p className="flex gap-3"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 theme-success" /> Every manual trade is recorded in the paper ledger only.</p>
            <p className="flex gap-3"><ArrowUpFromLine className="mt-0.5 h-4 w-4 shrink-0 theme-accent" /> BUY/SELL will use live executable data when the backend paper endpoint is connected.</p>
            <p className="flex gap-3"><ArrowDownToLine className="mt-0.5 h-4 w-4 shrink-0 theme-warning" /> No Angel One order is sent from this page.</p>
            <p className="flex gap-3"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 theme-warning" /> Auto-adjustment remains HOLD and does not control Custom Strategy.</p>
          </div>
        </Card>
      </div>
    </div>
  );
}
