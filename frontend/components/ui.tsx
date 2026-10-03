import type { ReactNode } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-2xl border theme-border theme-surface theme-shadow ${className}`}>{children}</section>;
}

export function StatusDot({ live = false }: { live?: boolean }) {
  return (
    <span
      className="inline-block h-2 w-2 rounded-full"
      style={{ backgroundColor: live ? "var(--app-success)" : "var(--app-subtle)" }}
      aria-hidden="true"
    />
  );
}

export function PageTitle({ eyebrow, title, description }: { eyebrow?: string; title: string; description?: string }) {
  return (
    <div className="mb-6">
      {eyebrow ? <p className="mb-1 text-xs font-semibold uppercase tracking-[0.18em] theme-accent">{eyebrow}</p> : null}
      <h1 className="text-2xl font-semibold tracking-tight theme-text sm:text-3xl">{title}</h1>
      {description ? <p className="mt-2 max-w-3xl text-sm leading-6 theme-muted">{description}</p> : null}
    </div>
  );
}
