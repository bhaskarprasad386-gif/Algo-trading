"use client";

import { useEffect, useState } from "react";
import { Bell, Bot, Plus, Trash2 } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

const scanners = [
  { id: "cash-future", label: "Cash-Future" },
  { id: "calendar-spread", label: "Calendar Spread" },
  { id: "synthetic-future-cash-carry", label: "Synthetic Arbitrage" },
  { id: "box-spread", label: "Box Spread" },
];

const metricsByScanner: Record<string, Array<{ id: string; label: string }>> = {
  "cash-future": [{id:"gap",label:"Gap"},{id:"gross_profit",label:"Gross Profit"},{id:"net_profit",label:"Net Profit"}],
  "calendar-spread": [{id:"gap",label:"Gap"},{id:"gross_profit",label:"Gross Profit"}],
  "synthetic-future-cash-carry": [{id:"gap",label:"Executable Edge"},{id:"gross_profit",label:"Gross Profit"}],
  "box-spread": [{id:"gap",label:"Executable Edge"},{id:"gross_profit",label:"Gross Profit"}],
};
const operators = [">=", ">", "<=", "<", "="];

export default function CustomAlertPage() {
  const [alertEnabled, setAlertEnabled] = useState(true);
  const [masterSaving, setMasterSaving] = useState(false);
  const [alertName, setAlertName] = useState("");
  const [scanner, setScanner] = useState("cash-future");
  const [metric, setMetric] = useState("gap");
  const [operator, setOperator] = useState(">=");
  const [threshold, setThreshold] = useState("");
  const [mobile, setMobile] = useState("");
  const [contacts, setContacts] = useState<any[]>([]);
  const [selectedContactId, setSelectedContactId] = useState<number | null>(null);
  const [contactOpen, setContactOpen] = useState(false);
  const [contactLabel, setContactLabel] = useState("Primary");
  const [contactSaving, setContactSaving] = useState(false);
  const [contactChannels, setContactChannels] = useState({sms:false,app:true,whatsapp:false,telegram:false,email:false});
  const [telegramChatId, setTelegramChatId] = useState("");
  const [savedRules, setSavedRules] = useState<any[]>([]);
  const [alertStatus, setAlertStatus] = useState({active_rules:0, triggered_30d:0, history_30d:0});
  const [botChannels, setBotChannels] = useState({whatsapp:false,telegram:false});
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);


  const base = appConfig.apiBaseUrl.replace(/\/$/, "");
  const metrics = metricsByScanner[scanner];

  const loadConfig = async () => {
    const response = await fetch(`${base}/api/v1/alerts/config`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    setAlertEnabled(Boolean(data?.alerts?.enabled));
    setBotChannels({whatsapp:Boolean(data?.notification_channels?.whatsapp), telegram:Boolean(data?.notification_channels?.telegram)});
    setSavedRules(Array.isArray(data?.rules) ? data.rules : []);
    const statusResponse = await fetch(`${base}/api/v1/alerts/status`, { cache: "no-store" });
    if (statusResponse.ok) { const statusData = await statusResponse.json(); setAlertStatus({active_rules:Number(statusData?.active_rules)||0, triggered_30d:Number(statusData?.triggered_30d)||0, history_30d:Number(statusData?.history_30d)||0}); }
    const contactsResponse = await fetch(`${base}/api/v1/alerts/contacts`, { cache: "no-store" });
    if (contactsResponse.ok) { const contactsData = await contactsResponse.json(); const loadedContacts = Array.isArray(contactsData?.contacts) ? contactsData.contacts : []; setContacts(loadedContacts); if (loadedContacts.length) setSelectedContactId(Number(loadedContacts[0].id)); }
  };

  useEffect(() => {
    let active = true;
    loadConfig().catch(() => {
      if (active) setError("Backend alert configuration unavailable");
    }).finally(() => {
      if (active) setPaperAutoLoading(false);
    });
    return () => { active = false; };
  }, []);

  const toggleMaster = async () => {
    if (masterSaving) return;
    const next = !alertEnabled;
    setMasterSaving(true); setError(null);
    try {
      const response = await fetch(`${base}/api/v1/alerts/master`, {
        method: "PUT", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({enabled: next}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || `HTTP ${response.status}`);
      setAlertEnabled(Boolean(data?.alerts?.enabled));
      setStatus(next ? "Alerts ON — notifications are enabled." : "Alerts OFF — scanner results remain visible; notifications are stopped.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to update alert master");
    } finally { setMasterSaving(false); }
  };

  const saveRule = async () => {
    const numeric = Number(threshold);
    if (!alertName.trim()) return setError("Enter an alert name.");
    const selectedContact = selectedContactId == null ? null : contacts.find((contact) => Number(contact.id) === selectedContactId);
    const contactMobile = String(selectedContact?.mobile_number || mobile).trim().replace(/\D/g, "");
    if (contactMobile.length < 7) return setError("Add/select a valid alert contact first.");
    const ruleMobile = contactMobile.startsWith("91") ? contactMobile : `91${contactMobile}`;
    const ruleWhatsapp = Boolean(selectedContact?.channels?.whatsapp);
    if (!Number.isFinite(numeric)) return setError("Enter a valid threshold.");
    setSaving(true); setError(null); setStatus(null);
    try {
      const response = await fetch(`${base}/api/v1/alerts/rules`, {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({
          name: alertName.trim(), strategy_id: scanner, metric, operator, threshold: numeric,
          min_gross_profit: metric === "gross_profit" ? Math.max(0, numeric) : 0,
          mobile_number: ruleMobile, whatsapp_enabled: ruleWhatsapp, enabled: true,
          max_loss: 10000, max_daily_capital: 10000000, max_simultaneous_positions: 20,
          cooldown_seconds: 60, priority: 0,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || `HTTP ${response.status}`);
      setSavedRules((old) => [data.rule, ...old]);
      setStatus("Alert saved successfully.");
      setAlertName(""); setThreshold("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to save alert");
    } finally { setSaving(false); }
  };

  const deleteRule = async (id: number) => {
    const response = await fetch(`${base}/api/v1/alerts/rules/${id}`, {method:"DELETE"});
    if (!response.ok) { setError("Unable to delete alert."); return; }
    setSavedRules((old) => old.filter((rule) => rule.id !== id));
  };

  return (
    <div className="space-y-5 theme-text">
      <PageTitle eyebrow="Phase 4 • Custom Alert" title="Custom Alert" description="Scanner-wide alert rules with a separate master notification switch." />

      <Card className="p-5">
        <div className="flex items-center gap-3">
          <Bell className="h-5 w-5 theme-accent" />
          <div className="flex-1"><h2 className="text-base font-semibold theme-text">Alert Master</h2><p className="text-sm theme-muted">{alertEnabled ? "ON — qualifying notifications can be delivered." : "OFF — notifications stop, but scanner results and alert data remain visible."}</p></div>
          <button type="button" onClick={toggleMaster} disabled={masterSaving} className={`min-w-20 rounded-xl border px-4 py-2 text-xs font-bold uppercase tracking-wider ${alertEnabled ? "theme-success-bg theme-success" : "theme-surface theme-muted"}`}>{masterSaving ? "Saving…" : alertEnabled ? "ON" : "OFF"}</button>
        </div>
      </Card>

      <Card className="p-5">
        <div className="flex items-center gap-3"><Bot className="h-5 w-5 theme-accent"/><div className="flex-1"><h2 className="text-base font-semibold theme-text">Notification Bots</h2><p className="text-sm theme-muted">Server-side bot credentials stay private; both channels are disabled until deployment configuration enables them.</p></div></div><div className="mt-4 grid grid-cols-2 gap-3"><div className="rounded-xl border theme-border theme-surface p-3"><p className="text-xs theme-muted">WHATSAPP BOT</p><p className="mt-1 text-sm font-bold theme-text">{botChannels.whatsapp ? "ENABLED" : "OFF"}</p></div><div className="rounded-xl border theme-border theme-surface p-3"><p className="text-xs theme-muted">TELEGRAM BOT</p><p className="mt-1 text-sm font-bold theme-text">{botChannels.telegram ? "ENABLED" : "OFF"}</p></div></div>
      </Card>

      <Card className="p-5">
        <div className="flex items-center gap-3"><Bell className="h-5 w-5 theme-accent"/><div className="flex-1"><h2 className="text-base font-semibold theme-text">Alert Status</h2><p className="text-sm theme-muted">Durable scanner alert activity; turning notifications OFF does not hide results.</p></div></div>
        <div className="mt-4 grid grid-cols-3 gap-3">
          <div className="rounded-xl border theme-border theme-surface p-3"><p className="text-xs theme-muted">ACTIVE ALERTS</p><p className="mt-1 text-2xl font-bold theme-text">{alertStatus.active_rules}</p></div>
          <div className="rounded-xl border theme-border theme-surface p-3"><p className="text-xs theme-muted">TRIGGERED</p><p className="mt-1 text-2xl font-bold theme-text">{alertStatus.triggered_30d}</p></div>
          <div className="rounded-xl border theme-border theme-surface p-3"><p className="text-xs theme-muted">30-DAY HISTORY</p><p className="mt-1 text-2xl font-bold theme-text">{alertStatus.history_30d}</p></div>
        </div>
      </Card>

      <Card className="p-5">
        <h2 className="text-base font-semibold theme-text">Create Custom Alert</h2>
        <p className="mt-1 text-sm theme-muted">The condition applies to the complete selected scanner, not a single symbol.</p>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <label className="space-y-2 text-sm"><span className="font-medium theme-text">Alert Name</span><input className="min-h-11 w-full px-3" value={alertName} onChange={(e)=>setAlertName(e.target.value)} placeholder="e.g. Cash-Future Gap Alert" /></label>
          <label className="space-y-2 text-sm"><span className="font-medium theme-text">Scanner / Strategy</span><select className="min-h-11 w-full px-3" value={scanner} onChange={(e)=>{const next=e.target.value;setScanner(next);setMetric(metricsByScanner[next][0].id);}}>{scanners.map(x=><option key={x.id} value={x.id}>{x.label}</option>)}</select></label>
          <label className="space-y-2 text-sm"><span className="font-medium theme-text">Metric</span><select className="min-h-11 w-full px-3" value={metric} onChange={(e)=>setMetric(e.target.value)}>{metrics.map(x=><option key={x.id} value={x.id}>{x.label}</option>)}</select></label>
          <div className="grid grid-cols-[1fr_1.4fr] gap-3">
            <label className="space-y-2 text-sm"><span className="font-medium theme-text">Operator</span><select className="min-h-11 w-full px-3" value={operator} onChange={(e)=>setOperator(e.target.value)}>{operators.map(x=><option key={x}>{x}</option>)}</select></label>
            <label className="space-y-2 text-sm"><span className="font-medium theme-text">Threshold</span><input className="min-h-11 w-full px-3" inputMode="decimal" value={threshold} onChange={(e)=>setThreshold(e.target.value)} placeholder="Enter value" /></label>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <div className="min-w-[240px] flex-1"><span className="block mb-2 text-sm font-medium theme-text">Alert Contact</span>{contacts.length ? <select aria-label="Alert contact" className="min-h-11 w-full px-3" value={selectedContactId ?? ""} onChange={(e)=>setSelectedContactId(e.target.value ? Number(e.target.value) : null)}>{contacts.map((contact)=><option key={contact.id} value={contact.id} disabled={!contact.enabled}>{contact.label} • +{contact.mobile_number}{contact.enabled ? "" : " • OFF"}</option>)}</select> : <button type="button" onClick={()=>setContactOpen(true)} className="min-h-11 w-full rounded-xl border theme-border theme-surface px-3 text-left text-sm theme-text"><Plus className="mr-2 inline h-4 w-4"/>Add mobile number</button>}</div>
          <button type="button" onClick={saveRule} disabled={saving} className="inline-flex min-h-11 items-center gap-2 rounded-xl border theme-border theme-accent-bg px-4 text-sm font-semibold">{saving ? "Saving…" : <><Plus className="h-4 w-4"/>Save Alert</>}</button>
        </div>
        {status && <p className="mt-3 text-sm theme-success">{status}</p>}
        {error && <p className="mt-3 text-sm theme-danger">{error}</p>}
      </Card>

      <Card className="p-5">
        <div className="flex items-center gap-3"><Bell className="h-5 w-5 theme-accent"/><div className="flex-1"><h2 className="text-base font-semibold theme-text">Notification Contacts</h2><p className="text-sm theme-muted">Multiple numbers are supported. Disable or delete a contact independently.</p></div><button type="button" onClick={()=>setContactOpen(true)} className="inline-flex min-h-10 items-center gap-1 rounded-xl border theme-border theme-accent-bg px-3 text-sm font-semibold"><Plus className="h-4 w-4"/> Add</button></div>
        <div className="mt-4 space-y-2">{contacts.length===0 && <p className="text-sm theme-muted">No contacts saved.</p>}{contacts.map((c)=><div key={c.id} className="flex items-center gap-3 rounded-xl border theme-border theme-surface p-3"><div className="min-w-0 flex-1"><p className="font-semibold theme-text">{c.label} • +{c.mobile_number}</p><p className="text-xs theme-muted">SMS {c.channels.sms?"ON":"OFF"} · App {c.channels.app?"ON":"OFF"} · WhatsApp {c.channels.whatsapp?"ON":"OFF"} · Email {c.channels.email?"ON":"OFF"} · {c.enabled?"CONTACT ON":"CONTACT OFF"}</p></div><button type="button" onClick={async()=>{const response=await fetch(base+"/api/v1/alerts/contacts/"+c.id,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({label:c.label,mobile_number:c.mobile_number,enabled:!c.enabled,sms_enabled:c.channels.sms,app_enabled:c.channels.app,whatsapp_enabled:c.channels.whatsapp,telegram_enabled:c.channels.telegram,telegram_chat_id:c.telegram_chat_id||"",email_enabled:c.channels.email})});if(response.ok){const data=await response.json();setContacts((x)=>x.map((v)=>v.id===c.id?data.contact:v));}}} className="rounded-lg border theme-border p-2 theme-accent" aria-label={c.enabled?"Disable contact":"Enable contact"}>{c.enabled?"OFF":"ON"}</button><button type="button" onClick={async()=>{const response=await fetch(base+"/api/v1/alerts/contacts/"+c.id,{method:"DELETE"});if(response.ok)setContacts((x)=>x.filter((v)=>v.id!==c.id));}} className="rounded-lg border theme-border p-2 theme-danger" aria-label="Delete contact"><Trash2 className="h-4 w-4"/></button></div>)}</div>
      </Card>
      <Card className="p-5">
        <h2 className="text-base font-semibold theme-text">Saved Alerts</h2>
        <div className="mt-4 space-y-3">
          {savedRules.length === 0 && <p className="text-sm theme-muted">No saved alert rules yet.</p>}
          {savedRules.map((rule) => {
            const scannerLabel = scanners.find(x=>x.id===rule.strategy_id)?.label || rule.strategy_id;
            return <div key={rule.id} className="flex items-center gap-3 rounded-xl border theme-border theme-surface p-4">
              <div className="min-w-0 flex-1"><p className="font-semibold theme-text">{rule.name}</p><p className="text-xs theme-muted">{scannerLabel} • {rule.metric} {rule.operator} {rule.threshold} • {rule.enabled ? "Rule ON" : "Rule OFF"}</p></div>
              <button type="button" onClick={()=>deleteRule(rule.id)} className="rounded-lg border theme-border p-2 theme-danger" aria-label="Delete alert"><Trash2 className="h-4 w-4"/></button>
            </div>;
          })}
        </div>
      </Card>

      {contactOpen && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"><Card className="w-full max-w-lg p-5"><div className="flex items-center justify-between"><h2 className="text-lg font-semibold theme-text">Add Notification Contact</h2><button type="button" onClick={()=>setContactOpen(false)} className="theme-muted">✕</button></div><div className="mt-4 grid gap-3"><label className="text-sm"><span className="block mb-1 theme-text">Label</span><input className="min-h-11 w-full px-3" value={contactLabel} onChange={(e)=>setContactLabel(e.target.value)} /></label><label className="text-sm"><span className="block mb-1 theme-text">Mobile Number</span><input className="min-h-11 w-full px-3" inputMode="numeric" value={mobile} onChange={(e)=>setMobile(e.target.value.replace(/\D/g,"").slice(0,15))} placeholder="919876543210" /></label><label className="text-sm"><span className="block mb-1 theme-text">Telegram Chat ID</span><input className="min-h-11 w-full px-3" inputMode="text" value={telegramChatId} onChange={(e)=>setTelegramChatId(e.target.value.slice(0,128))} placeholder="Required only for Telegram" /></label><div className="grid grid-cols-2 gap-2 text-sm">{(["sms","app","whatsapp","telegram","email"] as const).map((key)=><label key={key} className="flex items-center gap-2 rounded-xl border theme-border p-3"><input type="checkbox" checked={contactChannels[key]} onChange={(e)=>setContactChannels((x)=>({...x,[key]:e.target.checked}))}/>{key.toUpperCase()}</label>)}</div></div><button type="button" disabled={contactSaving} onClick={async()=>{setContactSaving(true);try{const response=await fetch(base+"/api/v1/alerts/contacts",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({label:contactLabel,mobile_number:mobile,enabled:true,sms_enabled:contactChannels.sms,app_enabled:contactChannels.app,whatsapp_enabled:contactChannels.whatsapp,telegram_enabled:contactChannels.telegram,telegram_chat_id:telegramChatId.trim(),email_enabled:contactChannels.email})});const data=await response.json();if(!response.ok)throw new Error(data?.detail||"Unable to save contact");setContacts((x)=>[...x,data.contact]);setSelectedContactId(Number(data.contact.id));setMobile(data.contact.mobile_number);setContactLabel("Primary");setTelegramChatId("");setContactChannels({sms:false,app:true,whatsapp:false,telegram:false,email:false});setContactOpen(false)}catch(e){setError(e instanceof Error?e.message:"Unable to save contact")}finally{setContactSaving(false)}}} className="mt-4 min-h-11 w-full rounded-xl border theme-border theme-accent-bg font-semibold">{contactSaving?"Saving…":"Save Contact"}</button></Card></div>}
    </div>
  );
}
