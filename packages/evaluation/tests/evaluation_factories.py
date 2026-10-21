"""Shared factories for the evaluation tests.

The fixtures are written to clear section 10.5's floors rather than to be small, because a test that relaxed a
floor everywhere would stop noticing when production code ignored one. Only the two counts no hand-built set
can reach - twenty counterexample classes and fifty retrieval queries - are lowered, and they are lowered in
one place.
"""

from datetime import date, datetime

from bioadapt_evaluation.blind import DELIVERY_FIELD_ORDER, REQUIRED_FIGURES
from bioadapt_evaluation.blind import (
    BaselineArm,
    BlindPacket,
    BlindReview,
    BlindScore,
    Reviewer,
)
from bioadapt_evaluation.leakage import BenchmarkSource, CandidateSource, RetrievalJudgement, SourceKind
from bioadapt_evaluation.metrics import MetricId, MetricObservation
from bioadapt_evaluation.release import GateSignal, HarmLevel, RedTeamCase
from bioadapt_evaluation.truth import (
    Adjudication,
    AnnotationCall,
    BlindAnnotation,
    RetrievalQuery,
    TruthClass,
    TruthFloors,
    TruthSet,
    TruthStratum,
)
from bioadapt_schemas.enums import LabelProvenance, LicenseReviewStatus

TODAY = date(2026, 5, 1)
MADE_AT = datetime(2026, 4, 20, 9, 0)
DATASET_A = "ds_pbmc_01"
DATASET_B = "ds_pbmc_02"
DATASET_C = "ds_pbmc_03"
RARE = "rare_state"
COMMON = "cd14_monocyte"
DOUBLET = "doublet_artefact"
AMBIENT = "ambient_rna_artefact"
EXPERT_1 = "expert_01"
EXPERT_2 = "expert_02"
LEAD = "evaluation_lead_01"
RUN_ID = "run_0001"
BENCHMARK_RUN = "eval_0001"
COMPARISON = "cmp_0001"


def floors(**overrides) -> TruthFloors:
    """Section 10.5's floors, with only the two corpus counts lowered to what a fixture can hold."""
    fields = {"min_counterexample_classes": 2, "min_retrieval_pairs": 2}
    fields.update(overrides)
    return TruthFloors(**fields)


def truth_class(label: str = RARE, dataset_id: str = DATASET_A, **overrides) -> TruthClass:
    fields = {
        "label": label,
        "dataset_id": dataset_id,
        "stratum": TruthStratum.RARE,
        "cell_count": 400,
        "donor_count": 4,
        "label_provenance": LabelProvenance.CURATED_EXPERT,
    }
    fields.update(overrides)
    return TruthClass(**fields)


def common_class(dataset_id: str) -> TruthClass:
    return truth_class(
        COMMON,
        dataset_id,
        stratum=TruthStratum.COMMON,
        cell_count=5000,
        donor_count=6,
    )


def counterexample_class(label: str) -> TruthClass:
    return truth_class(
        label,
        DATASET_A,
        stratum=TruthStratum.COUNTEREXAMPLE,
        cell_count=300,
        donor_count=3,
        synthetic=True,
        license_review=LicenseReviewStatus.APPROVED,
        source_ref="generated_doublet_sim_v1",
    )


def annotation(
    annotator_id: str = EXPERT_1,
    label: str = RARE,
    dataset_id: str = DATASET_A,
    **overrides,
) -> BlindAnnotation:
    fields = {
        "annotation_id": f"ann_{annotator_id}_{label}_{dataset_id}",
        "annotator_id": annotator_id,
        "label": label,
        "dataset_id": dataset_id,
        "call": AnnotationCall.CONFIRMED,
        "made_at": MADE_AT,
    }
    fields.update(overrides)
    return BlindAnnotation(**fields)


def adjudication(label: str = RARE, dataset_id: str = DATASET_A, **overrides) -> Adjudication:
    fields = {
        "dataset_id": dataset_id,
        "label": label,
        "annotator_ids": [EXPERT_1, EXPERT_2],
        "decided_by": LEAD,
        "resolution": AnnotationCall.CONFIRMED,
        "reason": "the two reviewers read different cluster resolutions; the frozen marker panel settles it",
        "decided_at": MADE_AT,
    }
    fields.update(overrides)
    return Adjudication(**fields)


def retrieval_query(query_id: str = "q_01", **overrides) -> RetrievalQuery:
    fields = {
        "query_id": query_id,
        "question": "which population carries the interferon signature in this donor set",
        "expected_evidence_refs": ["ev_marker_01", "ev_de_02"],
    }
    fields.update(overrides)
    return RetrievalQuery(**fields)


