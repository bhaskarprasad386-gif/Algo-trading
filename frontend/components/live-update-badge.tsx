"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RefreshCw, XCircle } from "lucide-react";
import { appConfig } from "@/lib/config";

export function LiveUpdateBadge() {
  const [state, setState] = useState<"CHECKING" | "UPDATED" | "ERROR">("CHECKING");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [checkCount, setCheckCount] = useState(0);
  const [rowCount, setRowCount] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/live?limit=1", { cache: "no-store" });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error("backend");
        if (!cancelled) {
          setState("UPDATED");
          setUpdatedAt(Date.now());
          setCheckCount((count) => count + 1);
          setRowCount(Array.isArray(body?.data) ? body.data.length : 0);
        }
      } catch {
        if (!cancelled) setState("ERROR");
      }
    };
    void check();
    const timer = window.setInterval(check, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const age = updatedAt == null ? null : Math.max(0, Math.floor((now - updatedAt) / 1000));
  const time = updatedAt == null ? "—" : new Date(updatedAt).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" });

  return (
    <div className="mb-4 rounded-xl border theme-border theme-surface px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-semibold">
          {state === "UPDATED" ? <CheckCircle2 className="h-4 w-4 theme-success" /> : state === "ERROR" ? <XCircle className="h-4 w-4 theme-danger" /> : <RefreshCw className="h-4 w-4 animate-spin theme-warning" />}
          <span className={state === "UPDATED" ? "theme-success" : state === "ERROR" ? "theme-danger" : "theme-warning"}>
            {state === "UPDATED" ? "DATA UPDATED" : state === "ERROR" ? "UPDATE FAILED" : "CHECKING DATA"}
          </span>
        </div>
        <span className="theme-muted">AUTO-CHECK 1.5s · CHECKS {checkCount}</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 theme-muted">
        {state === "UPDATED" ? (
          <>
            <span>Last backend response: <b className="theme-text">{time}</b></span>
            <span>Age: <b className={age != null && age <= 3 ? "theme-success" : "theme-warning"}>{age ?? 0}s</b></span>
            <span>HTTP: <b className="theme-success">200 OK</b></span>
            <span>Rows returned: <b className="theme-text">{rowCount ?? 0}</b></span>
          </>
        ) : state === "ERROR" ? (
          <span>Backend response not confirmed — last successful update remains the reference.</span>
        ) : (
          <span>Waiting for the first backend response…</span>
        )}
      </div>
    </div>
  );
}
