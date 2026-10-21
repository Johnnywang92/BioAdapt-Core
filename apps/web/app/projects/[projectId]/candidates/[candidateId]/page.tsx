"use client";

import Link from "next/link";
import { useState } from "react";

import { Chip, Empty, Field, Loading, Notice, Panel, ProblemNote, StatusChip } from "@/components/ui";
import { findings, type Caller } from "@/lib/api";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync, useSubmit } from "@/lib/useAsync";
import type {
  CardEvidenceRead,
  Evidence,
  EvidenceCard,
  FinalStatus,
  ReviewDecision,
  ReviewEvent,
  ReviewScope,
  SignOff,
} from "@/lib/types";

const DECISIONS: ReviewDecision[] = [
  "approve",
  "revise",
  "return_for_analysis",
  "reject",
  "mark_conflict",
  "mark_unknown",
];
const SCOPES: ReviewScope[] = ["card", "field", "membership"];
const SIGN_OFFS: SignOff[] = ["full", "conditional", "not_signed"];

/**
 * One card, one screen (plan V2.0 section 13.4).
 *
 * The design target is 25 minutes a card, so the first screen holds exactly what a verdict needs: the
 * card text, two supporting records, one counter-record. Literature, the rest of the evidence, the
 * provenance table and the trail are on demand. Nothing here offers a way to write a number by hand:
 * a revision goes through the review route, which re-labels the field as human-sourced and keeps the
 * original value reviewable (§5.4), and a model never appears as an author of a value.
 */
