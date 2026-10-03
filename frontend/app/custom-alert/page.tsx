"use client";

import { useEffect, useState } from "react";
import { Bell, Bot, Mail, MessageSquare, ShieldCheck, Smartphone } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

const scanners = ["Cash-Future", "Calendar Spread", "Synthetic Arbitrage", "Box Spread"];
const metricsByScanner: Record<string, string[]> = {
  "Cash-Future": ["Gap", "Gross Profit", "Net Profit", "Volume / OI"],
  "Calendar Spread": ["Gap", "Gross Profit", "Net Profit", "Volume / OI"],
  "Synthetic Arbitrage": ["Gap", "Gross Profit", "Net Profit", "IV / Premium"],
  "Box Spread": ["Gross Profit", "Net Profit", "Spread Value"],
};
const notificationOptions: Array<{ Icon: LucideIcon; label: string; key: "sms" | "app" | "email" }> = [
  { Icon: MessageSquare, label: "SMS Alert", key: "sms" },
  { Icon: Smartphone, label: "App Notification", key: "app" },
  { Icon: Mail, label: "Email Alert", key: "email" },
];

export default function CustomAlertPage() {
  const [mobile, setMobile] = useState("");
  const [sms, setSms] = useState(true);
  const [app, setApp] = useState(true);
  const [email, setEmail] = useState(false);
  const [paperAutoExecute, setPaperAutoExecute] = useState(false);
  const [paperAutoAmount, setPaperAutoAmount] = useState(10000000);
  const [paperAutoLoading, setPaperAutoLoading] = useState(true);
  const [paperAutoSaving, setPaperAutoSaving] = useState(false);
  const [paperAutoError, setPaperAutoError] = useState<string | null>(null);
  const [scanner, setScanner] = useState("Cash-Future");
  const [metric, setMetric] = useState("Gap");
  const metrics = metricsByScanner[scanner];
  const notificationState = { sms, app, email };
  const notificationSetters = { sms: setSms, app: setApp, email: setEmail };

  useEffect(() => {
    let active = true;
    const base = appConfig.apiBaseUrl.replace(/\/$/, "");
    fetch(`${base}/api/v1/alerts/config`, { cache: "no-store" })
      .then(async (response) => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); })
      .then((data) => {
        if (!active) return;
        setPaperAutoExecute(Boolean(data?.paper?.enabled));
        const amount = Number(data?.paper?.paper_amount);
        if (Number.isFinite(amount) && amount >= 0) setPaperAutoAmount(amount);
        setPaperAutoError(null);
      })
      .catch(() => { if (active) setPaperAutoError("Backend status unavailable"); })
      .finally(() => { if (active) setPaperAutoLoading(false); });
    return () => { active = false; };
  }, []);

  const togglePaperAutoExecute = async () => {
    if (paperAutoSaving) return;
    const next = !paperAutoExecute;
    setPaperAutoSaving(true);
    setPaperAutoError(null);
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(`${base}/api/v1/alerts/paper`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next, paper_amount: paperAutoAmount, emergency_stop: false }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || `HTTP ${response.status}`);
      setPaperAutoExecute(Boolean(data?.paper?.enabled));
      const amount = Number(data?.paper?.paper_amount);
      if (Number.isFinite(amount) && amount >= 0) setPaperAutoAmount(amount);
    } catch (error) {
      setPaperAutoError(error instanceof Error ? error.message : "Unable to update paper auto-execute");
    } finally {
      setPaperAutoSaving(false);
    }
  };

  return (
    <div className="space-y-5 theme-text">
      <PageTitle eyebrow="Phase 4 • Custom Alert" title="Custom Alert" description="Apply an alert across the selected scanner and trigger it from the strategy metric you choose." />

      <Card className="p-5">
        <div className="mb-5">
          <h2 className="text-base font-semibold theme-text">Create Custom Alert</h2>
          <p className="mt-1 text-sm theme-muted">The alert monitors the complete selected scanner, not one manually entered symbol.</p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="space-y-2 text-sm"><span className="font-medium theme-text">Alert Name</span><input className="min-h-11 w-full px-3" placeholder="e.g. Cash-Future Gross Profit" /></label>
          <label className="space-y-2 text-sm"><span className="font-medium theme-text">Scanner / Strategy</span><select className="min-h-11 w-full px-3" value={scanner} onChange={(event) => { const next = event.target.value; setScanner(next); setMetric(metricsByScanner[next][0]); }}>{scanners.map((item) => <option key={item}>{item}</option>)}</select></label>
          <label className="space-y-2 text-sm"><span className="font-medium theme-text">Metric</span><select className="min-h-11 w-full px-3" value={metric} onChange={(event) => setMetric(event.target.value)}>{metrics.map((item) => <option key={item}>{item}</option>)}</select></label>
          <div className="grid grid-cols-[1fr_1.4fr] gap-3">
            <label className="space-y-2 text-sm"><span className="font-medium theme-text">Operator</span><select className="min-h-11 w-full px-3" defaultValue=">="><option>&gt;=</option><option>&gt;</option><option>&lt;=</option><option>&lt;</option><option>=</option></select></label>
            <label className="space-y-2 text-sm"><span className="font-medium theme-text">Value</span><input className="min-h-11 w-full px-3" inputMode="decimal" placeholder={metric === "Gross Profit" || metric === "Net Profit" ? "₹ 5,000" : "1.00%"} /></label>
          </div>
        </div>
        <div className="mt-4 rounded-xl border theme-border theme-surface-2 p-4 text-sm">
          <p className="font-semibold theme-text">Scanner-wide condition</p>
          <p className="mt-1 theme-muted">Alert when <span className="font-semibold theme-text">{scanner}</span> scanner finds an eligible opportunity where <span className="font-semibold theme-text">{metric}</span> meets the configured value.</p>
        </div>
      </Card>

      <Card className="p-5">
        <div className="mb-4 flex items-center gap-2"><Bell className="h-5 w-5 theme-accent" /><div><h2 className="text-base font-semibold theme-text">Notification Settings</h2><p className="text-sm theme-muted">Choose where a triggered alert should be delivered.</p></div></div>
        <label className="block max-w-xl space-y-2 text-sm"><span className="font-medium theme-text">Mobile Number</span><div className="flex gap-2"><span className="inline-flex min-h-11 items-center rounded-xl border theme-border theme-surface-2 px-3 text-sm theme-muted">+91</span><input className="min-h-11 min-w-0 flex-1 px-3" inputMode="numeric" autoComplete="tel" maxLength={10} value={mobile} onChange={(event) => setMobile(event.target.value.replace(/\D/g, "").slice(0, 10))} placeholder="9876543210" /></div><span className="block text-xs theme-muted">SMS delivery will be connected to the notification backend later. No SMS is sent by this UI.</span></label>
        <div className="mt-5 grid gap-3 sm:grid-cols-3">{notificationOptions.map(({ Icon, label, key }) => { const enabled = notificationState[key]; const setter = notificationSetters[key]; return <button key={label} type="button" onClick={() => setter(!enabled)} className="flex min-h-14 items-center gap-3 rounded-xl border theme-border theme-surface px-4 text-left"><Icon className="h-5 w-5 theme-accent" /><span className="flex-1"><span className="block text-sm font-semibold theme-text">{label}</span><span className="block text-xs theme-muted">{enabled ? "ON" : "OFF"}</span></span></button>; })}</div>
      </Card>

      <Card className="theme-border theme-warning-bg p-5">
        <div className="flex items-start gap-3"><Bot className="mt-0.5 h-5 w-5 shrink-0 theme-warning" /><div><h2 className="text-base font-semibold theme-text">Alert Action</h2><p className="mt-1 text-sm theme-muted">When the scanner condition triggers, the configured execution mode can act automatically.</p></div></div>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <button type="button" onClick={togglePaperAutoExecute} disabled={paperAutoLoading || paperAutoSaving} className="flex min-h-16 items-center gap-3 rounded-xl border theme-border theme-surface px-4 text-left disabled:cursor-wait disabled:opacity-70"><ShieldCheck className="h-5 w-5 theme-accent" /><span className="flex-1"><span className="block text-sm font-semibold theme-text">Paper Auto-Execute</span><span className="block text-xs theme-muted">{paperAutoLoading ? "Checking backend setting…" : paperAutoSaving ? "Saving…" : paperAutoExecute ? "ON — qualifying alert will create a paper trade" : "OFF"}</span></span><span className={paperAutoExecute ? "rounded-lg border theme-border theme-success-bg px-2 py-1 text-[10px] font-bold uppercase tracking-wider theme-success" : "rounded-lg border theme-border px-2 py-1 text-[10px] font-bold uppercase tracking-wider theme-muted"}>{paperAutoLoading ? "…" : paperAutoExecute ? "ON" : "OFF"}</span></button>
          <div className="flex min-h-16 items-center gap-3 rounded-xl border theme-border theme-surface px-4"><Bot className="h-5 w-5 theme-muted" /><span className="flex-1"><span className="block text-sm font-semibold theme-text">Live Auto-Execute</span><span className="block text-xs theme-muted">OFF — enabled only through the backend live-order safety gate</span></span><span className="rounded-lg border theme-border theme-warning-bg px-2 py-1 text-[10px] font-bold uppercase tracking-wider theme-warning">Locked</span></div>
        </div>
        <p className="mt-3 text-xs theme-muted">Paper Auto-Execute is saved to the backend global paper setting for this user. When ON, qualifying scanner alerts may create paper trades using the alert’s executable paper legs; when OFF, alerts do not create paper trades. Live broker orders remain OFF.</p>
        {paperAutoError && <p className="mt-2 text-xs theme-danger">Paper auto setting: {paperAutoError}</p>}
      </Card>

      <Card className="p-5">
        <h2 className="text-base font-semibold theme-text">Alert Status</h2>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">{["Active Alerts", "Triggered Alerts", "30-Day History"].map((label) => <div key={label} className="rounded-xl border theme-border theme-surface p-4"><p className="text-xs font-semibold uppercase tracking-wider theme-muted">{label}</p><p className="mt-2 text-xl font-bold theme-text">—</p><p className="mt-1 text-xs theme-muted">Backend alert statistics endpoint pending</p></div>)}</div>
      </Card>
    </div>
  );
}