"use client";

import { useState } from "react";

import { Field, Loading, Notice, ProblemNote } from "@/components/ui";
import { approvals as approvalsApi, plans as plansApi, runs as runsApi, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useAsync, useSubmit } from "@/lib/useAsync";
import type { Approval, PlanNode, PlanSummary, RunSummary } from "@/lib/types";

/** §9.7: read at the hard cap. The node this grant covers can sit in any recorded plan version; a plan the
 * screen never fetched is a node it cannot name, and a node it cannot name is a form it should not show. */
const PLAN_LIMIT = 500;

interface NodeContext {
  run: RunSummary | null;
  plan: PlanSummary | null;
  node: PlanNode | null;
}

function text(row: unknown, name: string): string {
  const record = row as Record<string, unknown> | null;
  const value = record?.[name];
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (typeof value === "object") {
    return (
      Object.entries(value as Record<string, unknown>)
        .filter(([, one]) => one !== null && one !== undefined && one !== "")
        .map(([key, one]) => `${key}=${Array.isArray(one) ? one.join("|") : String(one)}`)
        .join(" · ") || "—"
    );
  }
  return String(value);
}

/**
 * The execution grant, issued by the person who decided the gate (§9.6, 第四批③).
 *
 * There is nothing to fill in here but the last two lines. The submission's bytes live on the plan node
 * the gate names, and the plan is immutable, so what a person approved at the gate and what the grant
 * executes are the same bytes by construction rather than by a comparison somebody can forget to make.
 * This screen is therefore a review: it reads those bytes back and shows them, so the person issuing the
 * grant is looking at the submission rather than at a name.
 */
