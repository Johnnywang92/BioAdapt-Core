"use client";

import { useState } from "react";

import { Field, Notice } from "@/components/ui";
import { useT } from "@/lib/i18n";
import type { AdhocLimits, ToolRow } from "@/lib/types";

const PROVENANCE_FIELDS = ["input_hashes", "parameters", "container_image_digest", "tool_version"];
const LICENSE_STATUSES = ["approved", "not_required", "pending", "rejected"];
const ID_SYSTEMS = ["ensembl", "refseq", "gencode", "hgnc_symbol", "other"];
/** The cage admits two outcomes (§9.6): stop, or hand the node to a person. A retry would spend the gate twice. */
const FAILURE_POLICIES = ["halt", "escalate_to_human"];
const REPRODUCIBILITY = ["byte", "numeric", "conclusion"];
const DIGEST = /^sha256:[0-9a-f]{64}$/;

/** The bytes of one submission, in the shape the tool contract reads (§9.4, §9.6). */
export interface AdhocSpec {
  tool_name: string;
  tool_version: string;
  task: string;
  tool_class: "project_adhoc";
  supported_modalities: string[];
  supported_species: string[];
  input_schema: { name: string; version: string };
  output_schema: { name: string; version: string };
  container_image: { repository: string; tag: string; digest: string };
  resources: {
    cpu: number;
    memory_mb: number;
    max_minutes: number;
    measured_peak_memory_mb: number;
    measured_on: string;
  };
  license: {
    spdx_id: string;
    display_name: string;
    review_status: string;
    reviewed_at: string | null;
    note: string | null;
  };
  quality_checks: string[];
  provenance_fields: string[];
  assumptions: string[];
  invalid_when: string[];
  reference_compatibility: {
    requires_counts: boolean;
    id_system: string;
    needs_log: boolean | null;
    embedding_source: string | null;
  };
  failure_policy: string;
  retry_limit: 0;
  network: { mode: "closed" };
  reproducibility_level_target: string;
  [extra: string]: unknown;
}

/** One step's own implementation: which step it is, and the bytes that implement it. */
export interface AdhocSubmission {
  task: string;
  spec: AdhocSpec;
}

interface Draft {
  toolName: string;
  toolVersion: string;
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
  reason: string;
  assumptions: string;
  invalidWhen: string;
  requiresCounts: string;
  idSystem: string;
  failurePolicy: string;
  reproducibility: string;
}

function emptyDraft(row: ToolRow, initial?: AdhocSpec): Draft {
  if (initial) {
    return {
      toolName: initial.tool_name,
      toolVersion: initial.tool_version,
      modalities: initial.supported_modalities.join("\n"),
      species: initial.supported_species.join("\n"),
      inputSchemaName: initial.input_schema.name,
      inputSchemaVersion: initial.input_schema.version,
      outputSchemaName: initial.output_schema.name,
      outputSchemaVersion: initial.output_schema.version,
      repository: initial.container_image.repository,
      tag: initial.container_image.tag,
      digest: initial.container_image.digest,
      cpu: String(initial.resources.cpu),
      memoryMb: String(initial.resources.memory_mb),
      maxMinutes: String(initial.resources.max_minutes),
      measuredPeakMb: String(initial.resources.measured_peak_memory_mb),
      measuredOn: initial.resources.measured_on,
      licenseSpdx: initial.license.spdx_id,
      licenseName: initial.license.display_name,
      licenseStatus: initial.license.review_status,
      licenseReviewedAt: initial.license.reviewed_at ?? "",
      licenseNote: initial.license.note ?? "",
      qualityChecks: initial.quality_checks.join("\n"),
      reason: initial.assumptions[0] ?? "",
      assumptions: initial.assumptions.slice(1).join("\n"),
      invalidWhen: initial.invalid_when.join("\n"),
      requiresCounts: String(initial.reference_compatibility.requires_counts),
      idSystem: initial.reference_compatibility.id_system,
      failurePolicy: initial.failure_policy,
      reproducibility: initial.reproducibility_level_target,
    };
  }
  return {
    toolName: "",
    toolVersion: "",
    modalities: "",
    species: "",
    inputSchemaName: row.input_schema.name,
    inputSchemaVersion: "",
    outputSchemaName: row.output_schema.name,
    outputSchemaVersion: "",
    repository: "",
    tag: "",
    digest: "",
    cpu: String(row.min_resources.cpu),
    memoryMb: String(row.min_resources.memory_gb * 1024),
    maxMinutes: String(row.min_resources.max_minutes),
    measuredPeakMb: "",
    measuredOn: "",
    licenseSpdx: "",
    licenseName: "",
    licenseStatus: "approved",
    licenseReviewedAt: "",
    licenseNote: "",
    qualityChecks: "",
    reason: "",
    assumptions: "",
    invalidWhen: "",
    requiresCounts: "",
    idSystem: "",
    failurePolicy: "halt",
    reproducibility: "numeric",
  };
}

