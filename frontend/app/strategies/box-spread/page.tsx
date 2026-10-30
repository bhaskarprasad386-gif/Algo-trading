import { StrategyWorkspace } from "@/components/strategy-workspace";
import BoxSpreadEdgeTimingAnalysis from "@/components/BoxSpreadEdgeTimingAnalysis";
import BoxSpreadPostMarketHistory from "@/components/BoxSpreadPostMarketHistory";

export default function BoxSpreadWorkspacePage() {
  return (
    <div className="space-y-4">
      <BoxSpreadEdgeTimingAnalysis />
      <BoxSpreadPostMarketHistory />
      <StrategyWorkspace slug="box-spread" />
    </div>
  );
}
