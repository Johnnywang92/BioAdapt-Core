"use client";

import { useEffect, useState } from "react";

import { expiryNote, useIdentity } from "@/lib/identity";
import { useT } from "@/lib/i18n";
import { Chip, Field } from "@/components/ui";

/**
 * Who is acting (plan V2.0 sections 11.2, 13.1).
 *
 * This is not a login: the platform decides permissions from the token's signature, and the API
 * answers 401/403 whatever this panel believes. The panel exists because §13.1 separates the person
 * who drafted a conclusion from the person who signs it - a demo that cannot switch identities
 * cannot show that rule working.
 */
export function IdentityBar({ embedded = false, onDone }: { embedded?: boolean; onDone?: () => void }) {
  const t = useT();
  const { identity, projectId, error, setToken, setProjectId, clear, selectPreset } = useIdentity();
  const [draftToken, setDraftToken] = useState("");
  const [draftProject, setDraftProject] = useState("");
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (!identity) setNote(null);
  }, [identity]);

  const subject = identity?.subjectId ?? "";

  function apply() {
    if (!draftToken.trim()) {
      setNote("paste a platform token first — the workbench stores no credentials of its own");
      return;
    }
    setToken(draftToken);
    setNote(null);
    onDone?.();
  }

  async function handlePreset(roleKey: string) {
    const ok = await selectPreset(roleKey);
    if (ok) {
      setNote(null);
      onDone?.();
    } else {
      setNote(`Token for ${roleKey} is not available from dev API. Paste from .workbench/tokens.json.`);
    }
  }

  function openProject() {
    if (!draftProject.trim()) {
      setNote("give the project id the screens should scope to");
      return;
    }
    setProjectId(draftProject);
    setNote(null);
    onDone?.();
  }

  const expiry = identity ? expiryNote(identity.token) : null;

  const body = (
    <div className="identity-body">
      <p className="muted">
        {t(
          "The token decides the role; this panel only says which token a person is holding. A service account can draft, and can never sign off or decide an approval (§11.4).",
        )}
      </p>

      <div className="identity-quick">
        <div className="identity-quick__title">{t("Quick Role Switcher (Reference / Dev)")}</div>
        <div className="identity-quick__buttons">
          <button
            type="button"
            className={identity?.isSolo ? "identity-preset is-active" : "identity-preset secondary"}
            onClick={() => handlePreset("solo")}
            title={t("Full permissions without SoD blocks: self-approvals and self-sign-offs enabled")}
          >
            {t("Solo Mode (All-in-One)")}
          </button>
          <button
            type="button"
            className={identity?.subjectId === "user_analyst_01" && !identity?.isSolo ? "identity-preset is-active" : "identity-preset secondary"}
            onClick={() => handlePreset("analyst")}
          >
            {t("Analyst")}
          </button>
          <button
            type="button"
            className={identity?.subjectId === "user_pi_01" && !identity?.isSolo ? "identity-preset is-active" : "identity-preset secondary"}
            onClick={() => handlePreset("pi")}
          >
            {t("PI (Lead)")}
          </button>
          <button
            type="button"
            className={identity?.subjectId === "user_expert_01" ? "identity-preset is-active" : "identity-preset secondary"}
            onClick={() => handlePreset("expert")}
          >
            {t("Domain Expert")}
          </button>
          <button
            type="button"
            className={identity?.subjectId === "user_expert_02" ? "identity-preset is-active" : "identity-preset secondary"}
            onClick={() => handlePreset("expert2")}
          >
            {t("Expert 2 (Independent)")}
          </button>
        </div>
      </div>

      {identity?.isSolo ? (
        <div className="identity-solo-note">
          <strong>{t("Solo Mode Active")}:</strong>{" "}
          {t("Separation of duties (SoD) restrictions are bypassed. You can request and self-approve gates, issue adhoc grants, and sign off conclusions as the author.")}
        </div>
      ) : null}

      {error ? <div className="error">{t(error)}</div> : null}
      {note ? <div className="notice">{t(note)}</div> : null}

      {identity ? (
        <div className="rows">
          <div className="identity-chips">
            <strong className="mono">{subject}</strong>
            {identity.isSolo ? (
              <Chip title="solo_mode" status="supported">
                {t("Solo Operator")}
              </Chip>
            ) : null}
            {identity.roles.map((role) => (
              <Chip key={role} title={role}>
                {t(role)}
              </Chip>
            ))}
            <Chip>{t(identity.isServiceAccount ? "service account" : "human")}</Chip>
            <Chip status={identity.mfaVerified ? "supported" : undefined}>
              {t(identity.mfaVerified ? "mfa verified" : "no mfa")}
            </Chip>
            <button className="secondary" onClick={clear} type="button">
              {t("forget token")}
            </button>
          </div>
          <p className="muted mono">{expiry ? t(expiry.text, expiry.vars) : t("no expiry claim to show")}</p>
          {!identity.projectIds.includes(projectId) && projectId ? (
            <div className="notice">
              {t("This token does not list project")} <span className="mono">{projectId}</span>.{" "}
              {t(
                "The API scopes every read to the token, so the screens will report a denial rather than borrow another project's rows (§11.2).",
              )}
            </div>
          ) : null}
        </div>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            apply();
          }}
        >
          <Field
            label={t("Compact JWS")}
            hint={t("held in this tab only: sessionStorage, never localStorage, never a URL")}
          >
            <textarea
              rows={3}
              spellCheck={false}
              value={draftToken}
              onChange={(event) => setDraftToken(event.target.value)}
              placeholder="header.payload.signature"
            />
          </Field>
          <button type="submit">{t("Use this token")}</button>
        </form>
      )}

      <form
        className="identity-project"
        onSubmit={(event) => {
          event.preventDefault();
          openProject();
        }}
      >
        <Field label={t("Project")} hint={t("every route below is project scoped on the server")}>
          <input
            value={draftProject}
            onChange={(event) => setDraftProject(event.target.value)}
            placeholder="prj_reference_pbmc"
          />
        </Field>
        <div className="identity-project__actions">
          <button type="submit" disabled={!identity}>
            {t("Open project")}
          </button>
          {projectId ? (
            <span className="muted">
              {t("current")} <span className="mono">{projectId}</span>
            </span>
          ) : null}
        </div>
      </form>
    </div>
  );

  if (embedded) return body;

  return (
    <div className="panel">
      <header>
        <div>
          <h2>{t("Acting as")}</h2>
          <p className="muted">
            {t(
              "The token decides the role; this panel only says which token a person is holding. A service account can draft, and can never sign off or decide an approval (§11.4).",
            )}
          </p>
        </div>
      </header>
      {body}
    </div>
  );
}