/**
 * One project's own implementation of one step, stated once, at plan time (§9.6).
 *
 * The submission is the bytes a later grant executes, so they are asked for here rather than at the gate:
 * the plan is what pins them, and a person who signed a gate over a tool nobody had described yet was
 * signing a name. Nothing here is filled in on the analyst's behalf - the cage settings are fixed by the
 * deployment, the schema names are read off the step they replace, and every other line is stated by the
 * one who owns the code. A line left blank keeps the submission off the plan rather than going out as a
 * guess the server would refuse.
 */
export function AdhocSpecForm({
  row,
  limits,
  initial,
  onSubmit,
  onClear,
}: {
  row: ToolRow;
  limits: AdhocLimits | null;
  initial?: AdhocSpec;
  onSubmit: (submission: AdhocSubmission) => void;
  onClear: () => void;
}) {
  const t = useT();
  const [draft, setDraft] = useState<Draft>(() => emptyDraft(row, initial));

  const set = (name: keyof Draft) => (event: { target: { value: string } }) =>
    setDraft((current) => ({ ...current, [name]: event.target.value }));

  const missing = missingFields(draft);
  const overCap = capProblems(draft, limits);

  function send() {
    if (missing.length || overCap.length) return;
    onSubmit({ task: row.task, spec: specOf(draft, row) });
  }

  return (
    <fieldset>
      <legend>{t("your own tool for this step")}</legend>
      <p className="muted">
        {t(
          "the registered step stays the default; this is the tool your project puts in its place. The bytes below are what a PI will be asked to approve, so they are stated here rather than at the gate - the plan is what pins them, and the grant is what executes exactly those bytes (§9.6). The outputs of the step are recorded as produced by this tool, which is why the approval is the point",
        )}
      </p>

      <div className="grid">
        <Field label="tool_name" hint={t("the name your own code carries; it is not the registered one")}>
          <input className="mono" value={draft.toolName} onChange={set("toolName")} />
        </Field>
        <Field label="tool_version" hint={t("a pinned version: a floating one cannot be signed over")}>
          <input className="mono" value={draft.toolVersion} onChange={set("toolVersion")} />
        </Field>
      </div>
      <div className="grid">
        <Field label="supported_modalities" hint={t("one per line, as this code really reads them")}>
          <input value={draft.modalities} onChange={set("modalities")} />
        </Field>
        <Field
          label="supported_species"
          hint={t("a species nobody checked cannot be claimed; leave it out rather than widen it")}
        >
          <input value={draft.species} onChange={set("species")} />
        </Field>
      </div>

      <div className="grid">
        <Field label="input_schema.name">
          <input className="mono" value={draft.inputSchemaName} onChange={set("inputSchemaName")} />
        </Field>
        <Field label="input_schema.version">
          <input className="mono" value={draft.inputSchemaVersion} onChange={set("inputSchemaVersion")} />
        </Field>
        <Field label="output_schema.name">
          <input className="mono" value={draft.outputSchemaName} onChange={set("outputSchemaName")} />
        </Field>
        <Field label="output_schema.version">
          <input className="mono" value={draft.outputSchemaVersion} onChange={set("outputSchemaVersion")} />
        </Field>
      </div>

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
          <Field label="resources.cpu" hint={limits ? `${t("at most")} ${limits.max_cpu}` : undefined}>
            <input type="number" min={1} value={draft.cpu} onChange={set("cpu")} />
          </Field>
          <Field
            label="resources.memory_mb"
            hint={limits ? `${t("at most")} ${limits.max_memory_mb}` : undefined}
          >
            <input type="number" min={1} value={draft.memoryMb} onChange={set("memoryMb")} />
          </Field>
          <Field
            label="resources.max_minutes"
            hint={limits ? `${t("at most")} ${limits.max_minutes}` : undefined}
          >
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
          <Field label="resources.measured_on" hint={t("the dataset or run the measurement was taken on")}>
            <input value={draft.measuredOn} onChange={set("measuredOn")} />
          </Field>
        </div>
        <p className="muted">
          {t(
            "left as it stands: no network, no GPU, no egress, no retry, project-scoped assets (§9.6). A retry would spend a gate twice, so the platform does not offer one here",
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
        <Field
          label={t("why the registered step does not answer this question")}
          hint={t("the first of the assumptions below, and the line the approver reads first")}
        >
          <textarea rows={2} value={draft.reason} onChange={set("reason")} />
        </Field>
        <div className="grid">
          <Field label="quality_checks" hint={t("one per line")}>
            <textarea rows={2} value={draft.qualityChecks} onChange={set("qualityChecks")} />
          </Field>
          <Field label="assumptions" hint={t("one per line, besides the reason above")}>
            <textarea rows={2} value={draft.assumptions} onChange={set("assumptions")} />
          </Field>
          <Field
            label="invalid_when"
            hint={t("one per line; the contract refuses a submission that cannot say when its own result stops holding")}
          >
            <textarea rows={2} value={draft.invalidWhen} onChange={set("invalidWhen")} />
          </Field>
        </div>
        <div className="grid">
          <Field
            label="reference_compatibility.requires_counts"
            hint={t("whether this code needs raw counts or a normalised matrix")}
          >
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
        <legend>{t("how it may fail, and what it promises")}</legend>
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
        </div>
      </fieldset>

      {missing.length ? (
        <p className="muted">
          {t("{n} lines still have to be stated:", { n: String(missing.length) })}{" "}
          <span className="mono">{missing.join(", ")}</span>
        </p>
      ) : null}
      {overCap.length ? (
        <Notice>
          <strong>{t("this is outside the cage")}</strong>
          <ul className="steps">
            {overCap.map((one) => (
              <li key={one}>{one}</li>
            ))}
          </ul>
        </Notice>
      ) : null}

      <div className="chips">
        <button type="button" onClick={send} disabled={!!missing.length || !!overCap.length}>
          {t("use this for the step")}
        </button>
        <button className="secondary" type="button" onClick={onClear}>
          {t("keep the registered step")}
        </button>
      </div>
    </fieldset>
  );
}

function list(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((one) => one.trim())
    .filter(Boolean);
}

function missingFields(draft: Draft): string[] {
  const found: string[] = [];
  const text: Array<[keyof Draft, string]> = [
    ["toolName", "tool_name"],
    ["toolVersion", "tool_version"],
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
    ["reason", "why the registered step does not answer this question"],
    ["qualityChecks", "quality_checks"],
    ["invalidWhen", "invalid_when"],
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
  if (!draft.requiresCounts) found.push("reference_compatibility.requires_counts");
  if (!draft.idSystem) found.push("reference_compatibility.id_system");
  return found;
}

/** The cage, restated as the deployment's own numbers rather than as a promise (§9.6). */
function capProblems(draft: Draft, limits: AdhocLimits | null): string[] {
  if (!limits) return [];
  const found: string[] = [];
  const cpu = Number(draft.cpu);
  const memory = Number(draft.memoryMb);
  const minutes = Number(draft.maxMinutes);
  if (Number.isFinite(cpu) && cpu > limits.max_cpu) found.push(`cpu ${cpu} exceeds ad-hoc cap ${limits.max_cpu}`);
  if (Number.isFinite(memory) && memory > limits.max_memory_mb)
    found.push(`memory ${memory}MB exceeds ad-hoc cap ${limits.max_memory_mb}MB`);
  if (Number.isFinite(minutes) && minutes > limits.max_minutes)
    found.push(`runtime ${minutes}min exceeds ad-hoc cap ${limits.max_minutes}min`);
  return found;
}

function specOf(draft: Draft, row: ToolRow): AdhocSpec {
  return {
    tool_name: draft.toolName.trim(),
    tool_version: draft.toolVersion.trim(),
    task: row.task,
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
      reviewed_at: draft.licenseReviewedAt.trim() || null,
      note: draft.licenseNote.trim() || null,
    },
    quality_checks: list(draft.qualityChecks),
    provenance_fields: PROVENANCE_FIELDS,
    assumptions: [draft.reason.trim(), ...list(draft.assumptions)].filter(Boolean),
    invalid_when: list(draft.invalidWhen),
    reference_compatibility: {
      requires_counts: draft.requiresCounts === "true",
      id_system: draft.idSystem,
      needs_log: null,
      embedding_source: null,
    },
    failure_policy: draft.failurePolicy,
    retry_limit: 0,
    network: { mode: "closed" },
    reproducibility_level_target: draft.reproducibility,
  };
}
