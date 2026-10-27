import { StrategyWorkspace } from "@/components/strategy-workspace";
import BoxSpreadEdgeTimingAnalysis from "@/components/BoxSpreadEdgeTimingAnalysis";
import BoxSpreadPaperTrading from "@/components/BoxSpreadPaperTrading";

export default function BoxSpreadWorkspacePage() {
  return (
    <div className="space-y-4">
      <BoxSpreadPaperTrading />
      <BoxSpreadEdgeTimingAnalysis />
      <StrategyWorkspace slug="box-spread" />
    </div>
  );
}
