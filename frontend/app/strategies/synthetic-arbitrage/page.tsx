import { StrategyWorkspace } from "@/components/strategy-workspace";
import { LiveUpdateBadge } from "@/components/live-update-badge";

export default function SyntheticArbitrageWorkspacePage() {
  return (
    <>
      <LiveUpdateBadge />
      <StrategyWorkspace slug="synthetic-arbitrage" />
    </>
  );
}
