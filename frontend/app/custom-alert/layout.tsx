import type { ReactNode } from "react";

export default function CustomAlertLayout({ children }: { children: ReactNode }) {
  return <div className="space-y-4">{children}</div>;
}
