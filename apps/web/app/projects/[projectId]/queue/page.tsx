"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Chip, Empty, Field, Loading, Notice, Panel, ProblemNote, StatusChip } from "@/components/ui";
import { findings, runs as runsApi, type Caller } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync, useDebounced, useSubmit } from "@/lib/useAsync";
import type { CardScore, DraftResponse, ExpertQueue, Page, RunListItem } from "@/lib/types";

/**
 * The expert queue (plan V2.0 section 13.4).
 *
 * The order is not a UI decision. `impact` and `uncertainty` are read off the draft and its gate
 * verdict by the service, and the ceiling that drives `uncertainty` is a gate reading of stored
 * numbers, so this screen can only show what re-deriving the run produced. What is not in the queue
 * is still shown, because §13.4 files the overflow as `observed` rather than dropping it - a low
 * score is an appendix entry, not a disappearance.
 */
export default function QueuePage({ params }: { params: { projectId: string } }) {
  return (
    <Suspense fallback={<Loading on />}>
      <Queues projectId={params.projectId} />
    </Suspense>
  );
}

function Queues({ projectId }: { projectId: string }) {
  const t = useT();
  const { caller, projectId: scoped, blocked } = useProjectScope(projectId);
  // A run page links here with the run it is looking at; without that the screen would queue a
  // different run than the one the person clicked away from.
  const askedFor = useSearchParams().get("run_id") ?? "";
  const [runId, setRunId] = useState(askedFor);

  useEffect(() => {
    setRunId(askedFor);
  }, [askedFor]);

  const listed = useAsync<Page<RunListItem>>(
    () => (caller ? runsApi.list(caller, scoped, { limit: 50 }) : Promise.resolve({ items: [], limit: 0, offset: 0 })),
    [caller?.token, scoped],
  );

  useEffect(() => {
    if (!runId && listed.data?.items.length) setRunId(listed.data.items[0].run_id);
  }, [listed.data, runId]);

  if (!caller) {
    return (
      <Panel title={t("Expert queue")} note={t("nothing is read until a token is set")}>
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  return (
    <>
      <Panel
        title={t("Which run")}
        note={t(
          "cards are drafted from the products of one run; a cluster label is only an alias inside that run (§5.6)",
        )}
      >
        <ProblemNote error={listed.error} />
        <Field label={t("run")}>
          <select value={runId} onChange={(event) => setRunId(event.target.value)}>
            {listed.data?.items.map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {run.run_id} · {t(run.state)}
              </option>
            ))}
          </select>
        </Field>
        {!listed.loading && !listed.data?.items.length ? <Empty>{t("No run to queue yet.")}</Empty> : null}
      </Panel>
      {runId ? <Queue caller={caller} projectId={scoped} runId={runId} /> : null}
    </>
  );
}

