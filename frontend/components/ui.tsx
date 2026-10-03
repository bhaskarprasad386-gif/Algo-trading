import type { ReactNode } from "react";

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-2xl border border-slate-200 bg-white shadow-sm ${className}`}>
      {children}
    </section>
  );
}

export function StatusDot({ live = false }: { live?: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full ${live ? "bg-emerald-500" : "bg-slate-400"}`}
      aria-hidden="true"
    />
  );
}

export function PageTitle({
  eyebrow,
  title,
  description,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
}) {
  return (
    <div className="mb-6">
      {eyebrow ? <p className="mb-1 text-xs font-semibold uppercase tracking-[0.18em] text-sky-700">{eyebrow}</p> : null}
      <h1 className="text-2xl font-semibold tracking-tight text-slate-900 sm:text-3xl">{title}</h1>
      {description ? <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">{description}</p> : null}
    </div>
  );
}
