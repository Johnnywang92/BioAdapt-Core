"use client";

import Link from "next/link";

import { Chip } from "@/components/ui";
import { approvals as approvalsApi, assets, findings, plans as plansApi, runs as runsApi, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useWorkbench } from "@/lib/identity";
import { useAsync } from "@/lib/useAsync";
import type { Page, Approval, CandidateSet, DataAsset, PlanSummary, Project, RunListItem } from "@/lib/types";

/**
 * Where the work actually stands (plan V2.0 section 5.1).
 *
 * The order below is the pipeline order the nav already uses, and each row is answered from a read the
 * API made just now rather than from a note the browser kept: a step is called done only when the row
 * that would prove it exists. Who a step belongs to is read from the server too - a gate names the
 * subject it is assigned to, and only that person is told to expect their click to land.
 */

type Status = "done" | "you" | "other" | "next";

const STATUS_LABEL: Record<Status, string> = {
  done: "done",
  you: "waiting on you",
  other: "waiting on someone else",
  next: "your next step",
};

const STATUS_CHIP: Record<Status, string> = {
  done: "supported",
  you: "provisional",
  other: "unknown",
  next: "provisional",
};

interface Step {
  id: string;
  title: string;
  detail: string;
  href: string;
  status: Status;
}

export function NextSteps({
  caller,
  projectId,
  project,
}: {
  caller: Caller;
  projectId: string;
  project: Project;
}) {
  const t = useT();
  const { identity } = useWorkbench();

  const listed = useAsync<Page<DataAsset>>(() => assets.list(caller, projectId, { limit: 1 }), [caller.token, projectId]);
  const planList = useAsync<Page<PlanSummary>>(() => plansApi.list(caller, projectId, { limit: 1 }), [caller.token, projectId]);
  const runList = useAsync<Page<RunListItem>>(() => runsApi.list(caller, projectId, { limit: 20 }), [caller.token, projectId]);
  const gates = useAsync<Page<Approval>>(
    () => approvalsApi.list(caller, projectId, { state: "pending", limit: 50 }),
    [caller.token, projectId],
  );
  const candidates = useAsync<Page<CandidateSet>>(() => findings.listCandidates(caller, projectId, { limit: 1 }), [
    caller.token,
    projectId,
  ]);

  const errors = [listed.error, planList.error, runList.error, gates.error, candidates.error].filter(Boolean);
  const loading = [listed.loading, planList.loading, runList.loading, gates.loading, candidates.loading].some(Boolean);
  if (loading && !errors.length) return null;

  const runs_ = runList.data?.items ?? [];
  const latest = runs_[0];
  const pending = gates.data?.items ?? [];
  const mine = pending.filter((one) => one.assigned_to === identity?.subjectId);
  const confirmed = Boolean(project.thresholds?.confirmed);

  const dataDone = (listed.data?.items.length ?? 0) > 0;
  const planDone = (planList.data?.items.length ?? 0) > 0;
  const runDone = runs_.length > 0;

  const finishedStates = new Set([
    "QC_REVIEW",
    "CANDIDATE_DISCOVERY",
    "EVIDENCE_BUILDING",
    "TARGETED_ANALYSIS",
    "VALIDATION",
    "HUMAN_REVIEW",
    "REPORT_READY",
    "ARCHIVED",
  ]);
  const failedStates = new Set([
    "FAILED",
    "BLOCKED",
    "POLICY_BLOCKED",
    "RESOURCE_EXCEEDED",
    "BUDGET_EXCEEDED",
    "NEEDS_INPUT",
    "ESCALATED_TO_HUMAN",
    "RETRYABLE_FAILURE",
  ]);

  const steps: Step[] = [
    {
      id: "data",
      title: t("Register the counts"),
      detail: dataDone
        ? t("at least one asset is recorded in this project")
        : t("nothing is registered here yet, so a plan would have no input to name"),
      href: `/projects/${encodeURIComponent(projectId)}/data`,
      status: dataDone ? "done" : "next",
    },
    {
      id: "plan",
      title: t("Build and record a plan"),
      detail: planDone
        ? t("this project has a plan version a run can pin")
        : t("a run pins a plan version, and none is recorded yet"),
      href: `/projects/${encodeURIComponent(projectId)}/plans`,
      status: planDone ? "done" : dataDone ? "next" : "other",
    },
    {
      id: "run",
      title: t("Launch a run"),
      detail: runDone
        ? t("{n} runs recorded, latest in state {state}", { n: runs_.length, state: latest?.state ?? "" })
        : t("no run has been launched against this plan yet"),
      href: `/projects/${encodeURIComponent(projectId)}`,
      status: runDone ? (latest && failedStates.has(latest.state) ? "next" : "done") : planDone ? "next" : "other",
    },
    {
      id: "gates",
      title: t("Open the gates a run needs"),
      detail: pending.length
        ? (identity?.isSolo || mine.length)
          ? t("{n} of them are assigned to you", { n: identity?.isSolo ? pending.length : mine.length })
          : t("{n} are assigned to other people; a gate opens only on the subject it names (§11.6)", {
              n: pending.length,
            })
        : t("no gate is waiting in this inbox"),
      href: `/projects/${encodeURIComponent(projectId)}/approvals`,
      status: pending.length ? (identity?.isSolo || mine.length ? "you" : "other") : runDone && latest && !finishedStates.has(latest.state) ? "next" : "done",
    },
    {
      id: "review",
      title: t("Review the drafts"),
      detail: (candidates.data?.items.length ?? 0)
        ? t("candidates are recorded, so the queue has cards to read")
        : t("a run has to complete before cards exist to review"),
      href: `/projects/${encodeURIComponent(projectId)}/queue`,
      status: (candidates.data?.items.length ?? 0) ? "next" : "other",
    },
    {
      id: "deliver",
      title: t("Sign off and export"),
      detail: confirmed
        ? t("the cut set is attested, so these exits are open to the people the policy names")
        : t(
            "the stage-0 cut set is unconfirmed: signing a conclusion and exporting stay closed until a principal investigator confirms it with a second factor (§1.5)",
          ),
      href: `/projects/${encodeURIComponent(projectId)}/exports`,
      status: confirmed ? "next" : (identity?.isSolo || (identity?.roles.includes("principal_investigator") && identity?.mfaVerified)) ? "you" : "other",
    },
  ];

  return (
    <>
      {errors.length ? (
        <p className="muted">
          {t("Some of these counts could not be read, so the list below is not the whole picture.")}{" "}
          {t("Each panel that failed says why in the server's own words.")}
        </p>
      ) : null}
      <ol className="steps">
        {steps.map((step) => (
          <li key={step.id}>
            <Chip status={STATUS_CHIP[step.status]} title={step.status}>
              {t(STATUS_LABEL[step.status])}
            </Chip>{" "}
            <Link href={step.href}>{step.title}</Link>
            <span className="muted"> — {step.detail}</span>
          </li>
        ))}
      </ol>
    </>
  );
}

