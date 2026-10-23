import { StrategyWorkspace } from "@/components/strategy-workspace";
import { LiveUpdateBadge } from "@/components/live-update-badge";
import { SyntheticTracePanel } from "@/components/synthetic-trace-panel";
import { SyntheticAdvancedOverview } from "@/components/synthetic-advanced-overview";

export default function SyntheticArbitrageWorkspacePage() {
  return (
    <>
      <LiveUpdateBadge />
      <SyntheticAdvancedOverview />
      <SyntheticTracePanel />
      <StrategyWorkspace slug="synthetic-arbitrage" />
    </>
  );
}
