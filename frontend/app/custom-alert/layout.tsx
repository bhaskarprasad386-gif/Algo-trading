import type { ReactNode } from "react";
import BoxSpreadTimingPanel from "./BoxSpreadTimingPanel";

export default function CustomAlertLayout({ children }: { children: ReactNode }) {
  return (
    <div className="space-y-4">
      <BoxSpreadTimingPanel />
      {children}
    </div>
  );
}
