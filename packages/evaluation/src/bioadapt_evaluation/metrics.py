"""The metric dictionary: every release number, its denominator, and what counts as cheating (plan V2.0 10.4, 10.6).

Section 10.6 is a table of fourteen metrics, and its last column is the point of the whole exercise: an
improvement whose confidence interval covers the baseline, a risk-coverage curve bought by labelling
everything coarsely, a reproducibility rate that reports byte-level determinism to hide a GPU, a total
elapsed time with the queue wait inside it. Each of those is a way for a real number to say something
false, so the dictionary carries them as data - the required segments, the required levels, the hard floor
- and one evaluator applies them. The dictionary also holds section 10.4's constraint: before a baseline
exists, a single-number gate is forbidden, and only the safety metrics are exempt because they must block
from phase 1.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Mapping, Optional, Sequence

from pydantic import Field, model_validator

from bioadapt_schemas.entities import Contract, ImmutableContract


class MetricId(StrEnum):
    RARE_CLASS_RECALL = "rare_class_recall"
    ABSTENTION_QUALITY = "abstention_quality"
    CALIBRATION = "calibration"
    CITATION_TRACEABILITY = "citation_traceability"
    SUPPORT_VALIDITY = "support_validity"
    UNSUPPORTED_CONCLUSION_RATE = "unsupported_conclusion_rate"
    EXPERT_REVISION_RATE = "expert_revision_rate"
    REWORK_RATE = "rework_rate"
    END_TO_END_LATENCY = "end_to_end_latency"
    COMPUTE_REPRODUCIBILITY = "compute_reproducibility"
    SAFETY_GATES = "safety_gates"
    BYPASS_RATE = "bypass_rate"
    KNOWLEDGE_REVIEW_THROUGHPUT = "knowledge_review_throughput"
    OPEN_DISAGREEMENT_RATE = "open_disagreement_rate"


class CollectionPoint(StrEnum):
    STAGE5_EVALUATION = "stage5_evaluation"
    EVALUATION = "evaluation"
    L5_VERIFICATION = "l5_verification"
    RETRIEVAL_LAYER = "retrieval_layer"
    KNOWLEDGE_SERVICE = "knowledge_service"
    REVIEW_EVENT = "review_event"
    RUN_MANIFEST = "run_manifest"
    MONITORING = "monitoring"
    REGRESSION_JOB = "regression_job"
    CI_REDTEAM = "ci_redteam"
    PROJECT_REGISTRY = "project_registry"


class Direction(StrEnum):
    ABOVE = "above"
    BELOW = "below"
    #: The target is a relationship to a measured baseline, never a number of its own (§10.4).
    ABOVE_BASELINE = "above_baseline"
    BELOW_BASELINE = "below_baseline"


class MetricStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNMEASURED = "unmeasured"
    #: A number the plan refuses to turn into a gate: reported, with its interval, as a trend.
    TREND_ONLY = "trend_only"


class MetricDefinition(ImmutableContract):
    """One row of section 10.6, with its numerator, denominator and the unacceptable value beside it."""

    metric_id: MetricId
    numerator: str
    denominator: str
    collection_point: CollectionPoint
    direction: Direction
    target: Optional[float] = None
    #: The value that blocks a release on its own, whatever the target says.
    hard_floor: Optional[float] = None
    #: A metric whose target is a comparison must name the baseline it was measured against first.
    baseline_required: bool = False
    #: Safety metrics are hard blocks from phase 1, the one exception section 10.4 grants (§10.4).
    safety: bool = False
    #: Sub-measurements that must all be present, or the aggregate is the number the plan calls cheating.
    requires_segments: tuple[str, ...] = ()
    requires_levels: tuple[str, ...] = ()
    #: A companion ratio that must not move, e.g. the coarse-label share behind an abstention curve.
    requires_stable_companion: Optional[str] = None
    unacceptable: str = Field(min_length=1)

    @model_validator(mode="after")
    def _a_target_is_a_number_or_a_baseline(self) -> "MetricDefinition":
        if self.direction in (Direction.ABOVE, Direction.BELOW) and self.target is None:
            raise ValueError(f"{self.metric_id} states a direction without a number to compare against")
        if self.direction in (Direction.ABOVE_BASELINE, Direction.BELOW_BASELINE) and not self.baseline_required:
            raise ValueError(
                f"{self.metric_id} is stated as a comparison to a baseline but does not require one"
            )
        if self.baseline_required and not self.safety and self.target is not None:
            raise ValueError(
                f"{self.metric_id} asks for a baseline and also names a single number, which section 10.4 "
                "forbids before a baseline exists"
            )
        return self

    def describe(self) -> str:
        return f"{self.numerator} / {self.denominator} at {self.collection_point.value}"


class MetricObservation(ImmutableContract):
    """One measurement as the collection point filed it."""

    metric_id: MetricId
    numerator: Optional[float]
    denominator: Optional[float]
    collected_on: date
    #: The rate itself when the collection point computes it; otherwise derived from the two counts.
    value: Optional[float] = None
    baseline: Optional[float] = None
    interval: Optional[tuple[float, float]] = None
    segments: Mapping[str, float] = Field(default_factory=dict)
    levels: Mapping[str, float] = Field(default_factory=dict)
    #: A companion ratio's current value, e.g. how coarse the labels behind an abstention curve were.
    companions: Mapping[str, float] = Field(default_factory=dict)
    #: The same ratio measured for the baseline arm; a companion is only "stable" against something.
    companion_baselines: Mapping[str, float] = Field(default_factory=dict)
    #: A count of breached controls - an unauthorised access, an egress that was not blocked - which the plan
    #: treats as a block rather than as a numerator.
    violations: Sequence[str] = Field(default_factory=tuple)

    @property
    def rate(self) -> Optional[float]:
        if self.value is not None:
            return self.value
        if self.denominator in (None, 0) or self.numerator is None:
            return None
        return round(self.numerator / self.denominator, 6)


class MetricVerdict(Contract):
    metric_id: MetricId
    status: MetricStatus
    #: Only a PASS at a gate-eligible metric lets a release through; safety metrics always gate.
    release_blocking: bool
    value: Optional[float]
    reasons: list[str] = Field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "metric_id": self.metric_id.value,
            "status": self.status.value,
            "release_blocking": self.release_blocking,
            "value": self.value,
            "reasons": list(self.reasons),
        }


#: Section 10.6 transcribed as data. The strings in ``unacceptable`` are the plan's own column.
METRIC_DICTIONARY: Mapping[MetricId, MetricDefinition] = {
    item.metric_id: item
    for item in (
        MetricDefinition(
            metric_id=MetricId.RARE_CLASS_RECALL,
            numerator="candidates recovered that the experts pre-labelled",
            denominator="expert pre-labelled candidates, stratified by donor",
            collection_point=CollectionPoint.STAGE5_EVALUATION,
            direction=Direction.ABOVE_BASELINE,
            baseline_required=True,
            unacceptable="an interval that covers the baseline is reported as an improvement (§10.6)",
        ),
        MetricDefinition(
            metric_id=MetricId.ABSTENTION_QUALITY,
            numerator="residual error rate at the agreed coverage point",
            denominator="risk-coverage curve over the frozen set",
            collection_point=CollectionPoint.EVALUATION,
            direction=Direction.BELOW_BASELINE,
            baseline_required=True,
            requires_stable_companion="coarse_label_share",
            unacceptable="high coverage bought by labelling everything coarsely (§10.6)",
        ),
        MetricDefinition(
            metric_id=MetricId.CALIBRATION,
            numerator="expected calibration error, stratified by group size",
            denominator="groups scored by the platform",
            collection_point=CollectionPoint.EVALUATION,
            direction=Direction.BELOW,
            target=0.1,
            unacceptable="a threshold set before calibration was measured (§10.6)",
        ),
        MetricDefinition(
            metric_id=MetricId.CITATION_TRACEABILITY,
            numerator="claims whose source can be located",
            denominator="claims made",
            collection_point=CollectionPoint.L5_VERIFICATION,
            direction=Direction.ABOVE,
            target=0.95,
            hard_floor=0.9,
            unacceptable="below 0.9 (§10.6)",
        ),
        MetricDefinition(
            metric_id=MetricId.SUPPORT_VALIDITY,
            numerator="citations judged to actually support their claim",
            denominator="citations judged",
            collection_point=CollectionPoint.L5_VERIFICATION,
            direction=Direction.ABOVE,
            target=0.85,
            unacceptable="reading support validity as the same number as traceability (§10.6)",
        ),
        MetricDefinition(
            metric_id=MetricId.UNSUPPORTED_CONCLUSION_RATE,
            numerator="conclusions carried by no supporting evidence",
            denominator="conclusions made",
            collection_point=CollectionPoint.L5_VERIFICATION,
            direction=Direction.BELOW,
            target=0.02,
            #: The zero tolerance is not a floor on the rate: the collection point files the egress or
            #: permission conclusions as violations, and a violation blocks whatever the rate says.
            unacceptable="a nonzero count in the egress or permission family (§10.6)",
        ),
        MetricDefinition(
            metric_id=MetricId.EXPERT_REVISION_RATE,
            numerator="reviewed cards a person revised, split into label and status changes",
            denominator="reviewed cards",
            collection_point=CollectionPoint.REVIEW_EVENT,
            direction=Direction.BELOW,
            target=0.25,
            hard_floor=0.6,
            unacceptable="above 0.6, which says the candidate quality is the problem (§10.6)",
        ),
        MetricDefinition(
            metric_id=MetricId.REWORK_RATE,
            numerator="same-run reruns and conclusion revisions",
            denominator="projects",
            collection_point=CollectionPoint.RUN_MANIFEST,
            direction=Direction.BELOW,
            target=1.0,
            #: The row's own cheating rule is about its two halves, so they are named as required segments:
            #: a numerator that only counted the reruns is the number section 10.6 refuses.
            requires_segments=("reruns", "revisions"),
            unacceptable="reporting one of the two counts as the whole measure (§10.6)",
        ),
        MetricDefinition(
            metric_id=MetricId.END_TO_END_LATENCY,
            numerator="wall-clock seconds",
            denominator="runs",
            collection_point=CollectionPoint.MONITORING,
            direction=Direction.BELOW,
            target=3600.0,
            requires_segments=("platform_compute", "queue_wait", "review_wait"),
            unacceptable="a total elapsed time that hides the queue and review waits (§10.6)",
        ),
        MetricDefinition(
            metric_id=MetricId.COMPUTE_REPRODUCIBILITY,
            numerator="runs reproduced at each level",
            denominator="runs attempted",
            collection_point=CollectionPoint.REGRESSION_JOB,
            direction=Direction.ABOVE,
            target=1.0,
            requires_levels=("byte", "numeric", "conclusion"),
            unacceptable="byte-level determinism reported in place of a GPU's numeric noise (§10.6)",
        ),
        MetricDefinition(
            metric_id=MetricId.SAFETY_GATES,
            numerator="high-risk red-team cases passed",
            denominator="high-risk red-team cases run",
            collection_point=CollectionPoint.CI_REDTEAM,
            direction=Direction.ABOVE,
            target=1.0,
            safety=True,
            hard_floor=1.0,
            unacceptable="any high-risk failure, which blocks the release by itself (§10.8)",
        ),
        MetricDefinition(
            metric_id=MetricId.BYPASS_RATE,
            numerator="projects that completed an analysis outside the platform",
            denominator="registered projects",
            collection_point=CollectionPoint.PROJECT_REGISTRY,
            direction=Direction.BELOW,
            target=0.10,
            hard_floor=0.30,
            unacceptable="above 0.3, which triggers the section 12.3 retrospective (§10.6)",
        ),
        MetricDefinition(
            metric_id=MetricId.KNOWLEDGE_REVIEW_THROUGHPUT,
            numerator="claims reviewed",
            denominator="person-days",
            collection_point=CollectionPoint.KNOWLEDGE_SERVICE,
            direction=Direction.ABOVE,
            target=40.0,
            unacceptable="a throughput target set without the section 6.6 estimate behind it (§10.6)",
        ),
        MetricDefinition(
            metric_id=MetricId.OPEN_DISAGREEMENT_RATE,
            numerator="cited claims whose disagreement state is still open",
            denominator="cited claims",
            collection_point=CollectionPoint.RETRIEVAL_LAYER,
            direction=Direction.BELOW,
            target=0.05,
            unacceptable="an open disagreement served to a reviewer as settled (§10.6)",
        ),
    )
}


def dictionary_row(metric_id: MetricId) -> MetricDefinition:
    return METRIC_DICTIONARY[metric_id]


def evaluate_metric(
    definition: MetricDefinition, observation: MetricObservation
) -> MetricVerdict:
    """Apply the row's own rules to one measurement, including the ways the plan says it can be faked."""
    if observation.metric_id is not definition.metric_id:
        raise ValueError(
            f"the observation is for {observation.metric_id} and the row is {definition.metric_id}"
        )
    reasons: list[str] = []
    rate = observation.rate
    #: A violation is a count of a control that was breached, not a rate, so it blocks whatever the row's
    #: target says - which is how section 10.6's zero tolerance for an egress or permission conclusion works.
    if observation.violations:
        return MetricVerdict(
            metric_id=definition.metric_id,
            status=MetricStatus.FAIL,
            release_blocking=True,
            value=rate,
            reasons=[
                f"{definition.metric_id} reports {list(observation.violations)}: {definition.unacceptable}, "
                "and section 10.4 makes a breached control a hard block rather than a number to average"
            ],
        )
    missing_segments = [name for name in definition.requires_segments if name not in observation.segments]
    if missing_segments:
        reasons.append(
            f"{definition.metric_id} is reported without its segments {missing_segments}; {definition.unacceptable}"
        )
    missing_levels = [name for name in definition.requires_levels if name not in observation.levels]
    if missing_levels:
        reasons.append(
            f"{definition.metric_id} is reported without its levels {missing_levels}; {definition.unacceptable}"
        )
    if definition.requires_stable_companion:
        companion = definition.requires_stable_companion
        if companion not in observation.companions:
            reasons.append(
                f"{companion} was not measured, so {definition.metric_id} cannot be checked against "
                f"it: {definition.unacceptable}"
            )
        else:
            baseline = observation.companion_baselines.get(companion)
            if baseline is None:
                reasons.append(
                    f"{companion} was never measured for the baseline arm, so the check the plan puts on "
                    f"{definition.metric_id} cannot be made: {definition.unacceptable}"
                )
            elif observation.companions[companion] > baseline:
                reasons.append(
                    f"{companion} moved from {baseline} to {observation.companions[companion]}: "
                    f"{definition.unacceptable}"
                )
    if rate is None:
        reasons.append(
            f"no denominator was filed at {definition.collection_point.value}, so the metric is unmeasured "
            f"and cannot gate anything; {definition.unacceptable}"
        )
        return MetricVerdict(
            metric_id=definition.metric_id,
            status=MetricStatus.UNMEASURED,
            release_blocking=definition.safety,
            value=None,
            reasons=reasons,
        )
    if definition.baseline_required and observation.baseline is None:
        reasons.append(
            f"{definition.metric_id} is a comparison and no baseline has been established, so section 10.4 "
            "forbids using it as a single-number gate"
        )
        return MetricVerdict(
            metric_id=definition.metric_id,
            status=MetricStatus.TREND_ONLY,
            release_blocking=False,
            value=rate,
            reasons=reasons,
        )
    if definition.direction in (Direction.ABOVE_BASELINE, Direction.BELOW_BASELINE):
        if observation.interval is None:
            reasons.append(
                f"{definition.metric_id} claims a comparison with no interval: below the size floors the plan "
                "asks for a bootstrap interval and reports a trend (§10.5)"
            )
            return MetricVerdict(
                metric_id=definition.metric_id,
                status=MetricStatus.TREND_ONLY,
                release_blocking=False,
                value=rate,
                reasons=reasons,
            )
        low, high = observation.interval
        covers = low <= observation.baseline <= high
        improved = (
            rate > observation.baseline
            if definition.direction is Direction.ABOVE_BASELINE
            else rate < observation.baseline
        )
        if covers:
            reasons.append(
                f"the interval {observation.interval} covers the baseline {observation.baseline}, so reporting "
                f"an improvement is {definition.unacceptable}"
            )
            return MetricVerdict(
                metric_id=definition.metric_id,
                status=MetricStatus.FAIL if improved else MetricStatus.TREND_ONLY,
                release_blocking=False,
                value=rate,
                reasons=reasons,
            )
        #: Beating the baseline is not enough: a comparison row that arrived without its segments, its levels
        #: or its companion is the number section 10.6 calls cheating, whichever way the comparison went.
        status = MetricStatus.FAIL if reasons or not improved else MetricStatus.PASS
        reasons.append(
            f"the interval {observation.interval} does not cover the baseline {observation.baseline}"
        )
        return MetricVerdict(
            metric_id=definition.metric_id,
            status=status,
            release_blocking=False,
            value=rate,
            reasons=reasons,
        )
    if definition.direction is Direction.ABOVE and rate < (definition.target or 0.0):
        reasons.append(f"{rate} is below the target {definition.target}")
    if definition.direction is Direction.BELOW and rate > (definition.target or 0.0):
        reasons.append(f"{rate} is above the target {definition.target}")
    blocking = definition.safety
    if definition.hard_floor is not None:
        breached = (
            rate < definition.hard_floor
            if definition.direction is Direction.ABOVE
            else rate > definition.hard_floor
        )
        if breached:
            reasons.append(
                f"{rate} crosses the hard floor {definition.hard_floor}: {definition.unacceptable}"
            )
            blocking = True
    return MetricVerdict(
        metric_id=definition.metric_id,
        status=MetricStatus.FAIL if reasons else MetricStatus.PASS,
        release_blocking=blocking and bool(reasons),
        value=rate,
        reasons=reasons,
    )


def evaluate_all(
    observations: Sequence[MetricObservation],
    *,
    only: Sequence[MetricId] = (),
) -> list[MetricVerdict]:
    wanted = list(only) or list(METRIC_DICTIONARY)
    return [
        evaluate_metric(METRIC_DICTIONARY[one.metric_id], one)
        for one in observations
        if one.metric_id in wanted
    ]
