"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, RefreshCw } from "lucide-react";
import { Card } from "@/components/ui";
import { appConfig } from "@/lib/config";

type TimingGroup = {
  key: string;
  count: number;
  distinct_dates: number;
  max_edge: number;
  average_edge: number;
  average_edge_per_lot: number | null;
};

type TimingAnalysis = {
  strategy: string;
  source: string;
  timezone: string;
  total_saved_snapshots: number;
  positive_edge_observations: number;
  latest_observed_at: string | null;
  minimum_samples_for_average_conclusion: number;
  best_max_edge_day: TimingGroup | null;
  best_max_edge_time: TimingGroup | null;
  best_average_edge_day: TimingGroup | null;
  best_average_edge_time: TimingGroup | null;
  by_day: TimingGroup[];
  by_time: TimingGroup[];
  conclusion: string;
};

const fmt = (n: number | null | undefined) =>
  n == null || !Number.isFinite(Number(n))
    ? "—"
    : Number(n).toLocaleString("en-IN", { maximumFractionDigits: 4 });

export default function BoxSpreadEdgeTimingAnalysis() {
  const [data, setData] = useState<TimingAnalysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const base = appConfig.apiBaseUrl.replace(/\/$/, "");
      const response = await fetch(base + "/api/v1/scanner/box-spread/analysis", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const result = (await response.json()) as TimingAnalysis;
      if (result?.strategy !== "box-spread" || !Array.isArray(result.by_day) || !Array.isArray(result.by_time)) {
        throw new Error("Unexpected analysis response");
      }
      setData(result);
      setError(null);
      setUpdatedAt(new Date().toISOString());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analysis API unavailable");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const groupTable = (title: string, rows: TimingGroup[], label: string) => (
    <div className="min-w-0">
      <h3 className="mb-2 text-sm font-semibold theme-text">{title}</h3>
      {rows.length === 0 ? (
        <p className="rounded-lg border theme-border p-3 text-xs theme-muted">No positive-edge observations saved yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border theme-border">
          <table className="w-full min-w-[360px] text-left text-xs">
            <thead className="theme-surface-2 theme-muted"><tr>
              <th className="px-3 py-2">{label}</th><th className="px-3 py-2">Samples</th><th className="px-3 py-2">Days</th><th className="px-3 py-2">Max edge</th><th className="px-3 py-2">Avg edge</th>
            </tr></thead>
            <tbody>{rows.map((row) => <tr key={row.key} className="border-t theme-border">
              <td className="px-3 py-2 font-medium theme-text">{row.key}{label === "30-min IST" ? " IST" : ""}</td>
              <td className="px-3 py-2 theme-muted">{row.count.toLocaleString("en-IN")}</td>
              <td className="px-3 py-2 theme-muted">{row.distinct_dates.toLocaleString("en-IN")}</td>
              <td className="px-3 py-2 theme-muted">{fmt(row.max_edge)}</td>
              <td className="px-3 py-2 theme-muted">{row.distinct_dates >= (data?.minimum_distinct_dates_for_average_conclusion ?? 5) ? fmt(row.average_edge) : "Need 5 days"}</td>
            </tr>)}</tbody>
          </table>
        </div>
      )}
    </div>
  );

  return (
    <Card className="theme-border theme-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <Activity className="mt-0.5 h-5 w-5 theme-accent" />
          <div><h2 className="text-base font-semibold theme-text">Box Spread Edge Timing Analysis</h2>
            <p className="mt-1 text-xs theme-muted">Complete retained snapshot history · weekday and 30-minute IST buckets · read-only analysis</p>
          </div>
        </div>
        <button type="button" onClick={() => void refresh()} disabled={loading} className="inline-flex min-h-9 items-center gap-2 rounded-lg border theme-border px-3 text-xs font-semibold theme-text disabled:opacity-50">
          <RefreshCw className={"h-3.5 w-3.5 " + (loading ? "animate-spin" : "")} /> Refresh
        </button>
      </div>

      {error && <div role="status" className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs theme-text">
        Analysis refresh failed ({error}). {data ? "Last successfully loaded analysis is retained below." : "No verified analysis response is available yet."}
      </div>}

      {!data && loading && <p className="mt-4 text-sm theme-muted">Loading saved-history analysis…</p>}
      {!data && !loading && error && <p className="mt-3 text-sm theme-muted">Could not load the analysis. Check backend/API connectivity; no sample conclusion is fabricated.</p>}

      {data && <>
        <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Saved snapshots", data.total_saved_snapshots],
            ["Positive-edge observations", data.positive_edge_observations],
            ["Best average day", data.best_average_edge_day ? data.best_average_edge_day.key : "Need 5 dates"],
            ["Best average 30-min window", data.best_average_edge_time ? data.best_average_edge_time.key + " IST" : "Need more samples"],
          ].map(([label, value]) => <div key={String(label)} className="rounded-xl border theme-border theme-surface-2 p-3">
            <p className="text-xs theme-muted">{label}</p><p className="mt-1 break-words text-base font-semibold theme-text">{typeof value === "number" ? value.toLocaleString("en-IN") : value}</p>
          </div>)}
        </div>
        <div className="mt-3 rounded-xl border theme-border theme-surface-2 p-3">
          <p className="text-xs font-semibold uppercase tracking-wide theme-muted">Historical conclusion</p>
          <p className="mt-2 text-sm leading-6 theme-text">{data.conclusion}</p>
          <p className="mt-2 text-[11px] theme-muted">Source: {data.source} · Timezone: {data.timezone} · Average patterns require at least {data.minimum_distinct_dates_for_average_conclusion} distinct trading dates per bucket. Historical edge is not a guarantee of future returns.</p>
          <p className="mt-1 text-[11px] theme-muted">Latest saved observation: {data.latest_observed_at ? new Date(data.latest_observed_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) + " IST" : "None"}{updatedAt ? " · Checked " + new Date(updatedAt).toLocaleTimeString("en-IN") : ""}</p>
        </div>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          {groupTable("Weekday breakdown", data.by_day, "Weekday")}
          {groupTable("30-minute time windows", data.by_time, "30-min IST")}
        </div>
      </>}
    </Card>
  );
}
