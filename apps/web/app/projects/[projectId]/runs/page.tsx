"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Chip, Empty, Field, Loading, Panel, ProblemNote } from "@/components/ui";
import { runs as runsApi, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync, useAutoRefresh } from "@/lib/useAsync";
import type { Page, RunListItem } from "@/lib/types";

const PAGE_SIZE = 50;

/**
 * The states a run can sit in, in the order a run usually passes through them.
 *
 * These are the server's enum values, not a UI's own buckets: the group headings below are navigation
 * labels only, and choosing one sends that exact string to `GET /runs?state=`. A run's state is never
 * composed here, because §9.2 makes the transition endpoint the only thing allowed to say where a run is.
 */
const STATE_BANDS: [string, string[]][] = [
  ["getting underway", ["CREATED", "PREFLIGHT_RUNNING", "PREFLIGHT_PASSED", "PLAN_DRAFTED", "PLAN_APPROVED"]],
  ["being computed", ["ANALYSIS_RUNNING", "CANDIDATE_DISCOVERY", "EVIDENCE_BUILDING", "TARGETED_ANALYSIS", "VALIDATION"]],
  ["waiting for a person", ["QC_REVIEW", "HUMAN_REVIEW", "ESCALATED_TO_HUMAN", "NEEDS_INPUT", "BLOCKED"]],
  ["stopped short", ["RETRYABLE_FAILURE", "POLICY_BLOCKED", "RESOURCE_EXCEEDED", "BUDGET_EXCEEDED", "FAILED"]],
  ["finished", ["REPORT_READY", "ARCHIVED"]],
];

export default function RunsPage({ params }: { params: { projectId: string } }) {
  return (
    <Suspense fallback={<Loading on />}>
      <Runs projectId={params.projectId} />
    </Suspense>
  );
}

function Runs({ projectId }: { projectId: string }) {
  const t = useT();
  const { caller, projectId: scoped, blocked } = useProjectScope(projectId);
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  // The filter lives in the address bar, so a tracker can send a colleague the link to "everything
  // that is waiting for a person" rather than to a page that happens to be showing it.
  const state = searchParams.get("state") ?? "";
  const [live, setLive] = useState(false);
  const [offset, setOffset] = useState(0);

  useEffect(() => setOffset(0), [state, scoped]);

  const listed = useAsync<Page<RunListItem>>(
    () =>
      caller
        ? runsApi.list(caller, scoped, {
            ...(state ? { state } : {}),
            limit: PAGE_SIZE,
            offset,
          })
        : Promise.resolve({ items: [], limit: 0, offset: 0 }),
    [caller?.token, scoped, state, offset],
  );
  useAutoRefresh(listed.reload, 5000, live);

  if (!caller) {
    return (
      <Panel title={t("Runs")} note={t("nothing is read until a token is set")}>
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  const filter = (next: string) => {
    const params = new URLSearchParams(searchParams.toString());
    if (next) params.set("state", next);
    else params.delete("state");
    router.replace(`${pathname}?${params.toString()}`);
  };

  const rows = listed.data?.items ?? [];
  const nextOffset = listed.data?.next_offset ?? null;

  return (
    <>
      <Panel
        title={t("Runs")}
        note={t(
          "one row per run, read from the catalog: the state is where the state machine left it, and the queue position is what the scheduler answered just now",
        )}
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
        <div className="grid">
          <Field
            label={t("state")}
            hint={t("the filter is the server's own enum; an empty choice asks for every state")}
          >
            <select value={state} onChange={(event) => filter(event.target.value)}>
              <option value="">{t("every state")}</option>
              {STATE_BANDS.map(([band, states]) => (
                <optgroup key={band} label={t(band)}>
                  {states.map((one) => (
                    <option key={one} value={one}>
                      {`${t(one)} (${one})`}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </Field>
          <Field label={t("showing")} hint={t("runs per page, read from the catalog in the server's order")}>
            <p className="muted" style={{ margin: "6px 0 0" }}>
              {t("{n} of this page, from position {offset}", { n: rows.length, offset })}
            </p>
          </Field>
        </div>
        <ProblemNote error={listed.error} />
        <Loading on={listed.loading} />
        {rows.length ? (
          <>
            <table>
              <thead>
                <tr>
                  <th>{t("run")}</th>
                  <th>{t("state")}</th>
                  <th>{t("plan")}</th>
                  <th>{t("queue position")}</th>
                  <th>{t("started")}</th>
                  <th>{t("go on with")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((run) => (
                  <tr key={run.run_id}>
                    <td>
                      <Link href={`/projects/${scoped}/runs/${encodeURIComponent(run.run_id)}`}>
                        <span className="mono">{run.run_id}</span>
                      </Link>
                    </td>
                    <td>
                      <Chip
                        title={run.state}
                        status={
                          run.state === "FAILED" || run.state === "POLICY_BLOCKED" || run.state === "BUDGET_EXCEEDED"
                            ? "rejected"
                            : "provisional"
                        }
                      >
                        {t(run.state)}
                      </Chip>
                    </td>
                    <td className="mono">
                      {run.plan_id} @ {run.plan_version}
                    </td>
                    <td>{run.queue_position === null ? t("not queued") : run.queue_position}</td>
                    <td className="mono">{run.started_at ?? "—"}</td>
                    <td>
                      <Link href={`/projects/${scoped}/runs/${encodeURIComponent(run.run_id)}`}>
                        {t("journal and gates")}
                      </Link>{" "}
                      <span className="muted">·</span>{" "}
                      <Link href={`/projects/${scoped}/queue?run_id=${encodeURIComponent(run.run_id)}`}>
                        {t("expert queue")}
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <button
                type="button"
                className="secondary"
                disabled={offset === 0}
                onClick={() =>
                  setOffset((current) => Math.max(0, current - (listed.data?.limit ?? PAGE_SIZE)))
                }
              >
                {t("previous page")}
              </button>
              <button
                type="button"
                className="secondary"
                disabled={nextOffset === null}
                onClick={() => nextOffset !== null && setOffset(nextOffset)}
              >
                {t("next page")}
              </button>
            </div>
          </>
        ) : (
          !listed.loading && (
            <Empty>
              {state
                ? t("No run in this project is in that state. The list is what the server returned, so nothing is hidden by the filter.")
                : t("This project has no runs yet.")}
            </Empty>
          )
        )}
      </Panel>
    </>
  );
}
