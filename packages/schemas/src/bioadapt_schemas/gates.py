"""Quality gate rules as executable code (plan V2.0 sections 1.5, 3.3, 5.4, 7.2, 7.5, 7.8, 13.4)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .entities import Contract
from .enums import (
    CandidateType,
    CrossModalStatus,
    EvidenceKind,
    FieldSource,
    FinalStatus,
    PerturbationKind,
)
from .evidence import (
    ArtifactScreening,
    ContinuousStateCheck,
    Evidence,
    EvidenceCard,
    NoveltyVsCoverage,
    StabilitySummary,
)
from .thresholds import DEFAULT_THRESHOLDS, Thresholds

STATUS_ORDER = [
    FinalStatus.REJECTED,
    FinalStatus.UNKNOWN,
    FinalStatus.CONFLICT,
    FinalStatus.PROVISIONAL,
    FinalStatus.SUPPORTED,
]

ALLOWED_NUMERIC_SOURCES = (FieldSource.TOOL, FieldSource.HUMAN)


class GateViolation(ValueError):
    pass


class Verdict(Contract):
    passed: bool
    reasons: list[str] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)


class MarkerStatistics(BaseModel):
    """Tool-produced statistics for one candidate marker, as required by section 7.8."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gene: str
    fdr: float = Field(ge=0, le=1)
    log2fc: float
    detection_frequency_in: float = Field(ge=0, le=1)
    detection_frequency_excluded: float = Field(ge=0, le=1)
    donor_direction_consistency: float = Field(ge=0, le=1)
    multiplicity_strategy: str


def _rank(status: FinalStatus) -> int:
    return STATUS_ORDER.index(status)


def _min_status(a: FinalStatus, b: FinalStatus) -> FinalStatus:
    return a if _rank(a) <= _rank(b) else b


