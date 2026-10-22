"use client";

import Link from "next/link";
import { useState } from "react";

import { Field, Notice, Panel, ProblemNote } from "@/components/ui";
import { DeploymentNotice, useDeployment } from "@/components/DeploymentStatus";
import { assets as assetsApi, type Caller } from "@/lib/api";
import { digestHex, distinct, inspectDocument, looksH5ad, type Inspection } from "@/lib/countsDocument";
import { useT } from "@/lib/i18n";
import { useSubmit } from "@/lib/useAsync";
import type { DataAsset, FilteringState, PrivacyTier, Project, RetentionClass, UsageScope } from "@/lib/types";

/**
 * Registering data without a form to fill in (§5.9).
 *
 * The person picks a file; the rest is read from it. An id is derived from the digest, the format and
 * contract version come from the document, and the privacy tier defaults to the project's own - so the
 * only fields left to choose are the ones that are genuinely a person's declaration: whether the
 * object was filtered, and what it may be used for. Nothing here is registered behind a failed read:
 * a file this screen cannot parse is reported as one, and the upload does not happen.
 */
export function DataWizard({ caller, projectId, project }: { caller: Caller; projectId: string; project: Project }) {
  const t = useT();
  const submit = useSubmit();
  const deployment = useDeployment();
  const unavailable = !deployment.error && deployment.data?.object_upload === "unavailable";

  const [bytes, setBytes] = useState<Uint8Array | null>(null);
  const [fileName, setFileName] = useState("");
  const [digest, setDigest] = useState("");
  const [read, setRead] = useState<Inspection | null>(null);
  const [tooBig, setTooBig] = useState(false);
  const [isH5ad, setIsH5ad] = useState(false);
  const [form, setForm] = useState({
    privacy_tier: "" as PrivacyTier | "",
    usage_scope: "this_project_only" as UsageScope,
    retention_class: "run_linked" as RetentionClass,
    filtering_state: "none" as FilteringState,
    pipeline_name: "",
    pipeline_version: "",
    reference_build: "",
    reference_annotation_source: "",
  });

  const set = (name: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [name]: event.target.value }));

  async function pick(file: File | undefined) {
    setRead(null);
    setTooBig(false);
    setIsH5ad(false);
    if (!file) return;
    const buffer = new Uint8Array(await file.arrayBuffer());
    setBytes(buffer);
    setFileName(file.name);
    setDigest(await digestHex(buffer));
    if (looksH5ad(file.name, buffer)) {
      setIsH5ad(true);
      return;
    }
    // A counts matrix is often hundreds of megabytes; parsing it just to preview would freeze the tab.
    if (buffer.byteLength > 40_000_000) {
      setTooBig(true);
      return;
    }
    let text: string;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
    } catch {
      setRead({ problem: "not_utf8" });
      return;
    }
    try {
      const parsed = JSON.parse(text) as Record<string, unknown>;
      // The bytes the person picked have to hash to what the index will claim, so the digest stays
      // the file's own and the declaration below cannot disagree with it.
      setRead(inspectDocument(parsed));
    } catch {
      setRead({ problem: "not_json" });
    }
  }

  const preview = read && "preview" in read ? read.preview : null;
  const problem = read && "problem" in read ? read.problem : null;
  const assetId = digest ? `as_${digest.slice(0, 12)}` : "";
  const tier = (form.privacy_tier || project.privacy_tier) as PrivacyTier;
  // §5.4: "unknown" is a claim that nobody can show the filtering was done, and the contract only
  // admits it together with the pipeline that made it unknowable.
  const needsPipeline = form.filtering_state === "unknown";
  const pipelineWhole =
    !needsPipeline ||
    (form.pipeline_name.trim() !== "" &&
      form.pipeline_version.trim() !== "" &&
      form.reference_build.trim() !== "" &&
      form.reference_annotation_source.trim() !== "");
  const upstream = needsPipeline
    ? {
        name: form.pipeline_name,
        version: form.pipeline_version,
        reference_build: form.reference_build,
        reference_annotation_source: form.reference_annotation_source,
      }
    : null;

  function register() {
    if (!bytes || !preview || unavailable || !pipelineWhole) return;
    const declared: DataAsset = {
      asset_id: assetId,
      project_id: projectId,
      // The API rewrites this to the key it derived; sending the placeholder keeps the contract whole
      // without letting the client claim a location it does not own.
      uri: "pending://signed-upload",
      file_sha256: digest,
      content_fingerprint: digest,
      format: "json",
      schema_version: preview.schemaVersion ?? "expression_v1",
      size_bytes: bytes.byteLength,
      privacy_tier: tier,
      usage_scope: form.usage_scope,
      retention_class: form.retention_class,
      filtering_state: form.filtering_state,
      created_at: new Date().toISOString(),
      upstream_pipeline: upstream,
      genome: null,
      created_by_run_id: null,
    };
    void submit.run(() => assetsApi.register(caller, declared, bytes));
  }

  const recorded = submit.result as { asset_id: string; uri: string } | null;

  return (
    <Panel
      title={t("Register data")}
      note={t(
        "pick a file and this screen reads the rest from its bytes; the digest is computed here, so a conflict at completion means the object plane really holds something else",
      )}
    >
      <DeploymentNotice capability="object_upload" />
      <ProblemNote error={submit.error} />
      <form
        onSubmit={(event) => {
          event.preventDefault();
          register();
        }}
      >
        <Field
          label={t("file to register")}
          hint={t("a counts document the workflow can read, or an h5ad this deployment reports on instead")}
        >
          <input type="file" onChange={(event) => void pick(event.target.files?.[0])} />
        </Field>
        {fileName ? (
          <p className="muted mono">
            {fileName} · {bytes?.byteLength ?? 0} {t("bytes")} · sha256 {digest}
          </p>
        ) : null}
        {isH5ad ? (
          <Notice>
            {t(
              "This is an h5ad, and a browser cannot read HDF5. The reference converter runs as a command and refuses to guess the annotation: it maps specimen_id, donor_id and group onto the design row, keeps both sides of a lossy conversion, and drops every obs column nobody declared.",
            )}
            <p className="mono">
              .venv/bin/python scripts/convert_h5ad.py {fileName} --project-id {projectId} --genome-build …
              --annotation-source … --genome-version … --id-system …
            </p>
            <p>
              {t(
                "Its output is a JSON document plus a manifest in .workbench/admissions; registering that document is this same screen, with the file picked again.",
              )}{" "}
              <Link href={`/projects/${encodeURIComponent(projectId)}/plans`}>{t("what the plan expects")}</Link>
            </p>
          </Notice>
        ) : null}
        {tooBig ? (
          <Notice>
            {t(
              "This file is too large to preview in a browser, so nothing has been checked and nothing is registered. The digest above is real; the contents are not read.",
            )}
          </Notice>
        ) : null}
        {problem ? (
          <Notice>
            {t(
              "This file is not a document the workflow reads, so it is not registered. The reference input is the JSON product the converter writes, whose top level names the slot it answers for.",
            )}{" "}
            <span className="mono">{problem}</span>
          </Notice>
        ) : null}
        {preview ? (
          <dl className="facts">
            <dt>{t("slot")}</dt>
            <dd className="mono">
              {preview.slot ?? t("missing")}
              {preview.kind === "counts" ? null : ` — ${t("a run reads its root input from the counts slot")}`}
            </dd>
            <dt>{t("contract")}</dt>
            <dd className="mono">{`${preview.schemaVersion ?? "?"} · ${preview.transform ?? "?"}`}</dd>
            <dt>{t("cells")}</dt>
            <dd>{preview.cells.length}</dd>
            <dt>{t("genes")}</dt>
            <dd>{preview.genes.length}</dd>
            <dt>{t("donors")}</dt>
            <dd>
              {t("{donors} donors, {specimens} specimens, {groups} groups", {
                donors: distinct(preview.cells, "donor_id").length,
                specimens: distinct(preview.cells, "specimen_id").length,
                groups: distinct(preview.cells, "group").length,
              })}
            </dd>
            <dt>{t("identifier space")}</dt>
            <dd className="mono">
              {preview.idSystem ? `${preview.idSystem} ${preview.genomeVersion ?? ""}` : t("the document names none")}
            </dd>
          </dl>
        ) : null}
        {preview && preview.kind !== "counts" ? (
          <p className="muted">{t("The slot on this document is not counts, so no run can start from it.")}</p>
        ) : null}
        {preview?.missingDesign.length ? (
          <Notice>
            {t(
              "These design columns are empty on at least one cell, and every rarity and robustness judgement counts donors:",
            )}{" "}
            <span className="mono">{preview.missingDesign.join(", ")}</span>
          </Notice>
        ) : null}
        {preview && preview.duplicateKeys > 0 ? (
          <Notice>
            {t("{n} cell keys repeat; a matrix has to name each cell once.", { n: preview.duplicateKeys })}
          </Notice>
        ) : null}
        {preview && preview.transform && preview.transform !== "counts" ? (
          <Notice>
            {t("The reference pseudobulk step sums counts and refuses a matrix declared as")}{" "}
            <span className="mono">{preview.transform}</span>.
          </Notice>
        ) : null}
        {preview?.warnings.length ? (
          <ul className="steps">
            {preview.warnings.map((one) => (
              <li key={one} className="mono">
                {one}
              </li>
            ))}
          </ul>
        ) : null}

        <details>
          <summary>{t("Declaration fields")}</summary>
          <div className="grid">
            <Field label="privacy_tier" hint={t("defaults to the project tier")}>
              <select value={form.privacy_tier} onChange={set("privacy_tier")}>
                <option value="">{`${t(project.privacy_tier)} (${project.privacy_tier})`}</option>
                <option value="public">{`${t("public")} (public)`}</option>
                <option value="restricted">{`${t("restricted")} (restricted)`}</option>
                <option value="strict_local">{`${t("strict_local")} (strict_local)`}</option>
                <option value="hgr_controlled">{`${t("hgr_controlled")} (hgr_controlled)`}</option>
              </select>
            </Field>
            <Field
              label="usage_scope"
              hint={t("cross_project_allowed is what lets another project read this row at all")}
            >
              <select value={form.usage_scope} onChange={set("usage_scope")}>
                {(
                  ["this_project_only", "internal_improvement", "cross_project_allowed", "deliverable_visible"] as UsageScope[]
                ).map((one) => (
                  <option key={one} value={one}>
                    {`${t(one)} (${one})`}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="grid">
            <Field label="retention_class">
              <select value={form.retention_class} onChange={set("retention_class")}>
                {(["ephemeral", "run_linked", "deliverable", "archive"] as RetentionClass[]).map((one) => (
                  <option key={one} value={one}>
                    {`${t(one)} (${one})`}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="filtering_state"
              hint={t("unknown requires upstream pipeline metadata, so it is deliberately not the default guess here")}
            >
              <select value={form.filtering_state} onChange={set("filtering_state")}>
                {(
                  ["none", "barcode_filtered", "gene_filtered", "both_filtered", "unknown"] as FilteringState[]
                ).map((one) => (
                  <option key={one} value={one}>
                    {`${t(one)} (${one})`}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          {needsPipeline ? (
            <>
              <p className="muted">
                {t(
                  "An unknown filtering state is a statement that nobody can show the filtering was done, so the pipeline that produced the object becomes part of the record. All four fields go to the API verbatim.",
                )}
              </p>
              <div className="grid">
                <Field label={t("upstream pipeline name")}>
                  <input value={form.pipeline_name} onChange={set("pipeline_name")} />
                </Field>
                <Field label={t("version")}>
                  <input value={form.pipeline_version} onChange={set("pipeline_version")} />
                </Field>
              </div>
              <div className="grid">
                <Field label="reference_build">
                  <input value={form.reference_build} onChange={set("reference_build")} />
                </Field>
                <Field label="reference_annotation_source">
                  <input
                    value={form.reference_annotation_source}
                    onChange={set("reference_annotation_source")}
                  />
                </Field>
              </div>
            </>
          ) : null}
        </details>

        <button type="submit" disabled={unavailable || !preview || submit.busy || !pipelineWhole}>
          {submit.busy ? t("declaring, uploading, completing…") : t("Register this file")}
        </button>
        {!preview && bytes && !unavailable ? (
          <p className="muted">{t("Nothing is sent until this screen can read the file it is declaring.")}</p>
        ) : null}
      </form>
      {recorded ? (
        <Notice>
          {t("Recorded")} <span className="mono">{recorded.asset_id}</span> {t("at")}{" "}
          <span className="mono">{recorded.uri}</span> —{" "}
          <Link href={`/projects/${encodeURIComponent(projectId)}/plans`}>{t("build the plan around it")}</Link>
        </Notice>
      ) : null}
    </Panel>
  );
}
