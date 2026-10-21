"use client";

import Link from "next/link";
import { useState } from "react";

import { Chip, Empty, Field, Loading, Panel, ProblemNote } from "@/components/ui";
import { approvals as approvalsApi, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useWorkbench } from "@/lib/identity";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync } from "@/lib/useAsync";
import type { Approval, ApprovalState, Page } from "@/lib/types";

const STATES: ApprovalState[] = ["pending", "approved", "rejected", "expired"];

export default function ApprovalsPage({ params }: { params: { projectId: string } }) {
  const t = useT();
  const { caller, projectId, blocked } = useProjectScope(params.projectId);
  const { identity } = useWorkbench();
  const [state, setState] = useState<ApprovalState | "">("pending");
  const [overdue, setOverdue] = useState(false);

  const listed = useAsync<Page<Approval>>(
    () =>
      caller
        ? approvalsApi.list(caller, projectId, { state: state || undefined, overdue: overdue || undefined, limit: 100 })
        : Promise.resolve({ items: [], limit: 0, offset: 0 }),
    [caller?.token, projectId, state, overdue],
  );

  if (!caller) {
    return (
      <Panel title={t("Approvals")} note={t("nothing is read until a token is set")}>
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  return (
    <Panel
      title={t("Approval inbox")}
      note={t(
        "a lapsed window removes permission; it never lets a run advance (§9.2). Only a person who did not ask for the gate can open it (§11.6).",
      )}
      actions={
        <button className="secondary" type="button" onClick={listed.reload}>
          {t("re-read")}
        </button>
      }
    >
      <div className="grid">
        <Field label={t("state")}>
          <select value={state} onChange={(event) => setState(event.target.value as ApprovalState | "")}>
            <option value="">{t("any")}</option>
            {STATES.map((one) => (
              <option key={one} value={one}>
                {`${t(one)} (${one})`}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("overdue only")} hint={t("the overdue view of the same inbox")}>
          <input
            type="checkbox"
            style={{ width: "auto" }}
            checked={overdue}
            onChange={(event) => setOverdue(event.target.checked)}
          />
        </Field>
      </div>
      <ProblemNote error={listed.error} />
      <Loading on={listed.loading} />
      {listed.data && listed.data.items.length ? (
        <div className="rows">
          {listed.data.items.map((approval) => (
            <ApprovalRow
              key={approval.approval_id}
              caller={caller}
              projectId={projectId}
              approval={approval}
              subjectId={identity?.subjectId ?? ""}
              onChanged={listed.reload}
            />
          ))}
        </div>
      ) : (
        !listed.loading && <Empty>{t("No gate is waiting in this inbox.")}</Empty>
      )}
    </Panel>
  );
}

/**
 * One gate, and the three moves a person can make on it.
 *
 * The buttons are shown to everyone and decided by the server: hiding them on a guessed role would
 * make the screen look authoritative while the policy actually lives in the token (§11.2). A refusal
 * is reported with the denial reason the policy produced.
 */
function ApprovalRow({
  caller,
  projectId,
  approval,
  subjectId,
  onChanged,
}: {
  caller: Caller;
  projectId: string;
  approval: Approval;
  subjectId: string;
  onChanged: () => void;
}) {
  const t = useT();
  const [reason, setReason] = useState("");
  const [delegateTo, setDelegateTo] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (exc) {
      setError(exc);
    } finally {
      setBusy(false);
    }
  }

  const decided = approval.state !== "pending";

  return (
    <section className="panel">
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
        <span className="mono">{approval.approval_id}</span>
        <Chip title={approval.kind}>{t(approval.kind)}</Chip>
        <Chip
          title={approval.state}
          status={approval.state === "approved" ? "supported" : approval.state === "pending" ? "provisional" : "rejected"}
        >
          {t(approval.state)}
        </Chip>
        {approval.overdue ? <Chip status="conflict">{t("overdue")}</Chip> : null}
        <Link href={`/projects/${projectId}/runs/${encodeURIComponent(approval.run_id)}`} className="mono">
          {approval.run_id}
        </Link>
      </div>
      <dl className="facts">
        <dt>{t("asked for by")}</dt>
        <dd className="mono">{approval.requested_by}</dd>
        <dt>{t("assigned to")}</dt>
        <dd className="mono">
          {approval.assigned_to}
          {approval.delegated_from ? t(" · delegated from {who}", { who: approval.delegated_from }) : ""}
        </dd>
        <dt>{t("node")}</dt>
        <dd className="mono">{approval.node_id ?? "—"}</dd>
        <dt>{t("decide by")}</dt>
        <dd>
          {approval.sla_at}
          {approval.overdue_reason ? ` · ${approval.overdue_reason}` : ""}
        </dd>
        <dt>{t("decision")}</dt>
        <dd>
          {approval.decided_by ? (
            <span className="mono">
              {approval.decided_by} · {t(approval.state)} · {approval.decision_reason ?? t("no reason recorded")}
            </span>
          ) : (
            "—"
          )}
        </dd>
      </dl>
      <ProblemNote error={error} />
      {!decided ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
          }}
        >
          <Field label={t("reason")} hint={t("required: an approval without a reason is a missing audit row")}>
            <input value={reason} onChange={(event) => setReason(event.target.value)} />
          </Field>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              disabled={busy || !reason.trim() || approval.requested_by === subjectId}
              onClick={() =>
                void act(() => approvalsApi.decide(caller, projectId, approval.approval_id, true, reason))
              }
            >
              {t("approve")}
            </button>
            <button
              className="secondary"
              type="button"
              disabled={busy || !reason.trim()}
              onClick={() =>
                void act(() => approvalsApi.decide(caller, projectId, approval.approval_id, false, reason))
              }
            >
              {t("refuse")}
            </button>
          </div>
          {approval.requested_by === subjectId ? (
            <p className="muted">
              {t(
                "This gate was asked for by the subject you are acting as, so deciding it is refused here and at the API: the requester cannot open their own gate (§11.6). Delegate it instead.",
              )}
            </p>
          ) : null}
          <Field label={t("delegate to")} hint={t("moves the gate and records the move (§9.2)")}>
            <div style={{ display: "flex", gap: 8 }}>
              <input className="mono" value={delegateTo} onChange={(event) => setDelegateTo(event.target.value)} />
              <button
                className="secondary"
                type="button"
                disabled={busy || !delegateTo.trim()}
                onClick={() => void act(() => approvalsApi.delegate(caller, projectId, approval.approval_id, delegateTo))}
              >
                {t("delegate")}
              </button>
            </div>
          </Field>
        </form>
      ) : null}
    </section>
  );
}
