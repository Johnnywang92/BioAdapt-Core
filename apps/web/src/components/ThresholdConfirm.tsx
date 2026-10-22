"use client";

import { useState } from "react";

import { Field, Notice, Panel, ProblemNote } from "@/components/ui";
import { ApiProblem, projects as projectsApi, type Caller } from "@/lib/api";
import { useIdentity } from "@/lib/identity";
import { useT } from "@/lib/i18n";
import { useSubmit } from "@/lib/useAsync";
import type { Project } from "@/lib/types";

/**
 * Attesting the measured cut set (§1.5, appendix C1).
 *
 * Every number the project starts with is a stage-0 placeholder, so the act worth supporting is not
 * "click confirm": it is a person typing the numbers they measured and leaving an audit trail that
 * names where they came from. Three things this screen therefore refuses to do. It never pre-fills a
 * version name or a rationale, because a rationale that arrived already written proves nothing. It
 * never hides a cut the person did not touch - the before/after list says, in numbers, how much was
 * carried over. And it sends every field the API returned (§C1 restates the whole cut set), so a
 * placeholder cannot be attested by being left out.
 *
 * Whether the act is allowed at all - principal investigator, a human, a second factor - is the
 * server's reading of the token, not this screen's guess.
 */

const INTEGER_CUTS = new Set(["rare_min_cells", "rare_min_donors", "coverage_knn", "min_n_per_group", "expert_queue_card_limit"]);

/** The same grouping the schema declares its cuts in, so the form reads in the order §1.5 does. */
const CUT_GROUPS: { title: string; keys: string[] }[] = [
  { title: "rare populations", keys: ["rare_share_max", "rare_min_cells", "rare_min_donors"] },
  {
    title: "candidate confidence and coverage",
    keys: ["low_confidence_percentile", "classifier_margin_min", "coverage_knn", "coverage_distance_ratio_min"],
  },
  { title: "clustering stability", keys: ["stability_cc_ari_min", "stability_jaccard_min", "stability_pass_ratio"] },
  { title: "alignment", keys: ["alignment_continuation_min", "alignment_uncertain_min"] },
  { title: "group power", keys: ["min_n_per_group"] },
  {
    title: "differential expression support",
    keys: [
      "de_fdr_max",
      "de_min_abs_log2fc",
      "detection_frequency_in_min",
      "detection_frequency_excluded_max",
      "donor_consistency_min",
    ],
  },
  { title: "reproduction and labels", keys: ["numeric_tolerance", "label_agreement_min"] },
  { title: "expert queue", keys: ["expert_queue_card_limit"] },
  { title: "summary pages", keys: ["summary_rare_share_max", "summary_rare_min_cells"] },
];

/** Each cut, in the words a person needs to choose a number for it. */
const CUT_NOTES: Record<string, string> = {
  rare_share_max: "largest share of cells a population may hold and still be called rare",
  rare_min_cells: "fewest cells a population has to be seen in to be carried at all",
  rare_min_donors: "fewest donors a rare population has to appear in",
  low_confidence_percentile: "percentile of the support score under which a card is called low-confidence",
  classifier_margin_min: "smallest margin a state assignment needs to be trusted",
  coverage_knn: "how many neighbours a cell's local coverage is measured over",
  coverage_distance_ratio_min: "smallest local-to-reference distance ratio still counted as covered",
  stability_cc_ari_min: "lowest agreement two clusterings need to be called stable",
  stability_jaccard_min: "lowest cell overlap two clusterings need",
  stability_pass_ratio: "share of the stability tests a candidate has to pass",
  alignment_continuation_min: "score at which a matched cell is treated as the same population",
  alignment_uncertain_min: "score below which the alignment is called uncertain; sits under the cut above",
  min_n_per_group: "smallest group that may be tested at all",
  de_fdr_max: "largest adjusted p-value a differential result may carry",
  de_min_abs_log2fc: "smallest absolute fold change worth reporting",
  detection_frequency_in_min: "lowest share of a group's donors that have to express the gene",
  detection_frequency_excluded_max: "highest share outside the group still allowed to express it; under the floor above",
  donor_consistency_min: "lowest share of donors that have to agree with the reported direction",
  numeric_tolerance: "difference two products may show before they are called a mismatch",
  label_agreement_min: "lowest share of labels two products have to agree on",
  expert_queue_card_limit: "most cards one reviewer is handed at a time",
  summary_rare_share_max: "share above which the summary page calls a population rare",
  summary_rare_min_cells: "cell count below which the summary page calls a population thin",
};

