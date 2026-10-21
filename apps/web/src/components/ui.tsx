"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { ApiProblem } from "@/lib/api";
import type { FinalStatus } from "@/lib/types";

/** A titled surface. Every screen of the workbench is built out of these. */
export function Panel({
  title,
  note,
  actions,
  children,
}: {
  title: string;
  note?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <header>
        <div>
          <h2>{title}</h2>
          {note ? <p className="muted">{note}</p> : null}
        </div>
        {actions}
      </header>
      {children}
    </section>
  );
}

/**
 * The status chip reads the card's `final_status`, which §5.4 derives from stored numbers; the
 * workbench never lets a screen compose a status out of what it would like to see.
 */
export function StatusChip({ status }: { status: FinalStatus }) {
  return <span className={`chip chip--${status}`}>{status}</span>;
}

export function Chip({ children, status }: { children: ReactNode; status?: string }) {
  return <span className={status ? `chip chip--${status}` : "chip"}>{children}</span>;
}

export function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="facts">
      {rows.map(([label, value]) => (
        <div key={label} style={{ display: "contents" }}>
          <dt>{label}</dt>
          <dd>{value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * A refusal is shown in the server's own words, classed by its `class`: the workbench has to tell a
 * gate from a policy from a validation error, because they ask for different next actions (§9.7).
 */
export function ProblemNote({ error }: { error: unknown }) {
  if (!error) return null;
  if (error instanceof ApiProblem) {
    return (
      <div className="error" role="alert">
        <div>
          {`${error.status} · ${error.class} · ${error.code}`}
          {error.field ? ` · field ${error.field}` : ""}
        </div>
        <div>{error.message}</div>
        {error.retryable ? (
          <div>retryable{error.retry_after ? `, after ${error.retry_after}s` : ""}</div>
        ) : null}
        <div className="mono">trace {error.trace_id}</div>
      </div>
    );
  }
  return (
    <div className="error" role="alert">
      {error instanceof Error ? error.message : String(error)}
    </div>
  );
}

export function Notice({ children }: { children: ReactNode }) {
  if (!children) return null;
  return <div className="notice">{children}</div>;
}

export function Loading({ on }: { on: boolean }) {
  return on ? <p className="muted">reading…</p> : null;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="muted">{children}</p>;
}

/** A card link: the queue and the trail both point at the one-screen review, never at a raw id. */
export function CardLink({
  href,
  title,
  note,
  status,
}: {
  href: string;
  title: string;
  note?: ReactNode;
  status?: FinalStatus;
}) {
  return (
    <Link href={href} className="panel" style={{ display: "block" }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline" }}>
        <h3>{title}</h3>
        {status ? <StatusChip status={status} /> : null}
      </div>
      {note ? <p className="muted">{note}</p> : null}
    </Link>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label>
      <span>{label}</span>
      {children}
      {hint ? <span className="muted">{hint}</span> : null}
    </label>
  );
}
