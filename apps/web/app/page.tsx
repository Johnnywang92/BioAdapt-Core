"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Empty, Field, Loading, Notice, Panel, ProblemNote } from "@/components/ui";
import { projects, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useIdentity } from "@/lib/identity";
import { useAsync, useSubmit } from "@/lib/useAsync";
import type { Page, Project, ProjectCreate } from "@/lib/types";

/** A readable id from the words of the goal, offered so nobody has to invent one; it stays editable. */
function suggestProjectId(goal: string): string {
  const slug = goal
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40)
    .replace(/_+$/, "");
  return slug ? `prj_${slug}` : "";
}

/**
 * Starting a project (plan V2.0 section 5.8).
 *
 * A new project is registered at the restricted tier; the privacy tier is not a field on any screen.
 *
 * The id is never something to look up in another system: `GET /projects` lists what this token can
 * open, and a new project is picked from the ids the token already carries, because §11.2 answers a
 * write outside that scope with a refusal no screen can talk the server out of.
 */
export default function StartPage() {
  const t = useT();
  const router = useRouter();
  const { identity, projectId, setProjectId, enterSoloMode } = useIdentity();
  const caller: Caller | null = identity ? { token: identity.token, subjectId: identity.subjectId } : null;
  const listed = useAsync<Page<Project>>(
    () => (caller ? projects.list(caller, { limit: 100 }) : Promise.resolve({ items: [], limit: 0, offset: 0 })),
    [caller?.token],
  );
  const submit = useSubmit();
  const [form, setForm] = useState({
    project_id: "",
    study_goal: "",
    species: "",
    annotation_granularity_target: "",
    review_load_budget_hours_per_week: "4",
  });

  const set = (key: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [key]: event.target.value }));

  const known = new Set(listed.data?.items.map((one) => one.project_id) ?? []);
  const isGlobal = identity?.projectIds.includes("*") ?? false;
  const openable = (identity?.projectIds ?? []).filter((one) => one !== "*" && !known.has(one));
  const suggested = suggestProjectId(form.study_goal);

  function open(next: string) {
    setProjectId(next);
    router.push(`/projects/${encodeURIComponent(next)}`);
  }

  function create(id: string) {
    if (!caller) return;
    const body: ProjectCreate = {
      project_id: id,
      study_goal: form.study_goal,
      species: form.species,
      privacy_tier: "restricted",
      created_by: caller.subjectId,
      annotation_granularity_target: form.annotation_granularity_target,
      review_load_budget_hours_per_week: Number(form.review_load_budget_hours_per_week),
    };
    void submit.run(async () => {
      const created = await projects.create(caller, body);
      setProjectId(created.project_id);
      listed.reload();
      router.push(`/projects/${encodeURIComponent(created.project_id)}`);
      return created;
    });
  }

  return (
    <>
      <Panel
        title={t("⚡ Solo Mode Quickstart")}
        note={t("For solo researchers, evaluators, and full-pipeline walkthroughs without SoD blocks.")}
      >
        <div style={{ display: "flex", gap: "16px", alignItems: "center", flexWrap: "wrap", justifyContent: "space-between" }}>
          <div style={{ maxWidth: "680px" }}>
            <p style={{ margin: 0, fontWeight: 500, lineHeight: 1.5 }}>
              {t("Experience BioAdapt as a unified Solo Operator (PI + Analyst + Expert). Bypass separation-of-duties blocks to self-request, self-approve gates, issue adhoc execution grants, and sign off findings.")}
            </p>
            {identity?.isSolo ? (
              <p style={{ margin: "8px 0 0 0", color: "#059669", fontSize: "0.85rem", fontWeight: 600 }}>
                ✨ {t("Solo Mode Active")}: <span className="mono">{identity.subjectId}</span>
              </p>
            ) : null}
          </div>
          <div>
            <button
              type="button"
              style={{ background: "#10b981", borderColor: "#059669", color: "#fff", fontWeight: 700, padding: "8px 16px" }}
              onClick={async () => {
                await enterSoloMode("prj_scrnaseq");
                open("prj_scrnaseq");
              }}
            >
              🚀 {t("Launch Reference Project in Solo Mode")}
            </button>
          </div>
        </div>
      </Panel>

      <Panel
        title={t("Start or open a project")}
        note={t(
          "A project is the scope of every read: the API resolves the caller from the token and the project from the path.",
        )}
      >
        {projectId ? (
          <p>
            {t("Open")}{" "}
            <Link href={`/projects/${encodeURIComponent(projectId)}`}>
              <span className="mono">{projectId}</span>
            </Link>{" "}
            {t("to register data, approve a plan, read the expert queue and export the report.")}
          </p>
        ) : null}
        <ProblemNote error={listed.error} />
        <Loading on={listed.loading} />
        {caller && listed.data ? (
          listed.data.items.length ? (
            <table>
              <thead>
                <tr>
                  <th>{t("study goal")}</th>
                  <th>{t("species")}</th>
                  <th>{t("created")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {listed.data.items.map((one) => (
                  <tr key={one.project_id}>
                    <td>
                      <Link href={`/projects/${encodeURIComponent(one.project_id)}`}>{one.study_goal}</Link>
                      <div className="mono muted">{one.project_id}</div>
                    </td>
                    <td>{one.species}</td>
                    <td>{one.created_at ?? "—"}</td>
                    <td>
                      <button className="secondary" type="button" onClick={() => open(one.project_id)}>
                        {t("open it")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>
              {t("This token reaches no registered project yet. Create one below, or ask for a token scoped to the project you work on.")}
            </Empty>
          )
        ) : null}
        {!caller ? <p className="muted">{t("paste a token first")}</p> : null}
      </Panel>

      <Panel
        title={t("New project")}
        note={t(
          "created_by is taken from the token you are holding, never typed in: an audit row that names a person has to name the person acting.",
        )}
      >
        <ProblemNote error={submit.error} />
        {openable.length > 0 && (
          <Field
            label={t("project id")}
            hint={t("only the ids this token is scoped to can be opened here; anything else is refused before a row is written (§11.2)")}
          >
            <select value={form.project_id} onChange={set("project_id")}>
              <option value="">{t("choose")}</option>
              {openable.map((one) => (
                <option key={one} value={one}>
                  {one}
                </option>
              ))}
            </select>
          </Field>
        )}
        {isGlobal && (
          <Field
            label={t("project id")}
            hint={t("this token carries every project, so the id is yours to name; the suggestion comes from the study goal and you can change it")}
          >
            <input
              value={form.project_id}
              onChange={(event) => setForm((current) => ({ ...current, project_id: event.target.value }))}
              placeholder={suggested || t("prj_…")}
              className="mono"
            />
          </Field>
        )}
        {!caller && <p className="muted">{t("paste a token first")}</p>}
        {caller && !isGlobal && openable.length === 0 && (
          <Notice>
            {t(
              "This token is scoped only to projects that already exist, so no new project can be written with it. Every screen below is still reachable from the list above.",
            )}
          </Notice>
        )}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            create(form.project_id.trim() || suggested);
          }}
        >
          <div className="grid">
            <Field label="species">
              <input value={form.species} onChange={set("species")} placeholder="Homo sapiens" />
            </Field>
            <Field
              label="review_load_budget_hours_per_week"
              hint={t("§13.4: this number is what the queue size has to fit inside")}
            >
              <input
                value={form.review_load_budget_hours_per_week}
                onChange={set("review_load_budget_hours_per_week")}
                inputMode="decimal"
              />
            </Field>
          </div>
          <Field
            label="study_goal"
            hint={t("what the delivery has to answer; it scopes which candidates matter")}
          >
            <textarea rows={2} value={form.study_goal} onChange={set("study_goal")} />
          </Field>
          <Field label="annotation_granularity_target">
            <input
              value={form.annotation_granularity_target}
              onChange={set("annotation_granularity_target")}
              placeholder="cell_type_broad"
            />
          </Field>
          <button
            type="submit"
            disabled={
              !caller ||
              submit.busy ||
              !(form.project_id.trim() || suggested) ||
              !form.study_goal.trim() ||
              !form.species.trim() ||
              !form.annotation_granularity_target.trim()
            }
          >
            {submit.busy ? t("creating…") : t("Create project")}
          </button>
          <p className="muted">
            {t(
              "A project cannot attest its own cut set: until a principal investigator confirms the thresholds, the analysis still runs but signing a conclusion and exporting stay closed (§1.5).",
            )}
          </p>
        </form>
      </Panel>
    </>
  );
}
