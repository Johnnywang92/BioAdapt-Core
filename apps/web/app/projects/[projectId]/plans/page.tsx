"use client";

import { useState } from "react";

import { Field, Notice, Panel, ProblemNote } from "@/components/ui";
import { plans as plansApi, type Caller } from "@/lib/api";
import { useProjectScope } from "@/lib/useProjectScope";
import { useSubmit } from "@/lib/useAsync";
import type { AnalysisPlan, RecordedPlan } from "@/lib/types";

/** A plan that passes the contract's own validators; every field is still a claim the recorder owns. */
function template(projectId: string): string {
  return JSON.stringify(
    {
      plan_id: "plan_scrnaseq_v1",
      plan_version: "1",
      project_id: projectId,
      objective: "which candidate populations in this dataset survive a targeted evidence check",
      design_matrix: {
        formula: "~ group",
        group_variable: "group",
        min_n_per_group: 3,
        confounding_checks: [{ name: "batch_within_group", tool: "scrnaseq.batch_check", status: "PASS" }],
      },
      data_partitions: { rule: "by_specimen", discovery_specimens: ["spc_discovery_1"], confirmation_specimens: [] },
      qc_policy_id: "qc_scrnaseq_v1",
      loop_budget: { max_iterations: 1, max_compute_minutes: 120, max_token_cost_usd: 0 },
      root_inputs: [],
      nodes: [
        {
          node_id: "n_cluster",
          tool_name: "scrnaseq.cluster",
          tool_version: "0.1.0",
          tool_class: "registered",
          resources: { cpu: 4, memory_gb: 16, gpu: 0, max_minutes: 60 },
          privacy_policy: "restricted",
          requires_approval: false,
        },
      ],
      stop_conditions: ["input_schema_invalid", "critical_quality_failure", "unresolved_design_confounding"],
      expected_deliverables: ["candidate_sets", "evidence_cards"],
      provenance_policy: "every numeric field is tool output or a human revision; no model writes a value",
    },
    null,
    2,
  );
}

/**
 * Recording a plan (plan V2.0 sections 5.3, 9.6).
 *
 * This screen takes the whole contract as JSON on purpose. A plan is the artifact an expert approves,
 * so the workbench must not quietly normalise it: what the person edited is what the digest is taken
 * over, and the server's validators are the only thing between the two.
 */
export default function PlansPage({ params }: { params: { projectId: string } }) {
  const { caller, projectId, blocked } = useProjectScope(params.projectId);
  const submit = useSubmit();
  const [body, setBody] = useState(() => template(projectId));
  const [parseError, setParseError] = useState<string | null>(null);

  if (!caller) {
    return (
      <Panel title="Plans" note="nothing is recorded until a token is set">
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  function record(who: Caller) {
    let plan: AnalysisPlan;
    try {
      plan = JSON.parse(body) as AnalysisPlan;
    } catch (exc) {
      setParseError(exc instanceof Error ? exc.message : "the body is not JSON");
      return;
    }
    setParseError(null);
    void submit.run(() => plansApi.record(who, projectId, { ...plan, project_id: projectId }));
  }

  const recorded = submit.result as RecordedPlan | null;

  return (
    <Panel
      title="Record a plan"
      note="recording stores the plan and its digest; it authorises nothing. A gated plan still needs a person's decision before a node runs (§9.6)."
    >
      <p className="muted">
        Three rules the contract enforces itself, so this form does not argue about them: a design
        matrix needs a confounding check that has actually run, an ad-hoc tool node has to require
        approval, and a node that emits a conclusion asset cannot be ungated.
      </p>
      {parseError ? <div className="error">{parseError}</div> : null}
      <ProblemNote error={submit.error} />
      {recorded ? (
        <Notice>
          Recorded <span className="mono">{recorded.plan_id}</span> version{" "}
          <span className="mono">{recorded.plan_version}</span> as digest{" "}
          <span className="mono">{recorded.plan_sha256}</span>. Launch it from the project overview —
          an approval is requested against the run, not against this row.
        </Notice>
      ) : null}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          record(caller);
        }}
      >
        <Field label="plan body" hint="the full contract; unknown keys are refused, not ignored">
          <textarea
            rows={22}
            spellCheck={false}
            className="mono"
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
        </Field>
        <button type="submit" disabled={submit.busy}>
          {submit.busy ? "recording…" : "Record plan"}
        </button>
        <button className="secondary" type="button" onClick={() => setBody(template(projectId))}>
          reset to template
        </button>
      </form>
    </Panel>
  );
}
