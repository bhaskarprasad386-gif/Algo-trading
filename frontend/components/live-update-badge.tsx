"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RefreshCw, XCircle } from "lucide-react";
import { appConfig } from "@/lib/config";

type CheckState = "CHECKING" | "API_OK" | "ERROR";

export function LiveUpdateBadge() {
  const [state, setState] = useState<CheckState>("CHECKING");
  const [lastSuccessAt, setLastSuccessAt] = useState<number | null>(null);
  const [checkCount, setCheckCount] = useState(0);
  const [sampleRowCount, setSampleRowCount] = useState<number | null>(null);
  const [httpStatus, setHttpStatus] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;

    const check = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(
          base + "/api/v1/scanner/synthetic-cash-carry/live?limit=1",
          { cache: "no-store" },
        );
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error("Backend returned HTTP " + response.status);

        if (!cancelled) {
          setState("API_OK");
          setLastSuccessAt(Date.now());
          setCheckCount((count) => count + 1);
          setHttpStatus(response.status);
          setSampleRowCount(Array.isArray(body?.data) ? body.data.length : 0);
        }
      } catch {
        if (!cancelled) setState("ERROR");
      } finally {
        inFlight = false;
      }
    };

    void check();
    const timer = window.setInterval(() => void check(), 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const age = lastSuccessAt == null
    ? null
    : Math.max(0, Math.floor((now - lastSuccessAt) / 1000));
  const time = lastSuccessAt == null
    ? "—"
    : new Date(lastSuccessAt).toLocaleTimeString("en-IN", {
        hour12: false,
        timeZone: "Asia/Kolkata",
      });

  const stateLabel =
    state === "API_OK" ? "API RESPONSE OK" :
    state === "ERROR" ? "API CHECK FAILED" :
    "CHECKING API";
  const stateClass =
    state === "API_OK" ? "theme-success" :
    state === "ERROR" ? "theme-danger" :
    "theme-warning";

  return (
    <div className="mb-4 rounded-xl border theme-border theme-surface px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-semibold">
          {state === "API_OK" ? (
            <CheckCircle2 className="h-4 w-4 theme-success" />
          ) : state === "ERROR" ? (
            <XCircle className="h-4 w-4 theme-danger" />
          ) : (
            <RefreshCw className="h-4 w-4 animate-spin theme-warning" />
          )}
          <span className={stateClass}>{stateLabel}</span>
        </div>
        <span className="theme-muted">AUTO-CHECK 1.5s · SUCCESSFUL CHECKS {checkCount}</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 theme-muted">
        {lastSuccessAt != null ? (
          <>
            <span>Last successful API check: <b className="theme-text">{time}</b></span>
            <span>Response age: <b className={age != null && age <= 3 ? "theme-success" : "theme-warning"}>{age}s</b></span>
            <span>Last HTTP status: <b className="theme-text">{httpStatus ?? "—"}</b></span>
            <span>Sample rows (limit 1): <b className="theme-text">{sampleRowCount ?? "—"}</b></span>
          </>
        ) : (
          <span>No successful backend response confirmed yet.</span>
        )}
        {state === "ERROR" && (
          <span className="theme-danger">Current check failed; previous success details are retained. This does not confirm fresh market ticks or a trading opportunity.</span>
        )}
        {state === "API_OK" && (
          <span>API reachability only — market-feed freshness and arbitrage edge are reported separately.</span>
        )}
        {state === "CHECKING" && (
          <span>Waiting for the first backend response…</span>
        )}
      </div>
    </div>
  );
}
