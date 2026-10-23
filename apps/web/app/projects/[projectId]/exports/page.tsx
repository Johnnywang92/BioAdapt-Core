"use client";

import { useState } from "react";

import { Chip, Empty, Field, Loading, Notice, Panel, ProblemNote } from "@/components/ui";
import { DeploymentNotice, useDeployment } from "@/components/DeploymentStatus";
import { exports_ as exportsApi, intentKey, runs as runsApi, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync, useAutoRefresh, useSubmit } from "@/lib/useAsync";
import type {
  ExportEnvelope,
  ExportKind,
  ExportPreview,
  ExportRow,
  ExportStatus,
  FinalStatus,
  Page,
  RunListItem,
} from "@/lib/types";

const KINDS: ExportKind[] = ["tables", "figures", "code_bundle", "delivery_zip"];
const FINAL_STATUSES: FinalStatus[] = ["supported", "provisional", "conflict", "unknown", "rejected"];
const PAGE_SIZE = 50;
// The run list is read at the cap §9.7 allows, because this is now the screen a person picks a run from.
const RUN_PAGE_SIZE = 500;

/**
 * Delivery export (plan V2.0 sections 9.3, 9.7, 11.2).
 *
 * Pulling a large table out is a job, not a list request: the API answers 202 with an `export_id`, and
 * a download URL only exists once the artifact has been written and pointed at. A strict-local project
 * is refused before any of that, and the refusal is shown as the policy answer it is rather than as an
 * empty list — an export screen that looks "nothing to deliver" would hide the reason.
 *
 * Two things were added for the delivery side of 第三批. The filters are picked from lists the platform
 * reads (this project's runs, the card statuses) rather than typed, because a mis-typed `run_id` used to
 * turn into a 404 the person had to interpret. And the choice can be previewed: the counts come from rows
 * already filed, run through the same two gates queueing runs, so a delivery this project cannot have is
 * refused on this screen before a job row exists. The preview never claims to show the file — that is a
 * worker's output, and the panel below says in the deployment's own words whether a worker is connected.
 */