def iter_numeric_leaves(payload: Any, prefix: str = "") -> Iterator[tuple[str, float | int]]:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(warnings=False)
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield from iter_numeric_leaves(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            yield from iter_numeric_leaves(value, f"{prefix}[{index}]")
    elif isinstance(payload, bool):
        return
    elif isinstance(payload, (int, float)):
        yield prefix, payload


def _segment_matches(pattern: str, segment: str) -> bool:
    if pattern == "*" or pattern == segment:
        return True
    p_base, _, p_index = pattern.partition("[")
    s_base, _, s_index = segment.partition("[")
    return p_base == s_base and p_index.rstrip("]") == "*" and bool(s_index)


def _path_matches(pattern: str, path: str) -> bool:
    pattern_parts, path_parts = pattern.split("."), path.split(".")
    if len(pattern_parts) != len(path_parts):
        return False
    return all(_segment_matches(p, q) for p, q in zip(pattern_parts, path_parts, strict=True))


def audited_numeric_paths(card: EvidenceCard) -> list[str]:
    return [path for path, _ in iter_numeric_leaves(_audited_payload(card))]


def numeric_provenance_violations(card: EvidenceCard) -> list[str]:
    violations: list[str] = []
    for path in audited_numeric_paths(card):
        entry = card.field_provenance.get(path)
        if entry is None and not any(_path_matches(p, path) for p in card.field_provenance):
            violations.append(f"{path}: no provenance entry")
            continue
        matched = entry or next(
            (card.field_provenance[p] for p in card.field_provenance if _path_matches(p, path)), None
        )
        if matched and matched.source not in ALLOWED_NUMERIC_SOURCES:
            violations.append(f"{path}: numeric value sourced from {matched.source}")
    return violations


def _audited_payload(card: EvidenceCard) -> dict[str, Any]:
    return {
        name: getattr(card, name)
        for name in (
            "stability_summary",
            "novelty_vs_coverage",
            "composition_check",
            "identity_vs_state",
            "evidence",
        )
    }


def rare_verdict(
    size: int,
    total_cells: int,
    donor_count: int,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> Verdict:
    share = size / total_cells if total_cells else 1.0
    reasons: list[str] = []
    if share >= thresholds.rare_share_max:
        reasons.append(f"share {share:.4f} is not below rare ceiling {thresholds.rare_share_max}")
    if size < thresholds.rare_min_cells:
        reasons.append(f"size {size} below minimum {thresholds.rare_min_cells}")
    if donor_count < thresholds.rare_min_donors:
        reasons.append(f"donor_count {donor_count} below minimum {thresholds.rare_min_donors}")
    return Verdict(passed=not reasons, reasons=reasons, detail={"share": share})


def stability_verdict(
    summary: Optional[StabilitySummary],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> Verdict:
    if summary is None:
        return Verdict(passed=False, reasons=["no stability summary"])
    mandatory = set(PerturbationKind.mandatory_protocol())
    present = {r.kind for r in summary.results}
    missing = sorted(mandatory - present, key=str)
    if missing and not summary.approved_deviation:
        return Verdict(
            passed=False,
            reasons=[f"protocol incomplete, missing: {missing}"],
            detail={"missing": [str(m) for m in missing]},
        )
    evaluated = list(summary.results)
    passed_kinds, failed_kinds = [], []
    for result in evaluated:
        ok = (
            result.cc_ari is not None
            and result.jaccard is not None
            and result.cc_ari >= thresholds.stability_cc_ari_min
            and result.jaccard >= thresholds.stability_jaccard_min
        )
        (passed_kinds if ok else failed_kinds).append(str(result.kind))
    ratio = len(passed_kinds) / len(evaluated) if evaluated else 0.0
    notes = [f"deviation recorded for missing perturbations: {missing}"] if summary.approved_deviation and missing else []
    reasons = []
    if ratio < thresholds.stability_pass_ratio:
        reasons.append(
            f"passed {len(passed_kinds)}/{len(evaluated)} perturbations, below {thresholds.stability_pass_ratio}"
        )
    return Verdict(
        passed=ratio >= thresholds.stability_pass_ratio,
        reasons=reasons,
        detail={"passed": passed_kinds, "failed": failed_kinds, "ratio": ratio, "notes": notes},
    )


def marker_support_verdict(
    stats: MarkerStatistics,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> Verdict:
    reasons: list[str] = []
    if stats.fdr > thresholds.de_fdr_max:
        reasons.append(f"{stats.gene}: fdr {stats.fdr} above {thresholds.de_fdr_max}")
    if abs(stats.log2fc) < thresholds.de_min_abs_log2fc:
        reasons.append(f"{stats.gene}: |log2fc| {abs(stats.log2fc)} below {thresholds.de_min_abs_log2fc}")
    if stats.detection_frequency_in < thresholds.detection_frequency_in_min:
        reasons.append(
            f"{stats.gene}: in-group detection {stats.detection_frequency_in} below {thresholds.detection_frequency_in_min}"
        )
    if stats.detection_frequency_excluded > thresholds.detection_frequency_excluded_max:
        reasons.append(
            f"{stats.gene}: excluded detection {stats.detection_frequency_excluded} above "
            f"{thresholds.detection_frequency_excluded_max}"
        )
    if stats.donor_direction_consistency < thresholds.donor_consistency_min:
        reasons.append(
            f"{stats.gene}: donor consistency {stats.donor_direction_consistency} below {thresholds.donor_consistency_min}"
        )
    return Verdict(passed=not reasons, reasons=reasons, detail={"gene": stats.gene})


def support_rule_verdict(
    markers: Sequence[MarkerStatistics],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    *,
    require_min_markers: int = 1,
) -> Verdict:
    if not markers:
        return Verdict(passed=False, reasons=["no marker statistics"])
    qualifying = [m for m in markers if marker_support_verdict(m, thresholds).passed]
    reasons = []
    if len(qualifying) < require_min_markers:
        reasons.append(f"{len(qualifying)} qualifying markers, need >= {require_min_markers}")
    if all(m.multiplicity_strategy in ("none", "") for m in markers):
        reasons.append("multiplicity strategy not recorded")
    return Verdict(passed=not reasons, reasons=reasons, detail={"qualifying": [m.gene for m in qualifying]})


def artifact_verdict(screening: Optional[ArtifactScreening]) -> Verdict:
    if screening is None:
        return Verdict(passed=False, reasons=["artifact screening not run"])
    reasons = [f"blocked by {risk}" for risk in screening.blocked_by]
    reasons += [f"not screened: {risk}" for risk in screening.not_screened]
    return Verdict(passed=not reasons, reasons=reasons)


def novelty_verdict(
    coverage: Optional[NoveltyVsCoverage],
    stability: Verdict,
    artifacts: Verdict,
    continuous_state: Optional[ContinuousStateCheck],
) -> Verdict:
    if coverage is None:
        return Verdict(passed=False, reasons=["no coverage assessment"])
    reasons: list[str] = []
    if not coverage.coverage_gap:
        reasons.append("reference covers this set")
    if not stability.passed:
        reasons.append("not stable")
    if not artifacts.passed:
        reasons.append("artifacts not excluded")
    if continuous_state and continuous_state.is_axis_tail:
        reasons.append("consistent with the tail of a known continuous axis")
    return Verdict(passed=not reasons, reasons=reasons)


def independent_support_groups(card: EvidenceCard) -> set[str]:
    groups: set[str] = set()
    for eid in card.supporting_evidence_ids:
        evidence = card.evidence[eid]
        groups.add(evidence.independence_group or eid)
    return groups


def _supporting(card: EvidenceCard, kind: EvidenceKind) -> list[Evidence]:
    return [
        card.evidence[eid]
        for eid in card.supporting_evidence_ids
        if eid in card.evidence and card.evidence[eid].kind is kind
    ]


def _independent_groups(card: EvidenceCard, kind: EvidenceKind) -> set[str]:
    """Independence counted per evidence record's own group, because two records of one atlas is one source."""
    return {e.independence_group or e.evidence_id for e in _supporting(card, kind)}


def _artifact_screening_recorded(card: EvidenceCard) -> bool:
    return card.artifact_screening is not None


def _coverage_measured(card: EvidenceCard) -> bool:
    return card.novelty_vs_coverage is not None or bool(_supporting(card, EvidenceKind.REFERENCE_COVERAGE))


def _robustness_measured(card: EvidenceCard) -> bool:
    return card.stability_summary is not None or bool(_supporting(card, EvidenceKind.STABILITY))


def _marker_support_measured(card: EvidenceCard) -> bool:
    return bool(_supporting(card, EvidenceKind.MARKER_SPECIFICITY))


def _reference_applicability_recorded(card: EvidenceCard) -> bool:
    """Section 7.2's C2 asks for both branches of a disagreement, so one reference mapping is not enough."""
    return len(_independent_groups(card, EvidenceKind.REFERENCE_MAPPING)) >= 2


def _independent_confirmation_recorded(card: EvidenceCard) -> bool:
    return bool(card.confirmation_evidence_ids)


def _cross_donor_consistency_measured(card: EvidenceCard) -> bool:
    summary = card.stability_summary
    if summary is None:
        return False
    return any(result.kind is PerturbationKind.LEAVE_ONE_DONOR for result in summary.results)


def _continuous_state_checked(card: EvidenceCard) -> bool:
    if card.identity_vs_state.continuous_state_check is not None:
        return True
    return bool(_supporting(card, EvidenceKind.CONTINUOUS_STATE))


def _per_modality_evidence_recorded(card: EvidenceCard) -> bool:
    return len(_independent_groups(card, EvidenceKind.CROSS_MODAL)) >= 2


def _conflict_adjudicated(card: EvidenceCard) -> bool:
    """The comparison has to have been made and settled: an unexamined or still-open conflict is a verdict."""
    return card.cross_modal_status is not CrossModalStatus.NOT_YET_VALIDATED and not card.unresolved_conflict


def _composition_test_recorded(card: EvidenceCard) -> bool:
    check = card.composition_check
    return check.method is not None and check.result is not None


def _multiplicity_recorded(card: EvidenceCard) -> bool:
    return card.composition_check.multiplicity_strategy is not None


#: Section 7.2 replaces "special cell" with five candidate classes, each with the evidence set its claim
#: cannot stand without. The pairs are read as (what a reviewer would look for, the card-side check), so
#: the requirement is executable rather than prose.
MINIMUM_EVIDENCE_SETS: dict[CandidateType, tuple[tuple[str, Callable[[EvidenceCard], bool]], ...]] = {
    CandidateType.C1_UNASSIGNED: (
        ("artefact screening recorded", _artifact_screening_recorded),
        ("reference coverage measured", _coverage_measured),
        ("robustness protocol run", _robustness_measured),
        ("marker support measured", _marker_support_measured),
    ),
    CandidateType.C2_REFERENCE_CONFLICT: (
        ("both reference sources' applicability recorded", _reference_applicability_recorded),
        ("exclusionary marker recorded", _marker_support_measured),
        ("independent confirmation recorded", _independent_confirmation_recorded),
    ),
    CandidateType.C3_RARE_STABLE: (
        ("robustness protocol run", _robustness_measured),
        ("cross-donor consistency measured", _cross_donor_consistency_measured),
        ("artefact screening recorded", _artifact_screening_recorded),
        ("continuous-state check recorded", _continuous_state_checked),
    ),
    CandidateType.C4_CROSS_MODAL_DISCORDANT: (
        ("per-modality independent evidence recorded", _per_modality_evidence_recorded),
        ("conflict adjudicated", _conflict_adjudicated),
    ),
    CandidateType.C5_COMPOSITION_OUTLIER: (
        ("composition method and result recorded", _composition_test_recorded),
        ("multiplicity strategy recorded", _multiplicity_recorded),
    ),
}

#: Section 7.2's last column. C1, C2, C3 and C4 can reach ``supported`` - C1 and C3 only through the
#: confirmation rule already below, which is what their "when there is no independent confirmation" notes
#: say - and C5 cannot, because a composition outlier is a descriptive claim about a distribution, not an
#: identity claim about a cell.
CANDIDATE_CLASS_CEILINGS: dict[CandidateType, FinalStatus] = {
    CandidateType.C1_UNASSIGNED: FinalStatus.SUPPORTED,
    CandidateType.C2_REFERENCE_CONFLICT: FinalStatus.SUPPORTED,
    CandidateType.C3_RARE_STABLE: FinalStatus.SUPPORTED,
    CandidateType.C4_CROSS_MODAL_DISCORDANT: FinalStatus.SUPPORTED,
    CandidateType.C5_COMPOSITION_OUTLIER: FinalStatus.PROVISIONAL,
}

#: One step down the ladder a class claim falls when its own evidence set is incomplete. ``conflict`` is
#: skipped: it means two records disagree, not that a card holds too little.
_CLASS_FALLBACK = {
    FinalStatus.SUPPORTED: FinalStatus.PROVISIONAL,
    FinalStatus.PROVISIONAL: FinalStatus.UNKNOWN,
}


def candidate_class_gate(card: EvidenceCard) -> Verdict:
    """Section 7.2: does a card carry the evidence set for every candidate class it claims?

    A card may claim several classes, and each claim carries its own set, so the gate reads the union. A
    class whose set is incomplete is capped one step below its own ceiling: an unconfirmed C1 or C3 stays
    provisional, and a C5 that never ran its composition test has not earned even a descriptive label, so
    it reads unknown.
    """
    ceilings: dict[str, str] = {}
    reasons: list[str] = []
    missing: dict[str, list[str]] = {}
    ceiling = FinalStatus.SUPPORTED
    for claimed in card.candidate_types:
        absent = [label for label, held in MINIMUM_EVIDENCE_SETS[claimed] if not held(card)]
        class_ceiling = CANDIDATE_CLASS_CEILINGS[claimed]
        if absent:
            missing[claimed.value] = absent
            class_ceiling = _min_status(class_ceiling, _CLASS_FALLBACK[CANDIDATE_CLASS_CEILINGS[claimed]])
            reasons.append(f"{claimed.value} is missing its minimum evidence set: {absent}")
        ceilings[claimed.value] = class_ceiling
        ceiling = _min_status(ceiling, class_ceiling)
    return Verdict(
        passed=not missing,
        reasons=reasons,
        detail={"ceilings": ceilings, "missing": missing, "ceiling": ceiling},
    )


def final_status_ceiling(
    card: EvidenceCard,
    *,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    support_met: Optional[bool] = None,
    underpowered_design: bool = False,
) -> Verdict:
    """Ceiling on final_status given evidence structure (sections 1.5, 3.3, 5.4, 7.4)."""
    ceiling = FinalStatus.SUPPORTED
    reasons: list[str] = []
    notes: list[str] = []

    if not card.confirmation_evidence_ids:
        ceiling = _min_status(ceiling, FinalStatus.PROVISIONAL)
        reasons.append("no independent confirmation evidence")

    if card.unresolved_conflict:
        reasons.append("unresolved conflict present")
        ceiling = _min_status(ceiling, FinalStatus.CONFLICT)

    screening = artifact_verdict(card.artifact_screening)
    if not screening.passed:
        ceiling = _min_status(ceiling, FinalStatus.PROVISIONAL)
        reasons.extend(screening.reasons)

    stability = stability_verdict(card.stability_summary, thresholds)
    if not stability.passed:
        ceiling = _min_status(ceiling, FinalStatus.PROVISIONAL)
        reasons.extend(stability.reasons)

    if support_met is False:
        ceiling = _min_status(ceiling, FinalStatus.PROVISIONAL)
        reasons.append("support rule not met")

    if not card.supporting_evidence_ids:
        ceiling = _min_status(ceiling, FinalStatus.UNKNOWN)
        reasons.append("no supporting evidence")

    if card.supporting_evidence_ids and len(independent_support_groups(card)) < 2:
        notes.append("supporting evidence shares a single independence group")

    if underpowered_design and CandidateType.C5_COMPOSITION_OUTLIER in card.candidate_types:
        ceiling = _min_status(ceiling, FinalStatus.PROVISIONAL)
        reasons.append("design is underpowered for group comparisons")

    #: Section 7.2: the class the card claims sets both a ceiling and an evidence set of its own.
    classes = candidate_class_gate(card)
    ceiling = _min_status(ceiling, classes.detail["ceiling"])
    reasons.extend(classes.reasons)

    return Verdict(
        passed=not reasons,
        reasons=reasons,
        detail={"ceiling": ceiling, "notes": notes, "candidate_classes": classes.detail},
    )


def enforce_card_status(
    card: EvidenceCard,
    *,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    support_met: Optional[bool] = None,
    underpowered_design: bool = False,
) -> Verdict:
    violations = numeric_provenance_violations(card)
    if violations:
        raise GateViolation("; ".join(violations))
    verdict = final_status_ceiling(
        card,
        thresholds=thresholds,
        support_met=support_met,
        underpowered_design=underpowered_design,
    )
    ceiling = verdict.detail["ceiling"]
    if _rank(card.final_status) > _rank(ceiling):
        raise GateViolation(
            f"final_status {card.final_status} exceeds ceiling {ceiling}; reasons: {verdict.reasons}"
        )
    return verdict


def priority_score(impact: int, uncertainty: int, client_ask: bool) -> int:
    if not (0 <= impact <= 3 and 0 <= uncertainty <= 3):
        raise ValueError("impact and uncertainty are 0-3 scores")
    return impact * uncertainty + (2 if client_ask else 0)


def expert_queue(
    card_scores: Iterable[tuple[str, int]],
    limit: Optional[int] = None,
    *,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> tuple[list[str], list[str]]:
    """Section 7.2's queue rule: at most the recorded per-project cap reaches an expert, the rest is filed.

    The cap is a threshold rather than a call-site number, so a project that changes it changes a versioned
    record the card cites, and the low-score cards are not dropped: ``observed`` is the archive the plan
    asks for, still delivered in the report appendix.
    """
    cap = thresholds.expert_queue_card_limit if limit is None else limit
    if cap < 1:
        raise ValueError("the expert queue needs room for at least one card")
    ordered = sorted(card_scores, key=lambda item: (-item[1], item[0]))
    queue = [card_id for card_id, _ in ordered[:cap]]
    observed = [card_id for card_id, _ in ordered[cap:]]
    return queue, observed
