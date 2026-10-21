"use client";

import { useState } from "react";

import { Empty, Field, Loading, Notice, Panel, ProblemNote } from "@/components/ui";
import { assets as assetsApi, type Caller } from "@/lib/api";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync, useSubmit } from "@/lib/useAsync";
import type { DataAsset, FilteringState, Page, RetentionClass, UpstreamPipeline, UsageScope } from "@/lib/types";

const FILTERING: FilteringState[] = ["none", "barcode_filtered", "gene_filtered", "both_filtered", "unknown"];
const RETENTION: RetentionClass[] = ["ephemeral", "run_linked", "deliverable", "archive"];
const SCOPE: UsageScope[] = [
  "this_project_only",
  "internal_improvement",
  "cross_project_allowed",
  "deliverable_visible",
];

export default function DataPage({ params }: { params: { projectId: string } }) {
  const { caller, projectId, blocked } = useProjectScope(params.projectId);

  if (!caller) {
    return (
      <Panel title="Data" note="nothing is read until a token is set">
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  return (
    <>
      <Register caller={caller} projectId={projectId} />
      <Registered caller={caller} projectId={projectId} />
    </>
  );
}

/**
 * The three calls the API defines, in order (§5.9): declare, PUT the bytes to the signed URL, then
 * complete with the same declaration. The digest is computed here from the bytes the person picked,
 * so a 409 at completion means the object plane really holds something else — it cannot mean that a
 * number was typed in wrong somewhere upstream.
 */
function Register({ caller, projectId }: { caller: Caller; projectId: string }) {
  const submit = useSubmit();
  const [bytes, setBytes] = useState<ArrayBuffer | null>(null);
  const [fileName, setFileName] = useState("");
  const [digest, setDigest] = useState("");
  const [form, setForm] = useState({
    asset_id: "",
    format: "h5ad",
    schema_version: "1",
    privacy_tier: "",
    usage_scope: "this_project_only" as UsageScope,
    retention_class: "run_linked" as RetentionClass,
    filtering_state: "unknown" as FilteringState,
    content_fingerprint: "",
    pipeline_name: "",
    pipeline_version: "",
    reference_build: "",
    reference_annotation_source: "",
  });

  const set = (name: keyof typeof form) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [name]: event.target.value }));

  // §5.4: "unknown" is a statement that nobody can show the filtering was done, and it is only
  // admissible with the pipeline metadata that made it unknowable. The schema rejects the pair
  // otherwise, so the form asks for it rather than letting the person hit a 400 they were not warned
  // about.
  const needsPipeline = form.filtering_state === "unknown";
  const pipeline: UpstreamPipeline | null = needsPipeline
    ? {
        name: form.pipeline_name,
        version: form.pipeline_version,
        reference_build: form.reference_build,
        reference_annotation_source: form.reference_annotation_source,
      }
    : null;
  const pipelineWhole =
    !needsPipeline ||
    (form.pipeline_name.trim() !== "" &&
      form.pipeline_version.trim() !== "" &&
      form.reference_build.trim() !== "" &&
      form.reference_annotation_source.trim() !== "");

  async function pick(file: File | undefined) {
    if (!file) return;
    const buffer = await file.arrayBuffer();
    const hash = await crypto.subtle.digest("SHA-256", buffer);
    setBytes(buffer);
    setFileName(file.name);
    setDigest(hex(hash));
    setForm((current) => ({
      ...current,
      asset_id: current.asset_id || `as_${hex(hash).slice(0, 12)}`,
      content_fingerprint: current.content_fingerprint || hex(hash),
    }));
  }

  function register() {
    if (!bytes || !projectId) return;
    const declared: DataAsset = {
      asset_id: form.asset_id,
      project_id: projectId,
      // The API rewrites this to the key it derived; sending the placeholder keeps the contract whole
      // without letting the client claim a location it does not own.
      uri: "pending://signed-upload",
      file_sha256: digest,
      content_fingerprint: form.content_fingerprint || digest,
      format: form.format,
      schema_version: form.schema_version,
      size_bytes: bytes.byteLength,
      privacy_tier: (form.privacy_tier || "") as DataAsset["privacy_tier"],
      usage_scope: form.usage_scope,
      retention_class: form.retention_class,
      filtering_state: form.filtering_state,
      created_at: new Date().toISOString(),
      upstream_pipeline: pipeline,
      genome: null,
      created_by_run_id: null,
    };
    void submit.run(() => assetsApi.register(caller, declared, new Uint8Array(bytes)));
  }

  const recorded = submit.result as { asset_id: string; uri: string; key: string } | null;

  return (
    <Panel
      title="Register data"
      note="client-side direct upload is the only write path for object bytes: the API hands out a short-lived PUT signature for a key it derived itself"
    >
      <ProblemNote error={submit.error} />
      {recorded ? (
        <Notice>
          Recorded <span className="mono">{recorded.asset_id}</span> at <span className="mono">{recorded.uri}</span>
        </Notice>
      ) : null}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          register();
        }}
      >
        <Field label="object to upload" hint="the digest is computed from these bytes; a mismatch at completion is a conflict, not a warning">
          <input type="file" onChange={(event) => void pick(event.target.files?.[0])} />
        </Field>
        {fileName ? (
          <p className="muted mono">
            {fileName} · {bytes?.byteLength ?? 0} bytes · sha256 {digest}
          </p>
        ) : null}
        <div className="grid">
          <Field label="asset_id">
            <input className="mono" value={form.asset_id} onChange={set("asset_id")} />
          </Field>
          <Field label="format">
            <input value={form.format} onChange={set("format")} />
          </Field>
        </div>
        <div className="grid">
          <Field label="schema_version">
            <input value={form.schema_version} onChange={set("schema_version")} />
          </Field>
          <Field
            label="privacy_tier"
            hint="leave it at the project tier unless this object is genuinely less sensitive"
          >
            <select value={form.privacy_tier} onChange={set("privacy_tier")}>
              <option value="">choose</option>
              <option value="public">public</option>
              <option value="restricted">restricted</option>
              <option value="strict_local">strict_local</option>
              <option value="hgr_controlled">hgr_controlled</option>
            </select>
          </Field>
        </div>
        <div className="grid">
          <Field label="usage_scope" hint="cross_project_allowed is what lets another project read this row at all">
            <select value={form.usage_scope} onChange={set("usage_scope")}>
              {SCOPE.map((one) => (
                <option key={one} value={one}>
                  {one}
                </option>
              ))}
            </select>
          </Field>
          <Field label="retention_class">
            <select value={form.retention_class} onChange={set("retention_class")}>
              {RETENTION.map((one) => (
                <option key={one} value={one}>
                  {one}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field
          label="filtering_state"
          hint="unknown requires upstream pipeline metadata, so it is deliberately not the default guess here"
        >
          <select value={form.filtering_state} onChange={set("filtering_state")}>
            {FILTERING.map((one) => (
              <option key={one} value={one}>
                {one}
              </option>
            ))}
          </select>
        </Field>
        {needsPipeline ? (
          <>
            <p className="muted">
              This object is declared unfilterable-as-known, so the pipeline that produced it becomes part of the
              record. All four fields go to the API verbatim.
            </p>
            <div className="grid">
              <Field label="upstream pipeline name">
                <input value={form.pipeline_name} onChange={set("pipeline_name")} />
              </Field>
              <Field label="version">
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
        <Field label="content_fingerprint" hint="defaults to the file digest; a dataset-level fingerprint may differ">
          <input className="mono" value={form.content_fingerprint} onChange={set("content_fingerprint")} />
        </Field>
        <button
          type="submit"
          disabled={!bytes || submit.busy || !form.asset_id || !form.privacy_tier || !pipelineWhole}
        >
          {submit.busy ? "declaring, uploading, completing…" : "Register"}
        </button>
      </form>
    </Panel>
  );
}

function Registered({ caller, projectId }: { caller: Caller; projectId: string }) {
  const listed = useAsync<Page<DataAsset>>(
    () => assetsApi.list(caller, projectId, { limit: 100 }),
    [caller.token, projectId],
  );
  const [download, setDownload] = useState<{ asset_id: string; url: string; expires_at: string } | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function askFor(assetId: string) {
    setError(null);
    try {
      const grant = await assetsApi.download(caller, projectId, assetId);
      setDownload({ asset_id: grant.asset_id, url: grant.download_url, expires_at: grant.expires_at });
    } catch (exc) {
      setError(exc);
    }
  }

  return (
    <Panel
      title="Registered assets"
      note="a download link is a short-lived signature, handed out per request and never stored on the row"
      actions={
        <button className="secondary" type="button" onClick={listed.reload}>
          re-read
        </button>
      }
    >
      <ProblemNote error={listed.error ?? error} />
      <Loading on={listed.loading} />
      {download ? (
        <Notice>
          Signed for <span className="mono">{download.asset_id}</span> until{" "}
          <span className="mono">{download.expires_at}</span> —{" "}
          <a href={download.url}>use the link</a>
        </Notice>
      ) : null}
      {listed.data && listed.data.items.length ? (
        <table>
          <thead>
            <tr>
              <th>asset</th>
              <th>format</th>
              <th>tier</th>
              <th>scope</th>
              <th>retention</th>
              <th>filtering</th>
              <th>bytes</th>
              <th>produced by</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {listed.data.items.map((asset) => (
              <tr key={asset.asset_id}>
                <td className="mono">{asset.asset_id}</td>
                <td>{asset.format}</td>
                <td>{asset.privacy_tier}</td>
                <td>{asset.usage_scope}</td>
                <td>{asset.retention_class}</td>
                <td>{asset.filtering_state}</td>
                <td>{asset.size_bytes}</td>
                <td className="mono">{asset.created_by_run_id ?? "registered by hand"}</td>
                <td>
                  <button className="secondary" type="button" onClick={() => void askFor(asset.asset_id)}>
                    download
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        !listed.loading && <Empty>Nothing is registered in this project yet.</Empty>
      )}
    </Panel>
  );
}

function hex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
