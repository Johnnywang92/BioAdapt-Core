"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { IdentityBar } from "@/components/IdentityBar";
import { DeploymentStatus } from "@/components/DeploymentStatus";
import { Chip, ProblemNote } from "@/components/ui";
import { projects, type Caller } from "@/lib/api";
import { LOCALES, useLocale, useT } from "@/lib/i18n";
import { useIdentity } from "@/lib/identity";
import { useAsync } from "@/lib/useAsync";
import type { PrivacyTier } from "@/lib/types";

const SCREENS: [string, string][] = [
  ["", "overview"],
  ["/data", "data"],
  ["/plans", "plans"],
  ["/approvals", "approvals"],
  ["/runs", "runs"],
  ["/queue", "expert queue"],
  ["/knowledge", "knowledge base"],
  ["/exports", "exports"],
];

const LOCALE_LABELS: Record<string, string> = { en: "EN", zh: "中文" };

/**
 * The navigation is the pipeline order of plan V2.0 section 5.1, not a list of features: register,
 * plan, approve, run, queue, review, deliver. A card is reachable only through a run and a queue,
 * which is what §5.6 means by refusing to let a cluster label be an entry point.
 */
export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { locale, setLocale, t } = useLocale();
  const { projectId: remembered, identity, enterSoloMode, exitSoloMode } = useIdentity();
  // The address bar is what a person clicked, and it is what every screen reads under
  // (useProjectScope), so the nav follows it instead of the id the tab happens to remember.
  const fromPath = pathname.startsWith("/projects/") ? pathname.split("/")[2] ?? "" : "";
  const project = fromPath || remembered;
  const base = project ? `/projects/${project}` : "";

  return (
    <div className="shell">
      <div className="topbar">
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <div className="brand">BioAdapt</div>
          {project && (
            <ProjectPrivacy
              key={`${project}:${identity?.token ?? "anonymous"}`}
              projectId={fromPath ? decodeURIComponent(fromPath) : remembered}
              caller={identity ? { token: identity.token, subjectId: identity.subjectId } : null}
            />
          )}
        </div>
        <nav>
          {base ? (
            SCREENS.map(([suffix, label]) => {
              const href = `${base}${suffix}`;
              const active = suffix === "" ? pathname === href : pathname.startsWith(href);
              return (
                <Link key={suffix} href={href} style={active ? { fontWeight: 700 } : undefined}>
                  {t(label)}
                </Link>
              );
            })
          ) : (
            <span className="muted">{t("no project open")}</span>
          )}
          {/* Platform level, so it is not part of the pipeline order above: who may sign in is not a
              property of the project currently open (§11.2). */}
          <Link href="/keys" style={pathname.startsWith("/keys") ? { fontWeight: 700 } : undefined}>
            {t("sign-in keys")}
          </Link>
        </nav>
        <div className="actor" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <button
            type="button"
            onClick={() => {
              if (identity?.isSolo) {
                void exitSoloMode();
              } else {
                void enterSoloMode(project || undefined);
              }
            }}
            className={identity?.isSolo ? undefined : "secondary"}
            style={
              identity?.isSolo
                ? {
                    background: "#10b981",
                    borderColor: "#059669",
                    color: "#fff",
                    fontWeight: 700,
                    padding: "3px 9px",
                    fontSize: "0.78rem",
                    borderRadius: "6px",
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "4px",
                  }
                : {
                    borderColor: "#10b981",
                    color: "#059669",
                    fontWeight: 600,
                    padding: "3px 9px",
                    fontSize: "0.78rem",
                    borderRadius: "6px",
                    cursor: "pointer",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: "4px",
                  }
            }
            title={
              identity?.isSolo
                ? t("Solo Mode active. Click to switch to standard Analyst role.")
                : t("Click to enter Solo Mode (All-in-One researcher)")
            }
          >
            <span>⚡</span>
            <span>{identity?.isSolo ? t("Solo Mode (Active)") : t("Solo Mode")}</span>
          </button>

          {identity ? (
            <span className="mono" style={{ fontSize: "0.85rem" }}>{identity.subjectId}</span>
          ) : (
            <span className="muted" style={{ fontSize: "0.85rem" }}>{t("sign in below")}</span>
          )}
          <div className="locale">
            {LOCALES.map((one) => (
              <button
                key={one}
                type="button"
                className={one === locale ? undefined : "secondary"}
                aria-pressed={one === locale}
                onClick={() => setLocale(one)}
              >
                {LOCALE_LABELS[one]}
              </button>
            ))}
          </div>
        </div>
      </div>
      <main className="main">
        <DeploymentStatus />
        {children}
        <IdentityBar />
      </main>
    </div>
  );
}

const PRIVACY_NOTES: Record<PrivacyTier, string> = {
  public: "External model calls and exports remain subject to project policies and provider terms.",
  restricted: "Raw data cannot leave the project. Only permitted aggregate derivations may be used externally; cloud calls are checked field by field.",
  strict_local: "Local processing only. Cloud model calls and this export route are blocked by project policy.",
  hgr_controlled: "Human genetic resources are controlled. Cloud model calls and this export route are blocked by project policy.",
};

function ProjectPrivacy({ caller, projectId }: { caller: Caller | null; projectId: string }) {
  const t = useT();
  const read = useAsync(() => caller ? projects.read(caller, projectId) : Promise.resolve(null), [caller?.token, projectId]);
  const tier = !read.loading && !read.error ? read.data?.privacy_tier : undefined;
  return (
    <details className="privacy-details">
      <summary><Chip title={tier}>{tier ? t(tier) : t("Privacy policy not confirmed")}</Chip></summary>
      <div className="privacy-content">
        <strong>{t("privacy tier")}</strong>
        <p>{tier ? t(PRIVACY_NOTES[tier]) : t("The project privacy policy has not been read successfully. Do not assume local-only processing.")}</p>
        {tier ? <p className="mono">{tier}</p> : null}
        <ProblemNote error={read.error} />
        <button type="button" className="secondary" disabled={!caller || read.loading} onClick={read.reload}>{t("re-read")}</button>
      </div>
    </details>
  );
}
