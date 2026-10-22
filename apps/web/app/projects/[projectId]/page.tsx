"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Empty, Field, Loading, Notice, Panel, ProblemNote } from "@/components/ui";
import { DeploymentNotice } from "@/components/DeploymentStatus";
import { NextSteps, RoleHelp } from "@/components/NextSteps";
import { ThresholdConfirm } from "@/components/ThresholdConfirm";
import {
  assets as assetsApi,
  intentKey,
  plans as plansApi,
  projects as projectsApi,
  runs as runsApi,
  tools as toolsApi,
  type Caller,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync, useSubmit } from "@/lib/useAsync";
import type {
  DataAsset,
  Page,
  PlanSummary,
  Project,
  ReproducibilityLevel,
  RunListItem,
  RunSummary,
  ToolCatalog,
} from "@/lib/types";

const LEVELS: ReproducibilityLevel[] = ["byte", "numeric", "conclusion"];
const PRIORITIES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];

/**
 * §9.7: the launch pickers read at the hard cap. A plan or an asset that exists but lands on an unseen
 * page would look like it was never recorded, and the person would be sent off to re-register it.
 */
const PICKER_LIMIT = 500;

export default function ProjectOverview({ params }: { params: { projectId: string } }) {
  const t = useT();
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
      <Panel
        title={t("Project overview")}
        note={t("nothing is read until a token is set — the API resolves the caller from it")}
      >
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  return (
    <>
      <Panel
        title={`${t("Project")} ${projectId}`}
        note={t(
          "the tier on this row is what every later screen inherits: it decides egress, model calls and whether an export is possible at all",
        )}
        actions={
          <button className="secondary" type="button" onClick={() => { project.reload(); listed.reload(); }}>
            {t("re-read")}
          </button>
        }
      >
        <ProblemNote error={project.error} />
        <Loading on={project.loading} />
        {project.data && project.data.project_id ? (
          <dl className="facts">
            <dt>{t("study goal")}</dt>
            <dd>{project.data.study_goal}</dd>
            <dt>{t("species")}</dt>
            <dd>{project.data.species}</dd>
            <dt>{t("privacy tier")}</dt>
            <dd>{t(project.data.privacy_tier)}</dd>
            <dt>{t("annotation target")}</dt>
            <dd>{project.data.annotation_granularity_target}</dd>
            <dt>{t("review budget")}</dt>
            <dd>{project.data.review_load_budget_hours_per_week} {t("h/week")}</dd>
            <dt>{t("thresholds")}</dt>
            <dd>
              <span className="mono">{String(project.data.thresholds.version)}</span>
              {project.data.thresholds.confirmed === false ? (
                <span className="chip">
                  {t("unconfirmed stage-0 cut set · sign-off and export are closed (§1.5)")}
                </span>
              ) : null}
              {t(" · expert queue cap {cap} cards (§13.4)", {
                cap: String(project.data.thresholds.expert_queue_card_limit),
              })}
            </dd>
            <dt>{t("created")}</dt>
            <dd>{project.data.created_at ?? "—"}</dd>
          </dl>
        ) : null}
      </Panel>

      {project.data && (
        <Panel
          title={t("Where this stands")}
          note={t(
            "each line is a read the API made just now, in the order the work goes: register, plan, run, review, deliver",
          )}
          actions={
            <button
              className="secondary"
              type="button"
              onClick={() => {
                project.reload();
              }}
            >
              {t("re-read")}
            </button>
          }
        >
          <NextSteps caller={caller} projectId={projectId} project={project.data} />
        </Panel>
      )}

      {project.data && <ThresholdConfirm caller={caller} project={project.data} onConfirmed={project.reload} />}

      <LaunchRun caller={caller} projectId={projectId} onLaunched={listed.reload} />

      <Panel
        title={t("Runs")}
        note={t(
          "a launch answers 202 with a queue position; results are read from the cards this run produced, never from this row",
        )}
      >
        <ProblemNote error={listed.error} />
        <Loading on={listed.loading} />
        {listed.data && listed.data.items.length ? (
          <table>
            <thead>
              <tr>
                <th>{t("run")}</th>
                <th>{t("state")}</th>
                <th>{t("plan")}</th>
                <th>{t("queue")}</th>
                <th>{t("started")}</th>
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
                  <td>{t(run.state)}</td>
                  <td className="mono">
                    {run.plan_id} @ {run.plan_version}
                  </td>
                  <td>{run.queue_position === null ? t("not queued") : run.queue_position}</td>
                  <td>{run.started_at ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          !listed.loading && <Empty>{t("This project has no runs yet.")}</Empty>
        )}
      </Panel>

      <Panel
        title={t("What each role does here")}
        note={t(
          "separation of duties is the server's rule, not a screen setting: the person who asks for a gate is never the one who opens it (§11.6)",
        )}
      >
        <RoleHelp />
      </Panel>
    </>
  );
}

/**
 * Starting a run (sections 9.2, 9.7): every field here is chosen from a row the project already holds.
 *
 * The plan and its version come from `GET /plans` as one choice, because a version without its plan id is
 * not launchable and a typed pair can name a plan that was never recorded. The input asset comes from
 * `GET /assets` and is single, not multiple: §9.2 fills one root slot, and a launch handed two assets is a
 * run row that cannot execute. The registry snapshot comes from `GET /tools`, which is the same
 * construction the executor hydrates - so it is read on every load rather than remembered: the snapshot
 * covers the registered set this deployment holds, and execution refuses a run pinned against anything
 * else (§4.1). A live ad-hoc grant is deliberately not part of it (§9.6). The idempotency key
 * stays visible and editable, because telling a replay apart from a second run is a person's call.
 */
function LaunchRun({
  caller,
  projectId,
  onLaunched,
}: {
  caller: Caller;
  projectId: string;
  onLaunched: () => void;
}) {
  const t = useT();
  const submit = useSubmit();
  const plans = useAsync<Page<PlanSummary>>(
    () => plansApi.list(caller, projectId, { limit: PICKER_LIMIT }),
    [caller.token, projectId],
  );
  const stored = useAsync<Page<DataAsset>>(
    () => assetsApi.list(caller, projectId, { limit: PICKER_LIMIT }),
    [caller.token, projectId],
  );
  const catalog = useAsync<ToolCatalog>(() => toolsApi.read(caller, projectId), [caller.token, projectId]);

  const [digest, setDigest] = useState("");
  const [assetId, setAssetId] = useState("");
  const [form, setForm] = useState({
    priority: "5",
    reproducibility_level_target: "conclusion" as ReproducibilityLevel,
  });
  const [key, setKey] = useState("");

  useEffect(() => {
    setKey(intentKey(`run_${projectId}`));
  }, [projectId]);

  const rows = plans.data?.items ?? [];
  const assets = stored.data?.items ?? [];
  /**
   * The chosen plan is looked up on every read rather than remembered: a re-read that no longer carries
   * these bytes means the launch would pin a version this project does not hold, and the route refuses it.
   */
  const plan = digest ? rows.find((one) => one.plan_sha256 === digest) ?? null : null;
  const asset = assets.find((one) => one.asset_id === assetId) ?? null;
  const snapshot = catalog.data?.snapshot_id ?? "";
  const pinnedTools = plan
    ? [...new Set(plan.nodes.map((one) => `${one.tool_name}@${one.tool_version}`))]
    : [];

  const set = (name: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [name]: event.target.value }));

  /** Why the button is not clickable yet, in the screen's own words rather than a silent no-op. */
  const blocked = !plan
    ? t("pick a recorded plan first")
    : !asset
      ? t("pick the one asset this run is handed as its root input")
      : !snapshot
        ? t("the registry snapshot could not be read, so nothing can be pinned")
        : null;

  function launch() {
    if (!plan || !asset || !snapshot) return;
    void submit.run(async () => {
      const created = await runsApi.launch(
        caller,
        projectId,
        {
          plan_id: plan.plan_id,
          plan_version: plan.plan_version,
          input_asset_ids: [asset.asset_id],
          tool_registry_snapshot: snapshot,
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
      title={t("Launch a run")}
      note={t(
        "the plan has to be recorded and its input assets registered first; a launch that names an unknown asset is refused, not guessed around",
      )}
    >
      <DeploymentNotice capability="run_execution" />
      <ProblemNote error={submit.error} />
      {created ? (
        <Notice>
          {t("Queued")} <span className="mono">{created.run_id}</span>
          {t(", in state")} {t(created.state)}
          {t(", position")} {created.queue_position ?? "—"}.{" "}
          <Link href={`/projects/${projectId}/runs/${encodeURIComponent(created.run_id)}`}>
            {t("open the run")}
          </Link>
        </Notice>
      ) : null}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          launch();
        }}
      >
        <fieldset>
          <legend>{t("what to run")}</legend>
          <Field
            label={t("which plan")}
            hint={t("only a version this project recorded is offered, and its plan id comes with the choice")}
          >
            <select value={digest} onChange={(event) => setDigest(event.target.value)}>
              <option value="">{t("choose a recorded plan")}</option>
              {rows.map((one) => (
                <option key={one.plan_sha256} value={one.plan_sha256}>
                  {`${one.plan_id} · v${one.plan_version} · ${one.objective}`}
                </option>
              ))}
            </select>
          </Field>
          <ProblemNote error={plans.error} />
          <Loading on={plans.loading} />
          {plans.data && !rows.length ? (
            <Empty>
              {t("no plan is recorded in this project yet, so there is nothing to launch.")}{" "}
              <Link href={`/projects/${encodeURIComponent(projectId)}/plans`}>{t("build one")}</Link>
            </Empty>
          ) : null}
          {digest && !plan ? (
            <Notice>
              {t(
                "the plan chosen is no longer on this list; re-read and pick again rather than launching a version this project does not hold",
              )}
            </Notice>
          ) : null}
          {plan ? (
            <dl className="facts">
              <dt>{t("the plan starts from these slots")}</dt>
              <dd className="mono">{plan.root_inputs.join(", ") || t("none declared")}</dd>
              <dt>{t("steps")}</dt>
              <dd>
                {plan.nodes.length} · <span className="mono">{pinnedTools.join(", ") || "—"}</span>
              </dd>
              <dt>{t("recorded by")}</dt>
              <dd className="mono">
                {plan.created_by}
                {plan.created_at ? ` · ${plan.created_at}` : ""}
              </dd>
            </dl>
          ) : null}
          {plan && plan.root_inputs.length > 1 ? (
            <p className="muted">
              <span className="chip chip--provisional" title={t("§9.2: the workflow fills one root slot")}>
                {t("{n} root slots on one plan that takes one", { n: String(plan.root_inputs.length) })}
              </span>{" "}
              {t(
                "launching this plan is allowed and records a run; executing it is refused until one plan declares one slot",
              )}
            </p>
          ) : null}
        </fieldset>

        <fieldset>
          <legend>{t("the one asset this run is handed")}</legend>
          <p className="muted">
            {t(
              "a launch fills a single root slot, so exactly one registered asset is chosen here; a run handed two of them is a row that cannot execute (§9.2)",
            )}
          </p>
          <ProblemNote error={stored.error} />
          <Loading on={stored.loading} />
          <div className="rows">
            {assets.map((one) => (
              <label className="check" key={one.asset_id}>
                <input
                  type="radio"
                  name="input_asset"
                  checked={assetId === one.asset_id}
                  onChange={() => setAssetId(one.asset_id)}
                />
                <span className="mono">{one.asset_id}</span>
                <span className="muted">
                  {`${one.format} · ${one.size_bytes} ${t("bytes")} · ${t(one.privacy_tier)} · ${t(
                    one.retention_class,
                  )}`}
                </span>
                <span className="muted">
                  {one.created_by_run_id
                    ? `${t("produced by run")} ${one.created_by_run_id}`
                    : t("registered by hand")}
                </span>
                {one.format !== "json" ? (
                  <span
                    className="chip chip--unknown"
                    title={t(
                      "the reference workflow reads only the platform's own canonical JSON product, so an asset recorded in another format is refused when the run executes (§6.4)",
                    )}
                  >
                    {t("not a product format")}
                  </span>
                ) : null}
              </label>
            ))}
          </div>
          {stored.data && !assets.length ? (
            <Empty>
              {t("nothing is registered as data in this project yet.")}{" "}
              <Link href={`/projects/${encodeURIComponent(projectId)}/data`}>{t("register it first")}</Link>
            </Empty>
          ) : null}
        </fieldset>

        <fieldset>
          <legend>{t("what the run is pinned against")}</legend>
          <dl className="facts">
            <dt>tool_registry_snapshot</dt>
            <dd className="mono">{snapshot || t("not read")}</dd>
            <dt>{t("tools offered here")}</dt>
            <dd className="mono">
              {(catalog.data?.tools ?? []).map((one) => `${one.tool_name}@${one.tool_version}`).join(", ") || "—"}
            </dd>
          </dl>
          <ProblemNote error={catalog.error} />
          <Loading on={catalog.loading} />
          <p className="muted">
            {t(
              "read from this project's tools route, which is the same registry the executor hydrates, so the value is neither typed nor remembered: execution refuses a run pinned against a snapshot this deployment does not hold (§4.1). The snapshot counts the registered steps only - what an ad-hoc node may run is pinned by its own grant row (§9.6)",
            )}
          </p>
          <p>
            <button className="secondary" type="button" onClick={() => catalog.reload()}>
              {t("re-read the registry")}
            </button>
          </p>
          <Field label="reproducibility_level_target" hint={t("the claim this run sets out to support")}>
            <select value={form.reproducibility_level_target} onChange={set("reproducibility_level_target")}>
              {LEVELS.map((level) => (
                <option key={level} value={level}>
                  {`${t(level)} (${level})`}
                </option>
              ))}
            </select>
          </Field>
        </fieldset>

        <fieldset>
          <legend>{t("how this request enters the queue")}</legend>
          <div className="grid">
            <Field label="priority" hint={t("0 lowest, 9 highest")}>
              <select value={form.priority} onChange={set("priority")}>
                {PRIORITIES.map((one) => (
                  <option key={one} value={String(one)}>
                    {one}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="Idempotency-Key"
              hint={t("a resend of this click replays the original run; a new intent gets a new key")}
            >
              <input className="mono" value={key} onChange={(event) => setKey(event.target.value)} />
            </Field>
          </div>
        </fieldset>

        {blocked ? <p className="muted">{blocked}</p> : null}
        <button type="submit" disabled={submit.busy || blocked !== null}>
          {submit.busy ? t("launching…") : t("Launch")}
        </button>
      </form>
    </Panel>
  );
}
