import { StrategyWorkspace } from "@/components/strategy-workspace";
import { LiveUpdateBadge } from "@/components/live-update-badge";
import { SyntheticTracePanel } from "@/components/synthetic-trace-panel";

export default function SyntheticArbitrageWorkspacePage() {
  return (
    <>
      <LiveUpdateBadge />
      <SyntheticTracePanel />
      <StrategyWorkspace slug="synthetic-arbitrage" />
    </>
  );
}