export default function CardPage({ params }: { params: { projectId: string; candidateId: string } }) {
  const { caller, projectId, blocked } = useProjectScope(params.projectId);
  const candidateId = decodeURIComponent(params.candidateId);

  if (!caller) {
    return (
      <Panel title="Evidence card" note="nothing is read until a token is set">
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  return <Card caller={caller} projectId={projectId} candidateId={candidateId} />;
}

function Card({ caller, projectId, candidateId }: { caller: Caller; projectId: string; candidateId: string }) {
  const read = useAsync<CardEvidenceRead>(
    () => findings.evidence(caller, projectId, { candidate_id: candidateId }),
    [caller.token, projectId, candidateId],
  );
  const card = read.data?.evidence_card ?? null;
  const trail = useAsync<{ card_id: string; reviews: ReviewEvent[] }>(
    () => (card ? findings.reviews(caller, projectId, card.card_id) : Promise.resolve({ card_id: "", reviews: [] })),
    [caller.token, projectId, card?.card_id],
  );
  const review = useSubmit();
  const signOff = useSubmit();

  function refresh() {
    read.reload();
    trail.reload();
  }

  if (read.loading && !card) {
    return (
      <Panel title="Evidence card">
        <Loading on />
      </Panel>
    );
  }
  if (!card) {
    return (
      <Panel title="Evidence card" note="the card is read through the candidate that produced it, never through a cluster label">
        <ProblemNote error={read.error} />
      </Panel>
    );
  }

  const supporting = card.supporting_evidence_ids
    .map((id) => card.evidence[id])
    .filter((one): one is Evidence => Boolean(one))
    .sort((a, b) => Number(b.partition === "confirmation") - Number(a.partition === "confirmation"));
  const contradicting = card.contradicting_evidence_ids
    .map((id) => card.evidence[id])
    .filter((one): one is Evidence => Boolean(one));
  const triggers = card.trigger_signal_ids.map((id) => card.evidence[id]).filter(Boolean) as Evidence[];
  const literature = [...supporting, ...contradicting].filter((one) => one.source.doi || one.source.pmid);

  return (
    <>
      <Panel
        title={`${card.display_name} · ${card.card_id}`}
        note={`run ${card.run_id} · candidate ${card.candidate_id} · support rule ${card.support_rule_version} · thresholds ${card.thresholds_version}`}
        actions={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <StatusChip status={card.final_status} />
            <button className="secondary" type="button" onClick={refresh}>
              re-read
            </button>
          </div>
        }
      >
        <ProblemNote error={read.error} />
        <div className="card-screen">
          <div className="card-body">
            <dl className="facts">
              <dt>claimed</dt>
              <dd>
                {card.candidate_label ?? (
                  <span className="muted">
                    no label — hypothesis {card.hypothesis_type}
                    {card.unknown_reason ? `, reason ${card.unknown_reason}` : ""}
                  </span>
                )}
              </dd>
              <dt>label level</dt>
              <dd>
                {card.label_level}
                {card.ontology.term_id ? (
                  <span className="mono">
                    {" "}
                    · {card.ontology.ontology_name} {card.ontology.term_id} @ {card.ontology.ontology_version}
                  </span>
                ) : null}
              </dd>
              <dt>candidate types</dt>
              <dd>{card.candidate_types.join(", ") || "none"}</dd>
              <dt>status reason</dt>
              <dd>{card.status_reason ?? "—"}</dd>
              <dt>artifact screening</dt>
              <dd>
                {card.artifact_screening
                  ? Object.entries(card.artifact_screening)
                      .map(([risk, item]) => `${risk}: ${item.result}`)
                      .join(" · ")
                  : "not screened on this card"}
              </dd>
              <dt>composition</dt>
              <dd>{preview(card.composition_check)}</dd>
              <dt>stability</dt>
              <dd>{card.stability_summary ? preview(card.stability_summary) : "no perturbation summary"}</dd>
              <dt>novelty vs coverage</dt>
              <dd>{card.novelty_vs_coverage ? preview(card.novelty_vs_coverage) : "—"}</dd>
              <dt>cross modal</dt>
              <dd>{card.cross_modal_status}</dd>
            </dl>

            <h3>Two records that carry it</h3>
            <ul className="evidence-list">
              {supporting.slice(0, 2).map((one) => (
                <EvidenceRow key={one.evidence_id} evidence={one} card={card} />
              ))}
              {!supporting.length ? <li className="muted">nothing is listed as supporting evidence</li> : null}
            </ul>

            <h3>The record against it</h3>
            <ul className="evidence-list">
              {contradicting.slice(0, 1).map((one) => (
                <EvidenceRow key={one.evidence_id} evidence={one} card={card} contra />
              ))}
              {!contradicting.length ? <li className="muted">no counter-record is filed on this card</li> : null}
            </ul>

            {literature.length || supporting.length > 2 || contradicting.length > 1 || triggers.length ? (
              <details>
                <summary>the rest, and the literature ({literature.length + Math.max(0, supporting.length - 2)} items)</summary>
                <ul className="evidence-list">
                  {supporting.slice(2).map((one) => (
                    <EvidenceRow key={one.evidence_id} evidence={one} card={card} />
                  ))}
                  {contradicting.slice(1).map((one) => (
                    <EvidenceRow key={one.evidence_id} evidence={one} card={card} contra />
                  ))}
                  {triggers.map((one) => (
                    <EvidenceRow key={one.evidence_id} evidence={one} card={card} />
                  ))}
                </ul>
                <p className="muted">
                  Trigger signals are what made the system look here; §7.5 keeps them out of the support set, and
                  they are shown apart for the same reason.
                </p>
              </details>
            ) : null}
          </div>

          <div className="side">
            <Panel title="Review">
              <ProblemNote error={review.error} />
              {review.result ? (
                <Notice>
                  Recorded <span className="mono">{(review.result as { review_id: string }).review_id}</span>; the
                  card now reads <StatusChip status={(review.result as { final_status: FinalStatus }).final_status} />
                </Notice>
              ) : null}
              <ReviewForm card={card} caller={caller} projectId={projectId} onRecorded={refresh} submit={review} />
            </Panel>

            <Panel title="Sign-off" note="the person who drafted or reviewed this conclusion cannot sign it (§13.1)">
              <ProblemNote error={signOff.error} />
              {signOff.result ? (
                <Notice>
                  <span className="mono">{(signOff.result as { sign_off: string }).sign_off}</span> on a card in state{" "}
                  <StatusChip status={(signOff.result as { final_status: FinalStatus }).final_status} />
                </Notice>
              ) : null}
              <SignOffForm
                card={card}
                caller={caller}
                projectId={projectId}
                onRecorded={refresh}
                submit={signOff}
              />
            </Panel>

            <Panel title="Who wrote each number" note="§10.4: a value produced by a model is a defect, not a draft">
              <table>
                <thead>
                  <tr>
                    <th>field</th>
                    <th>source</th>
                    <th>object</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(card.field_provenance).map(([name, provenance]) => (
                    <tr key={name}>
                      <td className="mono">{name}</td>
                      <td>
                        <Chip status={provenance.source === "model" ? "conflict" : provenance.source === "human" ? "supported" : undefined}>
                          {provenance.source}
                        </Chip>
                      </td>
                      <td className="mono">{provenance.object_id}</td>
                    </tr>
                  ))}
                  {!Object.keys(card.field_provenance).length ? (
                    <tr>
                      <td colSpan={3} className="muted">
                        this card carries no field provenance rows
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </Panel>

            <Panel title="Trail" note="every revision supersedes a review id; nothing is silently overwritten">
              <ProblemNote error={trail.error} />
              <Loading on={trail.loading} />
              {trail.data?.reviews.length ? (
                <ol className="trail">
                  {trail.data.reviews.map((event) => (
                    <li key={event.review_id}>
                      <div>
                        <span className="mono">{event.review_id}</span> · {event.scope} · {event.decision} ·{" "}
                        <span className="mono">{event.reviewer_id}</span>
                      </div>
                      <div>{event.rationale}</div>
                      <div className="muted mono">{event.timestamp}</div>
                      {event.supersedes_review_id ? (
                        <div className="muted">supersedes {event.supersedes_review_id}</div>
                      ) : null}
                      {event.carry_over_from_candidate_id ? (
                        <div className="muted">carried over from {event.carry_over_from_candidate_id}</div>
                      ) : null}
                      {event.previous_value ? <div className="muted">was {preview(event.previous_value)}</div> : null}
                      {event.new_value ? <div className="muted">now {preview(event.new_value)}</div> : null}
                    </li>
                  ))}
                </ol>
              ) : (
                !trail.loading && <Empty>No person has acted on this card yet.</Empty>
              )}
            </Panel>
          </div>
        </div>
      </Panel>

      <p className="muted">
        <Link href={`/projects/${projectId}/queue`}>back to the queue</Link> ·{" "}
        <Link href={`/projects/${projectId}/runs/${encodeURIComponent(card.run_id)}`}>the run this card is about</Link> ·{" "}
        <Link href={`/projects/${projectId}/candidates/${encodeURIComponent(candidateId)}/alignment`}>
          what happened to this label in other runs
        </Link>
      </p>
    </>
  );
}

function EvidenceRow({ evidence, card, contra }: { evidence: Evidence; card: EvidenceCard; contra?: boolean }) {
  const independent =
    card.confirmation_evidence_ids.includes(evidence.evidence_id) && evidence.partition === "confirmation";
  return (
    <li className={contra ? "evidence evidence--contra" : "evidence"}>
      <div>
        <strong>{evidence.kind}</strong>{" "}
        <Chip>{evidence.role}</Chip> <Chip>{evidence.partition}</Chip>
        {independent ? <Chip status="supported">independent confirmation</Chip> : null}
      </div>
      <div className="muted mono">{sourceLabel(evidence)}</div>
      <div>{preview(evidence.content)}</div>
      <div className="muted">
        provenance <Chip status={evidence.provenance.source === "model" ? "conflict" : undefined}>{evidence.provenance.source}</Chip>{" "}
        {evidence.provenance.object_id}
        {evidence.independence_group ? ` · group ${evidence.independence_group}` : ""}
      </div>
    </li>
  );
}

function sourceLabel(evidence: Evidence): string {
  const source = evidence.source;
  const parts = [
    source.asset_id ? `asset ${source.asset_id}` : null,
    source.run_id ? `run ${source.run_id}` : null,
    source.dataset_id ? `reference ${source.dataset_id}@${source.version}` : null,
    source.doi ? `doi ${source.doi}` : null,
    source.pmid ? `pmid ${source.pmid}` : null,
    source.claim_id ? `claim ${source.claim_id}` : null,
    source.locator ? source.locator : null,
  ];
  return parts.filter(Boolean).join(" · ") || "no identifier";
}

/**
 * A review states its scope (§9.3).
 *
 * A card-scope decision can move the status only through the three routes the gate allows; a field
 * revision overwrites values and re-labels them as human-sourced; a membership note is recorded for
 * the next run and changes nothing here. `previous_value` is sent as the card this screen read, so a
 * stale review is refused with a 409 instead of quietly winning.
 */
function ReviewForm({
  card,
  caller,
  projectId,
  onRecorded,
  submit,
}: {
  card: EvidenceCard;
  caller: { token: string; subjectId: string };
  projectId: string;
  onRecorded: () => void;
  submit: ReturnType<typeof useSubmit>;
}) {
  const [scope, setScope] = useState<ReviewScope>("card");
  const [decision, setDecision] = useState<ReviewDecision>("approve");
  const [rationale, setRationale] = useState("");
  const [newValue, setNewValue] = useState("");
  const [supersedes, setSupersedes] = useState("");

  function send() {
    let parsed: Record<string, unknown> | undefined;
    if (scope === "field") {
      try {
        parsed = JSON.parse(newValue) as Record<string, unknown>;
      } catch {
        return;
      }
    }
    void submit
      .run(() =>
        findings.review(caller, projectId, card.card_id, {
          scope,
          decision,
          rationale,
          supersedes_review_id: supersedes || undefined,
          new_value: parsed,
          previous_value: parsed ? fieldsOf(card, parsed) : undefined,
        }),
      )
      .then((done) => {
        if (done) onRecorded();
      });
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <div className="grid">
        <Field label="scope">
          <select value={scope} onChange={(event) => setScope(event.target.value as ReviewScope)}>
            {SCOPES.map((one) => (
              <option key={one} value={one}>
                {one}
              </option>
            ))}
          </select>
        </Field>
        <Field label="decision">
          <select value={decision} onChange={(event) => setDecision(event.target.value as ReviewDecision)}>
            {DECISIONS.map((one) => (
              <option key={one} value={one}>
                {one}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="rationale" hint="required; it is the sentence a reader of the delivery will see">
        <textarea rows={3} value={rationale} onChange={(event) => setRationale(event.target.value)} />
      </Field>
      {scope === "field" ? (
        <Field
          label="new_value"
          hint='the card fields to overwrite, e.g. {"candidate_label": "intermediate state", "label_level": "cell_state"} — they become human-sourced'
        >
          <textarea rows={4} className="mono" value={newValue} onChange={(event) => setNewValue(event.target.value)} />
        </Field>
      ) : null}
      <Field label="supersedes_review_id" hint="a re-read that keeps the earlier verdict still gets its own row">
        <input className="mono" value={supersedes} onChange={(event) => setSupersedes(event.target.value)} />
      </Field>
      <button type="submit" disabled={submit.busy || !rationale.trim() || (scope === "field" && !newValue.trim())}>
        {submit.busy ? "recording…" : "Record review"}
      </button>
      {scope === "card" && ["mark_conflict", "mark_unknown", "reject"].includes(decision) ? (
        <p className="muted">this decision moves the status, and the gate decides whether it may</p>
      ) : null}
    </form>
  );
}

function SignOffForm({
  card,
  caller,
  projectId,
  onRecorded,
  submit,
}: {
  card: EvidenceCard;
  caller: { token: string; subjectId: string };
  projectId: string;
  onRecorded: () => void;
  submit: ReturnType<typeof useSubmit>;
}) {
  const [signOff, setSignOff] = useState<SignOff>("full");
  const [reason, setReason] = useState("");

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void submit
          .run(() => findings.signOff(caller, projectId, card.card_id, signOff, reason || undefined))
          .then((done) => {
            if (done) onRecorded();
          });
      }}
    >
      <Field label="sign_off">
        <select value={signOff} onChange={(event) => setSignOff(event.target.value as SignOff)}>
          {SIGN_OFFS.map((one) => (
            <option key={one} value={one}>
              {one}
            </option>
          ))}
        </select>
      </Field>
      <Field label="reason" hint={signOff === "not_signed" ? "required when withdrawing a sign-off" : "optional"}>
        <input value={reason} onChange={(event) => setReason(event.target.value)} />
      </Field>
      <button type="submit" disabled={submit.busy || (signOff === "not_signed" && !reason.trim())}>
        {submit.busy ? "recording…" : "Sign off"}
      </button>
      <p className="muted">
        currently <Chip>{card.sign_off}</Chip>
        {card.reviewer_id ? ` · signed by ${card.reviewer_id}` : " · not yet signed by a named person"}
      </p>
    </form>
  );
}

/** The values the screen saw, so the API can tell a stale review from a current one. */
function fieldsOf(card: EvidenceCard, changed: Record<string, unknown>): Record<string, unknown> {
  const stored = card as unknown as Record<string, unknown>;
  return Object.fromEntries(Object.keys(changed).map((name) => [name, stored[name] ?? null]));
}

function preview(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value !== "object") return String(value);
  const entries = Object.entries(value as Record<string, unknown>).slice(0, 3);
  return entries.map(([key, one]) => `${key}=${typeof one === "object" ? JSON.stringify(one) : String(one)}`).join(" · ") || "—";
}
