"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { IdentityBar } from "@/components/IdentityBar";
import { useWorkbench } from "@/lib/identity";

const SCREENS: [string, string][] = [
  ["", "overview"],
  ["/data", "data"],
  ["/plans", "plans"],
  ["/approvals", "approvals"],
  ["/queue", "expert queue"],
  ["/exports", "exports"],
];

/**
 * The navigation is the pipeline order of plan V2.0 section 5.1, not a list of features: register,
 * plan, approve, run, queue, review, deliver. A card is reachable only through a run and a queue,
 * which is what §5.6 means by refusing to let a cluster label be an entry point.
 */
export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { projectId: remembered, identity } = useWorkbench();
  // The address bar is what a person clicked, and it is what every screen reads under
  // (useProjectScope), so the nav follows it instead of the id the tab happens to remember.
  const fromPath = pathname.startsWith("/projects/") ? pathname.split("/")[2] ?? "" : "";
  const project = fromPath || remembered;
  const base = project ? `/projects/${project}` : "";

  return (
    <div className="shell">
      <div className="topbar">
        <div className="brand">BioAdapt</div>
        <nav>
          {base ? (
            SCREENS.map(([suffix, label]) => {
              const href = `${base}${suffix}`;
              const active = suffix === "" ? pathname === href : pathname.startsWith(href);
              return (
                <Link key={suffix} href={href} style={active ? { fontWeight: 700 } : undefined}>
                  {label}
                </Link>
              );
            })
          ) : (
            <span className="muted">no project open</span>
          )}
        </nav>
        <div className="actor">
          {identity ? (
            <span className="mono">{identity.subjectId}</span>
          ) : (
            <span className="muted">sign in below</span>
          )}
        </div>
      </div>
      <main className="main">
        {children}
        <IdentityBar />
      </main>
    </div>
  );
}
