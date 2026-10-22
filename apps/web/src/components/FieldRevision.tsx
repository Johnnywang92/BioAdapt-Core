"use client";

import { Field } from "@/components/ui";
import { useT } from "@/lib/i18n";
import type { EvidenceCard, LabelLevel, HypothesisType, UnknownReason } from "@/lib/types";

const HYPOTHESES: HypothesisType[] = ["cell_type", "cell_state", "continuous_state", "technical_artifact", "unknown"];
const UNKNOWN_REASONS: UnknownReason[] = [
  "reference_not_covered",
  "insufficient_evidence",
  "unresolved_conflict",
  "quality_limited",
];
const LABEL_LEVELS: LabelLevel[] = ["unknown_coarse", "cell_type_broad", "cell_type_fine", "cell_state", "provisional_project"];
const CELL_TYPE_LEVELS: LabelLevel[] = ["cell_type_broad", "cell_type_fine"];

/** The card fields this screen lets a reader restate (§5.4), as the screen has to hold them: text, or a tick. */
export interface Revision {
  hypothesis_type: HypothesisType;
  unknown_reason: string;
  candidate_label: string;
  label_level: LabelLevel;
  term_id: string;
  ontology_name: string;
  ontology_version: string;
  status_reason: string;
  unresolved_conflict: boolean;
}

/** What the card holds right now, in the same shape, so an untouched field cannot sneak onto the wire. */
export function revisionOf(card: EvidenceCard): Revision {
  return {
    hypothesis_type: card.hypothesis_type,
    unknown_reason: card.unknown_reason ?? "",
    candidate_label: card.candidate_label ?? "",
    label_level: card.label_level,
    term_id: card.ontology.term_id ?? "",
    ontology_name: card.ontology.ontology_name ?? "",
    ontology_version: card.ontology.ontology_version ?? "",
    status_reason: card.status_reason ?? "",
    unresolved_conflict: !!card.unresolved_conflict,
  };
}

/**
 * The two combinations the card contract refuses, applied here so the screen shows what will be sent.
 *
 * A reader who calls an unknown population an unknown hypothesis cannot also leave a label on it, and a
 * provisional project label cannot carry a Cell Ontology id. Both follow from fields the contract already
 * enforces, so the screen clears the field it makes impossible and says so, rather than letting a person
 * type a value they are about to be refused.
 */
export function appliedRevision(rev: Revision): Revision {
  const unknown = rev.hypothesis_type === "unknown";
  const provisional = rev.label_level === "provisional_project";
  return {
    ...rev,
    candidate_label: unknown ? "" : rev.candidate_label,
    term_id: provisional ? "" : rev.term_id,
    ontology_name: provisional ? "" : rev.ontology_name,
    ontology_version: provisional ? "" : rev.ontology_version,
  };
}

/**
 * Only the restated fields, as the review body wants them (§5.4).
 *
 * `new_value` replaces whole card fields, so the ontology's three lines go out as one merged object: what
 * the reader did not touch keeps the value the run wrote, including the mapping provenance the screen does
 * not ask for.
 */
export function revisionChanges(card: EvidenceCard, rev: Revision): Record<string, unknown> {
  const next = appliedRevision(rev);
  const before = revisionOf(card);
  const changed: Record<string, unknown> = {};
  if (next.hypothesis_type !== before.hypothesis_type) changed.hypothesis_type = next.hypothesis_type;
  if (next.unknown_reason !== before.unknown_reason) changed.unknown_reason = next.unknown_reason || null;
  if (next.candidate_label !== before.candidate_label) changed.candidate_label = next.candidate_label || null;
  if (next.label_level !== before.label_level) changed.label_level = next.label_level;
  if (next.status_reason !== before.status_reason) changed.status_reason = next.status_reason || null;
  if (next.unresolved_conflict !== before.unresolved_conflict) changed.unresolved_conflict = next.unresolved_conflict;
  const ontologyFields = ["term_id", "ontology_name", "ontology_version"] as const;
  if (ontologyFields.some((one) => next[one] !== before[one])) {
    changed.ontology = {
      ...card.ontology,
      term_id: next.term_id || null,
      ontology_name: next.ontology_name || null,
      ontology_version: next.ontology_version || null,
    };
  }
  return changed;
}

