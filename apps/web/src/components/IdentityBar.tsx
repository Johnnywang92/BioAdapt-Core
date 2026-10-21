"use client";

import { useEffect, useState } from "react";

import { expiryNote, useIdentity } from "@/lib/identity";
import { Chip, Field } from "@/components/ui";

/**
 * Who is acting (plan V2.0 sections 11.2, 13.1).
 *
 * This is not a login: the platform decides permissions from the token's signature, and the API
 * answers 401/403 whatever this panel believes. The panel exists because §13.1 separates the person
 * who drafted a conclusion from the person who signs it - a demo that cannot switch identities
 * cannot show that rule working.
 */
export function IdentityBar() {
  const { identity, projectId, error, setToken, setProjectId, clear } = useIdentity();
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
  }

  function openProject() {
    if (!draftProject.trim()) {
      setNote("give the project id the screens should scope to");
      return;
    }
    setProjectId(draftProject);
    setNote(null);
  }

  return (
    <div className="panel">
      <header>
        <div>
          <h2>Acting as</h2>
          <p className="muted">
            The token decides the role; this panel only says which token a person is holding. A
            service account can draft, and can never sign off or decide an approval (§11.4).
          </p>
        </div>
      </header>

      {error ? <div className="error">{error}</div> : null}
      {note ? <div className="notice">{note}</div> : null}

      {identity ? (
        <div className="rows">
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <strong className="mono">{subject}</strong>
            {identity.roles.map((role) => (
              <Chip key={role}>{role}</Chip>
            ))}
            <Chip>{identity.isServiceAccount ? "service account" : "human"}</Chip>
            <Chip status={identity.mfaVerified ? "supported" : undefined}>
              {identity.mfaVerified ? "mfa verified" : "no mfa"}
            </Chip>
            <button className="secondary" onClick={clear} type="button">
              forget token
            </button>
          </div>
          <p className="muted mono">{expiryNote(identity.token) ?? "no expiry claim to show"}</p>
          {!identity.projectIds.includes(projectId) && projectId ? (
            <div className="notice">
              This token does not list project <span className="mono">{projectId}</span>. The API
              scopes every read to the token, so the screens will report a denial rather than borrow
              another project&apos;s rows (§11.2).
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
          <Field label="Compact JWS" hint="held in this tab only: sessionStorage, never localStorage, never a URL">
            <textarea
              rows={3}
              spellCheck={false}
              value={draftToken}
              onChange={(event) => setDraftToken(event.target.value)}
              placeholder="header.payload.signature"
            />
          </Field>
          <button type="submit">Use this token</button>
        </form>
      )}

      <form
        style={{ marginTop: 12 }}
        onSubmit={(event) => {
          event.preventDefault();
          openProject();
        }}
      >
        <Field label="Project" hint="every route below is project scoped on the server">
          <input
            value={draftProject}
            onChange={(event) => setDraftProject(event.target.value)}
            placeholder="prj_reference_pbmc"
          />
        </Field>
        <button type="submit" disabled={!identity}>
          Open project
        </button>
        {projectId ? (
          <span className="muted" style={{ marginLeft: 8 }}>
            current: <span className="mono">{projectId}</span>
          </span>
        ) : null}
      </form>
    </div>
  );
}
