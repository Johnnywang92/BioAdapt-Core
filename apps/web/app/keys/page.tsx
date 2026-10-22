"use client";

import { useState } from "react";
import type { FormEvent } from "react";

import { Chip, Empty, Field, Loading, Notice, Panel, ProblemNote } from "@/components/ui";
import { credentials, projects, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useIdentity } from "@/lib/identity";
import { useAsync, useSubmit } from "@/lib/useAsync";
import type { CredentialRegistration, IamCredential, Page, Project } from "@/lib/types";

/** The roles the platform's policy table knows, in the order §11.2 introduces them. */
const ROLES = [
  "platform_admin",
  "principal_investigator",
  "analyst",
  "domain_expert",
  "knowledge_curator",
  "evaluation_lead",
  "compliance_officer",
  "service_agent",
];

/**
 * A `.pub` file may be pasted whole: the login helper writes the PEM block and then a comment line that
 * names the kid, the fingerprint and the subject. Only the block is key material, and the server reads
 * the identifiers off the key itself, so the note is dropped here instead of asking a person to edit it
 * out by hand.
 */
function pemBlock(text: string): string {
  const begin = text.indexOf("-----BEGIN");
  const endMarker = text.lastIndexOf("-----END");
  if (begin < 0 || endMarker < 0) return text.trim();
  const lineEnd = text.indexOf("\n", endMarker);
  return text.slice(begin, lineEnd < 0 ? text.length : lineEnd).trim();
}

/**
 * Who may sign in (plan V2.0 sections 11.2, 11.4, 13.1).
 *
 * A key file replaces the bearer token that used to be printed at deployment time, so this screen is
 * what makes the replacement worth having: it is the place the authority of a key is *written*, and the
 * place it is taken back. Revoking here is a row update, so the next login with that key is refused -
 * a person holding a token minted before the revocation runs out on its own TTL, which is minutes.
 *
 * Nothing on this screen is a login. The reference workbench still mints role tokens for the demo, and
 * §11.2 closes that route the moment a deployment declares itself configured.
 */