def blind_pair(label: str = RARE, dataset_id: str = DATASET_A, **overrides) -> list[BlindAnnotation]:
    """Two independent confirmations, which is the minimum section 10.5 accepts as an answer."""
    call = overrides.pop("call", AnnotationCall.CONFIRMED)
    return [
        annotation(EXPERT_1, label, dataset_id, call=call, **overrides),
        annotation(EXPERT_2, label, dataset_id, call=call, **overrides),
    ]


def truth_set(**overrides) -> TruthSet:
    """A frozen set that clears every floor: a curated rare class, a common one across three datasets, two
    synthetic counterexamples and two retrieval queries."""
    fields = {
        "truth_set_id": "ts_pbmc_rare",
        "version": "1",
        "frozen_on": TODAY,
        "purpose": "score rare population recovery against independently curated labels",
        "floors": floors(),
        "classes": [
            truth_class(),
            common_class(DATASET_A),
            common_class(DATASET_B),
            common_class(DATASET_C),
            counterexample_class(DOUBLET),
            counterexample_class(AMBIENT),
        ],
        "annotations": [
            *blind_pair(RARE, DATASET_A),
            *blind_pair(COMMON, DATASET_A),
            *blind_pair(COMMON, DATASET_B),
            *blind_pair(COMMON, DATASET_C),
            *blind_pair(DOUBLET, DATASET_A),
            *blind_pair(AMBIENT, DATASET_A),
        ],
        "retrieval_queries": [retrieval_query("q_01"), retrieval_query("q_02")],
    }
    fields.update(overrides)
    return TruthSet(**fields)


def marked(object_id: str, **overrides) -> BenchmarkSource:
    fields = {
        "object_id": object_id,
        "kind": SourceKind.DATASET,
        "benchmark_run_id": BENCHMARK_RUN,
        "marked_on": date(2026, 4, 1),
    }
    fields.update(overrides)
    return BenchmarkSource(**fields)


def used(object_id: str, used_for: str = "claim_01", **overrides) -> CandidateSource:
    fields = {
        "object_id": object_id,
        "kind": SourceKind.RETRIEVED_FRAGMENT,
        "used_for": used_for,
    }
    fields.update(overrides)
    return CandidateSource(**fields)


def judgement(
    query_id: str = "q_01",
    evidence_ref: str = "ev_marker_01",
    **overrides,
) -> RetrievalJudgement:
    fields = {
        "query_id": query_id,
        "evidence_ref": evidence_ref,
        "retrieved": True,
        "supports": True,
        "judged_by": "reviewer_01",
    }
    fields.update(overrides)
    return RetrievalJudgement(**fields)


def packet(packet_id: str = "pkt_a", comparison_id: str = COMPARISON, **overrides) -> BlindPacket:
    """One arm written into the delivery template, with no source identifier anywhere in it."""
    values = {
        "study_goal": "find the population that carries the interferon signature",
        "analysis_question": "is there a stable rare group across donors",
        "candidate": "candidate 001",
        "claim": "a rare CD14-negative group recurs in three of four donors",
        "supporting_evidence": "marker panel, per-donor counts",
        "contradicting_evidence": "one donor loses it at a coarser resolution",
        "abstention": "no claim made about the fourth donor",
        "reviewer_actions_needed": "confirm the marker panel against the reference",
    }
    written = overrides.pop("values", None)
    if written is not None:
        values = dict(zip(DELIVERY_FIELD_ORDER, written, strict=True))
    fields = {
        "packet_id": packet_id,
        "comparison_id": comparison_id,
        "fields": values,
        "figures": list(REQUIRED_FIGURES),
    }
    fields.update(overrides)
    return BlindPacket(**fields)


def reviewer(reviewer_id: str, **overrides) -> Reviewer:
    return Reviewer(reviewer_id=reviewer_id, **overrides)


def arm(arm_id: str, analyst_id: str, **overrides) -> BaselineArm:
    fields = {"arm_id": arm_id, "analyst_id": analyst_id, "workflow": "seurat_manual", "conclusions": 3, "hours": 9.0}
    fields.update(overrides)
    return BaselineArm(**fields)


#: Reviewers who mostly agree, which is what section 10.7 asks for before two arms become one number.
AGREEING = {"pkt_a": (4, 4, 3), "pkt_b": (5, 5, 4), "pkt_c": (2, 2, 3), "pkt_d": (5, 5, 5)}
#: Reviewers who read the same packet differently, which section 10.7 refuses to average.
DISAGREEING = {"pkt_a": (4, 4, 5), "pkt_b": (2, 5, 2)}
REVIEWERS = ("reviewer_01", "reviewer_02", "reviewer_03")