/** What each role is for, in the order the pipeline asks of them (§11.2, §13.1). */
const ROLE_HELP: [string, string][] = [
  [
    "solo_operator",
    "combines PI, analyst, and domain expert duties without separation-of-duties restrictions for walkthroughs and rapid evaluations",
  ],
  [
    "analyst",
    "registers data, builds the plan and launches runs; asks for a gate but cannot open the one it asked for",
  ],
  [
    "principal_investigator",
    "owns the project record: decides the gates assigned to them, confirms the cut set, and is the only role that may end a run",
  ],
  ["domain_expert", "reviews evidence cards in the queue and records the reason behind each decision"],
  ["compliance_officer", "holds the provider terms and the egress approval a cloud call needs before it is made"],
  ["knowledge_curator", "curates the private knowledge base a suggestion is retrieved from"],
  ["service_agent", "may execute work a person already authorised, and is refused everywhere a decision belongs to a person"],
];

export function RoleHelp() {
  const t = useT();
  const { identity } = useWorkbench();
  return (
    <>
      <p className="muted">
        {identity
          ? t("This token presents as {subject}, with roles {roles}.", {
              subject: identity.subjectId,
              roles: identity.roles.join(", ") || t("none"),
            })
          : t("No token is set, so nothing here can be read or acted on.")}
      </p>
      <ul className="role-help">
        {ROLE_HELP.map(([role, note]) => (
          <li key={role}>
            <Chip title={role}>{t(role)}</Chip> <span>{t(note)}</span>
          </li>
        ))}
      </ul>
      <p className="muted">
        {t(
          "This list says what a role is for. It is not a permission check: the API decides every action, and a refusal is shown in its own words.",
        )}
      </p>
    </>
  );
}
