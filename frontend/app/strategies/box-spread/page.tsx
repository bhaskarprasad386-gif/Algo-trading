import { StrategyWorkspace } from "@/components/strategy-workspace";
import BoxSpreadEdgeTimingAnalysis from "@/components/BoxSpreadEdgeTimingAnalysis";

export default function BoxSpreadWorkspacePage() {
  return (
    <div className="space-y-4">
      <BoxSpreadEdgeTimingAnalysis />
      <StrategyWorkspace slug="box-spread" />
    </div>
  );
}
