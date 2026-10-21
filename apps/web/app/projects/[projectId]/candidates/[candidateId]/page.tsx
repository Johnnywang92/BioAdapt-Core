"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Chip, Empty, Field, Loading, Notice, Panel, ProblemNote, StatusChip } from "@/components/ui";
import { findings, knowledge, type Caller } from "@/lib/api";
import { evidenceMarkers, queryMarkers } from "@/lib/evidencePresentation";
import { useT } from "@/lib/i18n";
import { useProjectScope } from "@/lib/useProjectScope";
import { useAsync, useSubmit } from "@/lib/useAsync";
import type {
  BioSuggestion,
  CardEvidenceRead,
  Evidence,
  EvidenceCard,
  FinalStatus,
  ReviewDecision,
  ReviewEvent,
  ReviewResponse,
  ReviewScope,
  SignOff,
  SignOffResponse,
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
  const t = useT();
  const { caller, projectId, blocked } = useProjectScope(params.projectId);
  const candidateId = decodeURIComponent(params.candidateId);

  if (!caller) {
    return (
      <Panel title={t("Evidence card")} note={t("nothing is read until a token is set")}>
        <p className="muted">{blocked}</p>
      </Panel>
    );
  }

  return <Card key={`${projectId}:${candidateId}:${caller.token}`} caller={caller} projectId={projectId} candidateId={candidateId} />;
}

