"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, RefreshCw, TriangleAlert, Server, WifiOff, Copy, ChevronDown } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";

type DiagnosticError = { id: string; time: string; component: string; severity: string; message: string; source?: string };
type CollectorPayload = { status: string; source?: string; server_time?: string; count?: number; errors?: DiagnosticError[] };
const CACHE_KEY = "algo.server-diagnostics.cache.v1";
const COLLECTOR_URL = "/server-diagnostics/api/errors?limit=100";

export default function ErrorsPage() {
  const [errors, setErrors] = useState<DiagnosticError[]>([]);
  const [loading, setLoading] = useState(true);
  const [collectorReachable, setCollectorReachable] = useState<boolean | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [source, setSource] = useState("VPS SYSTEMD JOURNAL");
  const [expanded, setExpanded] = useState<string[]>([]);
  const [filter, setFilter] = useState("ALL");
  const requestRef = useRef<AbortController | null>(null);

  const loadErrors = useCallback(async () => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    try {
      const response = await fetch(COLLECTOR_URL, { cache: "no-store", signal: controller.signal, headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("collector_http_" + response.status);
      const body = (await response.json()) as CollectorPayload;
      if (body?.status !== "ok" || !Array.isArray(body.errors)) throw new Error("collector_schema_invalid");
      if (requestRef.current !== controller) return;
      const receivedAt = new Date().toISOString();
      const nextErrors = body.errors;
      setErrors(nextErrors);
      setLastUpdated(receivedAt);
      setSource(body.source || "VPS SYSTEMD JOURNAL");
      setCollectorReachable(true);
      try { localStorage.setItem(CACHE_KEY, JSON.stringify({ errors: nextErrors, lastUpdated: receivedAt, source: body.source || "VPS SYSTEMD JOURNAL" })); } catch { /* optional browser cache */ }
    } catch {
      if (controller.signal.aborted || requestRef.current !== controller) return;
      // Preserve the last successful server response; never clear logs on fetch failure.
      setCollectorReachable(false);
    } finally {
      if (requestRef.current === controller) { requestRef.current = null; setLoading(false); }
    }
  }, []);

  useEffect(() => {
    try {
      const cached = localStorage.getItem(CACHE_KEY);
      if (cached) {
        const parsed = JSON.parse(cached) as { errors?: DiagnosticError[]; lastUpdated?: string; source?: string };
        if (Array.isArray(parsed.errors)) setErrors(parsed.errors);
        if (parsed.lastUpdated) setLastUpdated(parsed.lastUpdated);
        if (parsed.source) setSource(parsed.source);
      }
    } catch { /* ignore corrupt or unavailable cache */ }
    let stopped = false;
    let timer: number | undefined;
    const refresh = async () => { await loadErrors(); if (!stopped) timer = window.setTimeout(() => void refresh(), 5000); };
    void refresh();
    return () => { stopped = true; if (timer !== undefined) window.clearTimeout(timer); requestRef.current?.abort(); requestRef.current = null; };
  }, [loadErrors]);

  const filtered = filter === "ALL" ? errors : errors.filter((item) => item.severity.toUpperCase() === filter);
  const counts = {
    critical: errors.filter((item) => item.severity.toUpperCase() === "CRITICAL").length,
    error: errors.filter((item) => item.severity.toUpperCase() === "ERROR").length,
    warning: errors.filter((item) => item.severity.toUpperCase() === "WARNING").length,
  };
  const copyError = async (item: DiagnosticError) => {
    try { await navigator.clipboard.writeText([item.time, item.severity, item.component, item.source || "", item.message].filter(Boolean).join("\n")); } catch { /* optional clipboard */ }
  };

  return (
    <div className="min-h-screen theme-bg theme-text p-2 sm:p-4">
      <PageTitle eyebrow="VPS • Independent System Diagnostics" title="Server Error Logs" description="Direct VPS journal history — independent from the trading API process." />
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <Link href="/" className="inline-flex min-h-11 items-center gap-2 rounded-xl border theme-border theme-surface-2 px-3 text-xs font-bold theme-muted"><ArrowLeft size={15} /> Dashboard</Link>
        <button type="button" onClick={() => void loadErrors()} className="inline-flex min-h-11 items-center gap-2 rounded-xl theme-accent-bg px-4 text-xs font-bold theme-accent"><RefreshCw size={15} /> Refresh</button>
      </div>

      {collectorReachable === false && (
        <Card className="mb-3 rounded-2xl border border-amber-500/40 bg-amber-500/10 p-3">
          <div className="flex items-start gap-3"><WifiOff size={19} className="mt-0.5 shrink-0 text-amber-500" /><div className="min-w-0">
            <div className="text-sm font-bold text-amber-500">VPS diagnostics collector unreachable</div>
            <div className="mt-1 text-xs theme-muted">Showing the last successfully received server logs{lastUpdated ? " from " + new Date(lastUpdated).toLocaleString("en-IN") : " if a local cache exists"}. Logs are not being cleared.</div>
          </div></div>
        </Card>
      )}

      <Card className="mb-3 rounded-2xl border theme-border theme-surface-2 p-3 sm:p-4">
        <div className="mb-3 flex items-center justify-between gap-2"><div className="flex items-center gap-2 text-sm font-bold"><Server size={17} /> VPS COLLECTOR</div>
          <span className={"rounded-full px-2.5 py-1 text-[10px] font-bold " + (collectorReachable === true ? "theme-success-bg theme-success" : collectorReachable === false ? "theme-danger-bg theme-danger" : "theme-surface-2 theme-muted")}>{collectorReachable === true ? "CONNECTED" : collectorReachable === false ? "UNREACHABLE" : "CONNECTING"}</span>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="rounded-xl theme-surface-2 p-3"><div className="text-[10px] theme-subtle">Stored events</div><div className="mt-1 text-xl font-bold tabular-nums">{errors.length}</div></div>
          <div className="rounded-xl theme-surface-2 p-3"><div className="text-[10px] theme-subtle">Critical</div><div className="mt-1 text-xl font-bold text-red-500 tabular-nums">{counts.critical}</div></div>
          <div className="rounded-xl theme-surface-2 p-3"><div className="text-[10px] theme-subtle">Errors</div><div className="mt-1 text-xl font-bold text-red-500 tabular-nums">{counts.error}</div></div>
          <div className="rounded-xl theme-surface-2 p-3"><div className="text-[10px] theme-subtle">Warnings</div><div className="mt-1 text-xl font-bold text-amber-500 tabular-nums">{counts.warning}</div></div>
        </div>
        <div className="mt-3 flex flex-wrap justify-between gap-2 text-[10px] theme-subtle"><span>Source: {source}</span><span>Last successful sync: {lastUpdated ? new Date(lastUpdated).toLocaleTimeString("en-IN") : "Not yet received"}</span></div>
      </Card>

      <Card className="rounded-2xl border theme-border theme-surface-2 p-3 sm:p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2 text-sm font-bold"><TriangleAlert size={17} /> LATEST SERVER EVENTS</div>
          <div className="flex flex-wrap gap-1">{["ALL", "CRITICAL", "ERROR", "WARNING"].map((value) => <button key={value} type="button" onClick={() => setFilter(value)} className={"min-h-9 rounded-lg px-2.5 text-[10px] font-bold " + (filter === value ? "theme-accent-bg theme-accent" : "theme-surface-2 theme-muted")}>{value}</button>)}</div>
        </div>
        {loading && errors.length === 0 ? <div className="py-10 text-center text-xs theme-subtle">Connecting to independent VPS collector…</div> :
          filtered.length === 0 ? <div className="py-10 text-center text-xs theme-subtle">{errors.length === 0 ? "No server events received yet. Collector availability is shown above." : "No events match this filter."}</div> :
          <div className="space-y-2">{filtered.map((item) => {
            const isOpen = expanded.includes(item.id);
            const severity = item.severity.toUpperCase();
            const tone = severity === "CRITICAL" || severity === "ERROR" ? "text-red-500" : "text-amber-500";
            return <div key={item.id} className="min-w-0 rounded-xl border theme-border theme-surface-2 p-3">
              <div className="flex items-start justify-between gap-2"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className={"text-[10px] font-extrabold " + tone}>{severity}</span><span className="break-words text-xs font-bold">{item.component}</span></div><div className="mt-1 text-[10px] theme-subtle">{item.time ? new Date(item.time).toLocaleString("en-IN") : "Timestamp unavailable"}</div></div>
                <button type="button" onClick={() => void copyError(item)} aria-label="Copy error" className="grid h-9 w-9 shrink-0 place-items-center rounded-lg theme-surface-2"><Copy size={14} /></button></div>
              <div className="mt-2 break-words text-xs leading-5">{item.message}</div>
              <div className="mt-2 flex items-center justify-between gap-2"><span className="text-[10px] theme-subtle">{item.source || "systemd-journal"}</span><button type="button" onClick={() => setExpanded((current) => isOpen ? current.filter((id) => id !== item.id) : [...current, item.id])} className="inline-flex min-h-9 items-center gap-1 text-[10px] font-bold theme-accent">{isOpen ? "Hide details" : "Details"} <ChevronDown size={13} className={isOpen ? "rotate-180" : ""} /></button></div>
              {isOpen && <div className="mt-2 break-all rounded-lg border theme-border p-2 font-mono text-[10px] theme-muted">Event ID: {item.id}<br />Source: {item.source || "systemd-journal"}<br />Component: {item.component}</div>}
            </div>;
          })}</div>}
      </Card>
      <div className="px-1 py-3 text-[10px] leading-5 theme-subtle">Read-only diagnostics. Collector stores bounded, sanitized systemd journal events. If the collector or network path is down, this page shows the last successful browser-cached snapshot and its sync time.</div>
    </div>
  );
}