/** The field-by-field editor that stands in for a hand-written JSON object (§5.4, 第四批②). */
export function FieldRevisionFields({
  card,
  rev,
  set,
}: {
  card: EvidenceCard;
  rev: Revision;
  set: (name: keyof Revision, value: string | boolean) => void;
}) {
  const t = useT();
  const next = appliedRevision(rev);
  const changed = Object.keys(revisionChanges(card, rev)).length;
  const before = revisionOf(card);
  const unknown = next.hypothesis_type === "unknown";
  const provisional = next.label_level === "provisional_project";
  const needsOntology = CELL_TYPE_LEVELS.includes(next.label_level);
  /** The contract reads the three ontology lines as one reference, so a half-written one is refused rather than stored. */
  const termIdWithoutSource = !!next.term_id && (!next.ontology_name || !next.ontology_version);
  const sourceWithoutTerm = !next.term_id && (!!next.ontology_name || !!next.ontology_version);
  const setText = (name: keyof Revision) => (event: { target: { value: string } }) => set(name, event.target.value);

  return (
    <fieldset>
      <legend>{t("which fields this review restates")}</legend>
      <p className="muted">
        {t(
          "every line below starts as the value the card holds now, and only a line you change is sent; the fields you change are re-labelled as human-sourced against this review's id, and the value they replace stays readable in the trail (§5.4)",
        )}
      </p>
      <div className="grid">
        <Field label="hypothesis_type">
          <select value={next.hypothesis_type} onChange={setText("hypothesis_type")}>
            {HYPOTHESES.map((one) => (
              <option key={one} value={one}>
                {`${t(one)} (${one})`}
              </option>
            ))}
          </select>
        </Field>
        <Field label="label_level">
          <select value={next.label_level} onChange={setText("label_level")}>
            {LABEL_LEVELS.map((one) => (
              <option key={one} value={one}>
                {`${t(one)} (${one})`}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {unknown ? (
        <Field
          label="unknown_reason"
          hint={t("an unknown hypothesis has to say why it is unknown; the card contract refuses it otherwise")}
        >
          <select value={next.unknown_reason} onChange={setText("unknown_reason")}>
            <option value="">{t("keep what the card states")}</option>
            {UNKNOWN_REASONS.map((one) => (
              <option key={one} value={one}>
                {`${t(one)} (${one})`}
              </option>
            ))}
          </select>
        </Field>
      ) : null}
      <Field
        label="candidate_label"
        hint={
          unknown
            ? t("naming an unknown hypothesis as unknown clears the label this card carries")
            : t("the words this card says the population is; a label nobody can read is not a conclusion")
        }
      >
        <input value={next.candidate_label} onChange={setText("candidate_label")} disabled={unknown} />
      </Field>
      <div className="grid">
        <Field label="ontology.term_id">
          <input className="mono" value={next.term_id} onChange={setText("term_id")} disabled={provisional} />
        </Field>
        <Field label="ontology.ontology_name">
          <input className="mono" value={next.ontology_name} onChange={setText("ontology_name")} disabled={provisional} />
        </Field>
        <Field label="ontology.ontology_version">
          <input
            className="mono"
            value={next.ontology_version}
            onChange={setText("ontology_version")}
            disabled={provisional}
          />
        </Field>
      </div>
      <p className="muted">
        {provisional
          ? t("a provisional project label cannot carry an ontology id, so this choice clears all three lines")
          : needsOntology
            ? t(
                "a cell type label has to name the term, the ontology it comes from and that ontology's version; the contract refuses any one of the three missing",
              )
            : t("these three lines are the reference this label cites; leaving them as they are keeps the run's own mapping provenance")}
      </p>
      {termIdWithoutSource || sourceWithoutTerm ? (
        <div className="error" role="alert">
          {t(
            "a term id has to be named by an ontology and a version, and an ontology name has to point at a term: the contract refuses either half on its own",
          )}
        </div>
      ) : null}
      <Field
        label="status_reason"
        hint={t("the sentence that says why the card stands where it stands")}
      >
        <input value={next.status_reason} onChange={setText("status_reason")} />
      </Field>
      <label className="check">
        <input
          type="checkbox"
          checked={next.unresolved_conflict}
          onChange={(event) => set("unresolved_conflict", event.target.checked)}
        />
        <span>{t("the conflict this card carries is still unresolved")}</span>
        <span className="muted">{`${t("as read from the card")}: ${String(before.unresolved_conflict)}`}</span>
      </label>
      <p className="muted">
        {changed
          ? t(
              "{n} of these lines differ from the card as it was read, and those are the fields this review would overwrite",
              { n: String(changed) },
            )
          : t("nothing below differs from the card as it was read, so this review would have nothing to restate")}
      </p>
      <p className="muted">
        {t(
          "what the run measured is not offered here: evidence rows, ids, the thresholds version the card was judged against, its status and its signature are decided by the pipeline, the gate and the sign-off form, not by this list",
        )}
      </p>
    </fieldset>
  );
}
