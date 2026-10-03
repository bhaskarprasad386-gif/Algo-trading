"use client";

import { useState } from "react";
import { Bell, Mail, MessageSquare, Smartphone } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";

export default function CustomAlertPage() {
  const [mobile, setMobile] = useState("");
  const [sms, setSms] = useState(true);
  const [app, setApp] = useState(true);
  const [email, setEmail] = useState(false);

  return (
    <div className="space-y-5">
      <PageTitle eyebrow="Phase 4 • Custom Alert" title="Custom Alert" description="Create paper-safe alerts and configure where trigger notifications should be delivered." />

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
              <option>Cash-Future</option>
              <option>Calendar Spread</option>
              <option>Synthetic Arbitrage</option>
              <option>Box Spread</option>
              <option>Debit Strategy</option>
              <option>Credit Strategy</option>
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
          <div>
            <h2 className="text-base font-semibold text-white">Notification Settings</h2>
            <p className="text-sm text-algo-muted">Choose where you want a triggered alert delivered.</p>
          </div>
        </div>

        <label className="block max-w-xl space-y-2 text-sm">
          <span className="font-medium text-white">Mobile Number</span>
          <div className="flex gap-2">
            <span className="inline-flex min-h-11 items-center rounded-xl border border-algo-border bg-algo-surface px-3 text-sm text-algo-muted">+91</span>
            <input
              className="min-h-11 min-w-0 flex-1 px-3"
              inputMode="numeric"
              autoComplete="tel"
              maxLength={10}
              value={mobile}
              onChange={(event) => setMobile(event.target.value.replace(/\D/g, "").slice(0, 10))}
              placeholder="9876543210"
            />
          </div>
          <span className="block text-xs text-algo-muted">SMS delivery will be connected to the notification backend later. No SMS is sent by this UI.</span>
        </label>

        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          <button type="button" onClick={() => setSms(!sms)} className="flex min-h-14 items-center gap-3 rounded-xl border border-algo-border bg-algo-surface px-4 text-left">
            <MessageSquare className="h-5 w-5 text-algo-primary" />
            <span className="flex-1"><span className="block text-sm font-semibold text-white">SMS Alert</span><span className="block text-xs text-algo-muted">{sms ? "ON" : "OFF"}</span></span>
          </button>
          <button type="button" onClick={() => setApp(!app)} className="flex min-h-14 items-center gap-3 rounded-xl border border-algo-border bg-algo-surface px-4 text-left">
            <Smartphone className="h-5 w-5 text-algo-primary" />
            <span className="flex-1"><span className="block text-sm font-semibold text-white">App Notification</span><span className="block text-xs text-algo-muted">{app ? "ON" : "OFF"}</span></span>
          </button>
          <button type="button" onClick={() => setEmail(!email)} className="flex min-h-14 items-center gap-3 rounded-xl border border-algo-border bg-algo-surface px-4 text-left">
            <Mail className="h-5 w-5 text-algo-primary" />
            <span className="flex-1"><span className="block text-sm font-semibold text-white">Email Alert</span><span className="block text-xs text-algo-muted">{email ? "ON" : "OFF"}</span></span>
          </button>
        </div>
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