export function AdhocGrantForm({
  caller,
  projectId,
  approval,
  subjectId,
  onIssued,
}: {
  caller: Caller;
  projectId: string;
  approval: Approval;
  subjectId: string;
  onIssued: () => void;
}) {
  const t = useT();
  const [reason, setReason] = useState("");
  const [validForMinutes, setValidForMinutes] = useState("");
  const submit = useSubmit();

  const context = useAsync<NodeContext>(async () => {
    const run = await runsApi.read(caller, projectId, approval.run_id);
    const listed = await plansApi.list(caller, projectId, { limit: PLAN_LIMIT });
    const plan = listed.items.find((one) => one.plan_id === run.plan_id && one.plan_version === run.plan_version) ?? null;
    const node = plan?.nodes.find((one) => one.node_id === approval.node_id) ?? null;
    return { run, plan, node };
  }, [caller.token, projectId, approval.run_id, approval.node_id]);

  const node = context.data?.node ?? null;
  const nodeId = approval.node_id ?? "";
  const wrongClass = !!node && node.tool_class !== "project_adhoc";
  const submission = node?.adhoc_spec ?? null;
  const ready = !!node && !wrongClass && !!submission;

  function send() {
    if (!node || !nodeId) return;
    void submit
      .run(() =>
        approvalsApi.grantAdhoc(caller, projectId, approval.run_id, nodeId, {
          approval_id: approval.approval_id,
          reason: reason.trim() || undefined,
          valid_for_minutes: validForMinutes.trim() ? Number(validForMinutes) : undefined,
        }),
      )
      .then((done) => {
        if (done) onIssued();
      });
  }

  return (
    <fieldset>
      <legend>{t("Issue the execution grant")}</legend>
      <p className="muted">
        {t(
          "the gate says a person allowed this submission; the grant is what turns that decision into one execution. The bytes below are the ones the plan pins and the ones the engine will run, so there is nothing to type here - only to read (§9.6)",
        )}
      </p>
      <ProblemNote error={context.error} />
      <Loading on={context.loading} />

      {!nodeId ? (
        <Notice>
          {t("this gate does not name a node, so there is nothing here a grant could cover; the route that raised it should have carried the node id")}
        </Notice>
      ) : context.data && !context.data.plan ? (
        <Notice>
          {t(
            "the plan version this run pins is no longer listed for the project, so the node it names cannot be read here; a grant is issued against the plan the run actually pins",
          )}
        </Notice>
      ) : context.data?.plan && !node ? (
        <Notice>
          {t("this plan version has no node with that id, so the gate points at nothing the run can execute")}
        </Notice>
      ) : wrongClass ? (
        <Notice>
          {t(
            "this node is planned as a registered tool, and a registered tool needs no grant; only the plan's ad-hoc nodes can be made executable here",
          )}
        </Notice>
      ) : node && !submission ? (
        <Notice>
          {t(
            "this node carries no submission, so there are no bytes to grant: the plan has to state the ad-hoc tool it runs before a grant can execute it",
          )}
        </Notice>
      ) : null}

      {node && submission ? (
        <>
          <dl className="facts">
            <dt>{t("the node this grant covers")}</dt>
            <dd className="mono">{nodeId}</dd>
            <dt>{t("planned as")}</dt>
            <dd className="mono">
              {node.tool_name}@{node.tool_version}
            </dd>
            <dt>{t("read from")}</dt>
            <dd className="mono">
              {`${context.data?.run?.plan_id ?? ""}@${context.data?.run?.plan_version ?? ""}`}
            </dd>
            <dt>{t("asked for by")}</dt>
            <dd className="mono">{approval.requested_by}</dd>
            <dt>{t("decided by")}</dt>
            <dd className="mono">{approval.decided_by ?? "—"}</dd>
          </dl>
          {approval.decided_by && approval.decided_by !== subjectId ? (
            <p className="muted">
              {t(
                "this grant is the execution of one named decision, so the route issues it only for {who}, the subject that decided the gate - not for anyone who merely agrees with it (§11.6)",
                { who: approval.decided_by },
              )}
            </p>
          ) : null}

          <fieldset>
            <legend>{t("the submission this grant executes")}</legend>
            <dl className="facts">
              <dt>tool_name@tool_version</dt>
              <dd className="mono">
                {`${text(submission, "tool_name")}@${text(submission, "tool_version")}`}
              </dd>
              <dt>task</dt>
              <dd className="mono">{text(submission, "task")}</dd>
              <dt>container_image</dt>
              <dd className="mono">
                {`${text(submission, "container_image")}`}
              </dd>
              <dt>resources</dt>
              <dd className="mono">{text(submission, "resources")}</dd>
              <dt>license</dt>
              <dd className="mono">{text(submission, "license")}</dd>
              <dt>quality_checks</dt>
              <dd className="mono">{text(submission, "quality_checks")}</dd>
              <dt>assumptions</dt>
              <dd className="mono">{text(submission, "assumptions")}</dd>
              <dt>invalid_when</dt>
              <dd className="mono">{text(submission, "invalid_when")}</dd>
              <dt>reference_compatibility</dt>
              <dd className="mono">{text(submission, "reference_compatibility")}</dd>
              <dt>failure_policy</dt>
              <dd className="mono">{text(submission, "failure_policy")}</dd>
              <dt>retry_limit</dt>
              <dd className="mono">{text(submission, "retry_limit")}</dd>
              <dt>network</dt>
              <dd className="mono">{text(submission, "network")}</dd>
              <dt>provenance_fields</dt>
              <dd className="mono">{text(submission, "provenance_fields")}</dd>
            </dl>
            <details>
              <summary>{t("the bytes as they stand on the record")}</summary>
              <textarea rows={16} spellCheck={false} className="mono" readOnly value={JSON.stringify(submission, null, 2)} />
            </details>
          </fieldset>

          <form
            onSubmit={(event) => {
              event.preventDefault();
              send();
            }}
          >
            <fieldset>
              <legend>{t("the two lines that are still yours")}</legend>
              <div className="grid">
                <Field label="reason" hint={t("optional; left open, the row records the gate's own decision reason")}>
                  <input value={reason} onChange={(event) => setReason(event.target.value)} />
                </Field>
                <Field
                  label="valid_for_minutes"
                  hint={t("optional; the deployment's own window is the ceiling and asking for less is allowed")}
                >
                  <input type="number" min={1} value={validForMinutes} onChange={(event) => setValidForMinutes(event.target.value)} />
                </Field>
              </div>
              <p className="muted">
                {t(
                  "what this buys, in the deployment's own terms: one execution, no network, no GPU, no egress, no retry, project-scoped assets, and the grant is spent by the one execution it authorises (§9.6)",
                )}
              </p>
              <ProblemNote error={submit.error} />
              {submit.result ? (
                <Notice>
                  {t(
                    "Grant {id} is on the record: {tool}@{version} may execute at node {node}, live until {until}.",
                    {
                      id: textOf(submit.result, "grant_id"),
                      tool: node.tool_name,
                      version: node.tool_version,
                      node: nodeId,
                      until: textOf(submit.result, "expires_at"),
                    },
                  )}
                  {t(
                    " one gate buys one grant, so asking again answers with the row that already stands rather than a second permission (§9.6)",
                  )}
                </Notice>
              ) : null}
              <button type="submit" disabled={submit.busy || !ready}>
                {submit.busy ? t("issuing…") : t("Issue the execution grant")}
              </button>
            </fieldset>
          </form>
        </>
      ) : null}
    </fieldset>
  );
}

function textOf(result: unknown, name: string): string {
  const row = result as Record<string, unknown> | null;
  const value = row?.[name];
  return typeof value === "string" ? value : String(value ?? "—");
}
