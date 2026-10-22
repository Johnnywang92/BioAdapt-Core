"use client";

import { useState } from "react";

import { Field, Loading, Notice, ProblemNote } from "@/components/ui";
import { approvals as approvalsApi, plans as plansApi, runs as runsApi, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useAsync, useSubmit } from "@/lib/useAsync";
import type { Approval, Page, PlanNode, PlanSummary, RunSummary } from "@/lib/types";

const PROVENANCE_FIELDS = ["input_hashes", "parameters", "container_image_digest", "tool_version"];
const LICENSE_STATUSES = ["approved", "not_required", "pending", "rejected"];
const ID_SYSTEMS = ["ensembl", "refseq", "gencode", "hgnc_symbol", "other"];
/** The cage admits two outcomes (§9.6): stop, or hand the node to a person. A retry would spend the gate twice. */
const FAILURE_POLICIES = ["halt", "escalate_to_human"];
const REPRODUCIBILITY = ["byte", "numeric", "conclusion"];
const DIGEST = /^sha256:[0-9a-f]{64}$/;

/**
 * §9.7: read at the hard cap. The node this grant covers can sit in any recorded plan version; a plan the
 * screen never fetched is a node it cannot name, and a node it cannot name is a form it should not show.
 */
const PLAN_LIMIT = 500;

interface Draft {
  task: string;
  modalities: string;
  species: string;
  inputSchemaName: string;
  inputSchemaVersion: string;
  outputSchemaName: string;
  outputSchemaVersion: string;
  repository: string;
  tag: string;
  digest: string;
  cpu: string;
  memoryMb: string;
  maxMinutes: string;
  measuredPeakMb: string;
  measuredOn: string;
  licenseSpdx: string;
  licenseName: string;
  licenseStatus: string;
  licenseReviewedAt: string;
  licenseNote: string;
  qualityChecks: string;
  assumptions: string;
  invalidWhen: string;
  requiresCounts: string;
  idSystem: string;
  failurePolicy: string;
  reproducibility: string;
  reason: string;
  validForMinutes: string;
}

const EMPTY: Draft = {
  task: "",
  modalities: "",
  species: "",
  inputSchemaName: "",
  inputSchemaVersion: "",
  outputSchemaName: "",
  outputSchemaVersion: "",
  repository: "",
  tag: "",
  digest: "",
  cpu: "",
  memoryMb: "",
  maxMinutes: "",
  measuredPeakMb: "",
  measuredOn: "",
  licenseSpdx: "",
  licenseName: "",
  licenseStatus: "approved",
  licenseReviewedAt: "",
  licenseNote: "",
  qualityChecks: "",
  assumptions: "",
  invalidWhen: "",
  requiresCounts: "",
  idSystem: "",
  failurePolicy: "halt",
  reproducibility: "conclusion",
  reason: "",
  validForMinutes: "",
};

interface NodeContext {
  run: RunSummary | null;
  plan: PlanSummary | null;
  node: PlanNode | null;
}

