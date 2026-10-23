"use client";

import type { ReactNode } from "react";

import { Loading, Panel, ProblemNote } from "@/components/ui";
import { stats as statsApi } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync } from "@/lib/useAsync";
import type { ProjectStats } from "@/lib/types";

/**
 * The project's own numbers (plan V2.0 section 5.1).
 *
 * Every value here comes from one aggregate read, not from adding up list pages: those cap at 500
 * rows and report no total, so a screen that counted what it received would quietly understate a
 * large project. The three panels follow the pipeline order the nav uses - what is held, what the
 * analysis produced, what the process spent and is waiting on.
 */
export default function StatsPage({ params }: { params: { projectId: string } }) {
  const t = useT();
  const { caller, projectId, blocked } = useProjectScope(params.projectId);
  const read = useAsync<ProjectStats | null>(
    () => (caller ? statsApi.read(caller, projectId) : Promise.resolve(null)),
    [caller?.token, projectId],
  );

  if (!caller) {
    return (
      <Panel title={t("Statistics")} note={t("nothing is read until a token is set")}>
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  const stats = read.data;
  return (
    <>
      <Panel
        title={t("Statistics")}
        note={t(
          "counts and sums read from the catalog in one request; nothing here is counted from a page of rows",
        )}
        actions={
          <button className="secondary" type="button" onClick={read.reload}>
            {t("re-read")}
          </button>
        }
      >
        <ProblemNote error={read.error} />
        <Loading on={read.loading && !stats} />
        {stats ? (
          <dl className="facts">
            <dt>{t("created")}</dt>
            <dd>{stats.project.created_at ?? "—"}</dd>
            <dt>{t("thresholds")}</dt>
            <dd>
              <span className="mono">{stats.project.thresholds_version}</span>
              {" · "}
              {stats.project.thresholds_confirmed ? (
                t("confirmed")
              ) : (
                <span className="chip">{t("unconfirmed stage-0 cut set · sign-off and export are closed (§1.5)")}</span>
              )}
            </dd>
            <dt>{t("review budget")}</dt>
            <dd>
              {stats.project.review_load_budget_hours_per_week} {t("h/week")}
            </dd>
            <dt>{t("read at")}</dt>
            <dd>{formatTimestamp(stats.generated_at)}</dd>
          </dl>
        ) : null}
      </Panel>

      {stats ? (
        <>
          <DataPanel stats={stats} />
          <AnalysisPanel stats={stats} />
          <ProcessPanel stats={stats} />
        </>
      ) : null}
    </>
  );
}

function DataPanel({ stats }: { stats: ProjectStats }) {
  const t = useT();
  const { assets, plans } = stats.data;
  return (
    <Panel
      title={t("Data")}
      note={t("assets and plans this project holds, counted as the index files them")}
    >
      <div className="kpi-grid">
        <Kpi label={t("assets")} value={formatCount(assets.total)} />
        <Kpi
          label={t("stored bytes")}
          value={formatBytes(assets.size_bytes)}
          hint={t("{hand} registered by hand · {run} produced by runs", {
            hand: formatCount(assets.registered_by_hand),
            run: formatCount(assets.from_runs),
          })}
        />
        <Kpi
          label={t("plan versions")}
          value={formatCount(plans.versions)}
          hint={t("{n} distinct plans", { n: formatCount(plans.distinct) })}
        />
      </div>
      <div className="stat-grid">
        <Breakdown title={t("retention class")} entries={Object.entries(assets.by_retention_class)} />
        <Breakdown title={t("usage scope")} entries={Object.entries(assets.by_usage_scope)} />
        <Breakdown title={t("filtering state")} entries={Object.entries(assets.by_filtering_state)} />
      </div>
    </Panel>
  );
}

function AnalysisPanel({ stats }: { stats: ProjectStats }) {
  const t = useT();
  const { runs, candidates, cards, reviews } = stats.analysis;
  return (
    <Panel
      title={t("Analysis")}
      note={t("runs, candidate sets and evidence cards as the index holds them")}
    >
      <div className="kpi-grid">
        <Kpi
          label={t("runs")}
          value={formatCount(runs.total)}
          hint={t("{active} active · {closed} closed", {
            active: formatCount(runs.active),
            closed: formatCount(runs.closed),
          })}
        />
        <Kpi
          label={t("candidate sets")}
          value={formatCount(candidates.total)}
          hint={t("{cells} cells · {donors} donor assignments", {
            cells: formatCount(candidates.cells),
            donors: formatCount(candidates.donor_assignments),
          })}
        />
        <Kpi
          label={t("evidence cards")}
          value={formatCount(cards.total)}
          hint={t("{reviewed} reviewed · {signed} signed off · {reviews} review decisions", {
            reviewed: formatCount(cards.reviewed),
            signed: formatCount(cards.signed_off),
            reviews: formatCount(reviews.total),
          })}
        />
      </div>
      <div className="stat-grid">
        <Breakdown title={t("run states")} entries={Object.entries(runs.by_state)} />
        <Breakdown title={t("card status")} entries={Object.entries(cards.by_status)} />
      </div>
      <p className="stat-note">
        {t(
          "a run is active until it reaches REPORT_READY, ARCHIVED or FAILED - the same set the launch quota counts; a card is reviewed once a review decision is on record, and signed off once its sign_off is no longer not_signed",
        )}
      </p>
    </Panel>
  );
}

function ProcessPanel({ stats }: { stats: ProjectStats }) {
  const t = useT();
  const { durations, retries, usage, approvals, exports } = stats.process;
  return (
    <Panel
      title={t("Runs and processing")}
      note={t("the journal's retries and spend, the gates this project requested, and the exports it queued")}
    >
      <div className="kpi-grid">
        <Kpi
          label={t("average run duration")}
          value={formatSeconds(durations.average_seconds)}
          hint={t("median {value} · {n} runs with both timestamps", {
            value: formatSeconds(durations.median_seconds),
            n: formatCount(durations.runs),
          })}
        />
        <Kpi
          label={t("retries")}
          value={formatCount(retries.node + retries.business)}
          hint={t("{node} node · {business} business", {
            node: formatCount(retries.node),
            business: formatCount(retries.business),
          })}
        />
        <Kpi
          label={t("compute minutes")}
          value={formatCount(usage.compute_minutes)}
          hint={t("model cost {cost}", { cost: formatCost(usage.model_cost_usd) })}
        />
        <Kpi
          label={t("pending approvals")}
          value={formatCount(approvals.pending)}
          hint={t("{overdue} overdue · average decision {duration}", {
            overdue: formatCount(approvals.overdue),
            duration: formatSeconds(approvals.average_decision_seconds),
          })}
        />
        <Kpi label={t("export jobs")} value={formatCount(exports.total)} />
      </div>
      <div className="stat-grid">
        <Breakdown title={t("approvals by kind")} entries={Object.entries(approvals.by_kind)} />
        <Breakdown title={t("exports by status")} entries={Object.entries(exports.by_status)} />
        <Breakdown title={t("exports by kind")} entries={Object.entries(exports.by_kind)} />
      </div>
      <p className="stat-note">
        {t(
          "durations come only from runs that recorded both a start and a finish; the journal records usage per node, not a node's own wall clock",
        )}
      </p>
      <p className="stat-note">
        {t(
          "not on this screen: a worker's health and the queue's depth. No heartbeat is stored, and the reference scheduler keeps its queue in memory, so neither can be read from the catalog.",
        )}
      </p>
    </Panel>
  );
}

function Kpi({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="kpi">
      <span className="kpi__label">{label}</span>
      <strong className="kpi__value">{value}</strong>
      {hint ? <span className="kpi__hint">{hint}</span> : null}
    </div>
  );
}

function Breakdown({ title, entries }: { title: string; entries: [string, number][] }) {
  const t = useT();
  const total = entries.reduce((sum, [, count]) => sum + count, 0);
  return (
    <div className="stat-block">
      <h3>{title}</h3>
      {entries.length ? (
        <ul className="meter-list">
          {entries.map(([key, count]) => (
            <li key={key}>
              <div className="meter-list__head">
                <span title={key}>{t(key)}</span>
                <span className="mono">{formatCount(count)}</span>
              </div>
              <div className="meter" aria-hidden="true">
                <span style={{ width: `${total ? (count / total) * 100 : 0}%` }} />
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">{t("none recorded")}</p>
      )}
    </div>
  );
}

function formatCount(value: number): string {
  return value.toLocaleString();
}

function formatBytes(bytes: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return index === 0 ? `${value} B` : `${value.toFixed(1)} ${units[index]}`;
}

function formatSeconds(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${(seconds / 60).toFixed(1)} min`;
  if (seconds < 172800) return `${(seconds / 3600).toFixed(1)} h`;
  return `${(seconds / 86400).toFixed(1)} d`;
}

function formatCost(value: number): string {
  if (value === 0) return "$0.00";
  return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