export default function ExportsPage({ params }: { params: { projectId: string } }) {
  const t = useT();
  const { caller, projectId, blocked } = useProjectScope(params.projectId);

  if (!caller) {
    return (
      <Panel title={t("Exports")} note={t("nothing is read until a token is set")}>
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  return (
    <>
      <QueueExport caller={caller} projectId={projectId} />
      <ExportList caller={caller} projectId={projectId} />
    </>
  );
}

function QueueExport({ caller, projectId }: { caller: Caller; projectId: string }) {
  const t = useT();
  const submit = useSubmit();
  const [kind, setKind] = useState<ExportKind>("tables");
  const [runId, setRunId] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [key, setKey] = useState(() => intentKey("export"));
  const [preview, setPreview] = useState<ExportPreview | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [previewing, setPreviewing] = useState(false);
  const runs = useAsync<Page<RunListItem>>(
    () => runsApi.list(caller, projectId, { limit: RUN_PAGE_SIZE }),
    [caller.token, projectId],
  );

  function filters() {
    const chosen: Record<string, unknown> = {};
    if (runId) chosen.run_id = runId;
    if (statusFilter) chosen.final_status = statusFilter;
    return chosen;
  }

  async function check() {
    setPreviewing(true);
    setPreviewError(null);
    try {
      setPreview(
        await exportsApi.preview(caller, projectId, {
          kind,
          ...(runId ? { run_id: runId } : {}),
          ...(statusFilter ? { final_status: statusFilter } : {}),
        }),
      );
    } catch (exc) {
      setPreview(null);
      setPreviewError(exc);
    } finally {
      setPreviewing(false);
    }
  }

  function queue() {
    void submit.run(() => exportsApi.queue(caller, projectId, kind, filters(), key));
  }

  const queued = submit.result as ExportEnvelope | null;

  return (
    <Panel
      title={t("Queue an export")}
      note={t("preview the choice first: the counts are rows already filed, not the file a worker would write")}
    >
      <DeploymentNotice capability="export_generation" />
      <ProblemNote error={submit.error} />
      {queued ? (
        <Notice>
          <span className="mono">{queued.export_id}</span> {t("is")} {t(queued.status)}.
          {" "}{t("The request was recorded. A download is available only after a worker generates the file; see the deployment status above.")}
        </Notice>
      ) : null}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          queue();
        }}
      >
        <div className="grid">
          <Field label={t("kind")}>
            <select value={kind} onChange={(event) => { setKind(event.target.value as ExportKind); setPreview(null); }}>
              {KINDS.map((one) => (
                <option key={one} value={one}>
                  {`${t(one)} (${one})`}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label={t("which run")}
            hint={t("optional; limits the artefact to one run's products. The list is this project's runs, so nothing has to be typed.")}
          >
            <ProblemNote error={runs.error} />
            <select value={runId} onChange={(event) => { setRunId(event.target.value); setPreview(null); }}>
              <option value="">{t("every run in this project")}</option>
              {(runs.data?.items ?? []).map((run) => (
                <option key={run.run_id} value={run.run_id}>
                  {`${run.run_id} · ${t(run.state)}`}
                </option>
              ))}
            </select>
            {runs.data?.next_offset != null ? (
              <p className="muted">
                {t("this project has more runs than one page, so the list stops at {n}; the run screen pages through the rest.", {
                  n: runs.data.items.length,
                })}
              </p>
            ) : null}
          </Field>
        </div>
        <div className="grid">
          <Field
            label={t("which conclusions")}
            hint={t("optional; only cards filed at this status. A status nobody picked asks for every card.")}
          >
            <select value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setPreview(null); }}>
              <option value="">{t("every status")}</option>
              {FINAL_STATUSES.map((one) => (
                <option key={one} value={one}>
                  {`${t(one)} (${one})`}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Idempotency-Key" hint={t("a resend of this click returns the original job")}>
            <input className="mono" value={key} onChange={(event) => setKey(event.target.value)} />
          </Field>
        </div>
        <p>
          {t("Query preview")}:{" "}
          <span className="mono">
            {t(kind)} · {runId || t("every run in this project")} · {statusFilter ? t(statusFilter) : t("every status")}
          </span>
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="secondary" disabled={previewing} onClick={() => void check()}>
            {previewing ? t("reading…") : t("preview what these filters select")}
          </button>
          <button type="submit" disabled={submit.busy}>
            {submit.busy ? t("queueing…") : t("Queue")}
          </button>
        </div>
      </form>
      <ProblemNote error={previewError} />
      {preview ? <PreviewRows preview={preview} /> : null}
    </Panel>
  );
}

/** The reach of the choice, read from the catalog: which line the two filters can move is stated per line. */
function PreviewRows({ preview }: { preview: ExportPreview }) {
  const t = useT();
  const statuses = Object.entries(preview.evidence_cards.by_status) as [FinalStatus, number][];
  const bytes = preview.data_assets.size_bytes;
  return (
    <div className="rows" style={{ marginTop: 12 }}>
      <p className="muted">
        {t(
          "these are rows stored in this project right now, counted by the filters shown above. They are not the file: a worker writes that, and until one runs nothing here has left the platform.",
        )}
      </p>
      <p>
        {t("cards this delivery would draw from")}: <strong>{preview.evidence_cards.matched}</strong>
        {statuses.length ? (
          <span className="muted">
            {" "}
            · {statuses.map(([status, n]) => `${t(status)} ${n}`).join(" · ")}
          </span>
        ) : null}
      </p>
      <p className="muted">
        {t("candidate sets")}: {preview.candidate_sets.matched} · {t("assets")}: {preview.data_assets.matched}
        {bytes ? <> · <span className="mono">{`${bytes} ${t("bytes")}`}</span></> : null}
      </p>
      <p className="muted">
        {t("the status filter moves the card line only, because it is a column of the card table; the run filter moves all three.")}
        {" "}{t("threshold set")}: <span className="mono">{preview.thresholds_version}</span>
      </p>
      {!preview.evidence_cards.matched ? (
        <Notice>{t("nothing matches this choice, so the job would have nothing to write. Widen the filters or re-read after a review is filed.")}</Notice>
      ) : null}
    </div>
  );
}

function ExportList({ caller, projectId }: { caller: Caller; projectId: string }) {
  const t = useT();
  const listed = useAsync<Page<ExportRow>>(
    () => exportsApi.list(caller, projectId, { limit: PAGE_SIZE }),
    [caller.token, projectId],
  );
  const deployment = useDeployment();

  const [detail, setDetail] = useState<ExportRow | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [live, setLive] = useState(false);
  useAutoRefresh(listed.reload, 5000, live);

  async function poll(row: ExportRow) {
    setError(null);
    try {
      setDetail(await exportsApi.read(caller, projectId, row.export_id));
    } catch (exc) {
      setError(exc);
    }
  }

  const rows = listed.data?.items ?? [];
  const countOf = (status: ExportStatus) => rows.filter((row) => row.status === status).length;
  const failed = rows.filter((row) => row.failure_reason);
  const generation = deployment.data?.export_generation;

  return (
    <Panel
      title={t("Exports")}
      note={t("a download link is handed out per read and expires; the row itself is the durable thing")}
      actions={
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            className={live ? undefined : "secondary"}
            aria-pressed={live}
            onClick={() => setLive(!live)}
            title={t("watching re-reads this list every five seconds; it changes nothing")}
          >
            {live ? t("watching") : t("watch this list")}
          </button>
          <button className="secondary" type="button" disabled={listed.loading} onClick={listed.reload}>
            {t("re-read")}
          </button>
        </div>
      }
    >
      <ProblemNote error={listed.error ?? error} />
      {rows.length ? (
        <div className="notice" role="status" aria-live="polite">
          <p style={{ margin: 0 }}>
            {t("this page holds {n} export rows", { n: rows.length })}:{" "}
            {`${t("QUEUED")} ${countOf("QUEUED")} · ${t("RUNNING")} ${countOf("RUNNING")} · ${t("READY")} ${countOf(
              "READY",
            )} · ${t("FAILED")} ${countOf("FAILED")}`}
          </p>
          {failed.length ? (
            <p style={{ margin: "6px 0 0" }}>
              {failed.map((row) => (
                <span key={row.export_id} className="mono">
                  {row.export_id}: {row.failure_reason}
                  {"  "}
                </span>
              ))}
            </p>
          ) : null}
          {countOf("QUEUED") && generation !== "configured" ? (
            <p style={{ margin: "6px 0 0" }}>
              {t(
                "a queued row here is the platform agreeing to a job, not a job in progress: this deployment reports export generation as {state}, so re-reading cannot produce a file.",
                { state: generation ?? "unknown" },
              )}
            </p>
          ) : null}
        </div>
      ) : null}
      <Loading on={listed.loading} />
      {detail ? <ExportDetail row={detail} /> : null}
      {listed.data && listed.data.items.length ? (
        <table>
          <thead>
            <tr>
              <th>{t("export")}</th>
              <th>{t("kind")}</th>
              <th>{t("status")}</th>
              <th>{t("what the filters chose")}</th>
              <th>{t("requested by")}</th>
              <th>{t("created")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {listed.data.items.map((row) => (
              <tr key={row.export_id}>
                <td className="mono">{row.export_id}</td>
                <td>{t(row.kind)}</td>
                <td>
                  <Chip title={row.status} status={statusTone(row.status)}>
                    {t(row.status)}
                  </Chip>
                </td>
                <td className="mono">{filterSummary(row.filters, t)}</td>
                <td className="mono">{row.requested_by}</td>
                <td>{row.created_at ?? "—"}</td>
                <td>
                  <button className="secondary" type="button" onClick={() => void poll(row)}>
                    {t("poll")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        !listed.loading && <Empty>{t("No export has been queued in this project.")}</Empty>
      )}
    </Panel>
  );
}

/** The job row's own filters, read back so a person can tell two queued jobs apart without a CLI. */
function filterSummary(filters: Record<string, unknown>, t: (english: string, vars?: Record<string, string | number>) => string): string {
  const parts = Object.entries(filters ?? {}).map(([name, value]) => `${name}=${String(value)}`);
  return parts.length ? parts.join(" · ") : t("no filter: every row in this project");
}

function ExportDetail({ row }: { row: ExportRow }) {
  const t = useT();
  return (
    <div className="panel">
      <dl className="facts">
        <dt>{t("export")}</dt>
        <dd className="mono">{row.export_id}</dd>
        <dt>{t("status")}</dt>
        <dd>
          <Chip title={row.status} status={statusTone(row.status)}>
            {t(row.status)}
          </Chip>
        </dd>
        <dt>{t("artefact")}</dt>
        <dd className="mono">{row.artifact_asset_id ?? t("not written yet")}</dd>
        <dt>{t("failure reason")}</dt>
        <dd>{row.failure_reason ?? "—"}</dd>
        <dt>{t("download")}</dt>
        <dd>
          {row.download_url ? (
            <>
              <a href={row.download_url}>{t("use the link")}</a>{" "}
              <span className="muted mono">
                {t("until")} {row.expires_at} ({row.ttl_seconds}s)
              </span>
            </>
          ) : (
            <span className="muted">{t("only a READY export has a link")}</span>
          )}
        </dd>
      </dl>
    </div>
  );
}

function statusTone(status: ExportStatus): string {
  if (status === "READY") return "supported";
  if (status === "FAILED") return "rejected";
  return "provisional";
}
