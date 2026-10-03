import { Activity, ArrowUpRight, BriefcaseBusiness, CircleDollarSign, ShieldCheck } from "lucide-react";
import { Card, PageTitle, StatusDot } from "@/components/ui";

const metrics = [
  { label: "Paper Capital", value: "₹1,00,00,000", note: "Configured ledger", icon: CircleDollarSign },
  { label: "Available Balance", value: "—", note: "Backend integration in Phase 10", icon: WalletIcon },
  { label: "Today's P&L", value: "—", note: "Live position engine", icon: ArrowUpRight },
  { label: "Open Positions", value: "—", note: "Position engine", icon: BriefcaseBusiness },
];

const strategies = ["Cash-Future", "Calendar Spread", "Synthetic Arbitrage", "Box Spread", "Debit Strategy", "Credit Strategy"];

export default function HomePage() {
  return (
    <div>
      <PageTitle
        eyebrow="Phase 1 · Frontend Foundation"
        title="Command Center"
        description="Professional frontend shell for the trading system. Live values will be connected to FastAPI in the backend integration phase."
      />

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 rounded-full border border-algo-border bg-algo-surface px-3 py-2 text-xs text-algo-muted">
          <StatusDot live={false} />
          Market status · awaiting live API
        </div>
        <div className="flex items-center gap-2 rounded-full border border-algo-border bg-algo-surface px-3 py-2 text-xs text-algo-muted">
          <ShieldCheck size={14} className="text-algo-profit" />
          Paper-safe · broker orders OFF
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {metrics.map((item) => {
          const Icon = item.icon;
          return (
            <Card key={item.label} className="p-5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-algo-muted">{item.label}</span>
                <Icon size={18} className="text-algo-primary" />
              </div>
              <div className="mt-4 text-xl font-semibold text-white">{item.value}</div>
              <div className="mt-1 text-xs text-algo-muted">{item.note}</div>
            </Card>
          );
        })}
      </div>

      <div className="mt-6 grid gap-6 xl:grid-cols-[1.4fr_.8fr]">
        <Card className="p-5">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold text-white">Strategy Workspaces</h2>
              <p className="mt-1 text-xs text-algo-muted">Dedicated workspaces will be wired in Phase 11.</p>
            </div>
            <Activity size={18} className="text-algo-primary" />
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            {strategies.map((strategy) => (
              <div key={strategy} className="rounded-xl border border-algo-border bg-algo-surface p-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-white">{strategy}</span>
                  <span className="rounded-full bg-white/[.04] px-2 py-1 text-[10px] text-algo-muted">FOUNDATION</span>
                </div>
                <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/[.05]">
                  <div className="h-full w-1/3 rounded-full bg-algo-primary/70" />
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card className="p-5">
          <h2 className="text-base font-semibold text-white">System Status</h2>
          <div className="mt-5 space-y-3">
            {[
              ["Frontend", "Foundation"],
              ["FastAPI", "Not connected"],
              ["WebSocket", "Not connected"],
              ["Paper Orders", "OFF"],
            ].map(([label, value]) => (
              <div key={label} className="flex items-center justify-between border-b border-algo-border pb-3 last:border-0 last:pb-0">
                <span className="text-sm text-algo-muted">{label}</span>
                <span className="text-xs font-medium text-white">{value}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

function WalletIcon(props: React.SVGProps<SVGSVGElement>) {
  return <CircleDollarSign {...props} />;
}
