"use client";

import Link from "next/link";
import { useState } from "react";

import { Chip, Loading, Notice, Panel, ProblemNote } from "@/components/ui";
import { approvals as approvalsApi, plans as plansApi, runs as runsApi, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useAsync } from "@/lib/useAsync";
import type { Approval, ApprovalKind, PlanNode, PlanSummary, RunSummary } from "@/lib/types";

/** §9.7: a plan the screen never fetched is a node it cannot name. */
const PLAN_LIMIT = 500;
/** The gates of one run; a run with more than this many was not opened by hand. */
const GATE_LIMIT = 200;

/** The two gates an ad-hoc node waits on (§9.6), in the order they are read. */
const GATES: ApprovalKind[] = ["node", "adhoc_tool"];

interface Context {
  run: RunSummary;
  plan: PlanSummary | null;
  gates: Approval[];
}

/**
 * The gates an ad-hoc node waits on, and the one click that opens what is missing (§9.6).
 *
 * An ad-hoc node is gated twice: the node's own approval says a person allows the step to run, and
 * the ad-hoc gate is the one the execution grant is issued over. They are separate records with
 * separate digests, and the run needs both before that node can execute - so the screen opens them
 * together rather than leaving a person to work out which one is missing.
 *
 * The panel exists only when the plan this run pins really holds an ad-hoc node. A run of registered
 * tools has nothing to say here, and a screen that showed an empty gate board would be offering a
 * control that does nothing.
 */
export function AdhocRunGates({ caller, projectId, runId }: { caller: Caller; projectId: string; runId: string }) {
  const t = useT();
  const context = useAsync<Context>(async () => {
    const run = await runsApi.read(caller, projectId, runId);
    const listed = await plansApi.list(caller, projectId, { limit: PLAN_LIMIT });
    const plan =
      listed.items.find((one) => one.plan_id === run.plan_id && one.plan_version === run.plan_version) ?? null;
    const gates = await approvalsApi.list(caller, projectId, { run_id: runId, limit: GATE_LIMIT });
    return { run, plan, gates: gates.items };
  }, [caller.token, projectId, runId]);

  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const plan = context.data?.plan ?? null;
  const adHocNodes = (plan?.nodes ?? []).filter((node) => node.tool_class === "project_adhoc");

  async function openGates(node: PlanNode) {
    setBusy(node.node_id);
    setError(null);
    try {
      for (const kind of GATES) {
        if (context.data?.gates.some((one) => one.kind === kind && one.node_id === node.node_id)) continue;
        await approvalsApi.request(caller, projectId, runId, { kind, node_id: node.node_id });
      }
      context.reload();
    } catch (exc) {
      setError(exc);
    } finally {
      setBusy(null);
    }
  }

  if (!context.data) return null;
  if (!plan) {
    return (
      <Panel title={t("The ad-hoc steps of this run")}>
        <Notice>
          {t(
            "the plan version this run pins is not listed for the project, so the nodes it names cannot be read here",
          )}
        </Notice>
      </Panel>
    );
  }
  if (!adHocNodes.length) return null;

  return (
    <Panel
      title={t("The ad-hoc steps of this run")}
      note={t(
        "an ad-hoc step waits on two gates: the node's own, and the one the execution grant is issued over. Requesting them is not deciding them - each one belongs to whoever did not ask for it (§11.6)",
      )}
    >
      <ProblemNote error={context.error ?? error} />
      <div className="rows">
        {adHocNodes.map((node) => {
          const gates = context.data?.gates.filter((one) => one.node_id === node.node_id) ?? [];
          const missing = GATES.filter((kind) => !gates.some((one) => one.kind === kind));
          const granted = gates.filter((one) => one.kind === "adhoc_tool" && one.state === "approved").length;
          return (
            <section className="panel" key={node.node_id}>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
                <span className="mono">{node.node_id}</span>
                <span className="mono">
                  {node.tool_name}@{node.tool_version}
                </span>
              </div>
              <dl className="facts">
                {GATES.map((kind) => {
                  const gate = gates.find((one) => one.kind === kind);
                  return (
                    <div key={kind} style={{ display: "contents" }}>
                      <dt>{t(kind)}</dt>
                      <dd>
                        {gate ? (
                          <>
                            <Chip
                              title={gate.state}
                              status={
                                gate.state === "approved"
                                  ? "supported"
                                  : gate.state === "pending"
                                    ? "provisional"
                                    : "rejected"
                              }
                            >
                              {t(gate.state)}
                            </Chip>{" "}
                            <span className="mono">{gate.approval_id}</span>
                          </>
                        ) : (
                          t("not opened")
                        )}
                      </dd>
                    </div>
                  );
                })}
              </dl>
              {missing.length ? (
                <button type="button" disabled={busy === node.node_id} onClick={() => void openGates(node)}>
                  {busy === node.node_id
                    ? t("opening…")
                    : t("open the two gates this step waits on")}
                </button>
              ) : null}
              {missing.length ? null : granted ? (
                <p className="muted">
                  {t(
                    "both gates are open; the execution grant is issued from the approval inbox, by the person who decided the ad-hoc gate (§9.6)",
                  )}{" "}
                  <Link href={`/projects/${projectId}/approvals?run_id=${encodeURIComponent(runId)}`}>
                    {t("open the approval inbox")}
                  </Link>
                </p>
              ) : (
                <p className="muted">
                  {t("both gates are open, and each one still waits for a person who did not ask for it (§11.6)")}
                </p>
              )}
            </section>
          );
        })}
      </div>
      <Loading on={context.loading} />
    </Panel>
  );
}
