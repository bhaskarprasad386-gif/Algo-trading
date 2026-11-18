import { Activity, Layers3, ShieldCheck, Signal } from "lucide-react";

const indices = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"];

export default function BoxSpreadWorkspaceHeader() {
  return (
    <section className="box-workspace-header relative overflow-hidden rounded-2xl border border-slate-700/80 bg-[#081323] text-white shadow-[0_18px_55px_rgba(2,6,23,0.22)]">
      <div className="pointer-events-none absolute -right-16 -top-24 h-64 w-64 rounded-full bg-cyan-400/10 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-24 left-1/3 h-52 w-52 rounded-full bg-blue-500/10 blur-3xl" />
      <div className="relative p-5 sm:p-7">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
          <div className="max-w-3xl">
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full border border-cyan-300/20 bg-cyan-300/10 px-2.5 py-1 text-[9px] font-extrabold uppercase tracking-[0.18em] text-cyan-200"><Activity size={12}/> Live strategy workspace</span>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-900/70 px-2.5 py-1 text-[9px] font-bold uppercase tracking-wider text-slate-300"><Layers3 size={12}/> Four-leg box</span>
            </div>
            <h1 className="mt-4 text-2xl font-black tracking-tight sm:text-3xl">Box Spread <span className="text-cyan-300">Scanner</span></h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">Index options only. Monitor executable four-leg opportunities, quote depth, expiry classification and feed freshness from one dedicated workspace.</p>
          </div>
          <div className="flex min-w-[190px] items-center gap-3 rounded-xl border border-amber-400/20 bg-amber-400/[0.07] p-3">
            <div className="grid h-9 w-9 place-items-center rounded-lg bg-amber-300/10 text-amber-200"><ShieldCheck size={17}/></div>
            <div><p className="text-[9px] font-bold uppercase tracking-wider text-amber-200/70">Execution boundary</p><p className="mt-1 text-sm font-extrabold text-amber-100">Broker orders OFF</p></div>
          </div>
        </div>

      </div>
    </section>
  );
}
