import Link from "next/link";
import { ArrowLeft, ShieldCheck } from "lucide-react";
import BoxSpreadPaperTrading from "@/components/BoxSpreadPaperTrading";

export default function BoxSpreadPaperPage() {
  return (
    <main className="space-y-4">
      <Link href="/strategies/box-spread" className="inline-flex items-center gap-2 text-sm theme-muted hover:theme-text">
        <ArrowLeft size={15} /> Back to live Box Scanner
      </Link>
      <div className="rounded-2xl border theme-border theme-surface p-4 sm:p-5">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider theme-accent"><ShieldCheck size={15} /> Isolated simulation</div>
        <h1 className="mt-2 text-2xl font-semibold theme-text">Box Spread Paper Trading</h1>
        <p className="mt-1 max-w-3xl text-sm theme-muted">Separate virtual account, persistent ledger, four-leg simulated fills, and explicit exit/expiry settlement. Real broker orders remain disabled. The live scanner remains a read-only quote source.</p>
      </div>
      <BoxSpreadPaperTrading />
    </main>
  );
}
