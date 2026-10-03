"use client";

import { useState } from "react";
import { Bell, Bot, Mail, MessageSquare, ShieldCheck, Smartphone } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";

export default function CustomAlertPage() {
  const [mobile, setMobile] = useState("");
  const [sms, setSms] = useState(true);
  const [app, setApp] = useState(true);
  const [email, setEmail] = useState(false);
  const [paperAutoExecute, setPaperAutoExecute] = useState(false);

  return (
    <div className="space-y-5">
      <PageTitle eyebrow="Phase 4 • Custom Alert" title="Custom Alert" description="Create alerts, configure notifications, and define what should happen when an alert triggers." />

      <Card className="p-5">
        <div className="mb-5">
          <h2 className="text-base font-semibold text-white">Create Custom Alert</h2>
          <p className="mt-1 text-sm text-algo-muted">Alert conditions are saved only when the backend alert service is connected.</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="space-y-2 text-sm">
            <span className="font-medium text-white">Alert Name</span>
            <input className="min-h-11 w-full px-3" placeholder="e.g. Cash-Future Gap" />
          </label>
          <label className="space-y-2 text-sm">
            <span className="font-medium text-white">Strategy</span>
            <select className="min-h-11 w-full px-3" defaultValue="">
              <option value="" disabled>Select strategy</option>
              <option>Cash-Future</option><option>Calendar Spread</option><option>Synthetic Arbitrage</option><option>Box Spread</option><option>Debit Strategy</option><option>Credit Strategy</option>
            </select>
          </label>
          <label className="space-y-2 text-sm">
            <span className="font-medium text-white">Symbol / Instrument</span>
            <input className="min-h-11 w-full px-3" placeholder="Select or enter symbol" />
          </label>
          <label className="space-y-2 text-sm">
            <span className="font-medium text-white">Condition</span>
            <input className="min-h-11 w-full px-3" placeholder="e.g. Gap >= 1.00%" />
          </label>
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
            <p className="mt-1 text-sm text-algo-muted">When this alert triggers, the configured action will run automatically once the corresponding execution mode is enabled.</p>
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
        <p className="mt-3 text-xs text-algo-muted">If Live Auto-Execute is explicitly enabled in the backend safety configuration, a triggered alert can be routed to the execution engine automatically. This UI never bypasses the broker-order safety gate.</p>
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