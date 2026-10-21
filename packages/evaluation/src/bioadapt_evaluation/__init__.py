"""Evaluation: frozen truth sets, the metric dictionary, the blind protocol and the release gate.

The plan's section 10 makes every release number come from one of four places, and this package holds each in
its own module rather than in a scorer that knows all of them. ``truth`` is the frozen read-only set - who
labelled it blind, whether a disagreement was adjudicated, and the size floors that decide whether its numbers
may gate anything at all. ``leakage`` masks the sources a benchmark touched and reports the two retrieval rates
separately, because a locatable citation and a supporting citation are different claims. ``agreement`` and
``blind`` are the human half: Krippendorff alpha, and the fixed delivery template a reviewer sees without
knowing which arm produced it. ``metrics`` transcribes section 10.6's fourteen rows, including the ways each one
can be made to say something false. ``release`` is where the three tables meet.

Every module keeps the same split the rest of the platform uses: a stored contract holds measurements and
criteria, and the verdict is derived by whoever reads it. A truth set does not record that its class is
trustworthy, a red-team corpus does not record that the build may ship, and no metric row records its own
status.
"""

from . import agreement, blind, leakage, metrics, release, truth
from .agreement import Agreement, AgreementLevel, krippendorff
from .blind import (
    AGREEMENT_FLOOR,
    DELIVERY_FIELD_ORDER,
    MAX_BASELINE_ARMS,
    MIN_BASELINE_ARMS,
    MIN_EXTERNAL_REVIEWERS,
    MIN_REVIEWERS,
    REQUIRED_FIGURES,
    BaselineArm,
    BlindPacket,
    BlindReading,
    BlindReview,
    BlindScore,
    Reviewer,
)
from .leakage import (
    BenchmarkSource,
    CandidateSource,
    IsolationAttestation,
    RetrievalJudgement,
    RetrievalRates,
    SourceKind,
    isolate,
    retrieval_rates,
)
from .metrics import (
    METRIC_DICTIONARY,
    CollectionPoint,
    Direction,
    MetricDefinition,
    MetricId,
    MetricObservation,
    MetricStatus,
    MetricVerdict,
    dictionary_row,
    evaluate_all,
    evaluate_metric,
)
from .release import (
    GATE_RULES,
    GateDecision,
    GateRule,
    GateSignal,
    GateTrigger,
    HarmFloors,
    HarmLevel,
    RedTeamCase,
    RedTeamReading,
    ReleaseDecision,
    apply_gate_rules,
    evaluate_red_team,
    release_decision,
    safety_gate_observation,
)
from .truth import (
    Adjudication,
    AnnotationCall,
    BlindAnnotation,
    ClassReading,
    LabelUse,
    RetrievalQuery,
    TruthClass,
    TruthFloors,
    TruthReading,
    TruthSet,
    TruthStratum,
)

__all__ = [
    "AGREEMENT_FLOOR",
    "Adjudication",
    "AnnotationCall",
    "BaselineArm",
    "BenchmarkSource",
    "BlindAnnotation",
    "BlindPacket",
    "BlindReading",
    "BlindReview",
    "BlindScore",
    "CandidateSource",
    "ClassReading",
    "CollectionPoint",
    "DELIVERY_FIELD_ORDER",
    "Direction",
    "GATE_RULES",
    "GateDecision",
    "GateRule",
    "GateSignal",
    "GateTrigger",
    "HarmFloors",
    "HarmLevel",
    "IsolationAttestation",
    "MAX_BASELINE_ARMS",
    "METRIC_DICTIONARY",
    "MIN_BASELINE_ARMS",
    "MIN_EXTERNAL_REVIEWERS",
    "MIN_REVIEWERS",
    "MetricDefinition",
    "MetricId",
    "MetricObservation",
    "MetricStatus",
    "MetricVerdict",
    "REQUIRED_FIGURES",
    "RedTeamCase",
    "RedTeamReading",
    "ReleaseDecision",
    "RetrievalJudgement",
    "RetrievalQuery",
    "RetrievalRates",
    "Reviewer",
    "SourceKind",
    "TruthClass",
    "TruthFloors",
    "TruthReading",
    "TruthSet",
    "TruthStratum",
    "agreement",
    "apply_gate_rules",
    "blind",
    "dictionary_row",
    "evaluate_all",
    "evaluate_metric",
    "evaluate_red_team",
    "isolate",
    "krippendorff",
    "leakage",
    "metrics",
    "release",
    "release_decision",
    "retrieval_rates",
    "safety_gate_observation",
    "truth",
]