/**
 * The execution grant, filled in by the person who decided the gate (§9.6, 第四批③).
 *
 * A grant row pins the bytes of one tool spec, so the only honest screen is one that asks for those bytes.
 * Nothing here is invented on a person's behalf: the tool's name and version come from the plan node the
 * gate names, the four provenance fields and the cage settings come from the contract that requires them,
 * and every remaining line is stated by the one who signs. A field left blank stops the submission here
 * rather than going out as a guess the server would refuse - and a refusal, when it comes, is shown in the
 * server's own words. The screen does not hide itself from a subject who cannot issue the grant - §11.2
 * keeps that decision in the token - but it names the subject the route will accept before the form is filled.
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
  const [draft, setDraft] = useState<Draft>(EMPTY);
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
  const missing = missingFields(draft);
  const ready = !!node && !wrongClass && missing.length === 0;

  function set(name: keyof Draft) {
    return (event: { target: { value: string } }) => setDraft((current) => ({ ...current, [name]: event.target.value }));
  }

  function send() {
    if (!node || !nodeId) return;
    void submit
      .run(() =>
        approvalsApi.grantAdhoc(caller, projectId, approval.run_id, nodeId, {
          approval_id: approval.approval_id,
          spec: specOf(draft, node),
          reason: draft.reason.trim() || undefined,
          valid_for_minutes: draft.validForMinutes.trim() ? Number(draft.validForMinutes) : undefined,
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
          "the gate says a person allowed this submission; the grant is what turns that decision into one execution. Its row pins the exact bytes of the spec, so the lines below are the ones being signed - not a template (they cannot be read back from anywhere else, because this is the first time the platform sees them)",
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
      ) : null}

      {node && !wrongClass ? (
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
          <p className="muted">
            {t(
              "the name and the version are not asked for: the route refuses a grant whose spec names anything other than this node's plan entry, so the screen reads them from the plan instead",
            )}
          </p>
          {approval.decided_by && approval.decided_by !== subjectId ? (
            <p className="muted">
              {t(
                "this grant is the execution of one named decision, so the route issues it only for {who}, the subject that decided the gate - not for anyone who merely agrees with it (§11.6)",
                { who: approval.decided_by },
              )}
            </p>
          ) : null}

          <form
            onSubmit={(event) => {
              event.preventDefault();
              send();
            }}
          >
            <fieldset>
              <legend>{t("what the submission is")}</legend>
              <div className="grid">
                <Field label="task" hint={t("the step this code performs, in the words the plan uses for it")}>
                  <input value={draft.task} onChange={set("task")} />
                </Field>
                <Field label="supported_modalities" hint={t("one per line, as this code really reads them")}>
                  <input value={draft.modalities} onChange={set("modalities")} />
                </Field>
                <Field label="supported_species" hint={t("a species nobody checked cannot be claimed; leave it out rather than widen it")}>
                  <input value={draft.species} onChange={set("species")} />
                </Field>
              </div>
              <div className="grid">
                <Field label="input_schema.name">
                  <input value={draft.inputSchemaName} onChange={set("inputSchemaName")} />
                </Field>
                <Field label="input_schema.version">
                  <input value={draft.inputSchemaVersion} onChange={set("inputSchemaVersion")} />
                </Field>
                <Field label="output_schema.name">
                  <input value={draft.outputSchemaName} onChange={set("outputSchemaName")} />
                </Field>
                <Field label="output_schema.version">
                  <input value={draft.outputSchemaVersion} onChange={set("outputSchemaVersion")} />
                </Field>
              </div>
            </fieldset>

            <fieldset>
              <legend>{t("the image that runs")}</legend>
              <p className="muted">
                {t(
                  "a tag names a moving target, so the row is pinned by the digest the registry actually holds. Nobody can sign a permission over an image that was looked up later: a digest copied from the push is the only answer that means anything here",
                )}
              </p>
              <Field label="container_image.repository">
                <input className="mono" value={draft.repository} onChange={set("repository")} />
              </Field>
              <div className="grid">
                <Field label="container_image.tag">
                  <input className="mono" value={draft.tag} onChange={set("tag")} />
                </Field>
                <Field
                  label="container_image.digest"
                  hint={t("sha256: followed by 64 hexadecimal characters; anything else is refused here and by the contract")}
                >
                  <input className="mono" value={draft.digest} onChange={set("digest")} />
                </Field>
              </div>
            </fieldset>

            <fieldset>
              <legend>{t("what it asks for, and what it was measured on")}</legend>
              <div className="grid">
                <Field label="resources.cpu">
                  <input type="number" min={1} value={draft.cpu} onChange={set("cpu")} />
                </Field>
                <Field label="resources.memory_mb">
                  <input type="number" min={1} value={draft.memoryMb} onChange={set("memoryMb")} />
                </Field>
                <Field label="resources.max_minutes">
                  <input type="number" min={1} value={draft.maxMinutes} onChange={set("maxMinutes")} />
                </Field>
              </div>
              <div className="grid">
                <Field
                  label="resources.measured_peak_memory_mb"
                  hint={t("the number a run of this image actually produced, not the number asked for")}
                >
                  <input type="number" min={1} value={draft.measuredPeakMb} onChange={set("measuredPeakMb")} />
                </Field>
                <Field
                  label="resources.measured_on"
                  hint={t("the dataset or run the measurement was taken on")}
                >
                  <input value={draft.measuredOn} onChange={set("measuredOn")} />
                </Field>
              </div>
              <p className="muted">
                {t(
                  "the deployment caps what an ad-hoc node may ask for, and this screen does not restate those caps: ask for more and the grant route refuses, in its own words, below",
                )}
              </p>
            </fieldset>

            <fieldset>
              <legend>{t("the licence as it was reviewed")}</legend>
              <div className="grid">
                <Field label="license.spdx_id">
                  <input className="mono" value={draft.licenseSpdx} onChange={set("licenseSpdx")} />
                </Field>
                <Field label="license.display_name">
                  <input value={draft.licenseName} onChange={set("licenseName")} />
                </Field>
                <Field label="license.review_status">
                  <select value={draft.licenseStatus} onChange={set("licenseStatus")}>
                    {LICENSE_STATUSES.map((one) => (
                      <option key={one} value={one}>
                        {`${t(one)} (${one})`}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field
                  label="license.reviewed_at"
                  hint={t("an approved licence has to carry the date it was reviewed; not_required is the only status that needs none")}
                >
                  <input type="date" value={draft.licenseReviewedAt} onChange={set("licenseReviewedAt")} />
                </Field>
              </div>
              <Field label="license.note">
                <input value={draft.licenseNote} onChange={set("licenseNote")} />
              </Field>
            </fieldset>

            <fieldset>
              <legend>{t("what a reader has to be able to check")}</legend>
              <div className="grid">
                <Field label="quality_checks" hint={t("one per line")}>
                  <textarea rows={2} value={draft.qualityChecks} onChange={set("qualityChecks")} />
                </Field>
                <Field label="assumptions" hint={t("one per line")}>
                  <textarea rows={2} value={draft.assumptions} onChange={set("assumptions")} />
                </Field>
                <Field label="invalid_when" hint={t("one per line; the contract refuses a submission that cannot say when its own result stops holding")}>
                  <textarea rows={2} value={draft.invalidWhen} onChange={set("invalidWhen")} />
                </Field>
              </div>
              <div className="grid">
                <Field label="reference_compatibility.requires_counts" hint={t("whether this code needs raw counts or a normalised matrix")}>
                  <select value={draft.requiresCounts} onChange={set("requiresCounts")}>
                    <option value="">{t("not stated")}</option>
                    <option value="true">{t("yes")}</option>
                    <option value="false">{t("no")}</option>
                  </select>
                </Field>
                <Field label="reference_compatibility.id_system">
                  <select value={draft.idSystem} onChange={set("idSystem")}>
                    <option value="">{t("not stated")}</option>
                    {ID_SYSTEMS.map((one) => (
                      <option key={one} value={one}>
                        {one}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              <p className="muted">
                {t("provenance_fields")}: <span className="mono">{PROVENANCE_FIELDS.join(", ")}</span>
                {t(
                  " - these four are what the contract requires every tool to leave behind, so they are not offered as a choice",
                )}
              </p>
            </fieldset>

            <fieldset>
              <legend>{t("how it may fail, and how long the grant stays open")}</legend>
              <div className="grid">
                <Field label="failure_policy" hint={t("the cage admits only these two: stop, or hand the node to a person")}>
                  <select value={draft.failurePolicy} onChange={set("failurePolicy")}>
                    {FAILURE_POLICIES.map((one) => (
                      <option key={one} value={one}>
                        {`${t(one)} (${one})`}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="reproducibility_level_target">
                  <select value={draft.reproducibility} onChange={set("reproducibility")}>
                    {REPRODUCIBILITY.map((one) => (
                      <option key={one} value={one}>
                        {`${t(one)} (${one})`}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field
                  label="valid_for_minutes"
                  hint={t("optional; the deployment's own window is the ceiling and asking for less is allowed")}
                >
                  <input type="number" min={1} value={draft.validForMinutes} onChange={set("validForMinutes")} />
                </Field>
              </div>
              <p className="muted">
                {t(
                  "left as it stands: no network, no GPU, no egress, no retry, project-scoped assets, and the grant is spent by the one execution it authorises (§9.6). A retry would spend a gate twice, so the platform does not offer one here",
                )}
              </p>
              <Field
                label="reason"
                hint={t("optional; left open, the row records the gate's own decision reason")}
              >
                <input value={draft.reason} onChange={set("reason")} />
              </Field>
            </fieldset>

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
                  " one gate buys one grant, so sending these same lines again answers with the row that already stands rather than a second permission (§9.6)",
                )}
              </Notice>
            ) : null}
            {missing.length ? (
              <p className="muted">
                {t("{n} lines still have to be stated:", { n: String(missing.length) })}{" "}
                <span className="mono">{missing.join(", ")}</span>
              </p>
            ) : null}
            <button type="submit" disabled={submit.busy || !ready}>
              {submit.busy ? t("issuing…") : t("Issue the execution grant")}
            </button>
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

const LIST_FIELDS: Array<[keyof Draft, string]> = [
  ["qualityChecks", "quality_checks"],
  ["assumptions", "assumptions"],
  ["invalidWhen", "invalid_when"],
];

function missingFields(draft: Draft): string[] {
  const found: string[] = [];
  const text: Array<[keyof Draft, string]> = [
    ["task", "task"],
    ["modalities", "supported_modalities"],
    ["species", "supported_species"],
    ["inputSchemaName", "input_schema.name"],
    ["inputSchemaVersion", "input_schema.version"],
    ["outputSchemaName", "output_schema.name"],
    ["outputSchemaVersion", "output_schema.version"],
    ["repository", "container_image.repository"],
    ["tag", "container_image.tag"],
    ["measuredOn", "resources.measured_on"],
    ["licenseSpdx", "license.spdx_id"],
    ["licenseName", "license.display_name"],
  ];
  for (const [name, label] of text) if (!draft[name].trim()) found.push(label);
  if (!DIGEST.test(draft.digest.trim())) found.push("container_image.digest");
  for (const [name, label] of [
    ["cpu", "resources.cpu"],
    ["memoryMb", "resources.memory_mb"],
    ["maxMinutes", "resources.max_minutes"],
    ["measuredPeakMb", "resources.measured_peak_memory_mb"],
  ] as Array<[keyof Draft, string]>) {
    const value = Number(draft[name]);
    if (!draft[name].trim() || !Number.isInteger(value) || value < 1) found.push(label);
  }
  if (draft.licenseStatus === "approved" && !draft.licenseReviewedAt.trim()) found.push("license.reviewed_at");
  for (const [name, label] of LIST_FIELDS) if (!draft[name].trim()) found.push(label);
  if (!draft.requiresCounts) found.push("reference_compatibility.requires_counts");
  if (!draft.idSystem) found.push("reference_compatibility.id_system");
  if (draft.validForMinutes.trim() && !Number.isInteger(Number(draft.validForMinutes))) {
    found.push("valid_for_minutes");
  }
  return found;
}

function list(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((one) => one.trim())
    .filter(Boolean);
}

function specOf(draft: Draft, node: PlanNode): Record<string, unknown> {
  return {
    tool_name: node.tool_name,
    tool_version: node.tool_version,
    task: draft.task.trim(),
    tool_class: "project_adhoc",
    supported_modalities: list(draft.modalities),
    supported_species: list(draft.species),
    input_schema: { name: draft.inputSchemaName.trim(), version: draft.inputSchemaVersion.trim() },
    output_schema: { name: draft.outputSchemaName.trim(), version: draft.outputSchemaVersion.trim() },
    container_image: {
      repository: draft.repository.trim(),
      tag: draft.tag.trim(),
      digest: draft.digest.trim(),
    },
    resources: {
      cpu: Number(draft.cpu),
      memory_mb: Number(draft.memoryMb),
      max_minutes: Number(draft.maxMinutes),
      measured_peak_memory_mb: Number(draft.measuredPeakMb),
      measured_on: draft.measuredOn.trim(),
    },
    license: {
      spdx_id: draft.licenseSpdx.trim(),
      display_name: draft.licenseName.trim(),
      review_status: draft.licenseStatus,
      reviewed_at: draft.licenseReviewedAt.trim() || undefined,
      note: draft.licenseNote.trim() || undefined,
    },
    quality_checks: list(draft.qualityChecks),
    provenance_fields: PROVENANCE_FIELDS,
    assumptions: list(draft.assumptions),
    invalid_when: list(draft.invalidWhen),
    reference_compatibility: {
      requires_counts: draft.requiresCounts === "true",
      id_system: draft.idSystem,
    },
    failure_policy: draft.failurePolicy,
    retry_limit: 0,
    network: { mode: "closed" },
    reproducibility_level_target: draft.reproducibility,
  };
}
