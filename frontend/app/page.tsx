"use client";

import { useEffect, useState } from "react";
import { Bell, Clock, Zap } from "lucide-react";
import Link from "next/link";
import { Card, PageTitle } from "@/components/ui";
import { appConfig } from "@/lib/config";

const markets = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"];

const strategies = [
  { name: "Cash-Future", detail: "Pairs • Cash vs Futures •", href: "/strategies/cash-future" },
  { name: "Calendar Spread", detail: "Time spread • NIFTY •", href: "/strategies/calendar-spread" },
  { name: "Synthetic Arbitrage", detail: "Cash-Synth • Arbitrage •", href: "/strategies/synthetic-arbitrage" },
  { name: "Box Spread", detail: "Options • Box •", href: "/strategies/box-spread" },
  { name: "Custom Strategy", detail: "Independent • User-defined •", href: "/strategies/custom-strategy" },
  { name: "Strategy Scanner", detail: "Dedicated workspaces •", href: "/scanner" },
  { name: "Broker Orders", detail: "Always OFF • Paper safe •", href: "/paper-trading" },
];

const health = (api: string, ws: string) => [
  ["Frontend", "READY", "ok"],
  ["FastAPI", api, api === "CONNECTED" ? "ok" : api === "ERROR" ? "error" : "warn"],
  ["WebSocket", ws, ws === "CONNECTED" ? "ok" : ws === "ERROR" ? "error" : "warn"],
  ["Scanner", ws === "CONNECTED" ? "LIVE" : "AWAITING API", ws === "CONNECTED" ? "ok" : "warn"],
  ["Database", api === "CONNECTED" ? "CONNECTED" : "AWAITING API", api === "CONNECTED" ? "ok" : "warn"],
  ["Broker Orders", "OFF", "warn"],
] as const;

