"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Empty, Field, Loading, Notice, Panel, ProblemNote } from "@/components/ui";
import { intentKey, projects as projectsApi, runs as runsApi, type Caller } from "@/lib/api";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync, useSubmit } from "@/lib/useAsync";
import type { Page, Project, ReproducibilityLevel, RunListItem, RunSummary } from "@/lib/types";

const LEVELS: ReproducibilityLevel[] = ["byte", "numeric", "conclusion"];

export default function ProjectOverview({ params }: { params: { projectId: string } }) {
  const { caller, projectId, blocked } = useProjectScope(params.projectId);
  const project = useAsync<Project | null>(
    () => (caller ? projectsApi.read(caller, projectId) : Promise.resolve(null)),
    [caller?.token, projectId],
  );
  const listed = useAsync<Page<RunListItem>>(
    () => (caller ? runsApi.list(caller, projectId, { limit: 50 }) : Promise.resolve({ items: [], limit: 0, offset: 0 })),
    [caller?.token, projectId],
  );

  if (!caller) {
    return (
      <Panel title="Project overview" note="nothing is read until a token is set — the API resolves the caller from it">
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  return (
    <>
      <Panel
        title={`Project ${projectId}`}
        note="the tier on this row is what every later screen inherits: it decides egress, model calls and whether an export is possible at all"
        actions={
          <button className="secondary" type="button" onClick={() => { project.reload(); listed.reload(); }}>
            re-read
          </button>
        }
      >
        <ProblemNote error={project.error} />
        <Loading on={project.loading} />
        {project.data && project.data.project_id ? (
          <dl className="facts">
            <dt>study goal</dt>
            <dd>{project.data.study_goal}</dd>
            <dt>species</dt>
            <dd>{project.data.species}</dd>
            <dt>privacy tier</dt>
            <dd>{project.data.privacy_tier}</dd>
            <dt>regulatory profile</dt>
            <dd>{project.data.regulatory_profile.join(", ") || "none declared"}</dd>
            <dt>annotation target</dt>
            <dd>{project.data.annotation_granularity_target}</dd>
            <dt>review budget</dt>
            <dd>{project.data.review_load_budget_hours_per_week} h/week</dd>
            <dt>thresholds</dt>
            <dd>
              <span className="mono">{String(project.data.thresholds.version)}</span>
              {project.data.thresholds.confirmed === false ? (
                <span className="chip">unconfirmed stage-0 cut set (§7.3)</span>
              ) : null}
              {` · expert queue cap ${String(project.data.thresholds.expert_queue_card_limit)} cards (§13.4)`}
            </dd>
            <dt>created</dt>
            <dd>{project.data.created_at ?? "—"}</dd>
          </dl>
        ) : null}
      </Panel>

      <LaunchRun caller={caller} projectId={projectId} onLaunched={listed.reload} />

      <Panel
        title="Runs"
        note="a launch answers 202 with a queue position; results are read from the cards this run produced, never from this row"
      >
        <ProblemNote error={listed.error} />
        <Loading on={listed.loading} />
        {listed.data && listed.data.items.length ? (
          <table>
            <thead>
              <tr>
                <th>run</th>
                <th>state</th>
                <th>plan</th>
                <th>queue</th>
                <th>started</th>
              </tr>
            </thead>
            <tbody>
              {listed.data.items.map((run) => (
                <tr key={run.run_id}>
                  <td>
                    <Link href={`/projects/${projectId}/runs/${encodeURIComponent(run.run_id)}`}>
                      <span className="mono">{run.run_id}</span>
                    </Link>
                  </td>
                  <td>{run.state}</td>
                  <td className="mono">
                    {run.plan_id} @ {run.plan_version}
                  </td>
                  <td>{run.queue_position === null ? "not queued" : run.queue_position}</td>
                  <td>{run.started_at ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          !listed.loading && <Empty>This project has no runs yet.</Empty>
        )}
      </Panel>
    </>
  );
}

/** Starting a run (sections 9.2, 9.7): the key is shown so a person can tell a replay from a second run. */
function LaunchRun({
  caller,
  projectId,
  onLaunched,
}: {
  caller: Caller;
  projectId: string;
  onLaunched: () => void;
}) {
  const submit = useSubmit();
  const [form, setForm] = useState({
    plan_id: "",
    plan_version: "",
    input_asset_ids: "",
    tool_registry_snapshot: "",
    priority: "5",
    reproducibility_level_target: "conclusion" as ReproducibilityLevel,
  });
  const [key, setKey] = useState("");

  useEffect(() => {
    setKey(intentKey(`run_${projectId}`));
  }, [projectId]);

  const set = (name: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [name]: event.target.value }));

  function launch() {
    const assets = form.input_asset_ids
      .split(",")
      .map((one) => one.trim())
      .filter(Boolean);
    if (!assets.length) return;
    void submit.run(async () => {
      const created = await runsApi.launch(
        caller,
        projectId,
        {
          plan_id: form.plan_id,
          plan_version: form.plan_version,
          input_asset_ids: assets,
          tool_registry_snapshot: form.tool_registry_snapshot,
          parameters: {},
          reproducibility_level_target: form.reproducibility_level_target,
          priority: Number(form.priority),
        },
        key,
      );
      onLaunched();
      return created;
    });
  }

  const created = submit.result as RunSummary | null;

  return (
    <Panel
      title="Launch a run"
      note="the plan has to be recorded and its input assets registered first; a launch that names an unknown asset is refused, not guessed around"
    >
      <ProblemNote error={submit.error} />
      {created ? (
        <Notice>
          Queued <span className="mono">{created.run_id}</span> in state {created.state}, position{" "}
          {created.queue_position ?? "—"}.{" "}
          <Link href={`/projects/${projectId}/runs/${encodeURIComponent(created.run_id)}`}>open the run</Link>
        </Notice>
      ) : null}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          launch();
        }}
      >
        <div className="grid">
          <Field label="plan_id">
            <input value={form.plan_id} onChange={set("plan_id")} className="mono" />
          </Field>
          <Field label="plan_version">
            <input value={form.plan_version} onChange={set("plan_version")} className="mono" />
          </Field>
        </div>
        <Field label="input_asset_ids" hint="comma separated; each one has to be registered in this project">
          <input value={form.input_asset_ids} onChange={set("input_asset_ids")} className="mono" />
        </Field>
        <div className="grid">
          <Field label="tool_registry_snapshot" hint="the registry version the run is claimed reproducible against">
            <input value={form.tool_registry_snapshot} onChange={set("tool_registry_snapshot")} className="mono" />
          </Field>
          <Field label="reproducibility_level_target">
            <select value={form.reproducibility_level_target} onChange={set("reproducibility_level_target")}>
              {LEVELS.map((level) => (
                <option key={level} value={level}>
                  {level}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="grid">
          <Field label="priority" hint="0 lowest, 9 highest">
            <input value={form.priority} onChange={set("priority")} inputMode="numeric" />
          </Field>
          <Field label="Idempotency-Key" hint="a resend of this click replays the original run; a new intent gets a new key">
            <input className="mono" value={key} onChange={(event) => setKey(event.target.value)} />
          </Field>
        </div>
        <button type="submit" disabled={submit.busy}>
          {submit.busy ? "launching…" : "Launch"}
        </button>
      </form>
    </Panel>
  );
}