function Card({ caller, projectId, candidateId }: { caller: Caller; projectId: string; candidateId: string }) {
  const t = useT();
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
  const [prefillReview, setPrefillReview] = useState<{
    decision?: ReviewDecision;
    rationale?: string;
    newValue?: string;
    scope?: ReviewScope;
  } | null>(null);

  function refresh() {
    read.reload();
    trail.reload();
  }

  if (read.loading && !card) {
    return (
      <Panel title={t("Evidence card")}>
        <Loading on />
      </Panel>
    );
  }
  if (!card) {
    return (
      <Panel
        title={t("Evidence card")}
        note={t("the card is read through the candidate that produced it, never through a cluster label")}
      >
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
  const reviewResult = review.result as ReviewResponse | null;
  const signOffResult = signOff.result as SignOffResponse | null;

  return (
    <>
      <Panel
        title={`${card.display_name} · ${card.card_id}`}
        note={t("run {run} · candidate {candidate} · support rule {rule} · thresholds {thresholds}", {
          run: card.run_id,
          candidate: card.candidate_id,
          rule: card.support_rule_version,
          thresholds: card.thresholds_version,
        })}
        actions={
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <StatusChip status={card.final_status} />
            <button className="secondary" type="button" onClick={refresh}>
              {t("re-read")}
            </button>
          </div>
        }
      >
        <ProblemNote error={read.error} />
        <div className="card-screen">
          <div className="card-body">
            <dl className="facts">
              <dt>{t("claimed")}</dt>
              <dd>
                {card.candidate_label ?? (
                  <span className="muted">
                    {t("no label — hypothesis {type}", { type: card.hypothesis_type })}
                    {card.unknown_reason ? t(", reason {reason}", { reason: card.unknown_reason }) : ""}
                  </span>
                )}
              </dd>
              <dt>{t("label level")}</dt>
              <dd>
                {card.label_level}
                {card.ontology.term_id ? (
                  <span className="mono">
                    {" "}
                    · {card.ontology.ontology_name} {card.ontology.term_id} @ {card.ontology.ontology_version}
                  </span>
                ) : null}
              </dd>
              <dt>{t("candidate types")}</dt>
              <dd>{card.candidate_types.join(", ") || t("none")}</dd>
              <dt>{t("status reason")}</dt>
              <dd>{card.status_reason ?? "—"}</dd>
              <dt>{t("ceiling")}</dt>
              <dd>
                <StatusChip status={read.data?.ceiling ?? card.final_status} />
                {read.data?.gate_reasons.map((reason) => (
                  <p className="muted" key={reason}>
                    {reason}
                  </p>
                ))}
              </dd>
              <dt>{t("artifact screening")}</dt>
              <dd>
                {card.artifact_screening
                  ? Object.entries(card.artifact_screening)
                      .map(([risk, item]) => `${risk}: ${item.result}`)
                      .join(" · ")
                  : t("not screened on this card")}
              </dd>
              <dt>{t("composition")}</dt>
              <dd>{preview(card.composition_check)}</dd>
              <dt>{t("stability")}</dt>
              <dd>{card.stability_summary ? preview(card.stability_summary) : t("no perturbation summary")}</dd>
              <dt>{t("novelty vs coverage")}</dt>
              <dd>{card.novelty_vs_coverage ? preview(card.novelty_vs_coverage) : "—"}</dd>
              <dt>{t("cross modal")}</dt>
              <dd>{t(card.cross_modal_status)}</dd>
            </dl>

            {card.cross_modal_status === "discordant" ? (
              <div className="error" role="alert">
                <strong>{t("Cross-Modal Conflict Alert")}</strong>
                <p>{t("The card reports discordant modalities. Review the source evidence before drawing a conclusion; the alert alone does not establish a mechanism or statistical significance.")}</p>
              </div>
            ) : card.unresolved_conflict ? (
              <div className="error" role="alert">
                <strong>{t("Unresolved evidence conflict")}</strong>
                <p>{t("This card has an unresolved conflict. It is not necessarily a cross-modal conflict; inspect its contradicting evidence and gate reasons.")}</p>
              </div>
            ) : null}
            <CrossModalSummary card={card} />

            {read.data?.reviewer_memo ? (
              <div
                style={{
                  background: "#f8f9fa",
                  border: "1px solid #e2e8f0",
                  borderRadius: "8px",
                  padding: "16px",
                  marginTop: "16px",
                  marginBottom: "20px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                  <h3 style={{ margin: 0, fontSize: "1.05rem", display: "flex", alignItems: "center", gap: "8px" }}>
                    <span>🧠</span> {t("AI Review Memo")}
                  </h3>
                  {read.data.reviewer_memo.confidence != null ? (
                    <span className="chip chip--unknown">
                      {t("Confidence")}: {Math.round(read.data.reviewer_memo.confidence * 100)}%
                    </span>
                  ) : null}
                </div>
                <p style={{ margin: "4px 0 12px 0", color: "#334155", lineHeight: 1.5 }}>
                  {read.data.reviewer_memo.summary}
                </p>

                {read.data.reviewer_memo.power_warning ? (
                  <div
                    style={{
                      background: "#fffbeb",
                      border: "1px solid #fef3c7",
                      color: "#92400e",
                      padding: "8px 12px",
                      borderRadius: "6px",
                      marginBottom: "10px",
                      fontSize: "0.9rem",
                    }}
                  >
                    <strong>⚠️ {t("Statistical Power Alert")}:</strong> {read.data.reviewer_memo.power_warning}
                  </div>
                ) : null}

                {read.data.reviewer_memo.confounding_risks && read.data.reviewer_memo.confounding_risks.length > 0 ? (
                  <div style={{ marginBottom: "10px", fontSize: "0.9rem" }}>
                    <strong>{t("Confounding Risks")}:</strong>
                    <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                      {read.data.reviewer_memo.confounding_risks.map((risk, idx) => (
                        <li key={idx} style={{ color: "#b45309" }}>
                          {risk}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {read.data.reviewer_memo.stability_verdict ? (
                  <div style={{ marginBottom: "8px", fontSize: "0.9rem", color: "#475569" }}>
                    <strong>{t("Stability (5-Perturbation)")}:</strong> {read.data.reviewer_memo.stability_verdict}
                  </div>
                ) : null}

                {read.data.reviewer_memo.recommended_action ? (
                  <div
                    style={{
                      background: "#f0fdf4",
                      border: "1px solid #dcfce7",
                      color: "#166534",
                      padding: "8px 12px",
                      borderRadius: "6px",
                      marginTop: "10px",
                      fontSize: "0.9rem",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                    }}
                  >
                    <div>
                      <strong>🎯 {t("Recommended Action for Expert")}:</strong> {read.data.reviewer_memo.recommended_action}
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setPrefillReview({
                          decision: "revise",
                          scope: "card",
                          newValue: "",
                          rationale: t("Review memo suggestion: {action}", { action: read.data?.reviewer_memo?.recommended_action ?? "" }),
                        });
                      }}
                      style={{
                        background: "#16a34a",
                        color: "#fff",
                        border: "none",
                        borderRadius: "4px",
                        padding: "4px 8px",
                        cursor: "pointer",
                        fontSize: "0.8rem",
                        whiteSpace: "nowrap",
                        marginLeft: "12px",
                      }}
                    >
                      {t("Fill review draft")}
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}

            <h3>{t("Two records that carry it")}</h3>
            <ul className="evidence-list">
              {supporting.slice(0, 2).map((one) => (
                <EvidenceRow key={one.evidence_id} evidence={one} card={card} />
              ))}
              {!supporting.length ? <li className="muted">{t("nothing is listed as supporting evidence")}</li> : null}
            </ul>

            <h3>{t("The record against it")}</h3>
            <ul className="evidence-list">
              {contradicting.slice(0, 1).map((one) => (
                <EvidenceRow key={one.evidence_id} evidence={one} card={card} contra />
              ))}
              {!contradicting.length ? <li className="muted">{t("no counter-record is filed on this card")}</li> : null}
            </ul>

            {literature.length || supporting.length > 2 || contradicting.length > 1 || triggers.length ? (
              <details>
                <summary>
                  {t("the rest, and the literature ({n} items)", {
                    n: literature.length + Math.max(0, supporting.length - 2),
                  })}
                </summary>
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
                  {t(
                    "Trigger signals are what made the system look here; §7.5 keeps them out of the support set, and they are shown apart for the same reason.",
                  )}
                </p>
              </details>
            ) : null}

            <KnowledgeAdvice
              key={`${card.card_id}:${caller.token}:${JSON.stringify(evidenceMarkers(card.evidence))}`}
              card={card}
              caller={caller}
              projectId={projectId}
              clusterId={read.data?.cluster_id ?? ""}
              onPrefill={setPrefillReview}
            />
          </div>

          <div className="side">
            <Panel title={t("Review")}>
              <ProblemNote error={review.error} />
              {reviewResult ? (
                <Notice>
                  {t("Recorded")} <span className="mono">{reviewResult.review_id}</span>;{" "}
                  {t("the card now reads")} <StatusChip status={reviewResult.final_status} /> · {t("ceiling")}{" "}
                  <StatusChip status={reviewResult.ceiling} />
                  {reviewResult.gate_reasons.map((reason) => (
                    <p className="muted" key={reason}>
                      {reason}
                    </p>
                  ))}
                </Notice>
              ) : null}
              <ReviewForm
                card={card}
                caller={caller}
                projectId={projectId}
                onRecorded={refresh}
                submit={review}
                prefill={prefillReview}
              />
            </Panel>

            <Panel title={t("Sign-off")} note={t("the person who drafted or reviewed this conclusion cannot sign it (§13.1)")}>
              <ProblemNote error={signOff.error} />
              {signOffResult ? (
                <Notice>
                  <span className="mono">{signOffResult.sign_off}</span> {t("on a card in state")}{" "}
                  <StatusChip status={signOffResult.final_status} /> · {t("ceiling")}{" "}
                  <StatusChip status={signOffResult.ceiling} />
                  {signOffResult.gate_reasons.map((reason) => (
                    <p className="muted" key={reason}>
                      {reason}
                    </p>
                  ))}
                </Notice>
              ) : null}
              <SignOffForm
                card={card}
                ceiling={read.data?.ceiling ?? card.final_status}
                caller={caller}
                projectId={projectId}
                onRecorded={refresh}
                submit={signOff}
              />
            </Panel>

            <Panel title={t("Who wrote each number")} note={t("§10.4: a value produced by a model is a defect, not a draft")}>
              <table>
                <thead>
                  <tr>
                    <th>{t("field")}</th>
                    <th>{t("source")}</th>
                    <th>{t("object")}</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(card.field_provenance).map(([name, provenance]) => (
                    <tr key={name}>
                      <td className="mono">{name}</td>
                      <td>
                        <Chip
                          title={provenance.source}
                          status={
                            provenance.source === "model" ? "conflict" : provenance.source === "human" ? "supported" : undefined
                          }
                        >
                          {t(provenance.source)}
                        </Chip>
                      </td>
                      <td className="mono">{provenance.object_id}</td>
                    </tr>
                  ))}
                  {!Object.keys(card.field_provenance).length ? (
                    <tr>
                      <td colSpan={3} className="muted">
                        {t("this card carries no field provenance rows")}
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </Panel>

            <Panel title={t("Trail")} note={t("every revision supersedes a review id; nothing is silently overwritten")}>
              <ProblemNote error={trail.error} />
              <Loading on={trail.loading} />
              {trail.data?.reviews.length ? (
                <ol className="trail">
                  {trail.data.reviews.map((event) => (
                    <li key={event.review_id}>
                      <div>
                        <span className="mono">{event.review_id}</span> · {t(event.scope)} · {t(event.decision)} ·{" "}
                        <span className="mono">{event.reviewer_id}</span>
                      </div>
                      <div>{event.rationale}</div>
                      <div className="muted mono">{event.timestamp}</div>
                      {event.supersedes_review_id ? (
                        <div className="muted">{t("supersedes {id}", { id: event.supersedes_review_id })}</div>
                      ) : null}
                      {event.carry_over_from_candidate_id ? (
                        <div className="muted">
                          {t("carried over from {id}", { id: event.carry_over_from_candidate_id })}
                        </div>
                      ) : null}
                      {event.previous_value ? (
                        <div className="muted">
                          {t("was")} {preview(event.previous_value)}
                        </div>
                      ) : null}
                      {event.new_value ? (
                        <div className="muted">
                          {t("now")} {preview(event.new_value)}
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ol>
              ) : (
                !trail.loading && <Empty>{t("No person has acted on this card yet.")}</Empty>
              )}
            </Panel>
          </div>
        </div>
      </Panel>

      <p className="muted">
        <Link href={`/projects/${projectId}/queue`}>{t("back to the queue")}</Link> ·{" "}
        <Link href={`/projects/${projectId}/runs/${encodeURIComponent(card.run_id)}`}>
          {t("the run this card is about")}
        </Link>{" "}
        ·{" "}
        <Link href={`/projects/${projectId}/candidates/${encodeURIComponent(candidateId)}/alignment`}>
          {t("what happened to this label in other runs")}
        </Link>
      </p>
    </>
  );
}

function KnowledgeAdvice({ card, caller, projectId, clusterId, onPrefill }: {
  card: EvidenceCard;
  caller: Caller;
  projectId: string;
  clusterId: string;
  onPrefill: (value: { decision: ReviewDecision; scope: ReviewScope; newValue: string; rationale: string }) => void;
}) {
  const t = useT();
  const recordedMarkers = evidenceMarkers(card.evidence);
  const [input, setInput] = useState(recordedMarkers.join(", "));
  const [tissue, setTissue] = useState("");
  const [result, setResult] = useState<BioSuggestion | null>(null);
  const [selectedType, setSelectedType] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [filled, setFilled] = useState(false);
  const markers = queryMarkers(input);

  function clearResult() {
    setResult(null);
    setSelectedType("");
    setError(null);
    setFilled(false);
  }

  async function query() {
    if (!markers.length || !clusterId || busy) return;
    clearResult();
    setBusy(true);
    try {
      const answer = await knowledge.suggest(caller, projectId, clusterId, markers, tissue.trim());
      setResult(answer);
      setSelectedType(answer.suggested_cell_types[0] ?? "");
    } catch (exc) {
      setError(exc);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="evidence" aria-label={t("Knowledge Base RAG Advice")}>
      <h3>{t("Knowledge Base RAG Advice")}</h3>
      <p className="muted">{t("Markers are read from marker-specificity evidence, including discovery signals. They are retrieval inputs, not proof of identity.")}</p>
      {recordedMarkers.length ? (
        <p className="mono">{t("Recorded markers")}: {recordedMarkers.join(", ")}</p>
      ) : <Notice>{t("No marker genes are recorded on this card. Enter verified markers to query; no example genes will be substituted.")}</Notice>}
      <form onSubmit={(event) => { event.preventDefault(); void query(); }}>
        <Field label={t("Markers for this query")} hint={t("The first eight distinct genes are used. Check the exact query below before submitting.")}>
          <textarea rows={2} value={input} disabled={busy} onChange={(event) => { setInput(event.target.value); clearResult(); }} />
        </Field>
        <Field label={t("Tissue (optional)")} hint={t("Tissue is not supplied by this card. Leave blank unless you know the source; a cell label is not a tissue.")}>
          <input value={tissue} disabled={busy} onChange={(event) => { setTissue(event.target.value); clearResult(); }} />
        </Field>
        <p>{t("Query preview")}: <span className="mono">{clusterId || "—"} · {markers.join(", ") || "—"} · {tissue.trim() || t("Tissue not supplied")}</span></p>
        <button type="submit" disabled={busy || !markers.length || !clusterId}>{busy ? t("Querying knowledge base...") : t("Consult Knowledge Base")}</button>
        {!clusterId ? <Notice>{t("The source cluster is missing. Refresh the card before querying.")}</Notice> : null}
      </form>
      <ProblemNote error={error} />
      {error ? <p className="muted">{t("The query failed; no suggestion was applied. Check the error and retry.")}</p> : null}
      {result ? (
        <div className="rows" style={{ marginTop: 12 }}>
          <p>{t("Queried markers")}: <span className="mono">{result.markers.join(", ")}</span></p>
          {!result.citations.length ? <Notice>{t("No matching knowledge-base citations were found. Any proposed cell types are heuristic leads, not literature-backed conclusions.")}</Notice> : null}
          <p className="muted">{t("Retrieval heuristic score")}: {Math.round(result.confidence * 100)}% · {t("This score is based on retrieved matches, not a calibrated probability that a cell label is correct.")}</p>
          <div style={{ whiteSpace: "pre-wrap" }}>{result.rationale}</div>
          {result.citations.length ? (
            <details>
              <summary>{t("citations")} ({result.citations.length})</summary>
              {result.citations.map((citation, index) => (
                <blockquote key={`${citation.chunk.chunk_id}:${index}`}>
                  <strong>{citation.source_doc?.title ?? citation.chunk.doc_id}</strong> · {citation.chunk.section_path}
                  <p>{citation.chunk.content}</p>
                </blockquote>
              ))}
            </details>
          ) : null}
          {result.suggested_cell_types.length ? (
            <>
              <Field label={t("Suggested label for review")}>
                <select value={selectedType} onChange={(event) => { setSelectedType(event.target.value); setFilled(false); }}>
                  {result.suggested_cell_types.map((label) => <option key={label} value={label}>{label}</option>)}
                </select>
              </Field>
              <p>{t("Draft change")}: {card.candidate_label ?? t("no label")} → {selectedType}</p>
              <p className="muted">{t("Only the label is prefilled. Complete hypothesis and ontology fields before submitting; this button does not save a review or sign a conclusion.")}</p>
              <button type="button" disabled={!result.citations.length || !selectedType} onClick={() => {
                onPrefill({
                  decision: "revise",
                  scope: "field",
                  newValue: JSON.stringify({ candidate_label: selectedType }),
                  rationale: t("Knowledge-base suggestion for review: {label}. Rationale: {rationale}", { label: selectedType, rationale: result.rationale }),
                });
                setFilled(true);
              }}>{t("Fill review draft")}</button>
              {filled ? <Notice>{t("Review draft filled. Inspect and submit it in the Review panel; nothing has been saved yet.")}</Notice> : null}
            </>
          ) : <Notice>{t("The retrieved evidence is insufficient to suggest a cell label.")}</Notice>}
        </div>
      ) : null}
    </section>
  );
}

function EvidenceRow({ evidence, card, contra }: { evidence: Evidence; card: EvidenceCard; contra?: boolean }) {
  const t = useT();
  const independent =
    card.confirmation_evidence_ids.includes(evidence.evidence_id) && evidence.partition === "confirmation";
  return (
    <li className={contra ? "evidence evidence--contra" : "evidence"}>
      <div>
        <strong>{t(evidence.kind)}</strong> <Chip title={evidence.role}>{t(evidence.role)}</Chip>{" "}
        <Chip title={evidence.partition}>{t(evidence.partition)}</Chip>
        {independent ? <Chip status="supported">{t("independent confirmation")}</Chip> : null}
      </div>
      <div className="muted mono">{sourceLabel(evidence)}</div>
      <div>{preview(evidence.content)}</div>
      <div className="muted">
        {t("provenance")}{" "}
        <Chip title={evidence.provenance.source} status={evidence.provenance.source === "model" ? "conflict" : undefined}>
          {t(evidence.provenance.source)}
        </Chip>{" "}
        {evidence.provenance.object_id}
        {evidence.independence_group ? t(" · group {group}", { group: evidence.independence_group }) : ""}
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
  prefill,
}: {
  card: EvidenceCard;
  caller: { token: string; subjectId: string };
  projectId: string;
  onRecorded: () => void;
  submit: ReturnType<typeof useSubmit>;
  prefill?: {
    decision?: ReviewDecision;
    rationale?: string;
    newValue?: string;
    scope?: ReviewScope;
  } | null;
}) {
  const t = useT();
  const [scope, setScope] = useState<ReviewScope>("card");
  const [decision, setDecision] = useState<ReviewDecision>("approve");
  const [rationale, setRationale] = useState("");
  const [newValue, setNewValue] = useState("");
  const [supersedes, setSupersedes] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);

  useEffect(() => {
    if (prefill) {
      if (prefill.decision) setDecision(prefill.decision);
      if (prefill.rationale) setRationale(prefill.rationale);
      if (prefill.newValue !== undefined) setNewValue(prefill.newValue);
      if (prefill.scope) setScope(prefill.scope);
    }
  }, [prefill]);

  function send() {
    setParseError(null);
    let parsed: Record<string, unknown> | undefined;
    if (scope === "field") {
      try {
        const value: unknown = JSON.parse(newValue);
        if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
        parsed = value as Record<string, unknown>;
      } catch {
        setParseError(t("Review changes must be a valid JSON object. No review was submitted."));
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
        <Field label={t("scope")}>
          <select value={scope} onChange={(event) => setScope(event.target.value as ReviewScope)}>
            {SCOPES.map((one) => (
              <option key={one} value={one}>
                {`${t(one)} (${one})`}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("decision")}>
          <select value={decision} onChange={(event) => setDecision(event.target.value as ReviewDecision)}>
            {DECISIONS.map((one) => (
              <option key={one} value={one}>
                {`${t(one)} (${one})`}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label={t("rationale")} hint={t("required; it is the sentence a reader of the delivery will see")}>
        <textarea rows={3} value={rationale} onChange={(event) => setRationale(event.target.value)} />
      </Field>
      {scope === "field" ? (
        <Field
          label="new_value"
          hint={t(
            'the card fields to overwrite, e.g. {"candidate_label": "intermediate state", "label_level": "cell_state"} — they become human-sourced',
          )}
        >
          <textarea rows={4} className="mono" value={newValue} onChange={(event) => setNewValue(event.target.value)} />
        </Field>
      ) : null}
      <Field
        label="supersedes_review_id"
        hint={t("a re-read that keeps the earlier verdict still gets its own row")}
      >
        <input className="mono" value={supersedes} onChange={(event) => setSupersedes(event.target.value)} />
      </Field>
      {parseError ? <div className="error" role="alert">{parseError}</div> : null}
      <button type="submit" disabled={submit.busy || !rationale.trim() || (scope === "field" && !newValue.trim())}>
        {submit.busy ? t("recording…") : t("Record review")}
      </button>
      {scope === "card" ? (
        <p className="muted">
          {decision === "approve"
            ? t(
                "an approval adopts the card's conclusion, so it moves the status up to the ceiling; a card that states no hypothesis has nothing to adopt",
              )
            : t("this decision moves the status, and the gate decides whether it may")}
        </p>
      ) : null}
    </form>
  );
}

function SignOffForm({
  card,
  ceiling,
  caller,
  projectId,
  onRecorded,
  submit,
}: {
  card: EvidenceCard;
  ceiling: FinalStatus;
  caller: { token: string; subjectId: string };
  projectId: string;
  onRecorded: () => void;
  submit: ReturnType<typeof useSubmit>;
}) {
  const t = useT();
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
      <Field label={t("sign_off")}>
        <select value={signOff} onChange={(event) => setSignOff(event.target.value as SignOff)}>
          {SIGN_OFFS.map((one) => (
            <option key={one} value={one}>
              {`${t(one)} (${one})`}
            </option>
          ))}
        </select>
      </Field>
      <Field
        label={t("reason")}
        hint={signOff === "not_signed" ? t("required when withdrawing a sign-off") : t("optional")}
      >
        <input value={reason} onChange={(event) => setReason(event.target.value)} />
      </Field>
      <button type="submit" disabled={submit.busy || (signOff === "not_signed" && !reason.trim())}>
        {submit.busy ? t("recording…") : t("Sign off")}
      </button>
      <p className="muted">
        {t("currently")} <Chip title={card.sign_off}>{t(card.sign_off)}</Chip>
        {card.sign_off !== "not_signed" && card.reviewer_id
          ? t(" · signed by {who}", { who: card.reviewer_id })
          : t(" · not yet signed by a named person")}
      </p>
      <p className="muted">
        {t("this card can reach at most")} <StatusChip status={ceiling} />
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

function CrossModalSummary({ card }: { card: EvidenceCard }) {
  const t = useT();
  const evidence = Object.values(card.evidence).filter((item) => item.kind === "cross_modal");
  if (!evidence.length && card.cross_modal_status !== "discordant") return null;
  const number = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? String(value) : "—";
  const text = (value: unknown) => typeof value === "string" && value ? value : "—";
  return (
    <section className="evidence" aria-label={t("Cross-modal measured summary")}>
      <h3>{t("Cross-modal measured summary")}</h3>
      <p className="muted">{t("Only aggregate measurements are available, not paired cell observations. No scatter plot is drawn. Units, transformations and thresholds must be checked in the source asset.")}</p>
      {!evidence.length ? <Empty>{t("No cross-modal measurements are attached to this card.")}</Empty> : evidence.map((item) => (
        <div key={item.evidence_id} style={{ marginBottom: 12 }}>
          <strong>{text(item.content.gene_symbol)}</strong> · <Chip title={text(item.content.target_modality)}>{item.content.target_modality === "protein" ? "RNA / ADT" : item.content.target_modality === "atac" ? "RNA / ATAC" : text(item.content.target_modality)}</Chip>
          <div className="table-scroll">
            <table>
              <thead><tr><th>{t("RNA mean")}</th><th>{t("Target modality mean")}</th><th>Spearman ρ</th><th>{t("agreement")}</th></tr></thead>
              <tbody><tr><td>{number(item.content.rna_expression_mean)}</td><td>{number(item.content.target_modality_mean)}</td><td>{number(item.content.spearman_rho)}</td><td>{t(text(item.content.agreement))}</td></tr></tbody>
            </table>
          </div>
          {typeof item.content.conflict_rationale === "string" ? <p>{item.content.conflict_rationale}</p> : null}
          <p className="muted mono">{item.evidence_id} · {sourceLabel(item)}</p>
        </div>
      ))}
    </section>
  );
}
