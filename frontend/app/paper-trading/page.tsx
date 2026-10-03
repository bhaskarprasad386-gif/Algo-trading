import { PageTitle, Card } from "@/components/ui";
export default function PaperTradingPage() {
  return <><PageTitle eyebrow="Workspace" title="Manual Paper Trading" description="Manual paper execution workspace. Auto-adjustment remains HOLD." /><Card className="p-8 text-sm text-algo-muted">Paper execution UI foundation ready. No broker orders are sent.</Card></>;
}
