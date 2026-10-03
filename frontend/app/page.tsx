import { Activity, ArrowUpRight, BarChart3, Bell, BriefcaseBusiness, CircleDollarSign, Database, Gauge, Layers3, Network, ShieldCheck, WalletCards } from "lucide-react";
import Link from "next/link";
import { Card, PageTitle, StatusDot } from "@/components/ui";

const markets = [
  ["NIFTY", "No live data"],
  ["BANKNIFTY", "No live data"],
  ["FINNIFTY", "No live data"],
  ["MIDCPNIFTY", "No live data"],
  ["SENSEX", "No live data"],
];

const strategies = [
  { name: "Cash-Future", href: "/paper-trading", detail: "Cash + current/near future" },
  { name: "Calendar Spread", href: "/paper-trading", detail: "Expiry spread workspace" },
  { name: "Synthetic Arbitrage", href: "/paper-trading", detail: "Live option/future structure" },
  { name: "Box Spread", href: "/paper-trading", detail: "Four-leg paper structure" },
  { name: "Debit Strategy", href: "/paper-trading", detail: "Supported debit setups" },
  { name: "Credit Strategy", href: "/paper-trading", detail: "Supported credit setups" },
];

const health = [
  ["Frontend", "READY", "ok"],
  ["FastAPI", "NOT CONNECTED", "muted"],
  ["WebSocket", "NOT CONNECTED", "muted"],
  ["Scanner", "AWAITING API", "muted"],
  ["Database", "AWAITING API", "muted"],
  ["Broker Orders", "OFF", "safe"],
] as const;

