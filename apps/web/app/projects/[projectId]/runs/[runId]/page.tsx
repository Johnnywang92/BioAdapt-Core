"use client";

import Link from "next/link";
import { useState } from "react";

import { Chip, Field, Loading, Notice, Panel, ProblemNote } from "@/components/ui";
import { approvals as approvalsApi, runs as runsApi, type Caller } from "@/lib/api";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync, useSubmit } from "@/lib/useAsync";
import type { Approval, ApprovalKind, RunSummary } from "@/lib/types";

const KINDS: ApprovalKind[] = ["plan", "node", "adhoc_tool", "escalation"];

export default function RunPage({ params }: { params: { projectId: string; runId: string } }) {
  const { caller, projectId, blocked } = useProjectScope(params.projectId);
  const runId = decodeURIComponent(params.runId);

  if (!caller) {
    return (
      <Panel title="Run" note="nothing is read until a token is set">
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  return (
    <>
      <RunSummaryPanel caller={caller} projectId={projectId} runId={runId} />
      <Gate caller={caller} projectId={projectId} runId={runId} />
    </>
  );
}

function RunSummaryPanel({ caller, projectId, runId }: { caller: Caller; projectId: string; runId: string }) {
  const read = useAsync<RunSummary>(() => runsApi.read(caller, projectId, runId), [caller.token, projectId, runId]);
  const budget = useAsync(
    () => runsApi.budget(caller, projectId, runId),
    [caller.token, projectId, runId],
  );
  const cancel = useSubmit();
  const [reason, setReason] = useState("");

  return (
    <Panel
      title={`Run ${runId}`}
      note="a run is a claim about inputs, a plan digest and a registry snapshot; the cards it produced are read through the queue, never from this row"
      actions={
        <div style={{ display: "flex", gap: 8 }}>
          <button className="secondary" type="button" onClick={() => { read.reload(); budget.reload(); }}>
            re-read
          </button>
          <Link href={`/projects/${projectId}/queue?run_id=${encodeURIComponent(runId)}`}>expert queue</Link>
        </div>
      }
    >
      <ProblemNote error={read.error} />
      <Loading on={read.loading} />
      {read.data ? (
        <>
          <dl className="facts">
            <dt>state</dt>
            <dd>
              <Chip status={read.data.state === "FAILED" ? "rejected" : "provisional"}>{read.data.state}</Chip>
            </dd>
            <dt>queue position</dt>
            <dd>{read.data.queue_position === null ? "not queued" : read.data.queue_position}</dd>
            <dt>plan</dt>
            <dd className="mono">
              {read.data.plan_id} @ {read.data.plan_version}
            </dd>
            <dt>reproducibility target</dt>
            <dd>{read.data.reproducibility_level_target}</dd>
            <dt>registry snapshot</dt>
            <dd className="mono">{read.data.tool_registry_snapshot}</dd>
            <dt>inputs</dt>
            <dd>
              {Object.keys(read.data.input_hashes).length} assets, each pinned by digest —{" "}
              <span className="mono">{Object.keys(read.data.input_hashes).join(", ")}</span>
            </dd>
            <dt>tools</dt>
            <dd className="mono">
              {Object.entries(read.data.tool_versions)
                .map(([tool, version]) => `${tool}@${version}`)
                .join(", ") || "none declared"}
            </dd>
          </dl>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void cancel.run(() => runsApi.cancel(caller, projectId, runId, reason)).then(() => read.reload());
            }}
          >
            <Field label="end this run" hint="ending a run is a person's act; a service token is refused here (§9.1)">
              <div style={{ display: "flex", gap: 8 }}>
                <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="reason" />
                <button className="secondary" type="submit" disabled={cancel.busy || !reason.trim()}>
                  cancel run
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
              <th>loop budget</th>
              <th>used</th>
              <th>ceiling</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>iterations</td>
              <td>{budget.data.usage.iterations}</td>
              <td>{budget.data.ceiling.max_iterations}</td>
            </tr>
            <tr>
              <td>compute minutes</td>
              <td>{budget.data.usage.compute_minutes}</td>
              <td>{budget.data.ceiling.max_compute_minutes}</td>
            </tr>
            <tr>
              <td>model cost usd</td>
              <td>{budget.data.usage.model_cost_usd}</td>
              <td>{budget.data.ceiling.max_model_cost_usd}</td>
            </tr>
          </tbody>
        </table>
      ) : null}
      {budget.data && budget.data.exhausted.length > 0 ? (
        <div className="notice">
          The loop budget is exhausted: another targeted analysis is not permitted until a person raises the
          ceiling on the plan, which means a new plan version (§9.2). The rule says so in the server&apos;s words:
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
 * Requesting a gate (sections 9.2, 9.6, 11.6).
 *
 * `plan` and `escalation` are about the run as a whole and must not name a node; `node` and
 * `adhoc_tool` must. The API enforces that pairing, so the form only asks and reports the answer.
 */
function Gate({ caller, projectId, runId }: { caller: Caller; projectId: string; runId: string }) {
  const submit = useSubmit();
  const [kind, setKind] = useState<ApprovalKind>("plan");
  const [nodeId, setNodeId] = useState("");
  const [assignedTo, setAssignedTo] = useState("");
  const [planVersion, setPlanVersion] = useState("");

  const needsNode = kind === "node" || kind === "adhoc_tool";

  return (
    <Panel
      title="Request a gate"
      note="requesting is not deciding: the decision belongs to someone else, and a service account can neither decide nor delegate (§11.4)."
    >
      <ProblemNote error={submit.error} />
      {submit.result ? (
        <Notice>
          Gate <span className="mono">{(submit.result as Approval).approval_id}</span> is{" "}
          {(submit.result as Approval).state}, assigned to{" "}
          <span className="mono">{(submit.result as Approval).assigned_to ?? "the project owner"}</span>.
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
          <Field label="kind">
            <select value={kind} onChange={(event) => setKind(event.target.value as ApprovalKind)}>
              {KINDS.map((one) => (
                <option key={one} value={one}>
                  {one}
                </option>
              ))}
            </select>
          </Field>
          <Field label={needsNode ? "node_id" : "node_id (not used for this kind)"}>
            <input className="mono" value={nodeId} disabled={!needsNode} onChange={(event) => setNodeId(event.target.value)} />
          </Field>
        </div>
        <div className="grid">
          <Field label="assigned_to" hint="who is accountable; leaving it open routes the gate to the project owner">
            <input className="mono" value={assignedTo} onChange={(event) => setAssignedTo(event.target.value)} />
          </Field>
          <Field label="plan_version" hint={kind === "plan" ? "the version being approved" : "only used by a plan gate"}>
            <input className="mono" value={planVersion} disabled={kind !== "plan"} onChange={(event) => setPlanVersion(event.target.value)} />
          </Field>
        </div>
        <button type="submit" disabled={submit.busy || (needsNode && !nodeId.trim())}>
          {submit.busy ? "requesting…" : "Request approval"}
        </button>
      </form>
    </Panel>
  );
}
