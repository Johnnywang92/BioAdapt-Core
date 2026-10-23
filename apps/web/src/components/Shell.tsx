"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { IdentityBar } from "@/components/IdentityBar";
import { DeploymentStatus } from "@/components/DeploymentStatus";
import { LOCALES, useLocale, useT } from "@/lib/i18n";
import { useIdentity } from "@/lib/identity";

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
 *
 * The identity panel is a drawer rather than a section under every screen: it is demo tooling for
 * switching whose token the tab holds, and repeating it under each page pushed the actual work up.
 */
export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { t } = useLocale();
  const { projectId: remembered, identity, enterSoloMode, exitSoloMode } = useIdentity();
  const [menuOpen, setMenuOpen] = useState(false);
  const [identityOpen, setIdentityOpen] = useState(false);
  // The address bar is what a person clicked, and it is what every screen reads under
  // (useProjectScope), so the nav follows it instead of the id the tab happens to remember.
  const fromPath = pathname.startsWith("/projects/") ? pathname.split("/")[2] ?? "" : "";
  const project = fromPath || remembered;
  const base = project ? `/projects/${project}` : "";

  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!identityOpen) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIdentityOpen(false);
    };
    document.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [identityOpen]);

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar__brand-group">
          <Link href="/" className="brand">
            BioAdapt
          </Link>
          {project ? (
            <div className="project-context">
              <span className="project-context__id mono" title={project}>
                {project}
              </span>
            </div>
          ) : null}
        </div>

        <nav
          id="screens"
          className={menuOpen ? "topbar__nav is-open" : "topbar__nav"}
          aria-label={t("screens")}
        >
          {base ? (
            SCREENS.map(([suffix, label]) => {
              const href = `${base}${suffix}`;
              const active = suffix === "" ? pathname === href : pathname.startsWith(href);
              return (
                <Link
                  key={suffix}
                  href={href}
                  className={active ? "is-active" : undefined}
                  aria-current={active ? "page" : undefined}
                >
                  {t(label)}
                </Link>
              );
            })
          ) : (
            <span className="muted topbar__empty">{t("no project open")}</span>
          )}
          {/* Platform level, so it is not part of the pipeline order above: who may sign in is not a
              property of the project currently open (§11.2). */}
          <span className="topbar__nav-divider" aria-hidden="true" />
          <Link href="/keys" className={pathname.startsWith("/keys") ? "is-active" : undefined}>
            {t("sign-in keys")}
          </Link>
          <LocaleSwitch variant="nav" />
        </nav>

        <div className="topbar__actions">
          <button
            type="button"
            className={identity?.isSolo ? "solo-toggle is-active" : "solo-toggle"}
            aria-pressed={identity?.isSolo ?? false}
            onClick={() => {
              if (identity?.isSolo) {
                void exitSoloMode();
              } else {
                void enterSoloMode(project || undefined);
              }
            }}
            title={
              identity?.isSolo
                ? t("Solo Mode active. Click to switch to standard Analyst role.")
                : t("Click to enter Solo Mode (All-in-One researcher)")
            }
          >
            <span className="solo-toggle__mark" aria-hidden="true" />
            <span>{identity?.isSolo ? t("Solo Mode (Active)") : t("Solo Mode")}</span>
          </button>

          <button
            type="button"
            className="identity-button"
            onClick={() => setIdentityOpen(true)}
            title={t("identity and project settings")}
            aria-haspopup="dialog"
            aria-expanded={identityOpen}
          >
            <span className="identity-button__avatar" aria-hidden="true">
              {identity ? identity.subjectId.slice(0, 1).toUpperCase() : "?"}
            </span>
            <span className="identity-button__label mono">{identity ? identity.subjectId : t("sign in")}</span>
          </button>

          <LocaleSwitch />

          <button
            type="button"
            className="menu-toggle secondary"
            aria-expanded={menuOpen}
            aria-controls="screens"
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? t("close") : t("menu")}
          </button>
        </div>
      </header>

      {identityOpen ? (
        <div className="drawer-backdrop" role="presentation" onMouseDown={() => setIdentityOpen(false)}>
          <aside
            className="drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="identity-drawer-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="drawer__head">
              <div>
                <h2 id="identity-drawer-title">{t("Acting as")}</h2>
                <p className="muted">{t("identity and project settings")}</p>
              </div>
              <button
                type="button"
                className="icon-button"
                onClick={() => setIdentityOpen(false)}
                aria-label={t("close")}
              >
                ×
              </button>
            </div>
            <IdentityBar embedded onDone={() => setIdentityOpen(false)} />
          </aside>
        </div>
      ) : null}

      <main className="main">
        <DeploymentStatus />
        {children}
      </main>
    </div>
  );
}

function LocaleSwitch({ variant }: { variant?: "nav" }) {
  const { locale, setLocale, t } = useLocale();
  return (
    <div
      className={variant === "nav" ? "locale locale--nav" : "locale"}
      role="group"
      aria-label={t("language")}
    >
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
  );
}