export function ThresholdConfirm({
  caller,
  project,
  onConfirmed,
}: {
  caller: Caller;
  project: Project;
  onConfirmed: () => void;
}) {
  const t = useT();
  const { identity } = useIdentity();
  const submit = useSubmit();
  /** Only what the person actually typed. Anything else is read back from the record in force. */
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [version, setVersion] = useState("");
  const [rationale, setRationale] = useState("");

  const inForce = project.thresholds ?? {};
  const confirmed = inForce.confirmed === true;
  const currentVersion = String(inForce.version ?? "");
  const cutKeys = Object.keys(inForce).filter((name) => name !== "confirmed" && name !== "version");
  const known = new Set(CUT_GROUPS.flatMap((group) => group.keys));
  const rest = cutKeys.filter((name) => !known.has(name));

  const shown = (name: string) => edits[name] ?? String(inForce[name] ?? "");
  const changed = cutKeys.filter((name) => edits[name] !== undefined && edits[name].trim() !== String(inForce[name]));
  const carried = cutKeys.length - changed.length;

  const numbers = new Map<string, number | null>();
  for (const name of cutKeys) {
    const raw = shown(name).trim();
    const parsed = Number(raw);
    numbers.set(name, raw !== "" && Number.isFinite(parsed) ? parsed : null);
  }

  const problems: string[] = [];
  if (!version.trim()) problems.push(t("the cut set you are attesting has to be named, because the products cite that name"));
  if (!rationale.trim())
    problems.push(t("the rationale is the audit record of where these numbers came from, and it is empty"));
  for (const name of cutKeys) {
    const value = numbers.get(name);
    if (value === null) problems.push(`${name}: ${t("is not a number this form can send")}`);
    else if (INTEGER_CUTS.has(name) && !Number.isInteger(value)) problems.push(`${name}: ${t("counts cells, donors, neighbours or cards, so it has to be a whole number")}`);
  }
  const continuation = numbers.get("alignment_continuation_min");
  const uncertain = numbers.get("alignment_uncertain_min");
  if (continuation !== undefined && uncertain !== undefined && continuation !== null && uncertain !== null && uncertain > continuation)
    problems.push(t("alignment_uncertain_min has to sit at or below alignment_continuation_min"));
  const inside = numbers.get("detection_frequency_in_min");
  const outside = numbers.get("detection_frequency_excluded_max");
  if (inside !== undefined && outside !== undefined && inside !== null && outside !== null && outside >= inside)
    problems.push(t("the outside-group ceiling has to be below the in-group floor"));

  function submitConfirmation() {
    if (problems.length) return;
    const thresholds: Record<string, unknown> = { version: version.trim() };
    for (const name of cutKeys) {
      const value = numbers.get(name);
      thresholds[name] = INTEGER_CUTS.has(name) ? Math.trunc(value as number) : value;
    }
    void submit
      .run(() => projectsApi.confirmThresholds(caller, project.project_id, thresholds, rationale.trim()))
      .then((answered) => {
        if (!answered) return;
        setEdits({});
        setVersion("");
        setRationale("");
        onConfirmed();
      });
  }

  const conflict =
    submit.error instanceof ApiProblem && submit.error.class === "conflict" && submit.error.field === "thresholds.version";

  return (
    <div id="thresholds">
      <Panel
        title={t("Decision thresholds")}
        note={t(
          "§1.5: the numbers every candidate is judged against. Until a principal investigator attests them, a run is still computed, drafted and queued - only signing a conclusion and exporting stay closed.",
        )}
      >
        <div className="rows">
          <div>
            <span className="muted">{t("in force")}</span> <span className="mono">{currentVersion}</span>
          </div>
          <div>
            <span className="muted">{t("attested")}</span>{" "}
            {confirmed ? t("yes - conclusions may be signed and exported") : t("no - sign-off and export are closed")}
          </div>
        </div>

        {confirmed ? (
          <Notice>
            {t("a confirmed set is not frozen: attesting another version replaces it, and the cards computed against the old one keep naming it.")}
          </Notice>
        ) : null}

        {identity && !identity.mfaVerified ? (
          <p className="muted">
            {t("this act asks the API for a second factor; a token without one is refused here, not by this screen.")}
          </p>
        ) : null}

        <details className="advanced">
          <summary>{confirmed ? t("attest a replacement cut set") : t("attest the measured cut set")}</summary>
          <p className="muted">
            {t(
              "every field the project carries is sent back, touched or not - that is what makes an untouched placeholder visible instead of silent.",
            )}
          </p>

          <div className="grid">
            <Field label="version" hint={t("the name the products cite; it has to differ from the one in force")}>
              <input className="mono" value={version} onChange={(event) => setVersion(event.target.value)} placeholder={t("name the set you measured")} />
            </Field>
          </div>

          <Field
            label="rationale"
            hint={t("what was measured, on which data, and where the measurement lives; stored in the audit exactly as typed")}
          >
            <input value={rationale} onChange={(event) => setRationale(event.target.value)} placeholder={t("state your own measurement")} />
          </Field>

          {CUT_GROUPS.filter((group) => group.keys.some((name) => cutKeys.includes(name))).map((group) => (
            <div key={group.title}>
              <h3>
                {t(group.title)} <span className="muted">· {group.keys.length}</span>
              </h3>
              <div className="grid">
                {group.keys
                  .filter((name) => cutKeys.includes(name))
                  .map((name) => (
                    <Field key={name} label={name} hint={CUT_NOTES[name] ? t(CUT_NOTES[name]) : undefined}>
                      <input
                        className="mono"
                        value={shown(name)}
                        inputMode={INTEGER_CUTS.has(name) ? "numeric" : "decimal"}
                        onChange={(event) => setEdits((current) => ({ ...current, [name]: event.target.value }))}
                      />
                    </Field>
                  ))}
              </div>
            </div>
          ))}

          {rest.length ? (
            <div>
              <h3>
                {t("carried by the record, unnamed here")} <span className="muted">· {rest.length}</span>
              </h3>
              <p className="muted">
                {t("the schema gained a cut this screen has not been told about; it is sent as it arrived unless you change it.")}
              </p>
              <div className="grid">
                {rest.map((name) => (
                  <Field key={name} label={name}>
                    <input
                      className="mono"
                      value={shown(name)}
                      onChange={(event) => setEdits((current) => ({ ...current, [name]: event.target.value }))}
                    />
                  </Field>
                ))}
              </div>
            </div>
          ) : null}

          <div className="rows">
            <div>
              <span className="muted">{t("changed from the set in force")}</span> {changed.length} / {cutKeys.length}
            </div>
            <div>
              <span className="muted">{t("carried over unchanged")}</span> {carried}
            </div>
          </div>
          {changed.length ? (
            <ul>
              {changed.map((name) => (
                <li key={name} className="mono">
                  {name}: {String(inForce[name])} → {shown(name).trim()}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{t("no number has been changed yet, so this would attest the placeholders as measured.")}</p>
          )}

          {problems.length ? (
            <div className="error">
              <div>{t("this form is not ready to send")}</div>
              <ul>
                {problems.map((one) => (
                  <li key={one}>{one}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {conflict ? (
            <p className="muted">
              {t("the API says that version name is already the one in force - name the new measurement differently.")}
            </p>
          ) : null}

          <ProblemNote error={submit.error} />
          <Notice>{t("refusals are quoted as the API wrote them.")}</Notice>

          <button type="button" disabled={submit.busy || problems.length > 0} onClick={submitConfirmation}>
            {submit.busy ? t("confirming…") : t("Confirm the measured cut set")}
          </button>
        </details>
      </Panel>
    </div>
  );
}