export default function HomePage() {
  const [capital, setCapital] = useState("10000000");
  const [apiStatus, setApiStatus] = useState<"checking" | "connected" | "error">("checking");
  const [wsStatus, setWsStatus] = useState<"connecting" | "connected" | "error">("connecting");
  const [snapshot, setSnapshot] = useState({ cashFuture: 0, calendar: 0, synthetic: 0, box: 0, opportunities: 0, timestamp: null as string | null });
  const [indexLtps, setIndexLtps] = useState<Record<string, { ltp: number | null; previousClose: number | null; changePercent: number | null }>>({});

  useEffect(() => {
    const controller = new AbortController();
    fetch(appConfig.apiBaseUrl.replace(/\/$/, "") + "/health", { cache: "no-store", signal: controller.signal })
      .then((response) => { if (!response.ok) throw new Error("health"); setApiStatus("connected"); })
      .catch(() => setApiStatus("error"));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const socket = new WebSocket(appConfig.wsUrl.replace(/\/$/, "") + "/ws/dashboard");
    socket.onopen = () => setWsStatus("connected");
    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message?.type !== "dashboard_snapshot") return;
        setSnapshot({
          cashFuture: Number(message?.integration?.cash_future?.count ?? 0),
          calendar: Number(message?.integration?.calendar_spread?.count ?? 0),
          synthetic: Number(message?.integration?.synthetic_arbitrage?.count ?? 0),
          box: Number(message?.integration?.box_spread?.count ?? 0),
          opportunities: Number(message?.scanner?.opportunity_count ?? 0),
          timestamp: message?.timestamp ?? null,
        });
      } catch { /* ignore malformed snapshots */ }
    };
    socket.onerror = () => setWsStatus("error");
    socket.onclose = () => setWsStatus("error");
    return () => socket.close();
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadMarketOverview = async () => {
      try {
        const response = await fetch(
          appConfig.apiBaseUrl.replace(/\/$/, "") + "/api/v1/market-data/overview",
          { cache: "no-store" },
        );
        if (!response.ok) throw new Error("market overview");
        const body = await response.json();
        const next: Record<string, { ltp: number | null; previousClose: number | null; changePercent: number | null }> = {};
        for (const row of Array.isArray(body?.indices) ? body.indices : []) {
          if (row?.symbol) {
            const ltp = row?.ltp == null ? null : Number(row.ltp);
            const previousClose = row?.close == null ? null : Number(row.close);
            const changePercent = ltp != null && previousClose != null && previousClose !== 0 ? ((ltp - previousClose) / previousClose) * 100 : null;
            next[String(row.symbol).toUpperCase()] = { ltp, previousClose, changePercent };
          }
        }
        if (!cancelled) setIndexLtps(next);
      } catch {
        if (!cancelled) setIndexLtps({});
      }
    };
    loadMarketOverview();
    const timer = window.setInterval(loadMarketOverview, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    const saved = window.localStorage.getItem("algo-paper-capital");
    if (saved && Number(saved) > 0) setCapital(saved);
  }, []);

  const saveCapital = (value: string) => {
    const normalized = value.replace(/[^0-9]/g, "");
    setCapital(normalized);
    if (Number(normalized) > 0) window.localStorage.setItem("algo-paper-capital", normalized);
  };

  const formattedCapital = Number(capital || 0).toLocaleString("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  });

  const apiLabel = apiStatus === "connected" ? "CONNECTED" : apiStatus === "error" ? "ERROR" : "CONNECTING";
  const wsLabel = wsStatus === "connected" ? "CONNECTED" : wsStatus === "error" ? "ERROR" : "CONNECTING";
  const scannerStatus = [
    { label: "Signals Detected", value: snapshot.opportunities.toLocaleString("en-IN") },
    { label: "Cash-Future Rows", value: snapshot.cashFuture.toLocaleString("en-IN") },
    { label: "Calendar Rows", value: snapshot.calendar.toLocaleString("en-IN") },
    { label: "Synthetic Rows", value: snapshot.synthetic.toLocaleString("en-IN") },
    { label: "Orders Placed", value: "0 orders" },
    { label: "Fills", value: "0 fills" },
    { label: "Errors", value: "0 errors" },
    { label: "Box Rows", value: snapshot.box.toLocaleString("en-IN") },
  ];

  return (
    <div className="min-h-screen bg-white p-4 text-[#0F172A]">
      <PageTitle eyebrow="Phase 2 • Home / Command Center" title="Command Center" description="" />
      <div className="-mt-8 mb-4 flex justify-end gap-2">
        <div className="inline-flex items-center gap-2 rounded-full border border-orange-500/30 bg-orange-500/10 px-3 py-1 text-[11px] font-medium text-orange-700"><span className="h-2 w-2 rounded-full bg-orange-400" />{wsLabel === "CONNECTED" ? "Live market feed connected" : "Market feed connecting"}</div>
        <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-[11px] font-medium text-emerald-700"><span className="h-2 w-2 rounded-full bg-emerald-400" />Paper-safe broker orders OFF</div>
      </div>

      <section className="mt-6" aria-label="Market overview">
        <h2 className="mb-3 text-[15px] font-semibold">Market Overview</h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
          {markets.map((symbol) => (
            <Link key={symbol} href={`/scanner?market=${encodeURIComponent(symbol)}`} className="block">
              <Card className="rounded-xl border-[#D7E0E8] bg-white p-4 transition hover:border-sky-500/40">
                <div className="text-center">
                  <div className="text-[12px] font-semibold tracking-wide">{symbol}</div>
                  <div className={`mt-3 text-lg font-bold ${indexLtps[symbol]?.changePercent != null && indexLtps[symbol]!.changePercent! > 0 ? "text-emerald-600" : indexLtps[symbol]?.changePercent != null && indexLtps[symbol]!.changePercent! < 0 ? "text-red-600" : "text-slate-900"}`}>{indexLtps[symbol]?.ltp == null ? "—" : indexLtps[symbol]!.ltp!.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                  <div className={`mt-1 text-[12px] font-semibold ${indexLtps[symbol]?.changePercent != null && indexLtps[symbol]!.changePercent! > 0 ? "text-emerald-600" : indexLtps[symbol]?.changePercent != null && indexLtps[symbol]!.changePercent! < 0 ? "text-red-600" : "text-slate-600"}`}>{indexLtps[symbol]?.changePercent == null ? "Waiting for change" : `${indexLtps[symbol]!.changePercent! > 0 ? "+" : ""}${indexLtps[symbol]!.changePercent!.toFixed(2)}%`}</div>
                  <div className="mt-1 text-[10px] text-[#64748B]">{indexLtps[symbol]?.previousClose == null ? "Previous close unavailable" : `Prev close: ${indexLtps[symbol]!.previousClose!.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}</div>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      </section>

      <div className="mt-6 grid gap-4 xl:grid-cols-[1.55fr_1fr]">
        <section aria-label="Paper ledger">
          <h2 className="mb-3 text-[15px] font-semibold">Paper Ledger</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Card className="rounded-xl border-[#D7E0E8] bg-white p-3"><div className="text-[11px] text-[#64748B]">Paper Capital</div><div className="mt-1 text-[16px] font-bold">{formattedCapital}</div><input inputMode="numeric" value={capital} onChange={(event) => saveCapital(event.target.value)} aria-label="Manual paper capital amount" className="mt-3 min-h-9 w-full rounded-lg border border-[#D7E0E8] bg-white px-2 text-xs text-[#0F172A] outline-none focus:border-sky-500/60" /></Card>
            <Card className="rounded-xl border-[#D7E0E8] bg-white p-3"><div className="flex gap-6"><div><div className="text-[11px] text-[#64748B]">Available Balance</div><div className="mt-1 text-sm">—</div></div><div><div className="text-[11px] text-[#64748B]">Today&apos;s P&amp;L</div><div className="mt-1 text-sm">—</div></div></div></Card>
            <Card className="rounded-xl border-[#D7E0E8] bg-white p-3"><div className="text-[11px] text-[#64748B]">Open Positions</div><div className="mt-1 text-sm">—</div></Card>
          </div>
        </section>

        <section aria-label="System health">
          <h2 className="mb-3 text-[15px] font-semibold">System Health</h2>
          <Card className="rounded-xl border-[#D7E0E8] bg-white p-2">
            {health(apiLabel, wsLabel).map(([name, value, state]) => (
              <div key={name} className="flex items-center justify-between px-2 py-1.5">
                <div className="flex items-center gap-2 text-[12px] text-[#334155]"><span className={`h-2 w-2 rounded-full ${state === "ok" ? "bg-emerald-400" : state === "warn" ? "bg-amber-400" : "bg-red-400"}`} />{name}</div>
                <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${state === "ok" ? "bg-emerald-100 text-emerald-700" : state === "warn" ? "bg-amber-500/20 text-amber-700" : "bg-red-500/20 text-red-700"}`}>{value}</span>
              </div>
            ))}
          </Card>
        </section>
      </div>

      <div className="mt-6 grid gap-4 xl:grid-cols-[1.55fr_1fr]">
        <section aria-label="Strategy workspaces"><h2 className="mb-3 text-[15px] font-semibold">Strategy Workspaces</h2><div className="grid grid-cols-2 gap-3 md:grid-cols-4">{strategies.map((strategy) => <Link key={strategy.name} href={strategy.href} className="block"><Card className="min-h-[85px] rounded-xl border-[#D7E0E8] bg-white p-3 transition hover:border-sky-500/40"><div className="text-[12px] font-semibold">{strategy.name}</div><div className="mt-1 text-[10px] text-[#64748B]">{strategy.detail ? `${strategy.detail} —` : " "}</div></Card></Link>)}</div></section>
        <div className="space-y-4">
          <section aria-label="Scanner status"><h2 className="mb-3 text-[15px] font-semibold">Scanner Status</h2><div className="grid grid-cols-2 gap-2 sm:grid-cols-4">{scannerStatus.map((item) => <Card key={item.label} className="rounded-xl border-[#D7E0E8] bg-white p-2.5"><div className="text-[10px] text-[#64748B]">{item.label}</div><div className="mt-1 text-[11px] text-[#334155]">{item.value}</div></Card>)}</div></section>
          <section aria-label="Operations"><h2 className="mb-3 text-[15px] font-semibold">Operations</h2><div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Link href="/scanner"><Card className="rounded-xl border-[#D7E0E8] bg-white p-3 transition hover:border-sky-500/40"><div className="flex gap-2"><Zap size={14} /><div><div className="text-[12px] font-semibold">Live Scanner</div><div className="text-[10px] text-[#64748B]">Open scanner console</div></div></div></Card></Link>
            <Link href="/custom-alert"><Card className="rounded-xl border-[#D7E0E8] bg-white p-3 transition hover:border-sky-500/40"><div className="flex gap-2"><Bell size={14} /><div><div className="text-[12px] font-semibold">Custom Alerts</div><div className="text-[10px] text-[#64748B]">Manage alerts</div></div></div></Card></Link>
            <Link href="/history"><Card className="rounded-xl border-[#D7E0E8] bg-white p-3 transition hover:border-sky-500/40"><div className="flex gap-2"><Clock size={14} /><div><div className="text-[12px] font-semibold">History</div><div className="text-[10px] text-[#64748B]">Trade history & logs</div></div></div></Card></Link>
          </div></section>
        </div>
      </div>

      <Card className="mt-6 flex items-center justify-between rounded-xl border-[#D7E0E8] bg-white p-3"><div className="min-w-0"><div className="text-[13px] font-semibold">Integration Boundary</div><div className="text-[11px] text-[#64748B]">External broker orders remain OFF. FastAPI market data and paper-trading services are connected independently.</div></div><button type="button" className="ml-3 shrink-0 rounded-lg bg-[#E8EEF3] px-3 py-1.5 text-[11px] transition hover:bg-[#DDE5EC]">View Connections</button></Card>
    </div>
  );
}
