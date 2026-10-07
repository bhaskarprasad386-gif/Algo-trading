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
  const [editingContactId, setEditingContactId] = useState<number | null>(null);
  const [contactChannels, setContactChannels] = useState({sms:false,app:true,whatsapp:false,telegram:false,email:false});
  const [telegramChatId, setTelegramChatId] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const openContactEditor = (c: any) => { setEditingContactId(Number(c.id)); setContactLabel(String(c.label || "")); setMobile(String(c.mobile_number || "")); setTelegramChatId(String(c.telegram_chat_id || "")); setContactEmail(String(c.email_address || "")); setContactChannels({sms:Boolean(c.channels?.sms),app:Boolean(c.channels?.app),whatsapp:Boolean(c.channels?.whatsapp),telegram:Boolean(c.channels?.telegram),email:Boolean(c.channels?.email)}); setContactOpen(true); setError(null); };
  const [savedRules, setSavedRules] = useState<any[]>([]);
  const [editingRuleId, setEditingRuleId] = useState<number | null>(null);
  const [alertStatus, setAlertStatus] = useState({active_rules:0, triggered_30d:0, history_30d:0});
  const [botChannels, setBotChannels] = useState({whatsapp:false,telegram:false,email:false});
  const [botPreferences, setBotPreferences] = useState({whatsapp:true,telegram:true,email:true});
  const [alertEmail, setAlertEmail] = useState("");
  const [channelSaving, setChannelSaving] = useState<string | null>(null);
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
    setBotChannels({whatsapp:Boolean(data?.notification_channels?.whatsapp), telegram:Boolean(data?.notification_channels?.telegram), email:Boolean(data?.notification_channels?.email)});
    setBotPreferences({whatsapp:data?.notification_preferences?.whatsapp !== false, telegram:data?.notification_preferences?.telegram !== false, email:data?.notification_preferences?.email !== false});
    setAlertEmail(String(data?.notification_preferences?.email_address || ""));
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
      if (active) setError((current) => current);
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

  const toggleBot = async (channel: "whatsapp" | "telegram" | "email") => {
    if (!botChannels[channel]) return setError(`${channel.toUpperCase()} bot is not configured on the server.`);
    if (channel === "email" && !alertEmail.trim()) return setError("Add an email address in a notification contact first.");
    setChannelSaving(channel); setError(null);
    try {
      const response = await fetch(base + "/api/v1/alerts/channels", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify({channel, enabled:!botPreferences[channel]})});
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "Unable to update notification channel");
      setBotPreferences((x)=>({...x,[channel]:Boolean(data.enabled)}));
      setStatus(`${channel.toUpperCase()} notifications ${data.enabled ? "ON" : "OFF"}.`);
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to update notification channel"); }
    finally { setChannelSaving(null); }
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

  const toggleRule = async (rule: any) => {
    const nextEnabled = !Boolean(rule.enabled);
    setError(null); setStatus(null);
    try {
      const response = await fetch(`${base}/api/v1/alerts/rules/${rule.id}`, {
        method: "PUT",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify({
          name: rule.name, strategy_id: rule.strategy_id, metric: rule.metric, operator: rule.operator,
          threshold: Number(rule.threshold), min_gross_profit: Number(rule.min_gross_profit) || 0,
          mobile_number: rule.mobile_number, whatsapp_enabled: Boolean(rule.whatsapp_enabled), enabled: nextEnabled,
          max_loss: Number(rule.max_loss) || 0, max_daily_capital: Number(rule.max_daily_capital) || 0,
          max_simultaneous_positions: Number(rule.max_simultaneous_positions) || 1,
          cooldown_seconds: Number(rule.cooldown_seconds) || 0, priority: Number(rule.priority) || 0,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || `HTTP ${response.status}`);
      setSavedRules((old) => old.map((item) => item.id === rule.id ? data.rule : item));
      setStatus(nextEnabled ? "Alert rule activated." : "Alert rule paused.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to update alert rule.");
    }
  };

  const editRule = (rule: any) => {
    setEditingRuleId(Number(rule.id));
    setAlertName(String(rule.name || ""));
    setScanner(String(rule.strategy_id || "cash-future"));
    setMetric(String(rule.metric || "gap"));
    setOperator(String(rule.operator || ">="));
    setThreshold(String(rule.threshold ?? ""));
    const contact = contacts.find((c) => String(c.mobile_number || "") === String(rule.mobile_number || "").replace(/^91/, ""));
    if (contact) setSelectedContactId(Number(contact.id));
    setStatus(null); setError(null);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const updateRule = async () => {
    if (editingRuleId == null) return;
    const numeric = Number(threshold);
    if (!alertName.trim()) return setError("Enter an alert name.");
    if (!Number.isFinite(numeric)) return setError("Enter a valid threshold.");
    const selectedContact = selectedContactId == null ? null : contacts.find((contact) => Number(contact.id) === selectedContactId);
    const contactMobile = String(selectedContact?.mobile_number || mobile).trim().replace(/\D/g, "");
    if (contactMobile.length < 7) return setError("Add/select a valid alert contact first.");
    const rule = savedRules.find((item) => Number(item.id) === editingRuleId);
    if (!rule) return setError("Alert rule no longer exists.");
    setSaving(true); setError(null); setStatus(null);
    try {
      const response = await fetch(base + "/api/v1/alerts/rules/" + editingRuleId, {
        method: "PUT", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({name: alertName.trim(), strategy_id: scanner, metric, operator, threshold: numeric, min_gross_profit: metric === "gross_profit" ? Math.max(0, numeric) : 0, mobile_number: contactMobile.startsWith("91") ? contactMobile : "91" + contactMobile, whatsapp_enabled: Boolean(selectedContact?.channels?.whatsapp), enabled: Boolean(rule.enabled), max_loss: Number(rule.max_loss) || 0, max_daily_capital: Number(rule.max_daily_capital) || 0, max_simultaneous_positions: Number(rule.max_simultaneous_positions) || 1, cooldown_seconds: Number(rule.cooldown_seconds) || 0, priority: Number(rule.priority) || 0}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.detail || "HTTP " + response.status);
      setSavedRules((old) => old.map((item) => item.id === editingRuleId ? data.rule : item));
      setEditingRuleId(null); setAlertName(""); setThreshold(""); setStatus("Alert updated successfully.");
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to update alert."); } finally { setSaving(false); }
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
        <div className="flex items-center gap-3"><Bot className="h-5 w-5 theme-accent"/><div className="flex-1"><h2 className="text-base font-semibold theme-text">Notification Bots</h2><p className="text-sm theme-muted">Server-side bot credentials stay private; each notification channel is enabled only when deployment configuration is available.</p></div></div><div className="mt-4 grid gap-3 sm:grid-cols-3">{(["whatsapp","telegram","email"] as const).map((channel)=><div key={channel} className="rounded-xl border theme-border theme-surface p-3"><div className="flex items-center justify-between gap-2"><p className="text-xs theme-muted">{channel.toUpperCase()}</p><button type="button" disabled={!botChannels[channel] || channelSaving===channel} onClick={()=>toggleBot(channel)} className={`rounded-lg border theme-border px-3 py-1 text-xs font-bold ${botPreferences[channel] ? "theme-success" : "theme-muted"}`}>{channelSaving===channel ? "…" : botPreferences[channel] ? "ON" : "OFF"}</button></div><p className="mt-2 text-xs theme-muted">{botChannels[channel] ? "Server configured" : "Not configured"}</p></div>)}</div>
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
          <button type="button" onClick={editingRuleId == null ? saveRule : updateRule} disabled={saving} className="inline-flex min-h-11 items-center gap-2 rounded-xl border theme-border theme-accent-bg px-4 text-sm font-semibold">{saving ? "Saving…" : editingRuleId == null ? <><Plus className="h-4 w-4"/>Save Alert</> : "Update Alert"}</button>
        </div>
        {status && <p className="mt-3 text-sm theme-success">{status}</p>}
        {error && <p className="mt-3 text-sm theme-danger">{error}</p>}
      </Card>

      <Card className="p-5">
        <div className="flex items-center gap-3"><Bell className="h-5 w-5 theme-accent"/><div className="flex-1"><h2 className="text-base font-semibold theme-text">Notification Contacts</h2><p className="text-sm theme-muted">Multiple numbers are supported. Disable or delete a contact independently.</p></div><button type="button" onClick={()=>setContactOpen(true)} className="inline-flex min-h-10 items-center gap-1 rounded-xl border theme-border theme-accent-bg px-3 text-sm font-semibold"><Plus className="h-4 w-4"/> Add</button></div>
        <div className="mt-4 space-y-2">{contacts.length===0 && <p className="text-sm theme-muted">No contacts saved.</p>}{contacts.map((c)=><div key={c.id} className="flex items-center gap-3 rounded-xl border theme-border theme-surface p-3"><div className="min-w-0 flex-1"><p className="font-semibold theme-text">{c.label} • {c.mobile_number ? "+"+c.mobile_number : c.email_address}</p><p className="text-xs theme-muted">{c.email_address || "No email saved"}</p><p className="text-xs theme-muted">SMS {c.channels.sms?"ON":"OFF"} · App {c.channels.app?"ON":"OFF"} · WhatsApp {c.channels.whatsapp?"ON":"OFF"} · Email {c.channels.email?"ON":"OFF"} · {c.enabled?"CONTACT ON":"CONTACT OFF"}</p></div><button type="button" onClick={()=>openContactEditor(c)} className="rounded-lg border theme-border p-2 theme-accent" aria-label="Edit contact">EDIT</button><button type="button" onClick={async()=>{const response=await fetch(base+"/api/v1/alerts/contacts/"+c.id,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({label:c.label,mobile_number:c.mobile_number,email_address:c.email_address||null,enabled:!c.enabled,sms_enabled:c.channels.sms,app_enabled:c.channels.app,whatsapp_enabled:c.channels.whatsapp,telegram_enabled:c.channels.telegram,telegram_chat_id:c.telegram_chat_id||"",email_enabled:c.channels.email})});if(response.ok){const data=await response.json();setContacts((x)=>x.map((v)=>v.id===c.id?data.contact:v));}}} className="rounded-lg border theme-border p-2 theme-accent" aria-label={c.enabled?"Disable contact":"Enable contact"}>{c.enabled?"OFF":"ON"}</button><button type="button" onClick={async()=>{const response=await fetch(base+"/api/v1/alerts/contacts/"+c.id,{method:"DELETE"});if(response.ok)setContacts((x)=>x.filter((v)=>v.id!==c.id));}} className="rounded-lg border theme-border p-2 theme-danger" aria-label="Delete contact"><Trash2 className="h-4 w-4"/></button></div>)}</div>
      </Card>
      <Card className="p-5">
        <h2 className="text-base font-semibold theme-text">Saved Alerts</h2>
        <div className="mt-4 space-y-3">
          {savedRules.length === 0 && <p className="text-sm theme-muted">No saved alert rules yet.</p>}
          {savedRules.map((rule) => {
            const scannerLabel = scanners.find(x=>x.id===rule.strategy_id)?.label || rule.strategy_id;
            return <div key={rule.id} className="flex items-center gap-3 rounded-xl border theme-border theme-surface p-4">
              <div className="min-w-0 flex-1"><p className="font-semibold theme-text">{rule.name}</p><p className="text-xs theme-muted">{scannerLabel} • {rule.metric} {rule.operator} {rule.threshold} • {rule.enabled ? "Rule ON" : "Rule OFF"}</p></div>
              <button type="button" onClick={()=>toggleRule(rule)} className={`rounded-lg border theme-border px-3 py-2 text-xs font-bold uppercase ${rule.enabled ? "theme-warning" : "theme-success"}`} aria-label={rule.enabled ? "Pause alert" : "Activate alert"}>{rule.enabled ? "PAUSE" : "ACTIVATE"}</button><button type="button" onClick={()=>editRule(rule)} className="rounded-lg border theme-border px-3 py-2 text-xs font-bold uppercase theme-accent" aria-label="Edit alert">EDIT</button>
              <button type="button" onClick={()=>deleteRule(rule.id)} className="rounded-lg border theme-border p-2 theme-danger" aria-label="Delete alert"><Trash2 className="h-4 w-4"/></button>
            </div>;
          })}
        </div>
      </Card>

      {contactOpen && <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"><Card className="w-full max-w-lg p-5"><div className="flex items-center justify-between"><h2 className="text-lg font-semibold theme-text">{editingContactId == null ? "Add Notification Contact" : "Edit Notification Contact"}</h2><button type="button" onClick={()=>{setContactOpen(false);setEditingContactId(null)}} className="theme-muted">✕</button></div><div className="mt-4 grid gap-3"><label className="text-sm"><span className="block mb-1 theme-text">Label</span><input className="min-h-11 w-full px-3" value={contactLabel} onChange={(e)=>setContactLabel(e.target.value)} /></label><label className="text-sm"><span className="block mb-1 theme-text">Mobile Number</span><input className="min-h-11 w-full px-3" inputMode="numeric" value={mobile} onChange={(e)=>setMobile(e.target.value.replace(/\D/g,"").slice(0,15))} placeholder="919876543210" /></label><label className="text-sm"><span className="block mb-1 theme-text">Email Address</span><input className="min-h-11 w-full px-3" type="email" value={contactEmail} onChange={(e)=>setContactEmail(e.target.value)} placeholder="alerts@example.com" /></label><label className="text-sm"><span className="block mb-1 theme-text">Telegram Chat ID</span><input className="min-h-11 w-full px-3" inputMode="text" value={telegramChatId} onChange={(e)=>setTelegramChatId(e.target.value.slice(0,128))} placeholder="Required only for Telegram" /></label><div className="grid grid-cols-2 gap-2 text-sm">{(["sms","app","whatsapp","telegram","email"] as const).map((key)=><label key={key} className="flex items-center gap-2 rounded-xl border theme-border p-3"><input type="checkbox" checked={contactChannels[key]} onChange={(e)=>setContactChannels((x)=>({...x,[key]:e.target.checked}))}/>{key.toUpperCase()}</label>)}</div></div><button type="button" disabled={contactSaving} onClick={async()=>{setContactSaving(true);try{const editing=editingContactId!=null;const url=editing?base+"/api/v1/alerts/contacts/"+editingContactId:base+"/api/v1/alerts/contacts";const response=await fetch(url,{method:editing?"PUT":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({label:contactLabel,mobile_number:mobile,email_address:contactEmail.trim() || null,enabled:editing?Boolean(contacts.find((v)=>Number(v.id)===editingContactId)?.enabled):true,sms_enabled:contactChannels.sms,app_enabled:contactChannels.app,whatsapp_enabled:contactChannels.whatsapp,telegram_enabled:contactChannels.telegram,telegram_chat_id:telegramChatId.trim(),email_enabled:contactChannels.email})});const data=await response.json();if(!response.ok)throw new Error(data?.detail||"Unable to save contact");setContacts((x)=>editing?x.map((v)=>v.id===editingContactId?data.contact:v):[...x,data.contact]);setSelectedContactId(Number(data.contact.id));setMobile("");setContactLabel("Primary");setContactEmail("");setTelegramChatId("");setContactChannels({sms:false,app:true,whatsapp:false,telegram:false,email:false});setEditingContactId(null);setContactOpen(false)}catch(e){setError(e instanceof Error?e.message:"Unable to save contact")}finally{setContactSaving(false)}}} className="mt-4 min-h-11 w-full rounded-xl border theme-border theme-accent-bg font-semibold">{contactSaving?"Saving…":editingContactId == null ? "Save Contact" : "Update Contact"}</button></Card></div>}
    </div>
  );
}
