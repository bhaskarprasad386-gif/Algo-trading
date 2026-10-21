"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, RefreshCw, XCircle } from "lucide-react";
import { appConfig } from "@/lib/config";

export function LiveUpdateBadge() {
  const [state, setState] = useState<"CHECKING" | "UPDATED" | "ERROR">("CHECKING");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(base + "/api/v1/scanner/synthetic-cash-carry/live?limit=1", { cache: "no-store" });
        if (!response.ok) throw new Error("backend");
        await response.json().catch(() => ({}));
        if (!cancelled) {
          setState("UPDATED");
          setUpdatedAt(Date.now());
        }
      } catch {
        if (!cancelled) setState("ERROR");
      }
    };
    void check();
    const timer = window.setInterval(check, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  const time = updatedAt == null ? "—" : new Date(updatedAt).toLocaleTimeString("en-IN", { hour12: false, timeZone: "Asia/Kolkata" });
  const age = updatedAt == null ? null : Math.max(0, Math.floor((Date.now() - updatedAt) / 1000));

  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-xl border theme-border theme-surface px-3 py-2 text-xs">
      <div className="flex items-center gap-2 font-semibold">
        {state === "UPDATED" ? <CheckCircle2 className="h-4 w-4 theme-success" /> : state === "ERROR" ? <XCircle className="h-4 w-4 theme-danger" /> : <RefreshCw className="h-4 w-4 animate-spin theme-warning" />}
        <span className={state === "UPDATED" ? "theme-success" : state === "ERROR" ? "theme-danger" : "theme-warning"}>
          {state === "UPDATED" ? "DATA UPDATED" : state === "ERROR" ? "UPDATE FAILED" : "CHECKING DATA"}
        </span>
      </div>
      <span className="theme-muted">
        {state === "UPDATED" ? `Backend response ${time}${age != null ? ` · ${age}s ago` : ""} · auto-check 1.5s` : state === "ERROR" ? "Backend response not confirmed" : "Waiting for backend response"}
      </span>
    </div>
  );
}