function Queue({ caller, projectId, runId }: { caller: Caller; projectId: string; runId: string }) {
  const t = useT();
  const draft = useSubmit();
  const [clientAsk, setClientAsk] = useState("");
  const [limit, setLimit] = useState("");
  // The queue is re-derived from the run's products (§13.4), so it is read once the two filters
  // settle rather than on every keystroke. The fields still hold what the person typed.
  const askSettled = useDebounced(clientAsk);
  const limitSettled = useDebounced(limit);

  const queue = useAsync<ExpertQueue>(
    () =>
      findings.queue(caller, projectId, runId, {
        client_ask: askSettled
          .split(",")
          .map((one) => one.trim())
          .filter(Boolean),
        limit: limitSettled ? Number(limitSettled) : undefined,
      }),
    [caller.token, projectId, runId, askSettled, limitSettled],
  );

  const scores = new Map<string, CardScore>(
    (queue.data?.scores ?? []).map((score) => [score.card_id, score]),
  );
  const drafted = draft.result as DraftResponse | null;

  return (
    <>
      <Panel
        title={t("Draft the cards")}
        note={t(
          "drafting files cards whose hypothesis is still unknown; a card a person has already acted on is refused, not overwritten (§9.1)",
        )}
      >
        <ProblemNote error={draft.error} />
        {drafted ? (
          <Notice>
            {t("{n} cards filed for run", { n: drafted.cards.length })}{" "}
            <span className="mono">{drafted.run_id}</span> {t("against thresholds")}{" "}
            <span className="mono">{drafted.thresholds_version}</span>. {t("Ceilings:")}{" "}
            {drafted.cards.map((one) => `${one.card_id.slice(-6)}→${t(one.ceiling)}`).join(", ")}
          </Notice>
        ) : null}
        <button type="button" disabled={draft.busy} onClick={() => void draft.run(() => findings.draft(caller, projectId, runId))}>
          {draft.busy ? t("drafting…") : t("Draft cards for this run")}
        </button>
        <p className="muted">
          {t(
            "The queue below is re-derived from the drafts, so it cannot drift from the run that produced them. Drafting needs the write role; reading it does not.",
          )}
        </p>
      </Panel>

      <Panel
        title={t("Queue")}
        note={t("score = impact (0-3) × uncertainty (0-3) + client ask (0/2)")}
        actions={
          <div style={{ display: "flex", gap: 8 }}>
            <button className="secondary" type="button" onClick={queue.reload}>
              {t("re-read")}
            </button>
          </div>
        }
      >
        <div className="grid">
          <Field
            label="client_ask"
            hint={t("candidate ids the project named as ones it is asking about; each adds 2")}
          >
            <input className="mono" value={clientAsk} onChange={(event) => setClientAsk(event.target.value)} />
          </Field>
          <Field label={t("limit")} hint={t("the plan's own cap by default (§13.4: 10 cards per project queue)")}>
            <input value={limit} onChange={(event) => setLimit(event.target.value)} inputMode="numeric" />
          </Field>
        </div>
        <ProblemNote error={queue.error} />
        <Loading on={queue.loading} />
        {queue.data ? (
          <>
            <p className="muted">
              {t(" {n} drafted", { n: queue.data.drafted })}
              {t(" · {n} in the queue", { n: queue.data.queue.length })}
              {t(" · {n} filed as observed", { n: queue.data.observed.length })}
              {t(" · cap {cap}", { cap: queue.data.limit })}
              {t(" · rules")} <span className="mono">{queue.data.rule_version}</span> {t("on thresholds")}{" "}
              <span className="mono">{queue.data.thresholds_version}</span>
            </p>
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>{t("population")}</th>
                  <th>{t("impact")}</th>
                  <th>{t("uncertainty")}</th>
                  <th>{t("ask")}</th>
                  <th>{t("score")}</th>
                  <th>{t("why this place")}</th>
                </tr>
              </thead>
              <tbody>
                {queue.data.queue.map((cardId, index) => {
                  const score = scores.get(cardId);
                  return (
                    <tr key={cardId}>
                      <td>{index + 1}</td>
                      <td>
                        <Link href={`/projects/${projectId}/candidates/${encodeURIComponent(score?.candidate_id ?? "")}`}>
                          {score?.display_name ?? cardId}
                        </Link>
                      </td>
                      <td>{score?.impact ?? "—"}</td>
                      <td>{score?.uncertainty ?? "—"}</td>
                      <td>{score?.client_ask ? <Chip status="supported">{t("yes")}</Chip> : t("no")}</td>
                      <td>{score?.score ?? "—"}</td>
                      <td>
                        <ul className="evidence-list">
                          {(score?.reasons ?? []).map((reason) => (
                            <li key={reason} className="muted">
                              {reason}
                            </li>
                          ))}
                        </ul>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        ) : null}
      </Panel>

      {queue.data?.observed.length ? (
        <Panel
          title={t("Filed as observed")}
          note={t(
            "over the cap, or below the score floor: kept for the report appendix, not shown to an expert this week (§13.4)",
          )}
        >
          <div className="rows">
            {queue.data.observed.map((cardId) => {
              const score = scores.get(cardId);
              return (
                <div key={cardId}>
                  <Link href={`/projects/${projectId}/candidates/${encodeURIComponent(score?.candidate_id ?? "")}`}>
                    {score?.display_name ?? cardId}
                  </Link>{" "}
                  <span className="muted">
                    {t("score")} {score?.score ?? "—"} · {score?.reasons[0] ?? t("no reason recorded")}
                  </span>
                </div>
              );
            })}
          </div>
        </Panel>
      ) : null}

      {drafted?.cards.some((one) => one.blocking_risks.length || !one.support_met) ? (
        <Panel
          title={t("What the gates already know")}
          note={t("a ceiling is not a reviewer's caution; it is a refusal the numbers force")}
        >
          {drafted.cards
            .filter((one) => !one.support_met || one.blocking_risks.length)
            .map((one) => (
              <div key={one.card_id} className="rows" style={{ marginBottom: 12 }}>
                <div>
                  <StatusChip status={one.ceiling} /> <span className="mono">{one.card_id}</span>{" "}
                  <span className="muted">{one.candidate_id}</span>
                </div>
                {one.gate_reasons.map((reason) => (
                  <p className="muted" key={reason}>
                    {reason}
                  </p>
                ))}
                {one.underpowered_design ? (
                  <p>{t("the design is under-powered for the claim this card would support")}</p>
                ) : null}
                {one.blocking_risks.map((risk) => (
                  <p key={risk}>
                    {t("blocking risk:")} {risk}
                  </p>
                ))}
              </div>
            ))}
        </Panel>
      ) : null}
    </>
  );
}