export default function KeyDirectoryPage() {
  const t = useT();
  const { identity } = useIdentity();
  const caller: Caller | null = identity ? { token: identity.token, subjectId: identity.subjectId } : null;
  const [activeOnly, setActiveOnly] = useState(true);
  const listed = useAsync<Page<IamCredential>>(
    () =>
      caller
        ? credentials.list(caller, { active_only: activeOnly, limit: 100 })
        : Promise.resolve({ items: [], limit: 0, offset: 0 }),
    [caller?.token, activeOnly],
  );
  const openable = useAsync<Page<Project>>(
    () => (caller ? projects.list(caller, { limit: 100 }) : Promise.resolve({ items: [], limit: 0, offset: 0 })),
    [caller?.token],
  );

  return (
    <>
      <Panel
        title={t("Keys that may sign in")}
        note={t(
          "A key file is authorised here, with the roles and projects a login with it is granted. The key itself never carries authority: revoking a row closes the next login rather than waiting for a token to expire (§11.2). Revoking is final for that key pair - the directory refuses to register the same public key twice, so a person whose key was revoked signs in with a newly generated one.",
        )}
        actions={
          <button className="secondary" type="button" disabled={!caller || listed.loading} onClick={listed.reload}>
            {t("re-read")}
          </button>
        }
      >
        {!caller ? (
          <p className="muted">{t("paste a token first")}</p>
        ) : (
          <>
            <ProblemNote error={listed.error} />
            <Loading on={listed.loading} />
            <label style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 12 }}>
              <input
                type="checkbox"
                style={{ width: "auto" }}
                checked={activeOnly}
                onChange={(event) => setActiveOnly(event.target.checked)}
              />
              {t("hide revoked keys")}
            </label>
            {listed.data?.items.length ? (
              <table>
                <thead>
                  <tr>
                    <th>{t("subject")}</th>
                    <th>{t("roles")}</th>
                    <th>{t("reaches")}</th>
                    <th>{t("a login with this key is")}</th>
                    <th>{t("fingerprint")}</th>
                    <th>{t("added")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {listed.data.items.map((row) => (
                    <CredentialRow key={row.kid} row={row} caller={caller} onChanged={listed.reload} />
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>
                {t("No key is registered here yet, so a key file cannot sign in - the routes refuse it until an admin writes a row.")}
              </Empty>
            )}
          </>
        )}
      </Panel>

      <RegisterKey
        caller={caller}
        projectIds={
          identity?.projectIds.includes("*")
            ? ["*", ...(openable.data?.items.map((one) => one.project_id) ?? [])]
            : identity?.projectIds ?? []
        }
        onRegistered={listed.reload}
      />

      <Panel
        title={t("How a person gets a key file")}
        note={t(
          "The private half is written on their own machine and never sent anywhere; the public half is what is pasted above. A token bought at login expires on its own, which is why there is nothing to rotate.",
        )}
      >
        <pre className="mono" style={{ whiteSpace: "pre-wrap" }}>
          {`.venv/bin/python scripts/bioadapt_login.py keygen --out ~/.ssh/bioadapt.pem --subject user_pi_02
# hand the printed .pub file to an admin; the private half stays on this machine
.venv/bin/python scripts/bioadapt_login.py login --key ~/.ssh/bioadapt.pem
# prints a short access token for the 'Acting as' panel`}
        </pre>
        <Notice>
          {t(
            "Losing the file costs a login, not the project: register a new public key for the same subject and revoke the old row. There is no second place a secret has to be rotated.",
          )}
        </Notice>
      </Panel>
    </>
  );
}

function CredentialRow({
  row,
  caller,
  onChanged,
}: {
  row: IamCredential;
  caller: Caller;
  onChanged: () => void;
}) {
  const t = useT();
  const submit = useSubmit();
  const revoked = row.revoked_at !== null;

  return (
    <tr>
      <td>
        <span className="mono">{row.subject_id}</span>
        {row.label ? <div className="muted">{row.label}</div> : null}
        <ProblemNote error={submit.error} />
      </td>
      <td>
        {row.roles.map((role) => (
          <Chip key={role} title={role}>
            {t(role)}
          </Chip>
        ))}
      </td>
      <td>
        {row.project_ids.map((one) => (
          <span key={one} className="mono">
            {one === "*" ? t("every project") : one}{" "}
          </span>
        ))}
      </td>
      <td>
        {row.is_service_account ? (
          <Chip title="is_service_account">{t("a pipeline, never a person (§11.4)")}</Chip>
        ) : (
          <Chip title="is_service_account">{t("a person")}</Chip>
        )}
        {row.amr.length ? (
          <Chip status="supported" title="amr">
            {t("second factor attested")}
          </Chip>
        ) : (
          <Chip title="amr">{t("no second factor")}</Chip>
        )}
        {row.solo_mode ? (
          <Chip status="supported" title="solo_mode">
            ⚡ {t("Solo Operator")}
          </Chip>
        ) : null}
      </td>
      <td>
        <div className="mono" title={row.kid}>
          {row.fingerprint}
        </div>
        <div className="muted mono">{revoked ? `${t("revoked by")} ${row.revoked_by}` : row.kid}</div>
      </td>
      <td>
        <div>{row.created_at ?? "—"}</div>
        <div className="muted mono">{t("by")} {row.created_by}</div>
      </td>
      <td>
        {revoked ? (
          <span className="muted">{t("revoked")}</span>
        ) : (
          <button
            className="secondary"
            type="button"
            disabled={submit.busy}
            onClick={() => void submit.run(async () => credentials.revoke(caller, row.kid)).then(onChanged)}
          >
            {submit.busy ? t("revoking…") : t("Revoke this key")}
          </button>
        )}
      </td>
    </tr>
  );
}

function RegisterKey({
  caller,
  projectIds,
  onRegistered,
}: {
  caller: Caller | null;
  projectIds: string[];
  onRegistered: () => void;
}) {
  const t = useT();
  const submit = useSubmit();
  const [form, setForm] = useState({
    subject_id: "",
    public_key: "",
    label: "",
    project_id: projectIds[0] ?? "",
    amr: true,
    solo_mode: false,
    is_service_account: false,
  });
  const [roles, setRoles] = useState<string[]>(["analyst"]);

  const set = (key: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));

  const parsed = pemBlock(form.public_key);
  const ready = Boolean(caller && form.subject_id.trim() && parsed && roles.length && form.project_id);

  async function write(event: FormEvent) {
    event.preventDefault();
    if (!caller) return;
    const body: CredentialRegistration = {
      subject_id: form.subject_id.trim(),
      public_key: parsed,
      roles,
      project_ids: [form.project_id === "*" ? "*" : form.project_id],
      amr: form.amr ? ["pwd+mfa"] : [],
      solo_mode: form.solo_mode,
      is_service_account: form.is_service_account,
      label: form.label.trim() || null,
    };
    const created = await submit.run(() => credentials.register(caller, body));
    if (created) {
      setForm((current) => ({ ...current, public_key: "", label: "" }));
      onRegistered();
    }
  }

  return (
    <Panel
      title={t("Authorise a key")}
      note={t(
        "Paste the contents of someone's .pub file. The key's own id and fingerprint are read off the key, so nothing here has to be copied into another system; what is chosen below is what a login with that key is granted.",
      )}
    >
      {!caller ? (
        <p className="muted">{t("paste a token first")}</p>
      ) : (
        <form onSubmit={write}>
          <ProblemNote error={submit.error} />
          {submit.result ? (
            <Notice>
              {t("Registered as")} <span className="mono">{(submit.result as IamCredential).kid}</span> ·{" "}
              {(submit.result as IamCredential).fingerprint}
            </Notice>
          ) : null}
          <div className="grid">
            <Field label={t("subject")} hint={t("the person or the pipeline this key belongs to; a login cannot claim another one")}>
              <input value={form.subject_id} onChange={set("subject_id")} placeholder="user_analyst_01" className="mono" />
            </Field>
            <Field label={t("project")} hint={t("the scope every route a login reaches is checked against (§11.2)")}>
              {projectIds.length ? (
                <select value={form.project_id} onChange={set("project_id")}>
                  {projectIds.map((one) => (
                    <option key={one} value={one}>
                      {one === "*" ? t("every project") : one}
                    </option>
                  ))}
                </select>
              ) : (
                <input value={form.project_id} onChange={set("project_id")} placeholder="prj_…" className="mono" />
              )}
            </Field>
          </div>
          <Field
            label={t("public key")}
            hint={t("the PEM block of a .pub file; the trailing note line is ignored, and the private half is never pasted anywhere")}
          >
            <textarea
              rows={5}
              spellCheck={false}
              value={form.public_key}
              onChange={set("public_key")}
              placeholder={"-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"}
              className="mono"
            />
          </Field>
          <Field label={t("roles")} hint={t("§13.1 refuses a pair that a live session could not hold together, so a key cannot be granted around the rules")}>
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              {ROLES.map((role) => (
                <label key={role} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <input
                    type="checkbox"
                    style={{ width: "auto" }}
                    checked={roles.includes(role)}
                    onChange={(event) =>
                      setRoles((current) =>
                        event.target.checked ? [...current, role] : current.filter((one) => one !== role),
                      )
                    }
                  />
                  <Chip title={role}>{t(role)}</Chip>
                </label>
              ))}
            </div>
          </Field>
          <Field label={t("what the login is granted")} hint={t("these three answers are the difference between a person, a pipeline and a shortcut")}>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <label style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
                <input
                  type="checkbox"
                  style={{ width: "auto", marginTop: 3 }}
                  checked={form.amr}
                  onChange={(event) => setForm((current) => ({ ...current, amr: event.target.checked }))}
                />
                <span>
                  {t("a second factor was attested for this subject")}
                  <span className="muted" style={{ display: "block" }}>
                    {t("without it the platform refuses the actions that need one: promoting a tool, deleting data, confirming thresholds, granting a role (§11.6)")}
                  </span>
                </span>
              </label>
              <label style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
                <input
                  type="checkbox"
                  style={{ width: "auto", marginTop: 3 }}
                  checked={form.is_service_account}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, is_service_account: event.target.checked }))
                  }
                />
                <span>
                  {t("this key signs in as a pipeline, not as a person")}
                  <span className="muted" style={{ display: "block" }}>
                    {t("it may only hold the service account role, and every gate a person has to open stays closed to it (§11.4)")}
                  </span>
                </span>
              </label>
              <label style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
                <input
                  type="checkbox"
                  style={{ width: "auto", marginTop: 3 }}
                  checked={form.solo_mode}
                  onChange={(event) => setForm((current) => ({ ...current, solo_mode: event.target.checked }))}
                />
                <span>
                  {t("this subject works alone")}
                  <span className="muted" style={{ display: "block" }}>
                    {t("a solo operator is exempt from separation-of-duties blocks, which is a decision about a person, not a convenience setting")}
                  </span>
                </span>
              </label>
            </div>
          </Field>
          <Field label={t("label")} hint={t("where this key lives, so a revocation can be explained to the person who holds it")}>
            <input value={form.label} onChange={set("label")} placeholder={t("workstation, office laptop")} />
          </Field>
          <button type="submit" disabled={!ready || submit.busy}>
            {submit.busy ? t("writing…") : t("Authorise this key")}
          </button>
        </form>
      )}
    </Panel>
  );
}