export default function HomePage() {
  return (
    <div>
      <PageTitle
        eyebrow="Phase 2 · Home / Command Center"
        title="Command Center"
        description="A single operational view for market status, paper capital, scanner health, positions and strategy workspaces. Live values appear only after the existing FastAPI/WebSocket integration is connected."
      />

      <div className="mb-6 flex flex-wrap gap-3">
        <div className="flex items-center gap-2 rounded-full border border-algo-border bg-algo-surface px-3 py-2 text-xs text-algo-muted">
          <StatusDot />
          Market feed · not connected
        </div>
        <div className="flex items-center gap-2 rounded-full border border-algo-border bg-algo-surface px-3 py-2 text-xs text-algo-muted">
          <ShieldCheck size={14} className="text-algo-profit" />
          Paper-safe · broker orders OFF
        </div>
      </div>

      <section aria-label="Market overview">
        <SectionHeading icon={<BarChart3 size={18} />} title="Market Overview" action="Live Scanner" href="/scanner" />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {markets.map(([symbol, status]) => (
            <Card key={symbol} className="p-4">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-white">{symbol}</span>
                <StatusDot />
              </div>
              <div className="mt-4 text-lg font-semibold text-algo-muted">—</div>
              <div className="mt-1 text-[11px] text-algo-muted">{status}</div>
            </Card>
          ))}
        </div>
      </section>

      <section className="mt-7" aria-label="Paper ledger">
        <SectionHeading icon={<WalletCards size={18} />} title="Paper Ledger" />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard icon={<CircleDollarSign size={18} />} label="Paper Capital" value="₹1,00,00,000" note="Configured paper ledger" />
          <MetricCard icon={<CircleDollarSign size={18} />} label="Available Balance" value="—" note="Awaiting backend data" />
          <MetricCard icon={<ArrowUpRight size={18} />} label="Today's P&L" value="—" note="No live position data" />
          <MetricCard icon={<BriefcaseBusiness size={18} />} label="Open Positions" value="—" note="No live position data" />
        </div>
      </section>

      <div className="mt-7 grid gap-6 xl:grid-cols-[1.35fr_.65fr]">
        <section aria-label="Strategy workspaces">
          <SectionHeading icon={<Layers3 size={18} />} title="Strategy Workspaces" action="Paper Trading" href="/paper-trading" />
          <div className="grid gap-3 sm:grid-cols-2">
            {strategies.map((strategy) => (
              <Link key={strategy.name} href={strategy.href} className="group">
                <Card className="h-full p-4 transition group-hover:border-algo-primary/50 group-hover:bg-algo-surface">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-sm font-semibold text-white">{strategy.name}</span>
                    <ArrowUpRight size={16} className="text-algo-muted transition group-hover:text-algo-primary" />
                  </div>
                  <p className="mt-2 text-xs leading-5 text-algo-muted">{strategy.detail}</p>
                  <div className="mt-4 inline-flex rounded-full border border-algo-border px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-algo-muted">
                    Paper workspace
                  </div>
                </Card>
              </Link>
            ))}
          </div>
        </section>

        <section aria-label="System health">
          <SectionHeading icon={<Gauge size={18} />} title="System Health" />
          <Card className="p-5">
            <div className="space-y-4">
              {health.map(([label, value, state]) => (
                <div key={label} className="flex items-center justify-between gap-3 border-b border-algo-border pb-3 last:border-0 last:pb-0">
                  <div className="flex min-w-0 items-center gap-2">
                    <StatusDot live={state === "ok" || state === "safe"} />
                    <span className="text-sm text-algo-muted">{label}</span>
                  </div>
                  <span className={state === "safe" ? "text-xs font-semibold text-algo-profit" : state === "ok" ? "text-xs font-semibold text-algo-primary" : "text-right text-[10px] font-semibold text-algo-muted"}>
                    {value}
                  </span>
                </div>
              ))}
            </div>
          </Card>
        </section>
      </div>

      <section className="mt-7" aria-label="Operations">
        <SectionHeading icon={<Activity size={18} />} title="Operations" />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <QuickLink href="/scanner" icon={<Activity size={18} />} title="Live Scanner" description="All supported live scanner output" />
          <QuickLink href="/custom-alert" icon={<Bell size={18} />} title="Custom Alerts" description="Create and monitor alert conditions" />
          <QuickLink href="/positions" icon={<BriefcaseBusiness size={18} />} title="Positions" description="Open paper positions and P&L" />
          <QuickLink href="/history" icon={<Database size={18} />} title="History" description="Saved alerts, trades and journal data" />
        </div>
      </section>

      <Card className="mt-7 p-4">
        <div className="flex items-start gap-3">
          <Network size={18} className="mt-0.5 shrink-0 text-algo-primary" />
          <div>
            <div className="text-sm font-medium text-white">Integration boundary</div>
            <p className="mt-1 text-xs leading-5 text-algo-muted">
              This command center does not manufacture market prices, signals or P&L. Phase 10 will connect these panels to the existing FastAPI and WebSocket services.
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
}

function SectionHeading({ icon, title, action, href }: { icon: React.ReactNode; title: string; action?: string; href?: string }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        <span className="text-algo-primary">{icon}</span>
        <h2 className="text-sm font-semibold uppercase tracking-[0.12em] text-white">{title}</h2>
      </div>
      {action && href ? <Link href={href} className="text-xs font-medium text-algo-primary hover:underline">{action} →</Link> : null}
    </div>
  );
}

function MetricCard({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: string; note: string }) {
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-algo-muted">{label}</span>
        <span className="text-algo-primary">{icon}</span>
      </div>
      <div className="mt-4 text-xl font-semibold text-white">{value}</div>
      <div className="mt-1 text-xs text-algo-muted">{note}</div>
    </Card>
  );
}

function QuickLink({ href, icon, title, description }: { href: string; icon: React.ReactNode; title: string; description: string }) {
  return (
    <Link href={href} className="group">
      <Card className="flex h-full items-start gap-3 p-4 transition group-hover:border-algo-primary/50 group-hover:bg-algo-surface">
        <span className="mt-0.5 text-algo-primary">{icon}</span>
        <span>
          <span className="block text-sm font-semibold text-white">{title}</span>
          <span className="mt-1 block text-xs leading-5 text-algo-muted">{description}</span>
        </span>
      </Card>
    </Link>
  );
}
