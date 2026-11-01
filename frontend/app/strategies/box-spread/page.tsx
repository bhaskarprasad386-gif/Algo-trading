import { StrategyWorkspace } from "@/components/strategy-workspace";
import BoxSpreadEdgeTimingAnalysis from "@/components/BoxSpreadEdgeTimingAnalysis";
import BoxSpreadPostMarketHistory from "@/components/BoxSpreadPostMarketHistory";
import BoxSpreadWorkspaceHeader from "@/components/BoxSpreadWorkspaceHeader";

export default function BoxSpreadWorkspacePage() {
  return (
    <div className="space-y-4">
      <BoxSpreadWorkspaceHeader />
      <BoxSpreadEdgeTimingAnalysis />
      <BoxSpreadPostMarketHistory />
      <StrategyWorkspace slug="box-spread" />
    </div>
  );
}