def review(
    *,
    credibility: dict[str, tuple[int, int, int]] = AGREEING,
    external: bool = True,
    **overrides,
) -> BlindReview:
    """A comparison with a full roster, every packet scored, and two traditional baselines by two analysts."""
    ids = list(credibility)
    scores = [
        BlindScore(
            reviewer_id=name,
            packet_id=packet_id,
            credibility=credibility[packet_id][position],
            conclusions_to_revise=position,
            reading_seconds=600 + 60 * position,
        )
        for packet_id in ids
        for position, name in enumerate(REVIEWERS)
    ]
    fields = {
        "comparison_id": COMPARISON,
        "packets": [packet(one, COMPARISON) for one in ids],
        "reviewers": [
            Reviewer(
                reviewer_id=name,
                involved_in_development=not external or name != REVIEWERS[-1],
            )
            for name in REVIEWERS
        ],
        "scores": scores,
        "baseline_arms": [arm("base_1", "analyst_01"), arm("base_2", "analyst_02", workflow="manual_gate_flow")],
    }
    fields.update(overrides)
    return BlindReview(**fields)


def observation(metric_id: MetricId, **overrides) -> MetricObservation:
    fields = {
        "metric_id": metric_id,
        "numerator": None,
        "denominator": None,
        "collected_on": TODAY,
    }
    fields.update(overrides)
    return MetricObservation(**fields)


#: One measurement per row that satisfies it, so a test can ask whether any row is impossible to pass.
PASSING: dict[MetricId, dict] = {
    MetricId.RARE_CLASS_RECALL: {
        "numerator": 45, "denominator": 60, "baseline": 0.6, "interval": (0.70, 0.80),
    },
    MetricId.ABSTENTION_QUALITY: {
        "numerator": 9, "denominator": 100, "baseline": 0.2, "interval": (0.05, 0.13),
        "companions": {"coarse_label_share": 0.10},
        "companion_baselines": {"coarse_label_share": 0.10},
    },
    MetricId.CALIBRATION: {"value": 0.07},
    MetricId.CITATION_TRACEABILITY: {"numerator": 97, "denominator": 100},
    MetricId.SUPPORT_VALIDITY: {"numerator": 88, "denominator": 100},
    MetricId.UNSUPPORTED_CONCLUSION_RATE: {"numerator": 1, "denominator": 100},
    MetricId.EXPERT_REVISION_RATE: {"numerator": 20, "denominator": 100},
    MetricId.REWORK_RATE: {
        "numerator": 4, "denominator": 5,
        "segments": {"reruns": 3.0, "revisions": 1.0},
    },
    MetricId.END_TO_END_LATENCY: {
        "value": 3000.0,
        "segments": {"platform_compute": 900.0, "queue_wait": 600.0, "review_wait": 1500.0},
    },
    MetricId.COMPUTE_REPRODUCIBILITY: {
        "value": 1.0,
        "levels": {"byte": 1.0, "numeric": 0.98, "conclusion": 1.0},
    },
    MetricId.SAFETY_GATES: {"numerator": 50, "denominator": 50},
    MetricId.BYPASS_RATE: {"numerator": 5, "denominator": 100},
    MetricId.KNOWLEDGE_REVIEW_THROUGHPUT: {"numerator": 88, "denominator": 2},
    MetricId.OPEN_DISAGREEMENT_RATE: {"numerator": 3, "denominator": 100},
}


def passing_observation(metric_id: MetricId) -> MetricObservation:
    return observation(metric_id, **PASSING[metric_id])


def red_case(case_id: str, harm_level: HarmLevel, **overrides) -> RedTeamCase:
    fields = {
        "case_id": case_id,
        "harm_level": harm_level,
        "control": "cross_project_access",
        "passed": True,
        "opened_on": date(2026, 4, 20),
    }
    fields.update(overrides)
    return RedTeamCase(**fields)


def corpus(
    *,
    high: int = 50,
    medium: int = 20,
    low: int = 5,
    high_failures: tuple[str, ...] = (),
    medium_failures: dict[str, date | None] | None = None,
) -> list[RedTeamCase]:
    """A red-team run at section 10.8's floors, with named failures on top."""
    medium_deadline = date(2026, 6, 30)
    cases = [
        red_case(f"high_{index:03d}", HarmLevel.HIGH, passed=f"high_{index:03d}" not in high_failures)
        for index in range(high)
    ]
    cases += [
        red_case(
            f"med_{index:03d}",
            HarmLevel.MEDIUM,
            control="deletion_residue",
            fix_deadline=(medium_failures or {}).get(f"med_{index:03d}", medium_deadline),
            passed=f"med_{index:03d}" not in (medium_failures or {}),
        )
        for index in range(medium)
    ]
    cases += [
        red_case(f"low_{index:03d}", HarmLevel.LOW, control="dependency_scan")
        for index in range(low)
    ]
    return cases


def signal(trigger, subject_id: str = "cand_001_run_0001", **overrides) -> GateSignal:
    return GateSignal(
        trigger=trigger,
        subject_id=subject_id,
        detail=overrides.pop("detail", "the run recorded it"),
        **overrides,
    )
