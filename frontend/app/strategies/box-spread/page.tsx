import { StrategyWorkspace } from "@/components/strategy-workspace";
import BoxSpreadPostMarketHistory from "@/components/BoxSpreadPostMarketHistory";
import BoxSpreadWorkspaceHeader from "@/components/BoxSpreadWorkspaceHeader";

export default function BoxSpreadWorkspacePage() {
  return (
    <main className="space-y-4">
      <BoxSpreadWorkspaceHeader />
      <StrategyWorkspace slug="box-spread" />
      <BoxSpreadPostMarketHistory />
    </main>
  );
}
