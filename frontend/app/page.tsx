"use client";

import { useEffect, useRef, useState } from "react";
import { Activity, Bell, Clock3, Database, Gauge, Radio, TriangleAlert, TrendingDown, TrendingUp, Zap, LayoutDashboard } from "lucide-react";
import Link from "next/link";
import { Card, PageTitle } from "@/components/ui";
import { appConfig, getDashboardWsUrl } from "@/lib/config";

const markets = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"];
const strategies = [
  { name: "Cash Future", detail: "Live Scanner • F&O •", href: "/scanner", icon: Radio },
  { name: "Calendar Spread", detail: "Time spread • NIFTY •", href: "/strategies/calendar-spread", icon: Activity },
  { name: "Synthetic Arbitrage", detail: "Cash-Synth • Arbitrage •", href: "/strategies/synthetic-arbitrage", icon: Zap },
  { name: "Box Spread", detail: "Options • Box •", href: "/strategies/box-spread", icon: Gauge },
  { name: "Custom Strategy", detail: "Independent • User-defined •", href: "/strategies/custom-strategy", icon: Activity },
  { name: "Strategy Scanner", detail: "Dedicated workspaces •", href: "/scanner", icon: Radio },
];

export default function HomePage() {
  const [apiStatus, setApiStatus] = useState<"checking" | "connected" | "degraded" | "error">("checking");
  const [apiDetail, setApiDetail] = useState("Waiting for health response");
  const [wsStatus, setWsStatus] = useState<"connecting" | "connected" | "error">("connecting");
  const [snapshot, setSnapshot] = useState({ calendar: 0, synthetic: 0, box: 0, opportunities: 0, timestamp: null as string | null, cashFutureObservedAt: null as string | null });
  const [indexLtps, setIndexLtps] = useState<Record<string, { ltp: number | null; previousClose: number | null; changePercent: number | null; status: string }>>({});
  const [marketSession, setMarketSession] = useState<"OPEN" | "CLOSED" | "UNKNOWN">("UNKNOWN");
  const [mcxSession, setMcxSession] = useState<"OPEN" | "CLOSED" | "UNKNOWN">("UNKNOWN");
  const [marketCheckedAt, setMarketCheckedAt] = useState<string | null>(null);
  const [marketOverviewError, setMarketOverviewError] = useState(false);
  const [runtimeLastSuccessAt, setRuntimeLastSuccessAt] = useState<number | null>(null);
  const [lastDashboardMessageAt, setLastDashboardMessageAt] = useState<number | null>(null);
  const [feedStatus, setFeedStatus] = useState<"checking" | "live" | "stale" | "no-data" | "closed" | "error">("checking");
  const [feedAge, setFeedAge] = useState<number | null>(null);
  const [runtime, setRuntime] = useState<{ticks: number; runtimeStartedAt: number | null; activeInstruments: number; socketGroups: number; connectedGroups: number; connectedGroupNames: string[]; requiredSocketGroups: string[]; requiredSocketGroupCount: number | null; missingSocketGroups: string[]; disconnectedGroups: string[]; maxSocketSessions: number; lastConnectFailure: any; lastSocketFailure: any; retiredTransportCount: number; brokerCooldownRemaining: number; failureRecoveryGroups: string[]; transportCloseTimedOutGroups: string[]; socketHealthByGroup: Record<string, any>; consumers: string[]; deliveryErrors: number; normalizerErrors: number; runners: Record<string, {running: boolean; detail: string; updates: number; payloads: number; instruments: number}>}>({ticks: 0, runtimeStartedAt: null, activeInstruments: 0, socketGroups: 0, connectedGroups: 0, connectedGroupNames: [], requiredSocketGroups: [], requiredSocketGroupCount: null, missingSocketGroups: [], disconnectedGroups: [], maxSocketSessions: 3, lastConnectFailure: null, lastSocketFailure: null, retiredTransportCount: 0, brokerCooldownRemaining: 0, failureRecoveryGroups: [], transportCloseTimedOutGroups: [], socketHealthByGroup: {}, consumers: [], deliveryErrors: 0, normalizerErrors: 0, runners: {}});
  const runnerCountersRef = useRef<Record<string, number> | null>(null);
  const [runnerLastActivity, setRunnerLastActivity] = useState<Record<string, number>>({});
  const [connectionsOpen, setConnectionsOpen] = useState(false);
  const [diagnosticErrors, setDiagnosticErrors] = useState<any[]>([]);
  const [diagnosticCount, setDiagnosticCount] = useState(0);
  const [diagnosticsLastSuccessAt, setDiagnosticsLastSuccessAt] = useState<number | null>(null);
  const [diagnosticsFetchFailed, setDiagnosticsFetchFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const checkServer = async () => {
      try {
        const response = await fetch(appConfig.apiBaseUrl.replace(/\/$/, "") + "/health", { cache: "no-store" });
        if (!response.ok) throw new Error("health HTTP " + response.status);
        const body = await response.json();
        const healthy = body?.status === "ok" && String(body?.database ?? "").toLowerCase() === "connected";
        if (!cancelled) {
          setApiStatus(healthy ? "connected" : "degraded");
          setApiDetail(healthy ? "API and database healthy" : "API responding; database/status degraded (" + String(body?.database ?? body?.status ?? "unknown") + ")");
        }
      } catch (error) {
        if (!cancelled) {
          setApiStatus("error");
          setApiDetail(error instanceof Error ? error.message : "Health endpoint unreachable");
        }
      }
    };
    void checkServer();
    const timer = window.setInterval(checkServer, 5000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let socket: WebSocket | null = null;
    let retryTimer: number | null = null;
    let retryMs = 1000;

    const connect = () => {
      if (cancelled) return;
      setWsStatus("connecting");
      socket = new WebSocket(getDashboardWsUrl() + "/ws/dashboard");
      socket.onopen = () => {
        retryMs = 1000;
        setWsStatus("connected");
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message?.type !== "dashboard_snapshot") return;
          const serverSnapshotTime = message?.timestamp == null ? NaN : new Date(message.timestamp).getTime();
          if (!Number.isFinite(serverSnapshotTime)) return;
          setLastDashboardMessageAt(Date.now());
          setSnapshot({
            calendar: Number(message?.integration?.calendar_spread?.count ?? 0),
            synthetic: Number(message?.integration?.synthetic_arbitrage?.count ?? 0),
            box: Number(message?.integration?.box_spread?.count ?? 0),
            opportunities: Number(message?.scanner?.opportunity_count ?? 0),
            timestamp: message?.timestamp ?? null,
            cashFutureObservedAt: message?.scanner?.health?.last_observation_at ?? null,
          });
        } catch {
          // Ignore malformed dashboard messages without losing the last valid snapshot.
        }
      };
      socket.onerror = () => setWsStatus("error");
      socket.onclose = () => {
        if (cancelled) return;
        setWsStatus("error");
        retryTimer = window.setTimeout(connect, retryMs);
        retryMs = Math.min(retryMs * 2, 10000);
      };
    };

    connect();
    return () => {
      cancelled = true;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadMarketOverview = async () => {
      try {
        const response = await fetch(appConfig.apiBaseUrl.replace(/\/$/, "") + "/api/v1/market-data/overview", { cache: "no-store" });
        if (!response.ok) throw new Error("market overview");
        const body = await response.json();
        const next: Record<string, { ltp: number | null; previousClose: number | null; changePercent: number | null; status: string }> = {};
        const sessionValue = body?.nse_market_session ?? body?.market_session;
        const session = sessionValue === "OPEN" ? "OPEN" : sessionValue === "CLOSED" ? "CLOSED" : "UNKNOWN";
        const mcxValue = body?.mcx_market_session;
        const mcx = mcxValue === "OPEN" ? "OPEN" : mcxValue === "CLOSED" ? "CLOSED" : "UNKNOWN";
        for (const row of Array.isArray(body?.indices) ? body.indices : []) {
          if (row?.symbol) {
            const ltp = row?.ltp == null ? null : Number(row.ltp);
            const previousClose = row?.close == null ? null : Number(row.close);
            const changePercent = ltp != null && previousClose != null && previousClose !== 0 ? ((ltp - previousClose) / previousClose) * 100 : null;
            next[String(row.symbol).toUpperCase()] = { ltp, previousClose, changePercent, status: String(row?.status ?? "NO_QUOTE").toUpperCase() };
          }
        }
        if (!cancelled) {
          setMarketSession(session);
          setMcxSession(mcx);
          setMarketCheckedAt(new Date().toISOString());
          setMarketOverviewError(false);
          setIndexLtps(next);
        }
      } catch {
        if (!cancelled) setMarketOverviewError(true);
        // Keep last values visible, but mark the source unavailable/stale.
      }
    };
    loadMarketOverview();
    const timer = window.setInterval(loadMarketOverview, 5000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadFeedHealth = async () => {
      try {
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(`${base}/api/v1/market-data/live-health`, { cache: "no-store" });
        if (!response.ok) throw new Error("feed health");
        const body = await response.json();
        if (cancelled) return;
        const status = String(body?.feed_status ?? "").toUpperCase();
        const session = String(body?.market_session ?? "").toUpperCase();
        const age = body?.runtime_feed?.age_seconds ?? body?.age_seconds;
        setFeedAge(age == null || !Number.isFinite(Number(age)) ? null : Number(age));
        if (session === "CLOSED") setFeedStatus("closed");
        else if (session !== "OPEN") setFeedStatus("no-data");
        else if (status === "LIVE" && age != null && Number.isFinite(Number(age)) && Number(age) >= 0 && Number(age) <= 5) setFeedStatus("live");
        else if (status === "STALE" || (age != null && Number.isFinite(Number(age)) && Number(age) > 5)) setFeedStatus("stale");
        else setFeedStatus("no-data");
      } catch {
        if (!cancelled) setFeedStatus("error");
      }
    };
    void loadFeedHealth();
    const timer = window.setInterval(loadFeedHealth, 5000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadRuntimeHealth = async () => {
      try {
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(base + "/api/v1/market-data/runtime/health", { cache: "no-store" });
        if (!response.ok) throw new Error("runtime health");
        const body = await response.json();
        if (cancelled) return;
        if (!body || typeof body !== "object" || !body.common_feed || typeof body.common_feed !== "object" || !Array.isArray(body.common_feed.connected_groups) || !Number.isFinite(Number(body.common_feed.active_instruments))) throw new Error("runtime health schema incomplete");
        const feed = body.common_feed;
        setRuntimeLastSuccessAt(Date.now());
        const runners = body?.runners ?? {};
        const currentCounters = {
          cashPayloads: Number(runners.cash_future?.records_received ?? runners.cash_future?.payload_callbacks ?? runners.cash_future?.callbacks ?? 0),
          cashUpdates: Number(runners.cash_future?.scanner_updates ?? runners.cash_future?.scanner_results ?? runners.cash_future?.observations ?? 0),
          syntheticPayloads: Number(runners.synthetic_arbitrage?.records_received ?? runners.synthetic_arbitrage?.payload_callbacks ?? runners.synthetic_arbitrage?.callbacks ?? 0),
          syntheticUpdates: Number(runners.synthetic_arbitrage?.scanner_updates ?? runners.synthetic_arbitrage?.scanner_results ?? runners.synthetic_arbitrage?.observations ?? 0),
          boxPayloads: Number(runners.box_spread?.records_received ?? runners.box_spread?.payload_callbacks ?? runners.box_spread?.callbacks ?? 0),
          boxUpdates: Number(runners.box_spread?.scanner_updates ?? runners.box_spread?.scanner_results ?? runners.box_spread?.observations ?? 0),
          calendarPayloads: Number(runners.calendar_spread?.records_received ?? runners.calendar_spread?.payload_callbacks ?? runners.calendar_spread?.callbacks ?? 0),
          calendarUpdates: Number(runners.calendar_spread?.scanner_updates ?? runners.calendar_spread?.scanner_results ?? runners.calendar_spread?.observations ?? 0),
        };
        const previousCounters = runnerCountersRef.current;
        if (previousCounters) {
          const increased = Object.keys(currentCounters).filter((key) => currentCounters[key as keyof typeof currentCounters] > (previousCounters[key] ?? 0));
          if (increased.length) setRunnerLastActivity((previous) => {
            const next = { ...previous };
            const now = Date.now();
            increased.forEach((key) => { next[key] = now; });
            return next;
          });
        }
        runnerCountersRef.current = currentCounters;
        const runnerDetail = (value: any) => {
          if (!value) return { running: false, detail: "UNAVAILABLE", updates: 0, payloads: 0, instruments: 0 };
          const running = Boolean(value.running);
          const nestedFeed = value.underlying_feed ?? value.feed ?? null;
          const instruments = Number(value.registered_instruments ?? value.active_instruments ?? nestedFeed?.active_instruments ?? nestedFeed?.subscriptions ?? 0);
          const payloads = Number(value.records_received ?? value.payload_callbacks ?? value.callbacks ?? 0);
          const updates = Number(value.scanner_updates ?? value.scanner_results ?? value.observations ?? value.signals ?? 0);
          const connected = nestedFeed?.connected_groups?.length ?? 0;
          const parts = [running ? "RUNNING" : "STOPPED"];
          if (Number.isFinite(instruments) && instruments > 0) parts.push(instruments.toLocaleString("en-IN") + " instruments");
          if (Number.isFinite(payloads) && payloads > 0) parts.push(payloads.toLocaleString("en-IN") + " payloads");
          if (Number.isFinite(updates) && updates > 0) parts.push(updates.toLocaleString("en-IN") + " scanner updates");
          if (connected > 0) parts.push(connected + " feed socket" + (connected === 1 ? "" : "s"));
          return { running, detail: parts.join(" • "), updates: Number.isFinite(updates) ? updates : 0, payloads: Number.isFinite(payloads) ? payloads : 0, instruments: Number.isFinite(instruments) ? instruments : 0 };
        };
        setRuntime({
          ticks: Number(feed.ticks_received ?? 0),
          runtimeStartedAt: feed.runtime_started_at == null ? null : Number(feed.runtime_started_at),
          activeInstruments: Number(feed.active_instruments ?? 0),
          socketGroups: Number(feed.socket_groups ?? 0),
          connectedGroups: Array.isArray(feed.connected_groups) ? feed.connected_groups.length : 0,
          connectedGroupNames: Array.isArray(feed.connected_groups) ? feed.connected_groups : [],
          requiredSocketGroups: Array.isArray(feed.required_socket_groups) ? feed.required_socket_groups : [],
          requiredSocketGroupCount: Number.isFinite(Number(feed.required_socket_group_count)) ? Number(feed.required_socket_group_count) : null,
          missingSocketGroups: Array.isArray(feed.missing_socket_groups) ? feed.missing_socket_groups : [],
          disconnectedGroups: Array.isArray(feed.disconnected_groups) ? feed.disconnected_groups : [],
          lastConnectFailure: feed.last_connect_failure ?? null,
          lastSocketFailure: feed.last_socket_failure ?? null,
          retiredTransportCount: Number(feed.retired_transport_count ?? 0),
          brokerCooldownRemaining: Number(feed.broker_cooldown_remaining_seconds ?? 0),
          failureRecoveryGroups: Array.isArray(feed.failure_recovery_groups) ? feed.failure_recovery_groups : [],
          transportCloseTimedOutGroups: Array.isArray(feed.transport_close_timed_out_groups) ? feed.transport_close_timed_out_groups : [],
          socketHealthByGroup: feed.socket_health_by_group && typeof feed.socket_health_by_group === "object" ? feed.socket_health_by_group : {},
          maxSocketSessions: Number(feed.max_socket_sessions ?? 3),
          consumers: Array.isArray(feed.consumers) ? feed.consumers : [],
          deliveryErrors: Number(feed.delivery_errors ?? 0),
          normalizerErrors: Number(feed.normalizer_errors ?? 0),
          runners: {
            cash_future: runnerDetail(runners.cash_future),
            synthetic_arbitrage: runnerDetail(runners.synthetic_arbitrage),
            box_spread: runnerDetail(runners.box_spread),
            calendar_spread: runnerDetail(runners.calendar_spread),
          },
        });
      } catch {
        // Keep last values for context; UI separately marks telemetry stale/unavailable.
      }
    };
    void loadRuntimeHealth();
    const timer = window.setInterval(loadRuntimeHealth, 1500);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadDiagnosticErrors = async () => {
      try {
        const base = appConfig.apiBaseUrl.replace(/\/$/, "");
        const response = await fetch(base + "/api/v1/diagnostics/errors?limit=50", { cache: "no-store" });
        if (!response.ok) throw new Error("diagnostics");
        const body = await response.json();
        if (!body || typeof body !== "object" || !Array.isArray(body.errors)) throw new Error("diagnostics schema incomplete");
        if (!cancelled) {
          setDiagnosticsLastSuccessAt(Date.now());
          setDiagnosticsFetchFailed(false);
          setDiagnosticErrors(Array.isArray(body?.errors) ? body.errors : []);
          setDiagnosticCount(Number.isFinite(Number(body?.count)) ? Number(body.count) : (Array.isArray(body?.errors) ? body.errors.length : 0));
        }
      } catch {
        if (!cancelled) setDiagnosticsFetchFailed(true);
        // Do not manufacture a fake runtime error when the diagnostic endpoint is unavailable.
      }
    };
    void loadDiagnosticErrors();
    const timer = window.setInterval(loadDiagnosticErrors, 2000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);


  const apiLabel = apiStatus === "connected" ? "CONNECTED" : apiStatus === "degraded" ? "DEGRADED" : apiStatus === "error" ? "ERROR" : "CONNECTING";
  const wsLabel = wsStatus === "connected" ? "CONNECTED" : wsStatus === "error" ? "ERROR" : "CONNECTING";
  const tickRuntimeLabel = runtime.runtimeStartedAt ? new Date(runtime.runtimeStartedAt * 1000).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
  const feedLabel = feedStatus === "live" ? "LIVE" : feedStatus === "stale" ? "STALE" : feedStatus === "no-data" ? "NO DATA" : feedStatus === "closed" ? "CLOSED" : feedStatus === "error" ? "ERROR" : "CHECKING";
  const feedAgeLabel = feedAge == null ? "Not available" : feedAge >= 3600 ? `${Math.floor(feedAge / 3600)}h ${Math.floor((feedAge % 3600) / 60)}m` : feedAge >= 60 ? `${Math.floor(feedAge / 60)}m ${Math.floor(feedAge % 60)}s` : `${feedAge.toFixed(1)}s`;
  const snapshotAge = snapshot.timestamp ? Math.max(0, (Date.now() - new Date(snapshot.timestamp).getTime()) / 1000) : null;
  const dashboardMessageAge = lastDashboardMessageAt == null ? null : Math.max(0, (Date.now() - lastDashboardMessageAt) / 1000);
  const runtimeTelemetryAge = runtimeLastSuccessAt == null ? null : Math.max(0, (Date.now() - runtimeLastSuccessAt) / 1000);
  const runtimeTelemetryStale = runtimeTelemetryAge == null || runtimeTelemetryAge > 8;
  const dashboardStreamStale = wsStatus !== "connected" || dashboardMessageAge == null || dashboardMessageAge > 15;
  const commonSocketState = runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.requiredSocketGroupCount == null ? "UNKNOWN" : runtime.requiredSocketGroupCount === 0 ? "DISCONNECTED" : runtime.requiredSocketGroups.length === runtime.requiredSocketGroupCount && runtime.requiredSocketGroups.every((group) => runtime.connectedGroupNames.includes(group)) && runtime.socketGroups === runtime.requiredSocketGroupCount && runtime.disconnectedGroups.length === 0 ? "CONNECTED" : runtime.connectedGroups > 0 ? "DEGRADED" : "DISCONNECTED";
  const observationAge = snapshot.cashFutureObservedAt ? Math.max(0, (Date.now() - new Date(snapshot.cashFutureObservedAt).getTime()) / 1000) : null;
  const observationAgeLabel = observationAge == null ? "No Cash-Future observation time" : observationAge < 60 ? `Cash-Future observation ${Math.round(observationAge)}s ago` : `Cash-Future observation stale • ${Math.round(observationAge / 60)}m ago`;
  const marketDataAge = marketCheckedAt ? Math.max(0, (Date.now() - new Date(marketCheckedAt).getTime()) / 1000) : null;
  const effectiveMarketSession = marketOverviewError && (marketDataAge == null || marketDataAge > 15) ? "UNKNOWN" : effectiveMarketSession;
  const effectiveMcxSession = marketOverviewError && (marketDataAge == null || marketDataAge > 15) ? "UNKNOWN" : effectiveMcxSession;
  const scannerStatus = [
    { label: "Positive Net-Gap Signals (max 50)", value: snapshot.opportunities.toLocaleString("en-IN") },
    { label: "Calendar Rows", value: snapshot.calendar.toLocaleString("en-IN") },
    { label: "Synthetic Rows", value: snapshot.synthetic.toLocaleString("en-IN") },
    { label: "Box Rows", value: snapshot.box.toLocaleString("en-IN") },
  ];

  return (
    <div className="dashboard-page space-y-5">
      <div className="mb-4 flex flex-col gap-3 rounded-2xl border theme-border theme-surface-2 p-4 sm:flex-row sm:items-center sm:justify-between"><div className="flex items-center gap-3"><span className="grid h-11 w-11 place-items-center rounded-xl theme-accent-bg theme-accent"><LayoutDashboard size={22} /></span><div><div className="text-[10px] font-bold uppercase tracking-[0.2em] theme-accent">ALGO TRADING</div><PageTitle eyebrow="LIVE MARKET OPERATIONS" title="Trading Dashboard" description="Live market status, feed health and scanner activity at a glance." /></div></div><div className="flex items-center gap-2 self-start rounded-full border theme-border px-3 py-2 text-[10px] font-bold sm:self-auto"><span className={"h-2 w-2 rounded-full "+(apiStatus === "error" ? "theme-danger-bg" : apiStatus === "connected" && !runtimeTelemetryStale && !dashboardStreamStale && commonSocketState === "CONNECTED" && feedStatus === "live" ? "theme-success-bg" : "theme-warning-bg")}></span>{apiStatus === "error" ? "SERVER OFFLINE" : apiStatus === "degraded" ? "SERVER DEGRADED" : apiStatus !== "connected" ? "CHECKING SERVER" : runtimeTelemetryStale ? "RUNTIME TELEMETRY STALE" : dashboardStreamStale ? "DASHBOARD STREAM STALE" : commonSocketState === "DISCONNECTED" ? "WEBSOCKET DISCONNECTED" : commonSocketState === "DEGRADED" ? "WEBSOCKET DEGRADED" : commonSocketState === "UNKNOWN" ? "WEBSOCKET HEALTH UNKNOWN" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED" : feedStatus === "stale" ? "MARKET DATA STALE" : feedStatus === "live" && commonSocketState === "CONNECTED" ? "SYSTEM LIVE" : "NO LIVE MARKET DATA"}</div></div>
      <section aria-label="Market and connection status">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[
            { label: "NSE EQUITY", value: effectiveMarketSession, detail: "09:15–15:30 IST", Icon: Activity },
            { label: "MCX COMMODITIES", value: effectiveMcxSession, detail: "Session from market API", Icon: Activity },
            { label: "API SERVER", value: apiStatus === "connected" ? "ONLINE" : apiStatus === "degraded" ? "DEGRADED" : apiStatus === "error" ? "OFFLINE" : "CHECKING", detail: apiDetail, Icon: Database },
            { label: "COMMON WEBSOCKET", value: commonSocketState, detail: `${runtime.connectedGroups}/${runtime.requiredSocketGroupCount == null ? "?" : runtime.requiredSocketGroupCount} required groups connected • ${runtime.socketGroups} active sockets • cap ${runtime.maxSocketSessions}`, Icon: Radio },
          ].map((item) => {
            const Icon = item.Icon;
            const positive = item.label === "API SERVER" ? apiStatus === "connected" : item.label === "COMMON WEBSOCKET" ? commonSocketState === "CONNECTED" : item.label === "NSE EQUITY" || item.label === "MCX COMMODITIES" ? item.value === "OPEN" : false;
            const warning = item.value === "CLOSED" || item.value === "UNKNOWN" || item.value === "DEGRADED" || item.value === "UNKNOWN" || item.value === "NO SUBSCRIPTIONS" || item.value === "CHECKING";
            return <Card key={item.label} className="flex min-h-[74px] items-center gap-3 rounded-xl border theme-border theme-surface-2 p-3">
              <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-full ${positive ? "theme-success-bg theme-success" : warning ? "theme-warning-bg theme-warning" : "theme-danger-bg theme-danger"}`}><Icon size={18} /></span>
              <div className="min-w-0">
                <div className="text-[9px] font-bold uppercase tracking-[0.12em] theme-subtle">{item.label}</div>
                <div className={`mt-1 text-[12px] font-extrabold ${positive ? "theme-success" : warning ? "theme-warning" : "theme-danger"}`}>{item.value}</div>
                <div className="mt-0.5 text-[9px] theme-subtle">{item.detail}</div>
              </div>
            </Card>;
          })}
        </div>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border theme-border theme-surface-2 px-3 py-2">
          <div className="text-[9px] leading-4 theme-subtle">Exchange session is schedule-based. API response time is not the same as exchange tick time. Cash-Future freshness uses its server observation timestamp; other scanner ages reflect browser-observed counter movement.</div>
          <div className={`inline-flex shrink-0 items-center gap-2 rounded-full px-2.5 py-1 text-[10px] font-semibold ${feedLabel === "LIVE" ? "theme-success-bg theme-success" : feedLabel === "CLOSED" ? "theme-warning-bg theme-warning" : "theme-danger-bg theme-danger"}`}><Radio size={12} /> {feedLabel === "LIVE" ? "Market feed LIVE" : feedLabel === "CLOSED" ? "NSE MARKET CLOSED" : `Market feed ${feedLabel.toLowerCase()}`}</div>
        </div>
      </section>

      <section aria-label="Market overview">
        <div className="mb-3 flex items-center gap-2"><Activity size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">Market Overview</h2></div>
        <div className="grid grid-cols-1 gap-2.5 min-[380px]:grid-cols-2 sm:gap-3 md:grid-cols-3 xl:grid-cols-6">
          {markets.map((symbol) => {
            const quote = indexLtps[symbol];
            const quoteAvailable = !marketOverviewError && marketDataAge != null && marketDataAge <= 15 && (effectiveMarketSession === "OPEN" ? quote?.status === "LIVE" : effectiveMarketSession === "CLOSED" ? quote?.status === "CLOSED_LAST_CLOSE" : false);
            const change = quoteAvailable ? quote?.changePercent : null;
            const positive = change != null && change > 0;
            const negative = change != null && change < 0;
            return (
                <Card key={symbol} className="relative min-w-0 overflow-hidden rounded-2xl p-3 sm:p-4">
                  <span className="absolute inset-y-0 left-0 w-1 theme-accent-bg" />
                  <div className="pl-1">
                    <div className="flex items-center justify-between"><span className="text-[12px] font-bold tracking-wide">{symbol}</span>{effectiveMarketSession === "CLOSED" ? <span className="rounded-full theme-warning-bg theme-warning px-2 py-0.5 text-[9px] font-bold">CLOSED</span> : null}{positive ? <TrendingUp size={15} className="theme-success" /> : negative ? <TrendingDown size={15} className="theme-danger" /> : <Activity size={15} className="theme-subtle" />}</div>
                    <div className="mt-3 flex items-center justify-between gap-2">
                      <div className={"break-words text-base font-bold sm:text-lg " + (positive ? "theme-success" : negative ? "theme-danger" : "theme-text")}>{!quoteAvailable || quote?.ltp == null ? (effectiveMarketSession === "CLOSED" ? "Last close unavailable" : "No broker quote") : quote.ltp.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                      {(!quoteAvailable || quote?.ltp == null) && <Radio size={14} className="theme-warning" aria-label="No verified quote" />}
                    </div>
                    <div className={"mt-1 text-[11px] font-bold sm:text-[12px] " + (positive ? "theme-success" : negative ? "theme-danger" : "theme-muted")}>{change == null ? "No live change" : (change > 0 ? "+" : "") + change.toFixed(2) + "%"}</div>
                    <div className="mt-1 break-words text-[10px] theme-subtle">{!quoteAvailable || quote?.previousClose == null ? "Previous close unavailable" : effectiveMarketSession === "CLOSED" ? "Last close: " + quote.previousClose.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "Prev close: " + quote.previousClose.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                    <div className="mt-3 flex items-center justify-between border-t theme-border pt-2 text-[10px] font-semibold theme-muted">
                      <span>{effectiveMarketSession === "CLOSED" ? "Last trading close" : quoteAvailable ? "Broker quote returned" : "Awaiting broker quote"}</span><span>{effectiveMarketSession === "CLOSED" ? "LAST CLOSE" : quoteAvailable ? "QUOTE RESPONSE" : "NO DATA"}</span>
                    </div>
                  </div>
                </Card>
            );
          })}
        </div>
      </section>

      <div className="mt-6 grid gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(300px,0.8fr)]">
        <div className="min-w-0">
        <section aria-label="System health">
          <div className="mb-3 flex items-center justify-between gap-3"><div className="flex items-center gap-2"><Gauge size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">System Health</h2></div><div className="text-[10px] theme-subtle">Auto-refreshing live telemetry</div></div>
          <Card className="overflow-hidden rounded-2xl border theme-border theme-surface-2 p-0 shadow-sm">
            <div className="overflow-x-auto"><table className="w-full border-collapse text-left md:min-w-[1080px]">
              <thead className="hidden theme-surface md:table-header-group"><tr className="border-b theme-border text-[9px] uppercase tracking-[0.12em] theme-subtle">
                <th className="px-3 py-3 font-bold">Feed / Scanner</th><th className="px-3 py-3 font-bold">WebSocket / State</th><th className="px-3 py-3 font-bold">Live ticks / updates</th><th className="px-3 py-3 font-bold">Tick / update age</th><th className="px-3 py-3 font-bold">Data flow</th><th className="px-3 py-3 font-bold">Scanner position</th>
              </tr></thead><tbody className="block md:table-row-group">
                {(() => {
                  const now = Date.now();
                  const activityAge = (key: string) => runnerLastActivity[key] == null ? null : Math.max(0, (now - runnerLastActivity[key]) / 1000);
                  const ageText = (age: number | null) => age == null ? "No recent sample" : age < 60 ? Math.round(age) + "s" : Math.round(age / 60) + "m";
                  const freshness = (age: number | null) => age != null && age <= 10 ? "RECEIVING" : age != null && age <= 30 ? "STALE" : "NO RECENT UPDATES";
                  const serverDown = apiStatus === "error";
                  const wsState = commonSocketState;
                  const overallFlow = serverDown ? "SERVER UNREACHABLE" : runtimeTelemetryStale ? "RUNTIME TELEMETRY UNAVAILABLE" : wsState === "DISCONNECTED" ? "WEBSOCKET DISCONNECTED" : wsState === "DEGRADED" ? "WEBSOCKET DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET HEALTH UNKNOWN" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED • NSE TICKS NOT EXPECTED" : feedStatus === "live" ? "LIVE TICKS" : feedStatus === "stale" ? "STALE MARKET DATA" : feedStatus === "error" ? "FEED HEALTH ERROR" : "NO MARKET DATA";
                  const runnerState = (running: boolean, age: number | null) => serverDown ? "SERVER OFFLINE" : runtimeTelemetryStale ? "TELEMETRY STALE" : wsState === "DISCONNECTED" ? "WEBSOCKET DOWN" : wsState === "DEGRADED" ? "FEED DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET STATUS UNKNOWN" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED" : !running ? "STOPPED" : age != null && age <= 10 ? "RECEIVING" : age != null && age <= 30 ? "STALE" : "NO DATA";
                  const cashAge = activityAge("cashPayloads");
                  const syntheticAge = activityAge("syntheticPayloads");
                  const boxAge = activityAge("boxPayloads");
                  const calendarAge = activityAge("calendarPayloads");
                  const optionsAge = boxAge != null && calendarAge != null ? Math.min(boxAge, calendarAge) : boxAge ?? calendarAge;
                  const cashScannerAge = observationAge;
                  const rows = [
                    {name:"API SERVER", state:serverDown ? "OFFLINE" : apiStatus === "degraded" ? "DEGRADED" : apiStatus === "connected" ? "ONLINE" : "CHECKING", updates:0, age:apiStatus === "connected" || apiStatus === "degraded" ? "Health endpoint responding" : "No response", flow:serverDown ? "SERVER UNREACHABLE" : apiStatus === "degraded" ? "DATABASE / SERVER DEGRADED" : apiStatus === "connected" ? "API + DATABASE HEALTHY" : "CHECKING", position:apiDetail},
                    {name:"OVERALL COMMON FEED", state:serverDown ? "SERVER OFFLINE" : wsState, updates:runtime.ticks, age:feedAgeLabel, flow:overallFlow, position:runtime.activeInstruments.toLocaleString("en-IN")+" active instruments • "+runtime.connectedGroups+"/"+(runtime.requiredSocketGroupCount == null ? "?" : runtime.requiredSocketGroupCount)+" required groups • "+runtime.socketGroups+" active sockets • cap "+runtime.maxSocketSessions},
                    {name:"CASH MARKET DATA", state:serverDown ? "SERVER OFFLINE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DOWN" : wsState === "DEGRADED" ? "FEED DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET STATUS UNKNOWN" : runtimeTelemetryStale ? "TELEMETRY STALE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED" : freshness(cashAge), updates:runtime.runners.cash_future?.payloads ?? 0, age:ageText(cashAge), flow:serverDown ? "SERVER UNREACHABLE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DISCONNECTED" : wsState === "DEGRADED" ? "WEBSOCKET DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET HEALTH UNKNOWN" : runtimeTelemetryStale ? "RUNTIME TELEMETRY UNAVAILABLE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED • NSE TICKS NOT EXPECTED" : freshness(cashAge), position:runtime.runners.cash_future?.instruments ? runtime.runners.cash_future.instruments.toLocaleString("en-IN")+" instruments • "+(runtime.runners.cash_future?.payloads ?? 0).toLocaleString("en-IN")+" cumulative payloads" : "No instrument count"},
                    {name:"OPTIONS DATA", state:serverDown ? "SERVER OFFLINE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DOWN" : wsState === "DEGRADED" ? "FEED DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET STATUS UNKNOWN" : runtimeTelemetryStale ? "TELEMETRY STALE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED" : runnerState(Boolean(runtime.runners.box_spread?.running || runtime.runners.calendar_spread?.running), optionsAge), updates:(runtime.runners.box_spread?.payloads ?? 0)+(runtime.runners.calendar_spread?.payloads ?? 0), age:ageText(optionsAge), flow:serverDown ? "SERVER UNREACHABLE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DISCONNECTED" : wsState === "DEGRADED" ? "WEBSOCKET DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET HEALTH UNKNOWN" : runtimeTelemetryStale ? "RUNTIME TELEMETRY UNAVAILABLE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED • NSE TICKS NOT EXPECTED" : freshness(optionsAge), position:"Box + Calendar • cumulative counters alone do not prove fresh data"},
                    {name:"FUTURES DATA", state:serverDown ? "SERVER OFFLINE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DOWN" : wsState === "DEGRADED" ? "FEED DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET STATUS UNKNOWN" : runtimeTelemetryStale ? "TELEMETRY STALE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED" : runnerState(Boolean(runtime.runners.synthetic_arbitrage?.running), syntheticAge), updates:runtime.runners.synthetic_arbitrage?.payloads ?? 0, age:ageText(syntheticAge), flow:serverDown ? "SERVER UNREACHABLE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DISCONNECTED" : wsState === "DEGRADED" ? "WEBSOCKET DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET HEALTH UNKNOWN" : runtimeTelemetryStale ? "RUNTIME TELEMETRY UNAVAILABLE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED • NSE TICKS NOT EXPECTED" : freshness(syntheticAge), position:"Synthetic feed • cumulative counter "+(runtime.runners.synthetic_arbitrage?.payloads ?? 0).toLocaleString("en-IN")},
                    {name:"CASH-FUTURE SCANNER", state:serverDown ? "SERVER OFFLINE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DOWN" : wsState === "DEGRADED" ? "FEED DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET STATUS UNKNOWN" : runtimeTelemetryStale ? "TELEMETRY STALE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED" : !runtime.runners.cash_future?.running ? "STOPPED" : cashScannerAge != null && cashScannerAge <= 5 ? "RECEIVING" : cashScannerAge != null && cashScannerAge <= 30 ? "STALE" : "NO DATA", updates:runtime.runners.cash_future?.updates ?? 0, age:cashScannerAge == null ? "No observation" : ageText(cashScannerAge), flow:serverDown ? "SERVER UNREACHABLE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DISCONNECTED" : wsState === "DEGRADED" ? "WEBSOCKET DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET HEALTH UNKNOWN" : runtimeTelemetryStale ? "RUNTIME TELEMETRY UNAVAILABLE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED • NSE TICKS NOT EXPECTED" : cashScannerAge != null && cashScannerAge <= 5 ? "LIVE OBSERVATION" : cashScannerAge != null && cashScannerAge <= 30 ? "STALE OBSERVATION" : "NO RECENT OBSERVATION", position:runtime.runners.cash_future?.detail ?? "UNAVAILABLE"},
                    {name:"SYNTHETIC ARBITRAGE", state:serverDown ? "SERVER OFFLINE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DOWN" : wsState === "DEGRADED" ? "FEED DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET STATUS UNKNOWN" : runtimeTelemetryStale ? "TELEMETRY STALE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED" : runnerState(Boolean(runtime.runners.synthetic_arbitrage?.running), syntheticAge), updates:runtime.runners.synthetic_arbitrage?.updates ?? 0, age:ageText(syntheticAge), flow:serverDown ? "SERVER UNREACHABLE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DISCONNECTED" : wsState === "DEGRADED" ? "WEBSOCKET DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET HEALTH UNKNOWN" : runtimeTelemetryStale ? "RUNTIME TELEMETRY UNAVAILABLE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED • NSE TICKS NOT EXPECTED" : freshness(syntheticAge), position:runtime.runners.synthetic_arbitrage?.detail ?? "UNAVAILABLE"},
                    {name:"BOX SPREAD SCANNER", state:serverDown ? "SERVER OFFLINE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DOWN" : wsState === "DEGRADED" ? "FEED DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET STATUS UNKNOWN" : runtimeTelemetryStale ? "TELEMETRY STALE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED" : runnerState(Boolean(runtime.runners.box_spread?.running), boxAge), updates:runtime.runners.box_spread?.updates ?? 0, age:ageText(boxAge), flow:serverDown ? "SERVER UNREACHABLE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DISCONNECTED" : wsState === "DEGRADED" ? "WEBSOCKET DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET HEALTH UNKNOWN" : runtimeTelemetryStale ? "RUNTIME TELEMETRY UNAVAILABLE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED • NSE TICKS NOT EXPECTED" : freshness(boxAge), position:runtime.runners.box_spread?.detail ?? "UNAVAILABLE"},
                    {name:"CALENDAR SPREAD SCANNER", state:serverDown ? "SERVER OFFLINE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DOWN" : wsState === "DEGRADED" ? "FEED DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET STATUS UNKNOWN" : runtimeTelemetryStale ? "TELEMETRY STALE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED" : runnerState(Boolean(runtime.runners.calendar_spread?.running), calendarAge), updates:runtime.runners.calendar_spread?.updates ?? 0, age:ageText(calendarAge), flow:serverDown ? "SERVER UNREACHABLE" : runtime.activeInstruments === 0 ? "NO SUBSCRIPTIONS" : runtime.connectedGroups === 0 ? "WEBSOCKET DISCONNECTED" : wsState === "DEGRADED" ? "WEBSOCKET DEGRADED" : wsState === "UNKNOWN" ? "WEBSOCKET HEALTH UNKNOWN" : runtimeTelemetryStale ? "RUNTIME TELEMETRY UNAVAILABLE" : effectiveMarketSession === "CLOSED" ? "NSE MARKET CLOSED • NSE TICKS NOT EXPECTED" : freshness(calendarAge), position:runtime.runners.calendar_spread?.detail ?? "UNAVAILABLE"},
                  ];
                  return rows.map((row) => {
                    const healthy = ["ONLINE","CONNECTED","RECEIVING"].includes(row.state) && ["HTTP HEALTH OK","LIVE TICKS","LIVE OBSERVATION","RECEIVING"].includes(row.flow);
                    const danger = ["OFFLINE","SERVER OFFLINE","WEBSOCKET DOWN","DISCONNECTED","NO DATA","NO RECENT UPDATES","NO RECENT OBSERVATION"].includes(row.state) || /UNREACHABLE|DISCONNECTED|NO DATA|NO RECENT/.test(row.flow);
                    return <tr key={row.name} className="mb-2 block rounded-xl border theme-border p-2 last:mb-0 md:mb-0 md:table-row md:border-0 md:border-b md:p-0">
                      <td className="block px-3 py-2 text-[10px] font-bold md:table-cell md:py-3"><span className="mb-1 block text-[9px] font-medium uppercase tracking-wide theme-subtle md:hidden">Feed / Scanner</span>{row.name}</td><td className="block px-3 py-2 md:table-cell md:py-3"><span className="mb-1 block text-[9px] font-medium uppercase tracking-wide theme-subtle md:hidden">WebSocket / State</span><span className={"inline-block rounded-full px-2 py-1 text-[9px] font-bold "+(healthy ? "theme-success-bg theme-success" : danger ? "theme-danger-bg theme-danger" : "theme-warning-bg theme-warning")}>{row.state}</span></td>
                      <td className="block px-3 py-2 text-[11px] font-bold tabular-nums md:table-cell md:py-3"><span className="mb-1 block text-[9px] font-medium uppercase tracking-wide theme-subtle md:hidden">Live ticks / updates</span>{Number(row.updates).toLocaleString("en-IN")}</td><td className="block px-3 py-2 text-[10px] font-semibold theme-muted md:table-cell md:py-3"><span className="mb-1 block text-[9px] font-medium uppercase tracking-wide theme-subtle md:hidden">Tick / update age</span>{row.age}</td>
                      <td className="block px-3 py-2 text-[9px] font-bold md:table-cell md:py-3"><span className="mb-1 block text-[9px] font-medium uppercase tracking-wide theme-subtle md:hidden">Data flow</span><span className={healthy ? "theme-success" : danger ? "theme-danger" : "theme-warning"}>{row.flow}</span></td><td className="block max-w-[360px] px-3 py-2 text-[10px] theme-muted md:table-cell md:py-3"><span className="mb-1 block text-[9px] font-medium uppercase tracking-wide theme-subtle md:hidden">Scanner position</span>{row.position}</td>
                    </tr>;
                  });
                })()}
              </tbody></table></div>
          </Card>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">{[
            ["Overall live ticks", runtime.ticks.toLocaleString("en-IN")],["Active instruments", runtime.activeInstruments.toLocaleString("en-IN")],
            ["Connected sockets", runtime.connectedGroups+"/"+runtime.maxSocketSessions],["Delivery / normalizer errors", runtime.deliveryErrors+" / "+runtime.normalizerErrors],
          ].map(([label,value]) => <div key={label} className="rounded-xl border theme-border theme-surface-2 px-3 py-2"><div className="text-[9px] uppercase tracking-wide theme-subtle">{label}</div><div className="mt-1 text-[11px] font-bold">{value}</div></div>)}</div>
        </section>
        </div>
        <aside className="min-w-0 space-y-4" aria-label="Live scanner panel">
          <section aria-label="Scanner status" className="rounded-2xl border theme-border theme-surface-2 p-4"><div className="mb-3 flex items-center justify-between gap-2"><div className="flex items-center gap-2"><Radio size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">Scanner Status</h2></div><div className="text-[10px] theme-subtle">{snapshotAge == null ? "No dashboard heartbeat yet" : snapshotAge < 10 ? `Heartbeat ${Math.round(snapshotAge)}s ago` : `Heartbeat stale • ${Math.round(snapshotAge)}s ago`} • {observationAgeLabel}</div></div><div className="grid grid-cols-2 gap-2">{scannerStatus.map((item) => <Card key={item.label} className="rounded-xl p-2.5"><div className="text-[10px] font-medium theme-subtle">{item.label}</div><div className="mt-1 text-[11px] font-semibold">{item.value}</div></Card>)}</div></section>
        <Card className="rounded-2xl border theme-border theme-surface-2 p-4"><div className="mb-3 flex items-center gap-2"><Database size={16} className="theme-accent" /><h2 className="text-[15px] font-semibold">Feed Snapshot</h2></div><div className="grid grid-cols-2 gap-2">{[["Live ticks",runtime.ticks.toLocaleString("en-IN")],["Active instruments",runtime.activeInstruments.toLocaleString("en-IN")],["Socket groups",runtime.connectedGroups+"/"+runtime.maxSocketSessions],["Feed age",feedAgeLabel]].map(([label,value])=><div key={label} className="rounded-xl border theme-border px-3 py-2"><div className="text-[9px] theme-subtle">{label}</div><div className="mt-1 text-[11px] font-bold">{value}</div></div>)}</div></Card>
        </aside>
      </div>

      <section aria-label="Error Diagnostics" className="mt-4">
        <Card className="rounded-2xl border theme-border theme-surface-2 p-3">
          <div className="grid grid-cols-[1fr_auto] items-center gap-3">
            <div className="flex min-w-0 items-center gap-3">
              <span className={diagnosticErrors.length ? "grid h-10 w-10 shrink-0 place-items-center rounded-xl theme-danger-bg theme-danger" : "grid h-10 w-10 shrink-0 place-items-center rounded-xl theme-success-bg theme-success"}>
                <TriangleAlert size={18} />
              </span>
              <div className="min-w-0">
                <div className="text-[13px] font-bold">Error Diagnostics</div>
                <div className="mt-0.5 text-[10px] theme-subtle">
                  {diagnosticCount ? `${diagnosticCount.toLocaleString("en-IN")} runtime errors recorded since API process start (not an active-error count)` : "No errors recorded by this endpoint since API process start"}
                </div>
              </div>
            </div>
            <Link href="/errors" className="inline-flex min-h-10 items-center justify-center rounded-xl theme-accent-bg px-4 text-[11px] font-bold theme-accent">
              View Errors
            </Link>
          </div>
        </Card>
      </section>
      <div className="mt-3 grid gap-3 xl:grid-cols-[1.6fr_1fr]">
        <section aria-label="Operations" className="rounded-xl border theme-border theme-surface-2 p-3">
          <div className="mb-3 flex items-center gap-2"><Zap size={15} className="theme-accent" /><h2 className="text-[13px] font-semibold">Operations</h2></div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <Link href="/scanner"><Card className="rounded-lg p-3 transition hover:-translate-y-0.5"><div className="flex gap-2"><Radio size={16} className="theme-success" /><div><div className="text-[11px] font-semibold">Cash Future</div><div className="text-[9px] theme-subtle">Open Cash Future scanner</div></div></div></Card></Link>
            <Link href="/custom-alert"><Card className="rounded-lg p-3 transition hover:-translate-y-0.5"><div className="flex gap-2"><Bell size={16} className="theme-warning" /><div><div className="text-[11px] font-semibold">Custom Alerts</div><div className="text-[9px] theme-subtle">Manage alerts</div></div></div></Card></Link>
            <Link href="/history"><Card className="rounded-lg p-3 transition hover:-translate-y-0.5"><div className="flex gap-2"><Clock3 size={16} className="theme-accent" /><div><div className="text-[11px] font-semibold">History</div><div className="text-[9px] theme-subtle">Trade history & logs</div></div></div></Card></Link>
          </div>
        </section>
        <Card className="flex flex-col justify-between gap-3 rounded-xl border theme-border theme-surface-2 p-3 sm:flex-row sm:items-center xl:flex-col xl:items-start">
          <div className="flex min-w-0 items-start gap-3"><Database size={16} className="mt-0.5 shrink-0 theme-accent" /><div><div className="text-[12px] font-semibold">Market Data Architecture</div><div className="mt-1 text-[10px] leading-4 theme-subtle">Shared live market data feeds the independent scanners. This dashboard displays API-reported feed and scanner health.</div></div></div>
          <button type="button" onClick={() => setConnectionsOpen(true)} className="shrink-0 rounded-lg theme-accent-bg px-3 py-2 text-[10px] font-bold theme-accent">View Connections →</button>
        </Card>
      </div>
      {connectionsOpen ? (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-labelledby="connections-title" onMouseDown={(event) => { if (event.target === event.currentTarget) setConnectionsOpen(false); }}>
          <Card className="w-full max-w-xl rounded-2xl border theme-border theme-surface-2 p-5 shadow-2xl">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div id="connections-title" className="text-[14px] font-bold">Connections</div>
                <div className="mt-1 text-[10px] theme-subtle">Current frontend connection state</div>
              </div>
              <button type="button" onClick={() => setConnectionsOpen(false)} className="rounded-lg px-2 py-1 text-xs theme-muted" aria-label="Close connections">✕</button>
            </div>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {[
                ["FastAPI", apiLabel + " • " + apiDetail],
                ["Dashboard WebSocket", wsLabel],
                ["Common Market Data Feed", feedLabel],
                ["Ticks Received (session)", runtime.ticks.toLocaleString("en-IN")],
                ["Tick Counter", runtime.runtimeStartedAt ? `Session • ${tickRuntimeLabel}` : "—"],
                ["Cash Future Runner", runtime.runners.cash_future?.detail ?? "UNAVAILABLE"],
                ["Synthetic Future Runner", runtime.runners.synthetic_arbitrage?.detail ?? "UNAVAILABLE"],
                ["Box Spread Runner", runtime.runners.box_spread?.detail ?? "UNAVAILABLE"],
                ["Calendar Spread Runner", runtime.runners.calendar_spread?.detail ?? "UNAVAILABLE"],
                ["Broker Orders", "OFF"],
                ["Common Feed Instruments", runtime.activeInstruments.toLocaleString("en-IN")],
                ["Socket Groups", runtime.connectedGroups + "/" + (runtime.requiredSocketGroupCount == null ? "?" : runtime.requiredSocketGroupCount) + " required • " + runtime.socketGroups + " active • cap " + runtime.maxSocketSessions],
                ["Runtime Telemetry", runtimeTelemetryStale ? "STALE / UNAVAILABLE" : "FRESH"],
                ["Diagnostics Endpoint", diagnosticsFetchFailed ? "REQUEST FAILED" : diagnosticsLastSuccessAt == null ? "NOT CHECKED" : Date.now() - diagnosticsLastSuccessAt > 8000 ? "STALE" : "RESPONDING"],
                ["Disconnected Groups", [...runtime.disconnectedGroups, ...runtime.missingSocketGroups].filter((value, index, values) => values.indexOf(value) === index).join(", ") || "None reported"],
                ["Last Connect Failure", runtime.lastConnectFailure ? String(runtime.lastConnectFailure.error ?? runtime.lastConnectFailure.error_type ?? JSON.stringify(runtime.lastConnectFailure)) : "None reported"],
                ["Last Socket Failure", runtime.lastSocketFailure ? String(runtime.lastSocketFailure.reason ?? JSON.stringify(runtime.lastSocketFailure)) : "None reported"],
                ["Retired Transports", String(runtime.retiredTransportCount)],
                ["Broker Cooldown", `${Math.ceil(runtime.brokerCooldownRemaining)}s remaining`],
                ["Recovery Pending Groups", runtime.failureRecoveryGroups.join(", ") || "None"],
                ["Transport Close Timeout Groups", runtime.transportCloseTimedOutGroups.join(", ") || "None"],
                ["Socket Health by Group", Object.entries(runtime.socketHealthByGroup).map(([group, health]) => `${group}: ${health.connected ? "connected" : health.connecting ? "connecting" : "disconnected"}${health.last_error ? " • " + health.last_error : ""}`).join(" | ") || "No socket details returned"],
                ["Dashboard Snapshot Age", dashboardMessageAge == null ? "No snapshot received" : Math.round(dashboardMessageAge) + "s"],
                ["Last Feed Tick Age", feedAgeLabel],
                ["Delivery / Normalizer Errors", runtime.deliveryErrors + " / " + runtime.normalizerErrors],
                ["NSE Session", effectiveMarketSession],
                ["MCX Session", effectiveMcxSession],
              ].map(([name, value]) => (
                <div key={name} className="flex items-center justify-between rounded-xl border theme-border theme-surface-2 px-3 py-2.5">
                  <span className="text-[11px] theme-muted">{name}</span>
                  <span className="text-[10px] font-bold theme-accent">{value}</span>
                </div>
              ))}
            </div>
            <div className="mt-4 rounded-xl border theme-border theme-surface-2 p-3">
              <div className="text-[10px] font-bold uppercase tracking-wide">Runtime Errors Recorded Since API Process Start</div>
              {diagnosticsLastSuccessAt == null ? <div className="mt-2 text-[10px] theme-warning">Diagnostics have not returned a successful sample yet.</div> : diagnosticsFetchFailed && Date.now() - diagnosticsLastSuccessAt > 8000 ? <div className="mt-2 text-[10px] theme-danger">Diagnostics endpoint unavailable. Previous error records are retained and may be stale.</div> : diagnosticErrors.length === 0 ? <div className="mt-2 text-[10px] theme-muted">No error records in the latest successful sample. This does not prove zero active faults.</div> : (
                <div className="mt-2 max-h-48 space-y-2 overflow-auto">
                  {diagnosticErrors.map((error) => (
                    <div key={error.id} className="rounded-lg border theme-border px-2.5 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[10px] font-bold">{error.component} • {error.severity}</span>
                        <span className="text-[9px] theme-subtle">{new Date(error.time).toLocaleString("en-IN")}</span>
                      </div>
                      <div className="mt-1 text-[10px]">{error.message}</div>
                      <div className="mt-1 text-[9px] theme-subtle">{[error.error_type && "Type: " + error.error_type, error.endpoint && "Endpoint: " + error.endpoint, error.status_code && "HTTP: " + error.status_code].filter(Boolean).join(" • ")}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="mt-4 rounded-xl border theme-border theme-accent-bg p-3"><div className="text-[10px] font-bold uppercase tracking-wide theme-accent">Common Feed Diagnostics • Session Counters</div><div className="mt-2 grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-4"><span>Consumers: <b>{runtime.consumers.length}</b></span><span>Delivery errors: <b>{runtime.deliveryErrors}</b></span><span>Normalizer errors: <b>{runtime.normalizerErrors}</b></span><span>Market: <b>{effectiveMarketSession}</b></span></div></div><div className="mt-4 flex justify-end">
              <Link href="/scanner" onClick={() => setConnectionsOpen(false)} className="rounded-xl theme-accent-bg px-3 py-2 text-[11px] font-bold theme-accent">Open Scanner</Link>
            </div>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
