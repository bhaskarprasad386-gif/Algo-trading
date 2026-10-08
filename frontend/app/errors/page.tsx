"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, RefreshCw, TriangleAlert } from "lucide-react";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

type DiagnosticError = {
  id: string;
  time: string;
  component: string;
  severity: string;
  message: string;
  error_type?: string | null;
  endpoint?: string | null;
  status_code?: number | null;
  context?: Record<string, unknown> | null;
};

export default function ErrorsPage() {
  const [errors, setErrors] = useState<DiagnosticError[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const loadErrors = async () => {
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(base + "/api/v1/diagnostics/errors?limit=100", { cache: "no-store" });
      if (!response.ok) throw new Error("diagnostics");
      const body = await response.json();
      setErrors(Array.isArray(body?.errors) ? body.errors : []);
      setLastUpdated(new Date().toISOString());
    } catch {
      // Do not manufacture an application error when the diagnostics endpoint is unavailable.
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadErrors();
    const timer = window.setInterval(() => void loadErrors(), 2000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="min-h-screen theme-bg theme-text p-1">
      <PageTitle
        eyebrow="System Diagnostics • Runtime"
        title="View Errors"
        description="Actual backend, market-feed, scanner, WebSocket and database runtime errors."
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <Link href="/" className="inline-flex min-h-10 items-center gap-2 rounded-xl border theme-border theme-surface-2 px-3 text-[11px] font-bold theme-muted">
          <ArrowLeft size={14} /> Command Center
        </Link>
        <button type="button" onClick={() => void loadErrors()} className="inline-flex min-h-10 items-center gap-2 rounded-xl theme-accent-bg px-3 text-[11px] font-bold theme-accent">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      <Card className="mb-4 rounded-2xl border theme-border theme-surface-2 p-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div>
            <div className="text-[9px] uppercase tracking-wide theme-subtle">Runtime Errors</div>
            <div className={errors.length ? "mt-1 text-xl font-bold theme-danger" : "mt-1 text-xl font-bold theme-success"}>{errors.length}</div>
          </div>
          <div>
            <div className="text-[9px] uppercase tracking-wide theme-subtle">Status</div>
            <div className="mt-1 text-sm font-bold">{errors.length ? "ATTENTION" : "HEALTHY"}</div>
          </div>
          <div>
            <div className="text-[9px] uppercase tracking-wide theme-subtle">Refresh</div>
            <div className="mt-1 text-sm font-bold">2s</div>
          </div>
          <div>
            <div className="text-[9px] uppercase tracking-wide theme-subtle">Last Updated</div>
            <div className="mt-1 text-[11px] font-bold">{lastUpdated ? new Date(lastUpdated).toLocaleTimeString("en-IN") : "—"}</div>
          </div>
        </div>
      </Card>

      {loading ? (
        <Card className="rounded-2xl border theme-border theme-surface-2 p-8 text-center text-[11px] theme-subtle">Loading diagnostics…</Card>
      ) : errors.length === 0 ? (
        <Card className="rounded-2xl border theme-border theme-surface-2 p-8 text-center">
          <div className="mx-auto grid h-12 w-12 place-items-center rounded-full theme-success-bg theme-success"><span className="h-3 w-3 rounded-full bg-[var(--app-success)]" /></div>
          <div className="mt-3 text-[14px] font-bold theme-success">No active errors</div>
          <div className="mt-1 text-[10px] theme-subtle">The runtime diagnostics buffer currently has no recorded errors.</div>
        </Card>
      ) : (
        <div className="space-y-3">
          {errors.map((error) => (
            <Card key={error.id} className="rounded-2xl border theme-border theme-surface-2 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-3">
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl theme-danger-bg theme-danger"><TriangleAlert size={17} /></span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[12px] font-bold">{error.component || "Runtime"}</span>
                      <span className="rounded-full theme-danger-bg px-2 py-0.5 text-[9px] font-bold theme-danger">{error.severity || "ERROR"}</span>
                    </div>
                    <div className="mt-1 text-[9px] theme-subtle">{new Date(error.time).toLocaleString("en-IN")}</div>
                  </div>
                </div>
                <span className="text-[9px] font-mono theme-subtle">{error.id}</span>
              </div>

              <div className="mt-3 rounded-xl border theme-border px-3 py-3 text-[11px] font-semibold break-words">{error.message || "No error message supplied."}</div>

              <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                {[
                  ["Type", error.error_type || "—"],
                  ["Endpoint", error.endpoint || "—"],
                  ["HTTP", error.status_code == null ? "—" : String(error.status_code)],
                  ["Context", error.context && Object.keys(error.context).length ? JSON.stringify(error.context) : "—"],
                ].map(([label, value]) => (
                  <div key={label} className="min-w-0 rounded-xl border theme-border theme-surface-2 px-3 py-2">
                    <div className="text-[9px] uppercase tracking-wide theme-subtle">{label}</div>
                    <div className="mt-1 break-words text-[10px] font-semibold">{value}</div>
                  </div>
                ))}
              </div>
            </Card>
          ))}
        </div>
      )}

      <div className="mt-4 text-[9px] theme-subtle">
        Normal market-closed status, zero ticks, and expected runner stops are not treated as errors. Diagnostics are runtime-only and refresh automatically.
      </div>
    </div>
  );
}
