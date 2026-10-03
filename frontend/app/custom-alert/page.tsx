"use client";

import { useState } from "react";
import { Bell, Bot, Mail, MessageSquare, ShieldCheck, Smartphone } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";

const scanners = [
  "Cash-Future",
  "Calendar Spread",
  "Synthetic Arbitrage",
  "Box Spread",
  "Debit Strategy",
  "Credit Strategy",
];

const metricsByScanner: Record<string, string[]> = {
  "Cash-Future": ["Gap", "Gross Profit", "Net Profit", "Volume / OI"],
  "Calendar Spread": ["Gap", "Gross Profit", "Net Profit", "Volume / OI"],
  "Synthetic Arbitrage": ["Gap", "Gross Profit", "Net Profit", "IV / Premium"],
  "Box Spread": ["Gross Profit", "Net Profit", "Spread Value"],
  "Debit Strategy": ["Gross Profit", "Net Profit", "Premium", "ROI"],
  "Credit Strategy": ["Gross Profit", "Net Profit", "Premium", "ROI"],
};

export default function CustomAlertPage() {
  const [mobile, setMobile] = useState("");
  const [sms, setSms] = useState(true);
  const [app, setApp] = useState(true);
  const [email, setEmail] = useState(false);
  const [paperAutoExecute, setPaperAutoExecute] = useState(false);
  const [scanner, setScanner] = useState("Cash-Future");
  const [metric, setMetric] = useState("Gap");
  const metrics = metricsByScanner[scanner];

  return (
    <div className="space-y-5">
      <PageTitle eyebrow="Phase 4 • Custom Alert" title="Custom Alert" description="Apply an alert across the selected scanner and trigger it from the strategy metric you choose." />

      <Card className="p-5">
        <div className="mb-5">
          <h2 className="text-base font-semibold text-white">Create Custom Alert</h2>
          <p className="mt-1 text-sm text-algo-muted">The alert monitors the complete selected scanner, not one manually entered symbol.</p>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="space-y-2 text-sm">
            <span className="font-medium text-white">Alert Name</span>
            <input className="min-h-11 w-full px-3" placeholder="e.g. Cash-Future Gross Profit" />
          </label>

          <label className="space-y-2 text-sm">
            <span className="font-medium text-white">Scanner / Strategy</span>
            <select className="min-h-11 w-full px-3" value={scanner} onChange={(event) => {
              const next = event.target.value;
              setScanner(next);
              setMetric(metricsByScanner[next][0]);
            }}>
              {scanners.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>

          <label className="space-y-2 text-sm">
            <span className="font-medium text-white">Metric</span>
            <select className="min-h-11 w-full px-3" value={metric} onChange={(event) => setMetric(event.target.value)}>
              {metrics.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>

          <div className="grid grid-cols-[1fr_1.4fr] gap-3">
            <label className="space-y-2 text-sm">
              <span className="font-medium text-white">Operator</span>
              <select className="min-h-11 w-full px-3" defaultValue=">=">
                <option>&gt;=</option><option>&gt;</option><option>&lt;=</option><option>&lt;</option><option>=</option>
              </select>
            </label>
            <label className="space-y-2 text-sm">
              <span className="font-medium text-white">Value</span>
              <input className="min-h-11 w-full px-3" inputMode="decimal" placeholder={metric === "Gross Profit" || metric === "Net Profit" ? "₹ 5,000" : "1.00%"} />
            </label>
          </div>
        </div>

        <div className="mt-4 rounded-xl border border-algo-border bg-algo-surface p-4 text-sm">
          <p className="font-semibold text-white">Scanner-wide condition</p>
          <p className="mt-1 text-algo-muted">
            Alert when <span className="text-white">{scanner}</span> scanner finds an eligible opportunity where <span className="text-white">{metric}</span> meets the configured value.
          </p>
        </div>
      </Card>

      <Card className="p-5">
        <div className="mb-4 flex items-center gap-2">
          <Bell className="h-5 w-5 text-algo-primary" />
          <div><h2 className="text-base font-semibold text-white">Notification Settings</h2><p className="text-sm text-algo-muted">Choose where a triggered alert should be delivered.</p></div>
        </div>
        <label className="block max-w-xl space-y-2 text-sm">
          <span className="font-medium text-white">Mobile Number</span>
          <div className="flex gap-2">
            <span className="inline-flex min-h-11 items-center rounded-xl border border-algo-border bg-algo-surface px-3 text-sm text-algo-muted">+91</span>
            <input className="min-h-11 min-w-0 flex-1 px-3" inputMode="numeric" autoComplete="tel" maxLength={10} value={mobile} onChange={(event) => setMobile(event.target.value.replace(/\D/g, "").slice(0, 10))} placeholder="9876543210" />
          </div>
          <span className="block text-xs text-algo-muted">SMS delivery will be connected to the notification backend later. No SMS is sent by this UI.</span>
        </label>
        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          {[
            [MessageSquare, "SMS Alert", sms, setSms],
            [Smartphone, "App Notification", app, setApp],
            [Mail, "Email Alert", email, setEmail],
          ].map(([Icon, label, enabled, setter]) => (
            <button key={label as string} type="button" onClick={() => (setter as (value: boolean) => void)(!(enabled as boolean))} className="flex min-h-14 items-center gap-3 rounded-xl border border-algo-border bg-algo-surface px-4 text-left">
              <Icon className="h-5 w-5 text-algo-primary" />
              <span className="flex-1"><span className="block text-sm font-semibold text-white">{label as string}</span><span className="block text-xs text-algo-muted">{enabled ? "ON" : "OFF"}</span></span>
            </button>
          ))}
        </div>
      </Card>

      <Card className="border-algo-warning/30 bg-algo-card p-5">
        <div className="flex items-start gap-3">
          <Bot className="mt-0.5 h-5 w-5 shrink-0 text-algo-warning" />
          <div>
            <h2 className="text-base font-semibold text-white">Alert Action</h2>
            <p className="mt-1 text-sm text-algo-muted">When the scanner condition triggers, the configured execution mode can act automatically.</p>
          </div>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <button type="button" onClick={() => setPaperAutoExecute(!paperAutoExecute)} className="flex min-h-16 items-center gap-3 rounded-xl border border-algo-border bg-algo-surface px-4 text-left">
            <ShieldCheck className="h-5 w-5 text-algo-primary" />
            <span className="flex-1"><span className="block text-sm font-semibold text-white">Paper Auto-Execute</span><span className="block text-xs text-algo-muted">{paperAutoExecute ? "ON — alert will create a paper trade" : "OFF"}</span></span>
          </button>
          <div className="flex min-h-16 items-center gap-3 rounded-xl border border-algo-border bg-algo-surface px-4">
            <Bot className="h-5 w-5 text-algo-muted" />
            <span className="flex-1"><span className="block text-sm font-semibold text-white">Live Auto-Execute</span><span className="block text-xs text-algo-muted">OFF — enabled only through the backend live-order safety gate</span></span>
            <span className="rounded-lg border border-algo-border px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-algo-warning">Locked</span>
          </div>
        </div>
        <p className="mt-3 text-xs text-algo-muted">If Live Auto-Execute is explicitly enabled in the backend safety configuration, a triggered scanner alert can be routed to the execution engine automatically. This UI never bypasses the broker-order safety gate.</p>
      </Card>

      <Card className="p-5">
        <h2 className="text-base font-semibold text-white">Alert Status</h2>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {["Active Alerts", "Triggered Alerts", "30-Day History"].map((label) => (
            <div key={label} className="rounded-xl border border-algo-border bg-algo-surface p-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-algo-muted">{label}</p>
              <p className="mt-2 text-xl font-bold text-white">—</p>
              <p className="mt-1 text-xs text-algo-muted">Backend not connected</p>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}