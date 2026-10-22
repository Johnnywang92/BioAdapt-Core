"use client";

import Link from "next/link";
import { useState } from "react";

import { Chip, Empty, Field, Loading, Notice, Panel, ProblemNote } from "@/components/ui";
import { DeploymentNotice } from "@/components/DeploymentStatus";
import { approvals as approvalsApi, runs as runsApi, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync, useAutoRefresh, useSubmit } from "@/lib/useAsync";
import type {
  Approval,
  ApprovalKind,
  Page,
  RunExecution,
  RunJournalEvent,
  RunSummary,
} from "@/lib/types";

const KINDS: ApprovalKind[] = ["plan", "node", "adhoc_tool", "escalation"];

/** The four kinds one journal line can be (§9.2); the API validates the value, so this only offers it. */
const EVENT_KINDS = ["state", "node_retry", "business_retry", "usage"];

export default function RunPage({ params }: { params: { projectId: string; runId: string } }) {
  const t = useT();
  const { caller, projectId, blocked } = useProjectScope(params.projectId);
  const runId = decodeURIComponent(params.runId);
  // One switch, shared by the two reads that answer "where is it now, and why there": a person watching
  // a run does not want to start and stop two pollers.
  const [live, setLive] = useState(false);

  if (!caller) {
    return (
      <Panel title={t("Run")} note={t("nothing is read until a token is set")}>
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  return (
    <>
      <RunSummaryPanel caller={caller} projectId={projectId} runId={runId} live={live} setLive={setLive} />
      <Journal caller={caller} projectId={projectId} runId={runId} live={live} />
      <Gate caller={caller} projectId={projectId} runId={runId} />
    </>
  );
}

/**
 * The run's own audit trail (§9.2): who moved it, from which state to which, and the sentence they gave.
 *
 * Nothing here is composed by the screen. A tracker that inferred "it is probably clustering" from a
 * timestamp would be the one place a run could be wrong about itself, so the panel shows the journal
 * rows and the reason field in the server's own words.
 */
function Journal({
  caller,
  projectId,
  runId,
  live,
}: {
  caller: Caller;
  projectId: string;
  runId: string;
  live: boolean;
}) {
  const t = useT();
  const [kind, setKind] = useState("");
  const read = useAsync<Page<RunJournalEvent>>(
    () => runsApi.journal(caller, projectId, runId, { ...(kind ? { kind } : {}), limit: 100 }),
    [caller.token, projectId, runId, kind],
  );
  useAutoRefresh(read.reload, 5000, live);

  return (
    <Panel
      title={t("Journal")}
      note={t(
        "every state change is written here with the actor who moved it and the reason they gave; a retry and a spend line are the same trail (§9.2)",
      )}
      actions={
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select value={kind} onChange={(event) => setKind(event.target.value)} aria-label={t("kind")}>
            <option value="">{t("every kind")}</option>
            {EVENT_KINDS.map((one) => (
              <option key={one} value={one}>
                {`${t(one)} (${one})`}
              </option>
            ))}
          </select>
          <button className="secondary" type="button" disabled={read.loading} onClick={read.reload}>
            {t("re-read")}
          </button>
        </div>
      }
    >
      <ProblemNote error={read.error} />
      <Loading on={read.loading} />
      {read.data && read.data.items.length ? (
        <table>
          <thead>
            <tr>
              <th>{t("when")}</th>
              <th>{t("kind")}</th>
              <th>{t("from")}</th>
              <th>{t("to")}</th>
              <th>{t("who")}</th>
              <th>{t("why it moved")}</th>
            </tr>
          </thead>
          <tbody>
            {read.data.items.map((event) => (
              <tr key={event.event_id}>
                <td className="mono">{event.at ?? "—"}</td>
                <td>
                  <Chip title={event.kind}>{t(event.kind)}</Chip>
                </td>
                <td>{t(event.from_state)}</td>
                <td>
                  <Chip title={event.to_state} status={event.to_state === "FAILED" ? "rejected" : undefined}>
                    {t(event.to_state)}
                  </Chip>
                </td>
                <td>
                  <span className="mono">{event.actor_id}</span>
                  <div className="muted mono">
                    {t(event.actor_kind)}
                    {event.node_id ? ` · ${event.node_id}` : ""}
                    {event.attempt > 1 ? ` · ${t("attempt")} ${event.attempt}` : ""}
                    {event.loop_iteration > 0 ? ` · ${t("iteration")} ${event.loop_iteration}` : ""}
                  </div>
                </td>
                <td>
                  {event.reason}
                  {event.compute_minutes > 0 || event.model_cost_usd > 0 ? (
                    <div className="muted">
                      {t("{compute} compute minutes · {cost} model cost", {
                        compute: event.compute_minutes,
                        cost: event.model_cost_usd,
                      })}
                    </div>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Empty>{t("No journal line has been written for this run, so nothing has moved it yet.")}</Empty>
      )}
    </Panel>
  );
}

function RunSummaryPanel({
  caller,
  projectId,
  runId,
  live,
  setLive,
}: {
  caller: Caller;
  projectId: string;
  runId: string;
  live: boolean;
  setLive: (value: boolean) => void;
}) {
  const t = useT();
  const read = useAsync<RunSummary>(() => runsApi.read(caller, projectId, runId), [caller.token, projectId, runId]);
  const budget = useAsync(
    () => runsApi.budget(caller, projectId, runId),
    [caller.token, projectId, runId],
  );
  useAutoRefresh(read.reload, 5000, live);
  useAutoRefresh(budget.reload, 5000, live);
  const cancel = useSubmit();
  const [reason, setReason] = useState("");
  const [executing, setExecuting] = useState(false);
  const [execution, setExecution] = useState<RunExecution | null>(null);
  const [executionError, setExecutionError] = useState<unknown>(null);

  const handleExecutePipeline = async () => {
    setExecuting(true);
    setExecution(null);
    setExecutionError(null);
    try {
      setExecution(await runsApi.execute(caller, projectId, runId));
      read.reload();
      budget.reload();
    } catch (err: unknown) {
      setExecutionError(err);
    } finally {
      setExecuting(false);
    }
  };

  return (
    <Panel
      title={`${t("Run")} ${runId}`}
      note={t(
        "a run is a claim about inputs, a plan digest and a registry snapshot; the cards it produced are read through the queue, never from this row",
      )}
      actions={
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            className={live ? undefined : "secondary"}
            aria-pressed={live}
            onClick={() => setLive(!live)}
            title={t("watching re-reads this run and its journal every five seconds; it never signs anything off")}
          >
            {live ? t("watching") : t("watch this run")}
          </button>
          <button className="secondary" type="button" onClick={() => { read.reload(); budget.reload(); }}>
            {t("re-read")}
          </button>
          <Link href={`/projects/${projectId}/queue?run_id=${encodeURIComponent(runId)}`}>{t("expert queue")}</Link>
        </div>
      }
    >
      <DeploymentNotice capability="run_execution" />

      {read.data && read.data.state !== "FAILED" && read.data.state !== "ANALYSIS_PASSED" && (
        <div
          style={{
            marginTop: 12,
            marginBottom: 16,
            padding: "12px",
            background: "rgba(37, 99, 235, 0.08)",
            border: "1px solid #bfdbfe",
            borderRadius: 6,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <strong style={{ color: "#1e40af" }}>{t("Run the analysis now")}</strong>
              <p style={{ margin: "2px 0 0", fontSize: "0.85rem", color: "var(--muted)" }}>
                {t(
                  "Execute DAG nodes (qc → normalize → pca → cluster → markers → pseudobulk_de → robustness), discover C1-C3 candidates, and draft evidence cards.",
                )}
              </p>
            </div>
            <button
              type="button"
              disabled={executing}
              onClick={handleExecutePipeline}
              style={{
                background: "#2563eb",
                color: "#fff",
                border: "none",
                padding: "8px 16px",
                borderRadius: 4,
                cursor: "pointer",
                fontWeight: 600,
                fontSize: "0.9rem",
              }}
            >
              {executing ? t("executing pipeline...") : t("Run the analysis now")}
            </button>
          </div>
          {executionError !== null && (
            <>
              <Notice>
                {t(
                  "Nothing ran and nothing moved: the run keeps the state it had. The sentence below is the server's own.",
                )}
              </Notice>
              <ProblemNote error={executionError} />
            </>
          )}
          {execution && <ExecutionResult projectId={projectId} execution={execution} />}
        </div>
      )}

      <ProblemNote error={read.error} />
      <Loading on={read.loading} />
      {read.data ? (
        <>
          <dl className="facts">
            <dt>{t("state")}</dt>
            <dd>
              <Chip title={read.data.state} status={read.data.state === "FAILED" ? "rejected" : "provisional"}>
                {t(read.data.state)}
              </Chip>
            </dd>
            <dt>{t("queue position")}</dt>
            <dd>{read.data.queue_position === null ? t("not queued") : read.data.queue_position}</dd>
            <dt>{t("plan")}</dt>
            <dd className="mono">
              {read.data.plan_id} @ {read.data.plan_version}
            </dd>
            <dt>{t("reproducibility target")}</dt>
            <dd>{t(read.data.reproducibility_level_target)}</dd>
            <dt>{t("registry snapshot")}</dt>
            <dd className="mono">{read.data.tool_registry_snapshot}</dd>
            <dt>{t("inputs")}</dt>
            <dd>
              {t("{n} assets, each pinned by digest", { n: Object.keys(read.data.input_hashes).length })} —{" "}
              <span className="mono">{Object.keys(read.data.input_hashes).join(", ")}</span>
            </dd>
            <dt>{t("tools")}</dt>
            <dd className="mono">
              {Object.entries(read.data.tool_versions)
                .map(([tool, version]) => `${tool}@${version}`)
                .join(", ") || t("none declared")}
            </dd>
          </dl>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void cancel.run(() => runsApi.cancel(caller, projectId, runId, reason)).then(() => read.reload());
            }}
          >
            <Field label={t("end this run")} hint={t("ending a run is a person's act; a service token is refused here (§9.1)")}>
              <div style={{ display: "flex", gap: 8 }}>
                <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder={t("reason")} />
                <button className="secondary" type="submit" disabled={cancel.busy || !reason.trim()}>
                  {t("cancel run")}
                </button>
              </div>
            </Field>
            <ProblemNote error={cancel.error} />
          </form>
        </>
      ) : null}
      <ProblemNote error={budget.error} />
      {budget.data ? (
        <table>
          <thead>
            <tr>
              <th>{t("loop budget")}</th>
              <th>{t("used")}</th>
              <th>{t("ceiling")}</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{t("iterations")}</td>
              <td>{budget.data.usage.iterations}</td>
              <td>{budget.data.ceiling.max_iterations}</td>
            </tr>
            <tr>
              <td>{t("compute minutes")}</td>
              <td>{budget.data.usage.compute_minutes}</td>
              <td>{budget.data.ceiling.max_compute_minutes}</td>
            </tr>
            <tr>
              <td>{t("model cost usd")}</td>
              <td>{budget.data.usage.model_cost_usd}</td>
              <td>{budget.data.ceiling.max_model_cost_usd}</td>
            </tr>
          </tbody>
        </table>
      ) : null}
      {budget.data && budget.data.exhausted.length > 0 ? (
        <div className="notice">
          {t(
            "The loop budget is exhausted: another targeted analysis is not permitted until a person raises the ceiling on the plan, which means a new plan version (§9.2). The rule says so in the server's words:",
          )}
          <ul>
            {budget.data.exhausted.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </Panel>
  );
}

/**
 * What one execute call actually achieved (§9.1). The response carries where the run stopped and
 * whether the next move belongs to a person, so this prints that - a run that stopped early is not
 * reported as a run that finished, and the driver's sentence is shown in its own words.
 */
function ExecutionResult({ projectId, execution }: { projectId: string; execution: RunExecution }) {
  const t = useT();
  const runId = execution.run_id;
  return (
    <div className="notice" style={{ marginTop: 10 }}>
      {execution.completed ? (
        <p>{t("Every node in the plan ran, and its outputs are recorded.")}</p>
      ) : (
        <p>
          {execution.stopped_at
            ? t("{node} did not complete, so this run is not finished.", { node: execution.stopped_at })
            : t("This run did not complete.")}
        </p>
      )}
      <ul>
        <li>{t("{n} nodes executed", { n: execution.nodes_executed.length })}</li>
        <li>{t("{n} outputs recorded", { n: execution.assets_produced.length })}</li>
        <li>{t("{n} candidate populations found", { n: execution.candidate_count })}</li>
        <li>{t("{n} evidence cards drafted", { n: execution.draft_count })}</li>
      </ul>
      {execution.reason ? (
        <p>
          {t("The run stopped with the server's own sentence:")} <span className="mono">{execution.reason}</span>
        </p>
      ) : null}
      {execution.gated_nodes.length > 0 ? (
        <p>
          {t("These nodes wait for a person's decision before they can run:")}{" "}
          <span className="mono">{execution.gated_nodes.join(", ")}</span>{" "}
          <Link href={`/projects/${projectId}/approvals?run_id=${encodeURIComponent(runId)}`}>
            {t("open the approval inbox")}
          </Link>
        </p>
      ) : null}
      {execution.warnings.length > 0 ? (
        <ul>
          {execution.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
      {execution.draft_count > 0 ? (
        <Link href={`/projects/${projectId}/queue?run_id=${encodeURIComponent(runId)}`}>
          {t("Review the drafts in the expert queue")}
        </Link>
      ) : null}
      {execution.candidate_count > 0 ? (
        <p>
          <Link href={`/projects/${projectId}/runs/${encodeURIComponent(runId)}/candidates`}>
            {t("Search the populations this run produced")}
          </Link>{" "}
          <span className="muted">
            {t("— {n} populations, each one only a name inside this run (§5.6)", { n: execution.candidate_count })}
          </span>
        </p>
      ) : null}
    </div>
  );
}

/**
 * Requesting a gate (sections 9.2, 9.6, 11.6).
 *
 * `plan` and `escalation` are about the run as a whole and must not name a node; `node` and
 * `adhoc_tool` must. The API enforces that pairing, so the form only asks and reports the answer.
 */
function Gate({ caller, projectId, runId }: { caller: Caller; projectId: string; runId: string }) {
  const t = useT();
  const submit = useSubmit();
  const [kind, setKind] = useState<ApprovalKind>("plan");
  const [nodeId, setNodeId] = useState("");
  const [assignedTo, setAssignedTo] = useState("");
  const [planVersion, setPlanVersion] = useState("");

  const needsNode = kind === "node" || kind === "adhoc_tool";

  return (
    <Panel
      title={t("Request a gate")}
      note={t(
        "requesting is not deciding: the decision belongs to someone else, and a service account can neither decide nor delegate (§11.4).",
      )}
    >
      <ProblemNote error={submit.error} />
      {submit.result ? (
        <Notice>
          {t("Gate")} <span className="mono">{(submit.result as Approval).approval_id}</span> {t("is")}{" "}
          {t((submit.result as Approval).state)}
          {t(", assigned to")}{" "}
          <span className="mono">{(submit.result as Approval).assigned_to ?? t("the project owner")}</span>.
        </Notice>
      ) : null}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit.run(() =>
            approvalsApi.request(caller, projectId, runId, {
              kind,
              node_id: needsNode ? nodeId : undefined,
              assigned_to: assignedTo || undefined,
              plan_version: kind === "plan" ? planVersion || undefined : undefined,
            }),
          );
        }}
      >
        <div className="grid">
          <Field label={t("kind")}>
            <select value={kind} onChange={(event) => setKind(event.target.value as ApprovalKind)}>
              {KINDS.map((one) => (
                <option key={one} value={one}>
                  {`${t(one)} (${one})`}
                </option>
              ))}
            </select>
          </Field>
          <Field label={needsNode ? "node_id" : t("node_id (not used for this kind)")}>
            <input className="mono" value={nodeId} disabled={!needsNode} onChange={(event) => setNodeId(event.target.value)} />
          </Field>
        </div>
        <div className="grid">
          <Field label="assigned_to" hint={t("who is accountable; leaving it open routes the gate to the project owner")}>
            <input className="mono" value={assignedTo} onChange={(event) => setAssignedTo(event.target.value)} />
          </Field>
          <Field
            label="plan_version"
            hint={kind === "plan" ? t("the version being approved") : t("only used by a plan gate")}
          >
            <input className="mono" value={planVersion} disabled={kind !== "plan"} onChange={(event) => setPlanVersion(event.target.value)} />
          </Field>
        </div>
        <button type="submit" disabled={submit.busy || (needsNode && !nodeId.trim())}>
          {submit.busy ? t("requesting…") : t("Request approval")}
        </button>
      </form>
    </Panel>
  );
}
