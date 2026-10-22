"use client";

import Link from "next/link";
import { useState } from "react";

import { Empty, Field, Loading, Notice, Panel, ProblemNote } from "@/components/ui";
import { PlanWizard } from "@/components/PlanWizard";
import { plans as plansApi, projects as projectsApi, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync, useSubmit } from "@/lib/useAsync";
import type { AnalysisPlan, Page, PlanSummary, Project, RecordedPlan } from "@/lib/types";

/**
 * Recording a plan (plan V2.0 sections 5.3, 9.6).
 *
 * The wizard above builds the body from rows the registry actually advertises; this panel is the same
 * contract taken as JSON on purpose. A plan is the artifact an expert approves, so the workbench must
 * not quietly normalise it: what the person edited is what the digest is taken over, and the server's
 * validators are the only thing between the two.
 */
export default function PlansPage({ params }: { params: { projectId: string } }) {
  const t = useT();
  const { caller, projectId, blocked } = useProjectScope(params.projectId);
  const project = useAsync<Project | null>(
    () => (caller ? projectsApi.read(caller, projectId) : Promise.resolve(null)),
    [caller?.token, projectId],
  );

  if (!caller) {
    return (
      <Panel title={t("Plans")} note={t("nothing is recorded until a token is set")}>
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  return (
    <>
      {project.data ? <PlanWizard caller={caller} projectId={projectId} project={project.data} /> : null}
      <Loading on={project.loading} />
      <ProblemNote error={project.error} />
      <Recorded caller={caller} projectId={projectId} />
      <details className="advanced">
        <summary>{t("write the whole contract as JSON")}</summary>
        <Advanced caller={caller} projectId={projectId} />
      </details>
    </>
  );
}

/** The contract, as a body the person wrote. Nothing here is pre-filled, because a pre-filled plan is a claim nobody made. */
function Advanced({ caller, projectId }: { caller: Caller; projectId: string }) {
  const t = useT();
  const submit = useSubmit();
  const [body, setBody] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  function record(who: Caller) {
    let plan: AnalysisPlan;
    try {
      plan = JSON.parse(body) as AnalysisPlan;
    } catch (exc) {
      setParseError(exc instanceof Error ? exc.message : t("the body is not JSON"));
      return;
    }
    setParseError(null);
    void submit.run(() => plansApi.record(who, projectId, { ...plan, project_id: projectId }));
  }

  const recorded = submit.result as RecordedPlan | null;

  return (
    <Panel
      title={t("Record a plan")}
      note={t(
        "recording stores the plan and its digest; it authorises nothing. A gated plan still needs a person's decision before a node runs (§9.6).",
      )}
    >
      <p className="muted">
        {t(
          "Three rules the contract enforces itself, so this form does not argue about them: a design matrix needs a confounding check that has actually run, an ad-hoc tool node has to require approval, and a node that emits a conclusion asset cannot be ungated.",
        )}
      </p>
      {parseError ? <div className="error">{parseError}</div> : null}
      <ProblemNote error={submit.error} />
      {recorded ? (
        <Notice>
          {t("Recorded")} <span className="mono">{recorded.plan_id}</span>
          {t(", version")} <span className="mono">{recorded.plan_version}</span>
          {t(" as digest")} <span className="mono">{recorded.plan_sha256}</span>.{" "}
          {t("Launch it from the project overview — an approval is requested against the run, not against this row.")}
        </Notice>
      ) : null}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          record(caller);
        }}
      >
        <Field
          label={t("plan body")}
          hint={t("the full contract; unknown keys are refused, not ignored - the wizard's review panel shows one that is complete")}
        >
          <textarea
            rows={22}
            spellCheck={false}
            className="mono"
            placeholder={'{\n  "plan_id": …, "plan_version": …, "objective": …,\n  "design_matrix": …, "data_partitions": …, "nodes": [ … ]\n}'}
            value={body}
            onChange={(event) => setBody(event.target.value)}
          />
        </Field>
        <button type="submit" disabled={submit.busy || !body.trim()}>
          {submit.busy ? t("recording…") : t("Record plan")}
        </button>
      </form>
    </Panel>
  );
}

/** Every version this project has, because a run pins one by id and version and no screen may pick for it. */
function Recorded({ caller, projectId }: { caller: Caller; projectId: string }) {
  const t = useT();
  const listed = useAsync<Page<PlanSummary>>(() => plansApi.list(caller, projectId, { limit: 50 }), [
    caller.token,
    projectId,
  ]);
  return (
    <Panel
      title={t("Recorded plans")}
      note={t("a launch names one of these versions and pins it; recording never authorised a run")}
      actions={
        <button className="secondary" type="button" onClick={listed.reload}>
          {t("re-read")}
        </button>
      }
    >
      <ProblemNote error={listed.error} />
      <Loading on={listed.loading} />
      {listed.data && listed.data.items.length ? (
        <table>
          <thead>
            <tr>
              <th>{t("plan")}</th>
              <th>{t("objective")}</th>
              <th>{t("steps")}</th>
              <th>{t("digest")}</th>
              <th>{t("recorded by")}</th>
            </tr>
          </thead>
          <tbody>
            {listed.data.items.map((plan) => (
              <tr key={`${plan.plan_id}@${plan.plan_version}`}>
                <td className="mono">{`${plan.plan_id}@${plan.plan_version}`}</td>
                <td>{plan.objective}</td>
                <td>{plan.nodes.length}</td>
                <td className="mono">{plan.plan_sha256.slice(0, 12)}</td>
                <td className="mono">{plan.created_by}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        !listed.loading && (
          <Empty>
            {t("Nothing is recorded here yet.")}{" "}
            <Link href={`/projects/${encodeURIComponent(projectId)}`}>{t("the overview launches a recorded version")}</Link>
          </Empty>
        )
      )}
    </Panel>
  );
}
