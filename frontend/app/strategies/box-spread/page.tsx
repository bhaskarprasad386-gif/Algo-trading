import { StrategyWorkspace } from "@/components/strategy-workspace";
import BoxSpreadPostMarketHistory from "@/components/BoxSpreadPostMarketHistory";
import BoxSpreadTraceArea from "@/components/BoxSpreadTraceArea";

export default function BoxSpreadWorkspacePage() {
  return (
    <main className="box-spread-page space-y-4">
      <nav aria-label="Box Spread workspace sections" className="box-workspace-nav sticky top-2 z-20 flex gap-1 overflow-x-auto rounded-xl border border-slate-700 bg-[#091321]/95 p-1.5 shadow-lg backdrop-blur">
        {[
          ["Overview", "#box-overview"], ["Scanner", "#box-scanner-controls"],
          ["Opportunities", "#box-live-opportunities"],
          ["History", "#box-history"], ["Trace", "#box-trace"], ["System Health", "#box-system-health"],
        ].map(([label, href]) => <a key={href} href={href} className="shrink-0 rounded-lg px-3 py-2 text-[11px] font-bold text-slate-300 transition hover:bg-cyan-400/10 hover:text-cyan-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-400">{label}</a>)}
      </nav>
      <StrategyWorkspace slug="box-spread" />
      <BoxSpreadPostMarketHistory />
      <BoxSpreadTraceArea />
    </main>
  );
}
