"use client";

import { useState } from "react";

import { Chip, Empty, Field, Loading, Notice, Panel, ProblemNote } from "@/components/ui";
import { exports_ as exportsApi, intentKey, projects as projectsApi, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync, useSubmit } from "@/lib/useAsync";
import type { ExportEnvelope, ExportKind, ExportRow, ExportStatus, Page, Project } from "@/lib/types";

const KINDS: ExportKind[] = ["tables", "figures", "code_bundle", "delivery_zip"];

/**
 * Delivery export (plan V2.0 sections 9.3, 9.7, 11.2).
 *
 * Pulling a large table out is a job, not a list request: the API answers 202 with an `export_id`, and
 * a download URL only exists once the artifact has been written and pointed at. A strict-local project
 * is refused before any of that, and the refusal is shown as the policy answer it is rather than as an
 * empty list — an export screen that looks "nothing to deliver" would hide the reason.
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

  function queue() {
    const filters: Record<string, unknown> = {};
    if (runId.trim()) filters.run_id = runId.trim();
    if (statusFilter.trim()) filters.final_status = statusFilter.trim();
    void submit.run(() => exportsApi.queue(caller, projectId, kind, filters, key));
  }

  const queued = submit.result as ExportEnvelope | null;

  return (
    <Panel
      title={t("Queue an export")}
      note={t("filters narrow the artefact; an unknown run id is refused rather than answered with everything")}
    >
      <ProblemNote error={submit.error} />
      {queued ? (
        <Notice>
          <span className="mono">{queued.export_id}</span> {t("is")} {t(queued.status)} — {t("poll")}{" "}
          <span className="mono">{queued.poll}</span> {t("until it reports READY.")}
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
            <select value={kind} onChange={(event) => setKind(event.target.value as ExportKind)}>
              {KINDS.map((one) => (
                <option key={one} value={one}>
                  {`${t(one)} (${one})`}
                </option>
              ))}
            </select>
          </Field>
          <Field label="run_id" hint={t("optional; limits the artefact to one run's products")}>
            <input className="mono" value={runId} onChange={(event) => setRunId(event.target.value)} />
          </Field>
        </div>
        <div className="grid">
          <Field label="final_status" hint={t("optional; e.g. only the cards that reached supported")}>
            <input value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} />
          </Field>
          <Field label="Idempotency-Key" hint={t("a resend of this click returns the original job")}>
            <input className="mono" value={key} onChange={(event) => setKey(event.target.value)} />
          </Field>
        </div>
        <button type="submit" disabled={submit.busy}>
          {submit.busy ? t("queueing…") : t("Queue")}
        </button>
      </form>
    </Panel>
  );
}

function ExportList({ caller, projectId }: { caller: Caller; projectId: string }) {
  const t = useT();
  const listed = useAsync<Page<ExportRow>>(
    () => exportsApi.list(caller, projectId, { limit: 50 }),
    [caller.token, projectId],
  );
  const tier = useAsync<Project>(
    () => projectsApi.read(caller, projectId),
    [caller.token, projectId],
  );

  const [detail, setDetail] = useState<ExportRow | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function poll(row: ExportRow) {
    setError(null);
    try {
      setDetail(await exportsApi.read(caller, projectId, row.export_id));
    } catch (exc) {
      setError(exc);
    }
  }

  return (
    <Panel
      title={t("Exports")}
      note={t("a download link is handed out per read and expires; the row itself is the durable thing")}
      actions={
        <button className="secondary" type="button" onClick={listed.reload}>
          {t("re-read")}
        </button>
      }
    >
      <ProblemNote error={listed.error ?? error} />
      {tier.data?.privacy_tier === "strict_local" || tier.data?.privacy_tier === "hgr_controlled" ? (
        <div className="notice">
          {t(
            "This project is {tier}: the export route refuses it whatever the role of the caller (§5.9). Delivery happens from the local side, so nothing is queued here.",
            { tier: t(tier.data.privacy_tier) },
          )}
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
