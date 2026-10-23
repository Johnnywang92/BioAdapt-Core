"""Section 10.6's fourteen rows, §6.8's attestation and §10.3-10.8's gate, read off the reference run's own records.

Three things separate this file from the unit suites it joins.

**No expectation is typed.** Every numerator and denominator is re-derived from rows the run left in the index
and the object store, and by a different route than the one that wrote them: the citation rate locates each
citation through the index and the bytes rather than through the reader that drafted the card, the latency
segments come out of the journal's own state and usage rows, the revision counts come out of the review table.
A number this file both writes and checks would only prove the check exists.

**Rows the run cannot count are filed, not dropped.** Four of the fourteen have no collection point in this
deployment, so their observation carries no counts at all; three more were counted and counted at zero, which
is a different statement - the platform asked and nothing arrived. Both read as unmeasured, and only the
safety row is allowed to block on being unmeasured.

**The clock stands still.** The harness moves time only when a test moves it, so the zero queue wait is a
measurement rather than a placeholder, and ``review_wait`` has no row at all until a person acts on a card -
which is the one way this suite can show that a total elapsed time is not the same number as the waits added
into it (§10.6).

The run's own size is the last thing under test. Five cards and one project clear none of §10.5's floors, so
§10.4 keeps its failures out of the gate: the release decision is asked both with a truth reading below the
floors and without one, and the only row that blocks either way is the safety row counted from §11.3's corpus.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

import evaluation_factories as E
import pytest
import redteam_cases as C

import card_factories as CF
import driver_factories as F
from bioadapt_evidence import build_queue
from bioadapt_evidence.assets import SLOT_CLUSTERS
from bioadapt_evaluation.leakage import (
    BenchmarkSource,
    CandidateSource,
    IsolationAttestation,
    SourceKind,
    isolate,
)
from bioadapt_evaluation.metrics import (
    METRIC_DICTIONARY,
    CollectionPoint,
    MetricId,
    MetricObservation,
    MetricStatus,
    MetricVerdict,
    dictionary_row,
    evaluate_all,
    evaluate_metric,
)
from bioadapt_evaluation.release import (
    GATE_RULES,
    GateSignal,
    GateTrigger,
    HarmFloors,
    HarmLevel,
    RedTeamReading,
    apply_gate_rules,
    evaluate_red_team,
    release_decision,
    safety_gate_observation,
)
from bioadapt_evaluation.truth import TruthReading
from bioadapt_schemas.entities import ReviewEvent
from bioadapt_schemas.enums import (
    AssetRole,
    EvidencePartition,
    FieldSource,
    FinalStatus,
    HypothesisType,
    LabelLevel,
    ReviewDecision,
    ReviewScope,
    RunEventKind,
    RunState,
    ToolClass,
)
from bioadapt_schemas.evidence import Evidence, EvidenceCard, FieldProvenance
from bioadapt_schemas.gates import (
    GateViolation,
    audited_numeric_paths,
    enforce_card_status,
    iter_numeric_leaves,
    numeric_provenance_violations,
)
from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_schemas.plan import AnalysisPlan, PlanNode
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS

#: The day the run's journal was written. Every row collected from the run or from a person acting on it
#: carries this date, because a metric without a collection date cannot be compared to a baseline (§10.6).
RUN_ON = F.NOW.date()

#: The red-team corpus ran on its own day, and the safety row is collected there rather than at the run.
CORPUS_ON = C.RUN_ON

#: How long the person in the reviewed plane took to read five cards. It is a delay the suite introduces so
#: that ``review_wait`` has something to measure; nothing in the plan asks for this particular number.
REVIEW_DELAY_MINUTES = 41

BENCHMARK_RUN = "eval_pbmc_v1"
MARKED_ON = date(2026, 5, 1)

#: The tolerance section 10.6's numeric level is read at: a GPU's noise is what this level exists to allow,
#: so anything smaller than it counts as the same answer.
ANSWER_TOLERANCE = 1e-9

#: Rows whose collection point files nothing at all for this deployment. No numerator source exists, so the
#: observation carries no counts rather than a counted zero: ``bypass_rate`` asks a project registry this
#: Catalog cannot answer (it is scoped to one project), and the other three ask a service this run never
#: called. A counted zero would read green; these do not.
NO_COLLECTION_POINT = (
    MetricId.BYPASS_RATE,
    MetricId.CALIBRATION,
    MetricId.KNOWLEDGE_REVIEW_THROUGHPUT,
    MetricId.OPEN_DISAGREEMENT_RATE,
)

#: Rows whose denominator the run does count, and counts at zero: no expert pre-labelled the candidates, no
#: risk-coverage curve was drawn over a frozen set, no citation was judged to support its claim (§10.6 keeps
#: unjudged citations out of the support denominator rather than counting them against it).
COUNTED_EMPTY = (
    MetricId.RARE_CLASS_RECALL,
    MetricId.ABSTENTION_QUALITY,
    MetricId.SUPPORT_VALIDITY,
)

#: The five cards, read the way §13.4 asks them to be read: one label corrected, one rejected, one re-read
#: and left as it was, two approved exactly as drafted. Reviewed five, revised two - 0.4, above the row's
#: target and below its hard floor, which is the difference between a report and a gate.
LABEL_REVISION = "c000"
STATUS_REVISION = "c001"
REREAD_WITHOUT_CHANGE = "c002"
APPROVED_AS_DRAFTED = ("c003", "c004")

CORPUS = C.corpus()
READING = evaluate_red_team(CORPUS, today=CORPUS_ON)

#: The same evaluation set both ways: below §10.5's floors the run's failures are trends, and this is the
#: reading that says so. ``None`` is the other answer - no truth set in play at all.
TOO_SMALL_TO_GATE: TruthReading = E.truth_set(report_only=True).reading()


def observed(
    metric_id: MetricId, *, collected_on: date = RUN_ON, **fields: Any
) -> MetricObservation:
    """File one measurement at its own collection point, with no counts unless the point supplied them."""
    fields.setdefault("numerator", None)
    fields.setdefault("denominator", None)
    return MetricObservation(metric_id=metric_id, collected_on=collected_on, **fields)


@dataclass
class Filed:
    """One run's cards as the index now holds them, and what each card said the moment it was filed."""

    plane: CF.Carded
    status_when_filed: dict[str, str]

    @property
    def harness(self) -> F.Harness:
        return self.plane.harness

    def cards(self) -> list[EvidenceCard]:
        return [self.harness.catalog.get_card(card_id) for card_id in sorted(self.status_when_filed)]

    def reviews(self) -> list[ReviewEvent]:
        return [
            event
            for card_id in sorted(self.status_when_filed)
            for event in self.harness.catalog.reviews(card_id)
        ]

    def card_id_of(self, display_name: str) -> str:
        return self.plane.draft(display_name).card_id


def file_cards(plane: CF.Carded) -> Filed:
    """File every draft the chain produced and remember what each card was when it landed."""
    for draft in plane.drafts:
        plane.service.persist(draft)
    return Filed(
        plane=plane,
        status_when_filed={
            draft.card_id: str(plane.harness.catalog.card_final_status(draft.card_id))
            for draft in plane.drafts
        },
    )


def _write(filed: Filed, card_id: str, updates: dict[str, Any]) -> EvidenceCard:
    """A person's write path, the one the review route uses: validate, gate against the card's own ceiling, persist."""
    card = filed.harness.catalog.get_card(card_id)
    revised = EvidenceCard.model_validate({**card.model_dump(), **updates})
    enforce_card_status(revised)
    filed.harness.catalog.update_card(revised)
    return revised


def _record(
    filed: Filed,
    *,
    review_id: str,
    card_id: str,
    scope: ReviewScope,
    decision: ReviewDecision,
    rationale: str,
    at: Any,
    previous_value: Optional[dict[str, Any]] = None,
    new_value: Optional[dict[str, Any]] = None,
) -> None:
    filed.harness.catalog.append_review(
        ReviewEvent(
            review_id=review_id,
            object_id=card_id,
            object_type="evidence_card",
            scope=scope,
            reviewer_id=F.ANALYST,
            decision=decision,
            rationale=rationale,
            timestamp=at,
            previous_value=previous_value,
            new_value=new_value,
        )
    )


def review_the_run(filed: Filed) -> Filed:
    """Let a person read all five cards, and record what each reading changed.

    The label revision writes a hypothesis, a label and a level together, because a card that carries a
    label while its hypothesis is still ``unknown`` is refused by the contract (§7.2). The re-read writes a
    reviewer and a timestamp but no status change, and an approval writes only a review row: both count as
    reviewed and neither counts as revised, which is the half of §10.6's revision row that a scorer written
    from review rows alone gets wrong.
    """
    filed.harness.clock.advance(minutes=REVIEW_DELAY_MINUTES)
    at = filed.harness.clock()

    label = filed.card_id_of(LABEL_REVISION)
    before = filed.harness.catalog.get_card(label)
    _write(
        filed,
        label,
        {
            "hypothesis_type": HypothesisType.CELL_STATE,
            "candidate_label": "interferon-high state",
            "label_level": LabelLevel.PROVISIONAL_PROJECT,
            "reviewer_id": F.ANALYST,
            "reviewed_at": at,
            "field_provenance": {
                **before.field_provenance,
                "candidate_label": FieldProvenance(
                    source=FieldSource.HUMAN,
                    object_id=label,
                    note="the label on this card is a reviewer's, not the drafting path's",
                ),
            },
        },
    )
    _record(
        filed,
        review_id="rev_label",
        card_id=label,
        scope=ReviewScope.FIELD,
        decision=ReviewDecision.REVISE,
        rationale="the axis and the marker read describe a state this population enters, not a type",
        at=at,
        previous_value={"hypothesis_type": str(before.hypothesis_type), "candidate_label": None},
        new_value={"hypothesis_type": "cell_state", "candidate_label": "interferon-high state"},
    )

    rejected = filed.card_id_of(STATUS_REVISION)
    _write(
        filed,
        rejected,
        {
            "final_status": FinalStatus.REJECTED,
            "reviewer_id": F.ANALYST,
            "reviewed_at": at,
            "review_reason": "the set does not survive the donor dropout",
        },
    )
    _record(
        filed,
        review_id="rev_reject",
        card_id=rejected,
        scope=ReviewScope.CARD,
        decision=ReviewDecision.REJECT,
        rationale="the set does not survive the donor dropout the robustness arm measured",
        at=at,
    )

    re_read = filed.card_id_of(REREAD_WITHOUT_CHANGE)
    _write(
        filed,
        re_read,
        {
            "reviewer_id": F.ANALYST,
            "reviewed_at": at,
            "review_reason": "read again against the same numbers; still not callable",
        },
    )
    _record(
        filed,
        review_id="rev_unknown",
        card_id=re_read,
        scope=ReviewScope.CARD,
        decision=ReviewDecision.MARK_UNKNOWN,
        rationale="read again against the same numbers; still not callable",
        at=at,
    )

    for index, name in enumerate(APPROVED_AS_DRAFTED):
        card_id = filed.card_id_of(name)
        _record(
            filed,
            review_id=f"rev_approve_{index}",
            card_id=card_id,
            scope=ReviewScope.CARD,
            decision=ReviewDecision.APPROVE,
            rationale="the draft reads correctly from the products it cites; nothing to change",
            at=at,
        )
    return filed


# ------------------------------------------------------------------ the rows, derived


def citation_pairs(filed: Filed) -> list[tuple[EvidenceCard, Evidence]]:
    """Every citation the filed cards make, ordered so a count of them is reproducible."""
    return [
        (card, record)
        for card in filed.cards()
        for record in sorted(card.evidence.values(), key=lambda item: item.evidence_id)
    ]


def locatable(filed: Filed, record: Evidence) -> bool:
    """Locate a citation the way a reviewer would - the index row, then the bytes behind it.

    This is deliberately not ``AssetReader``: the reader is what drafted the card, and a traceability rate
    computed through it would only prove that the card cites an asset id the run knew about. Here the object
    is fetched and digested again, so the number says the source is still findable and still the same thing.
    """
    source = record.source
    if source.asset_id is None:
        return bool(source.doi or source.pmid) and bool(source.locator)
    try:
        asset = filed.harness.catalog.get_asset(source.asset_id)
        raw = filed.harness.objects.get(asset.uri[len("s3://") :])
    except (LookupError, KeyError):
        return False
    return sha256_hex(raw) == asset.file_sha256


def traceability(filed: Filed) -> tuple[int, int]:
    pairs = citation_pairs(filed)
    return sum(1 for _card, record in pairs if locatable(filed, record)), len(pairs)


def unsupported_conclusions(filed: Filed) -> tuple[int, int]:
    cards = filed.cards()
    return sum(1 for card in cards if not card.supporting_evidence_ids), len(cards)


def reviewed_ids(filed: Filed) -> list[str]:
    """§10.6's denominator, counted from the review table: a card is reviewed because somebody signed a row."""
    return sorted({event.object_id for event in filed.reviews()})


def revisions(filed: Filed) -> tuple[list[str], dict[str, float]]:
    """The cards a person changed, split the way the row's numerator names its two halves."""
    rows = filed.reviews()
    label = {
        event.object_id
        for event in rows
        if event.scope is ReviewScope.FIELD and "candidate_label" in (event.new_value or {})
    }
    status = {
        card_id
        for card_id, filed_as in filed.status_when_filed.items()
        if str(filed.harness.catalog.get_card(card_id).final_status) != filed_as
    }
    return sorted(label | status), {"label": float(len(label)), "status": float(len(status))}


def journal_reruns(harness: F.Harness) -> float:
    return float(
        sum(
            1
            for event in harness.events()
            if event.kind in (RunEventKind.NODE_RETRY, RunEventKind.BUSINESS_RETRY)
        )
    )


def registered_projects(harness: F.Harness) -> float:
    return float(len({row["project_id"] for row in harness.catalog.list_runs()}))


def runs_recorded(harness: F.Harness) -> float:
    return float(len(harness.catalog.list_runs()))


def latency_observation(filed: Filed) -> MetricObservation:
    """The three waits §10.6 refuses to let a total hide, each read out of the journal or the review table.

    ``platform_compute`` is the charged minutes the usage rows filed, ``queue_wait`` is the span between the
    plan being approved and the first node reporting work, and ``review_wait`` is only filed once a review
    row exists - a run nobody has read has no review wait to report, and the row fails rather than rounding
    it to zero.
    """
    events = filed.harness.events()
    usage = [event for event in events if event.kind is RunEventKind.USAGE]
    approved = next(
        event.at for event in events if event.kind is RunEventKind.STATE and event.to_state is RunState.PLAN_APPROVED
    )
    segments = {
        "platform_compute": 60.0 * sum(event.compute_minutes for event in usage),
        "queue_wait": (min(event.at for event in usage) - approved).total_seconds(),
    }
    timestamps = [event.timestamp for event in filed.reviews()]
    if timestamps:
        segments["review_wait"] = (max(timestamps) - max(event.at for event in usage)).total_seconds()
    return observed(
        MetricId.END_TO_END_LATENCY,
        numerator=sum(segments.values()),
        denominator=runs_recorded(filed.harness),
        segments=segments,
    )


def rework_observation(
    harness: F.Harness, revised_count: int, *, collected_on: date = RUN_ON
) -> MetricObservation:
    reruns = journal_reruns(harness)
    return observed(
        MetricId.REWORK_RATE,
        numerator=reruns + float(revised_count),
        denominator=registered_projects(harness),
        collected_on=collected_on,
        segments={"reruns": reruns, "revisions": float(revised_count)},
    )


@dataclass
class Reproduction:
    """The three levels §10.6 asks a reproduction to report, and how much of the run they actually read."""

    levels: dict[str, float]
    leaves_compared: int
    product_slots: tuple[str, ...]


def conclusions(plane: CF.Carded) -> str:
    """What a run concludes about each candidate, as one canonical value.

    The ceiling, the reasons behind it, the hypothesis the card was filed under, the evidence roles it was
    read from and the provenance of every number on it - the conclusions a reviewer acts on, and nothing
    else. Two runs whose stored bytes differ only in floating-point noise must agree here, which is why
    ``conclusion`` is a separate level from ``byte`` and not a restatement of it.
    """
    return json.dumps(
        [
            {
                "candidate": draft.card.display_name,
                "ceiling": str(draft.ceiling),
                "gate_reasons": sorted(draft.gate_reasons),
                "hypothesis_type": str(draft.card.hypothesis_type),
                "evidence": sorted(
                    (str(record.kind), str(record.role), str(record.partition))
                    for record in draft.card.evidence.values()
                ),
                "numeric_provenance": sorted(
                    (path, str(entry.source)) for path, entry in draft.card.field_provenance.items()
                ),
            }
            for draft in sorted(plane.drafts, key=lambda item: item.card.display_name)
        ],
        sort_keys=True,
    )


def reproduce(first: CF.Carded, second: CF.Carded) -> Reproduction:
    """Compare two runs of the same pinned plan over the same root data at all three levels."""
    left, right = first.service.reader, second.service.reader
    slots = tuple(sorted(left.slots()))
    byte = all(
        left.product(slot).asset.file_sha256 == right.product(slot).asset.file_sha256
        for slot in slots
    )
    worst = 0.0
    leaves = 0
    for slot in slots:
        a = dict(iter_numeric_leaves(left.product(slot).document))
        b = dict(iter_numeric_leaves(right.product(slot).document))
        leaves += len(a)
        if a.keys() != b.keys():
            worst = float("inf")
            continue
        for path, value in a.items():
            worst = max(worst, abs(value - b[path]))
    agreed = conclusions(first) == conclusions(second)
    return Reproduction(
        levels={
            "byte": 1.0 if byte else 0.0,
            "numeric": 1.0 if worst <= ANSWER_TOLERANCE else 0.0,
            "conclusion": 1.0 if agreed else 0.0,
        },
        leaves_compared=leaves,
        product_slots=slots,
    )


def reproducibility_observation(made: Reproduction) -> MetricObservation:
    """A run counts as reproduced only when all three levels came back the same answer."""
    return observed(
        MetricId.COMPUTE_REPRODUCIBILITY,
        numerator=1.0 if all(value == 1.0 for value in made.levels.values()) else 0.0,
        denominator=1.0,
        levels=made.levels,
    )


def observations(
    filed: Filed,
    *,
    reproduction: Reproduction,
    red_team: RedTeamReading = READING,
) -> list[MetricObservation]:
    """One observation per row of §10.6, in dictionary order, filed from the rows this run left behind."""
    located, cited = traceability(filed)
    carried, conclusions_made = unsupported_conclusions(filed)
    revised, split = revisions(filed)
    rows: dict[MetricId, MetricObservation] = {
        MetricId.CITATION_TRACEABILITY: observed(
            MetricId.CITATION_TRACEABILITY, numerator=located, denominator=cited
        ),
        MetricId.UNSUPPORTED_CONCLUSION_RATE: observed(
            MetricId.UNSUPPORTED_CONCLUSION_RATE, numerator=carried, denominator=conclusions_made
        ),
        MetricId.EXPERT_REVISION_RATE: observed(
            MetricId.EXPERT_REVISION_RATE,
            numerator=len(revised),
            denominator=len(reviewed_ids(filed)),
            segments=split,
        ),
        MetricId.REWORK_RATE: rework_observation(filed.harness, len(revised)),
        MetricId.END_TO_END_LATENCY: latency_observation(filed),
        MetricId.COMPUTE_REPRODUCIBILITY: reproducibility_observation(reproduction),
        MetricId.SAFETY_GATES: safety_gate_observation(red_team, collected_on=CORPUS_ON),
    }
    for metric_id in NO_COLLECTION_POINT:
        rows[metric_id] = observed(metric_id)
    for metric_id in COUNTED_EMPTY:
        rows[metric_id] = observed(metric_id, numerator=0.0, denominator=0.0)
    missing = set(MetricId) - set(rows)
    assert not missing, f"rows nobody filed: {sorted(str(one) for one in missing)}"
    return [rows[metric_id] for metric_id in METRIC_DICTIONARY]


def verdicts(filed: Filed, **kwargs: Any) -> dict[MetricId, Any]:
    return {one.metric_id: one for one in evaluate_all(observations(filed, **kwargs))}


# ------------------------------------------------------------------ the gate rows this run fires


def adhoc_support_signals(filed: Filed, plan: AnalysisPlan) -> list[GateSignal]:
    """§10.3's row for a conclusion whose only support came through the ad-hoc channel.

    The node that produced each supporting record comes out of the asset's own lineage, and the channel it
    ran through comes out of the plan the run is pinned to - so the row fires on what the record says, not
    on what the call site remembered.
    """
    adhoc = {node.node_id for node in plan.nodes if node.tool_class is ToolClass.PROJECT_ADHOC}
    reader = filed.plane.service.reader
    made: list[GateSignal] = []
    for card in filed.cards():
        nodes = {
            reader.tool_of(card.evidence[evidence_id].source.asset_id)[0]
            for evidence_id in card.supporting_evidence_ids
        }
        if nodes and nodes <= adhoc:
            made.append(
                GateSignal(
                    trigger=GateTrigger.ADHOC_ONLY_SUPPORT,
                    subject_id=card.card_id,
                    detail=(
                        f"all {len(card.supporting_evidence_ids)} supporting records came from "
                        f"{sorted(nodes)}, registered as ad-hoc in the plan this run is pinned to"
                    ),
                )
            )
    return made


def run_signals(
    filed: Filed,
    *,
    plan: Optional[AnalysisPlan] = None,
    attestation: Optional[IsolationAttestation] = None,
    queue_cap: Optional[int] = None,
) -> list[GateSignal]:
    """Every §10.3 row the stored rows of this run fire, looked up from the records rather than asserted."""
    pinned = plan or filed.plane.service.plan
    made: list[GateSignal] = []
    for card in filed.cards():
        if card.supporting_evidence_ids and not card.confirmation_evidence_ids:
            made.append(
                GateSignal(
                    trigger=GateTrigger.SUPPORT_FROM_DISCOVERY_ONLY,
                    subject_id=card.card_id,
                    detail=(
                        f"{len(card.supporting_evidence_ids)} supporting records and none of them measured "
                        "in the confirmation partition (§7.4)"
                    ),
                )
            )
        for violation in numeric_provenance_violations(card):
            made.append(
                GateSignal(
                    trigger=GateTrigger.MODEL_SOURCED_NUMERIC_FIELD,
                    subject_id=card.card_id,
                    detail=violation,
                )
            )
    made += adhoc_support_signals(filed, pinned)
    for use in attestation.unscorable_uses if attestation else ():
        made.append(
            GateSignal(
                trigger=GateTrigger.BENCHMARK_MARKED_CITATION,
                subject_id=use,
                detail=f"{use} is scored only on sources the benchmark mark reaches (§6.8)",
            )
        )
    cap = queue_cap if queue_cap is not None else DEFAULT_THRESHOLDS.expert_queue_card_limit
    queue = build_queue(filed.plane.drafts, limit=cap)
    made += [
        GateSignal(
            trigger=GateTrigger.EXPERT_QUEUE_OVERFLOW,
            subject_id=card_id,
            detail=(
                f"the queue rule filed this card as observed rather than sending it to an expert, "
                f"against the cap of {queue.limit} (§13.4)"
            ),
        )
        for card_id in queue.observed
    ]
    return made


# ------------------------------------------------------------------ §6.8 over the real citations


def cited_ids(filed: Filed) -> list[str]:
    """The object ids the filed cards cite, in citation order.

    Every citation of this run names a stored asset rather than a literature locator, which is what lets the
    isolation below be expressed as a mask over object ids at all.
    """
    return [record.source.asset_id for _card, record in citation_pairs(filed)]


def uses_by(filed: Filed, *, per_claim: bool = True) -> dict[str, list[str]]:
    """The uses §6.8 scores, and the object ids each one cites - re-derived from the cards, not from ``isolate``."""
    made: dict[str, list[str]] = {}
    for card, record in citation_pairs(filed):
        use = f"{card.card_id}:{record.kind.value}" if per_claim else card.card_id
        made.setdefault(use, []).append(record.source.asset_id)
    return made


def cited_inputs(filed: Filed, asset_id: str) -> tuple[str, ...]:
    """What the node that wrote ``asset_id`` read, out of the lineage rows the run itself recorded.

    This is the run's own ``derived_from``: §6.8 propagates a benchmark mark to everything derived from the
    marked object, and a product's direct ancestors are recorded edges rather than a remembered list.
    """
    catalog = filed.harness.catalog
    produced = next(
        row
        for row in catalog.lineage(asset_id)
        if row.asset_id == asset_id and row.role is AssetRole.OUTPUT and row.run_id == filed.harness.run_id
    )
    return tuple(
        sorted(
            {
                row.asset_id
                for one in catalog.list_assets()
                for row in catalog.lineage(one.asset_id)
                if row.node_id == produced.node_id
                and row.run_id == produced.run_id
                and row.role is AssetRole.INPUT
                and row.asset_id != asset_id
            }
        )
    )


def citation_sources(
    filed: Filed, *, used_for: Any, with_lineage: bool = False
) -> list[CandidateSource]:
    made = []
    for card, record in citation_pairs(filed):
        asset_id = record.source.asset_id
        made.append(
            CandidateSource(
                object_id=asset_id,
                kind=SourceKind.DATASET,
                derived_from=cited_inputs(filed, asset_id) if with_lineage else (),
                used_for=used_for(card, record),
            )
        )
    return made


def claim_sources(filed: Filed, **kwargs: Any) -> list[CandidateSource]:
    """One use per (card, evidence kind): the granularity §6.8 scores a claim at."""
    return citation_sources(
        filed, used_for=lambda card, record: f"{card.card_id}:{record.kind.value}", **kwargs
    )


def card_sources(filed: Filed, **kwargs: Any) -> list[CandidateSource]:
    """One use per card: the granularity a delivery is scored at, where a card has several sources."""
    return citation_sources(filed, used_for=lambda card, _record: card.card_id, **kwargs)


def benchmark_mark(asset_ids: list[str], *, benchmark_run_id: str = BENCHMARK_RUN) -> list[BenchmarkSource]:
    return [
        BenchmarkSource(
            object_id=asset_id,
            kind=SourceKind.DATASET,
            benchmark_run_id=benchmark_run_id,
            marked_on=MARKED_ON,
        )
        for asset_id in asset_ids
    ]


def unscorable_uses(filed: Filed, marked: list[str], *, per_claim: bool = True, reached: Any = None) -> list[str]:
    """Which uses lose their score, computed without calling ``isolate``.

    ``reached`` is the propagation rule a reviewer would read off the lineage: a citation survives unless the
    mark names it or names one of the assets it was written from.
    """
    marked_set = set(marked)
    hits = reached or (lambda asset_id: asset_id in marked_set)
    return sorted(
        use
        for use, ids in uses_by(filed, per_claim=per_claim).items()
        if all(hits(one) for one in ids)
    )


def attest(
    filed: Filed,
    asset_ids: list[str],
    *,
    per_claim: bool = True,
    with_lineage: bool = False,
    benchmark_run_id: str = BENCHMARK_RUN,
    mark_run_id: Optional[str] = None,
) -> IsolationAttestation:
    """Mask the marked sources from the citations this run actually made.

    ``mark_run_id`` is separate from ``benchmark_run_id`` because a mark that names a different evaluation
    run is the case §6.8 has to survive: it is both orphaned and masked, so a stale mark cannot quietly
    score a run it was never written for.
    """
    candidates = (claim_sources if per_claim else card_sources)(filed, with_lineage=with_lineage)
    return isolate(
        benchmark_run_id,
        candidates,
        marked=benchmark_mark(asset_ids, benchmark_run_id=mark_run_id or benchmark_run_id),
        today=RUN_ON,
    )


def decision(
    filed: Filed,
    *,
    reproduction: Reproduction,
    truth: Optional[TruthReading] = None,
    red_team: RedTeamReading = READING,
    attestation: Optional[IsolationAttestation] = None,
    plan: Optional[AnalysisPlan] = None,
    queue_cap: Optional[int] = None,
) -> Any:
    return release_decision(
        metrics=evaluate_all(observations(filed, reproduction=reproduction, red_team=red_team)),
        gates=apply_gate_rules(
            run_signals(filed, plan=plan, attestation=attestation, queue_cap=queue_cap)
        ),
        red_team=red_team,
        truth=truth,
    )


@pytest.fixture(scope="module")
def unreviewed(carded: CF.Carded) -> Filed:
    """The run as the drafting path left it: every card filed, no person has read one."""
    return file_cards(carded)


@pytest.fixture(scope="module")
def reviewed(carded: CF.Carded) -> Filed:
    """A second plane of the same plan and data, whose cards a person has read.

    It is a second plane because §1.5's byte-level claim is a comparison between runs, and because the
    unreviewed state above has to stay readable after this one is acted on.
    """
    made = file_cards(CF.rerun_of(carded))
    return review_the_run(made)


@pytest.fixture(scope="module")
def reproduction(carded: CF.Carded, reviewed: Filed) -> Reproduction:
    return reproduce(carded, reviewed.plane)


def support_nodes(filed: Filed) -> dict[str, set[str]]:
    """The nodes each card's supporting records came from, read off the assets' own lineage."""
    reader = filed.plane.service.reader
    return {
        card.card_id: {reader.tool_of(card.evidence[one].source.asset_id)[0] for one in card.supporting_evidence_ids}
        for card in filed.cards()
    }


def adhoc_plan(plan: AnalysisPlan, node_ids: set[str]) -> AnalysisPlan:
    """The same plan with these nodes registered as project ad-hoc, through the validating constructor.

    Nothing is written to the index: the run stays pinned to the plan it ran, and this object only answers
    the question the §10.3 row asks - what would have fired had the support come through that channel.
    An ad-hoc node carries the bytes of the submission it runs (§9.6), so the hypothetical states them.
    """
    nodes = [
        PlanNode.model_validate(
            {
                **node.model_dump(),
                "tool_class": ToolClass.PROJECT_ADHOC,
                "requires_approval": True,
                "adhoc_spec": F.adhoc_spec(
                    tool_name=node.tool_name,
                    tool_version=node.tool_version,
                    task=F.TASK_OF[node.node_id],
                    deployment=F.DEPLOYMENT,
                    reason="the registered step does not answer this project's question",
                ).model_dump(mode="json"),
            }
        )
        if node.node_id in node_ids
        else node
        for node in plan.nodes
    ]
    return plan.model_copy(update={"nodes": nodes})


def verdict_of(filed: Filed, metric_id: MetricId, **kwargs: Any) -> MetricVerdict:
    return verdicts(filed, **kwargs)[metric_id]


@pytest.fixture(scope="module")
def disposable(carded: CF.Carded) -> Filed:
    """A plane of its own for the acts that damage records, so nothing else reads what they broke.

    It is a second run rather than a mutation of the first because §10.6's rows are read from the object
    store and the index: a test that rewrites a stored object or writes a card the gate would have refused
    changes what every later row says.
    """
    return file_cards(CF.rerun_of(carded))


# ------------------------------------------------------------------ the rows this run files


class TestTheRowsThisRunFiles:
    def test_every_row_of_the_dictionary_is_filed_once(self, unreviewed, reproduction):
        rows = observations(unreviewed, reproduction=reproduction)
        assert [one.metric_id for one in rows] == list(METRIC_DICTIONARY)
        assert len(rows) == len(set(MetricId))

    def test_each_row_is_dated_where_it_was_collected(self, unreviewed, reproduction):
        assert CORPUS_ON != RUN_ON
        for one in observations(unreviewed, reproduction=reproduction):
            point = dictionary_row(one.metric_id).collection_point
            assert one.collected_on == (CORPUS_ON if point is CollectionPoint.CI_REDTEAM else RUN_ON)

    def test_every_claim_the_run_made_still_leads_to_the_bytes_it_was_written_with(self, unreviewed):
        pairs = citation_pairs(unreviewed)
        assert all(record.source.asset_id is not None for _card, record in pairs)
        located, cited = traceability(unreviewed)
        assert cited == len(pairs) == sum(len(card.evidence) for card in unreviewed.cards())
        assert located == cited
        row = evaluate_metric(
            dictionary_row(MetricId.CITATION_TRACEABILITY),
            observed(MetricId.CITATION_TRACEABILITY, numerator=located, denominator=cited),
        )
        assert row.status is MetricStatus.PASS
        assert row.value == 1.0

    def test_one_damaged_object_is_a_lost_citation_and_a_shared_one_is_lost_by_every_card_that_cited_it(
        self, disposable
    ):
        """How much one broken object costs is the run's own fan-out, and whether it fails is the row's target."""
        definition = dictionary_row(MetricId.CITATION_TRACEABILITY)
        ids = cited_ids(disposable)
        total = len(ids)
        solo = next(one for one in dict.fromkeys(ids) if ids.count(one) == 1)
        shared = max(dict.fromkeys(ids), key=ids.count)
        disposable.harness.rewrite_object(solo, b"the same slot, rewritten bytes")
        located, cited = traceability(disposable)
        assert cited == total
        assert located == total - 1
        first = evaluate_metric(
            definition, observed(MetricId.CITATION_TRACEABILITY, numerator=located, denominator=cited)
        )
        assert first.status is MetricStatus.PASS
        disposable.harness.rewrite_object(shared, b"and one more, cited by several cards")
        located, cited = traceability(disposable)
        assert located == total - 1 - ids.count(shared)
        second = evaluate_metric(
            definition, observed(MetricId.CITATION_TRACEABILITY, numerator=located, denominator=cited)
        )
        assert second.status is MetricStatus.FAIL
        assert second.value < definition.target
        assert (second.value < definition.hard_floor) is second.release_blocking

    def test_no_conclusion_the_run_filed_rests_on_nothing(self, unreviewed, reproduction):
        carried, made = unsupported_conclusions(unreviewed)
        assert made == len(unreviewed.cards())
        assert carried == 0
        assert all(card.supporting_evidence_ids for card in unreviewed.cards())
        assert verdict_of(
            unreviewed, MetricId.UNSUPPORTED_CONCLUSION_RATE, reproduction=reproduction
        ).status is MetricStatus.PASS

    def test_the_safety_row_is_counted_from_the_corpus_rather_than_written_beside_it(
        self, unreviewed, reproduction
    ):
        high = [one for one in CORPUS if one.harm_level is HarmLevel.HIGH]
        assert READING.high_total == len(high)
        assert READING.high_passed == len([one for one in high if one.passed])
        filed = {
            one.metric_id: one for one in observations(unreviewed, reproduction=reproduction)
        }[MetricId.SAFETY_GATES]
        assert (filed.numerator, filed.denominator) == (float(READING.high_passed), float(READING.high_total))
        assert list(filed.violations) == READING.blockers
        row = verdict_of(unreviewed, MetricId.SAFETY_GATES, reproduction=reproduction)
        assert row.status is MetricStatus.PASS
        assert row.value == round(READING.high_passed / READING.high_total, 6)

    def test_a_total_elapsed_time_is_not_the_waits_added_into_it(self, unreviewed, reproduction):
        """The run finished inside the hour the row targets, and the row still fails - on its missing third."""
        definition = dictionary_row(MetricId.END_TO_END_LATENCY)
        one = {
            one.metric_id: one for one in observations(unreviewed, reproduction=reproduction)
        }[MetricId.END_TO_END_LATENCY]
        assert set(one.segments) == {"platform_compute", "queue_wait"}
        assert one.numerator < definition.target
        row = verdict_of(unreviewed, MetricId.END_TO_END_LATENCY, reproduction=reproduction)
        assert row.status is MetricStatus.FAIL
        assert any(name not in one.segments for name in definition.requires_segments)
        assert any("review_wait" in reason for reason in row.reasons)
        assert row.value == round(sum(one.segments.values()) / one.denominator, 6)

    def test_the_run_reproduces_at_all_three_levels(self, carded, reviewed, reproduction):
        """Same addresses over two control planes: the ids coincide because a byte-identical product is one object."""
        assert set(reproduction.levels) == {"byte", "numeric", "conclusion"}
        assert all(value == 1.0 for value in reproduction.levels.values())
        assert reproduction.leaves_compared > 0
        assert carded.assets == reviewed.plane.assets
        assert carded.harness.objects is not reviewed.plane.harness.objects
        assert carded.harness.catalog.conn is not reviewed.plane.harness.catalog.conn
        assert set(reproduction.product_slots) < set(carded.assets)

    def test_a_byte_level_claim_missing_its_conclusion_level_is_the_number_the_plan_refuses(
        self, unreviewed, reproduction
    ):
        definition = dictionary_row(MetricId.COMPUTE_REPRODUCIBILITY)
        filed = {
            one.metric_id: one for one in observations(unreviewed, reproduction=reproduction)
        }[MetricId.COMPUTE_REPRODUCIBILITY]
        assert evaluate_metric(definition, filed).status is MetricStatus.PASS
        stripped = filed.model_copy(
            update={"levels": {name: value for name, value in filed.levels.items() if name != "conclusion"}}
        )
        row = evaluate_metric(definition, stripped)
        assert row.status is MetricStatus.FAIL
        assert any("conclusion" in reason for reason in row.reasons)


# ------------------------------------------------------------------ the rows nobody has earned


class TestTheRowsNobodyHasEarned:
    @pytest.mark.parametrize("metric_id", NO_COLLECTION_POINT)
    def test_a_row_whose_collection_point_this_deployment_never_called_files_no_counts(
        self, metric_id, unreviewed, reproduction
    ):
        one = {one.metric_id: one for one in observations(unreviewed, reproduction=reproduction)}[metric_id]
        assert one.numerator is None
        assert one.denominator is None
        assert one.rate is None
        row = evaluate_metric(dictionary_row(metric_id), one)
        assert row.status is MetricStatus.UNMEASURED
        assert row.release_blocking is False
        assert dictionary_row(metric_id).collection_point.value in row.reasons[0]

    @pytest.mark.parametrize("metric_id", COUNTED_EMPTY)
    def test_a_counted_zero_is_a_different_statement_from_no_counts_at_all(
        self, metric_id, unreviewed, reproduction
    ):
        one = {one.metric_id: one for one in observations(unreviewed, reproduction=reproduction)}[metric_id]
        assert one.numerator == 0.0
        assert one.denominator == 0.0
        assert one.rate is None
        row = evaluate_metric(dictionary_row(metric_id), one)
        assert row.status is MetricStatus.UNMEASURED
        assert row.release_blocking is False

    def test_only_the_safety_row_is_allowed_to_block_on_being_unmeasured(self, unreviewed, reproduction):
        assert [one for one in MetricId if dictionary_row(one).safety] == [MetricId.SAFETY_GATES]
        never_measured = evaluate_metric(
            dictionary_row(MetricId.SAFETY_GATES), observed(MetricId.SAFETY_GATES)
        )
        assert never_measured.status is MetricStatus.UNMEASURED
        assert never_measured.release_blocking is True
        for row in verdicts(unreviewed, reproduction=reproduction).values():
            if row.status is MetricStatus.UNMEASURED:
                assert row.metric_id is not MetricId.SAFETY_GATES
                assert row.release_blocking is False

    def test_the_rows_left_unmeasured_are_named_rather_than_dropped(self, unreviewed, reproduction):
        unmeasured = {
            row.metric_id
            for row in verdicts(unreviewed, reproduction=reproduction).values()
            if row.status is MetricStatus.UNMEASURED
        }
        assert unmeasured == set(NO_COLLECTION_POINT) | set(COUNTED_EMPTY) | {MetricId.EXPERT_REVISION_RATE}


# ------------------------------------------------------------------ what a person changes


class TestWhatAPersonChanges:
    def test_the_third_wait_only_exists_once_somebody_has_read_a_card(self, reviewed, reproduction):
        definition = dictionary_row(MetricId.END_TO_END_LATENCY)
        one = {
            one.metric_id: one for one in observations(reviewed, reproduction=reproduction)
        }[MetricId.END_TO_END_LATENCY]
        assert set(one.segments) == set(definition.requires_segments)
        assert one.segments["review_wait"] == float(REVIEW_DELAY_MINUTES * 60)
        row = verdict_of(reviewed, MetricId.END_TO_END_LATENCY, reproduction=reproduction)
        assert row.status is MetricStatus.PASS
        assert row.reasons == []

    def test_the_review_table_says_every_card_was_read_and_two_were_changed(self, reviewed, reproduction):
        read = reviewed_ids(reviewed)
        assert read == sorted(reviewed.status_when_filed)
        revised, split = revisions(reviewed)
        assert len(revised) == len(set(revised)) == int(sum(split.values()))
        definition = dictionary_row(MetricId.EXPERT_REVISION_RATE)
        row = verdict_of(reviewed, MetricId.EXPERT_REVISION_RATE, reproduction=reproduction)
        assert row.value == round(len(revised) / len(read), 6)
        assert row.status is MetricStatus.FAIL
        assert row.value > definition.target
        assert row.value < definition.hard_floor
        assert row.release_blocking is False

    def test_an_approval_that_changed_nothing_is_read_but_not_revised(self, reviewed):
        revised, _ = revisions(reviewed)
        approved = [reviewed.card_id_of(one) for one in APPROVED_AS_DRAFTED]
        assert all(one in reviewed_ids(reviewed) for one in approved)
        assert not set(approved) & set(revised)
        for card_id in approved:
            assert str(reviewed.harness.catalog.get_card(card_id).final_status) == reviewed.status_when_filed[card_id]

    def test_a_rejected_conclusion_is_still_a_conclusion_the_platform_made(self, reviewed, reproduction):
        rejected = reviewed.card_id_of(STATUS_REVISION)
        card = reviewed.harness.catalog.get_card(rejected)
        assert card.final_status is FinalStatus.REJECTED
        assert str(reviewed.status_when_filed[rejected]) != str(FinalStatus.REJECTED)
        carried, made = unsupported_conclusions(reviewed)
        assert made == len(reviewed.status_when_filed)
        assert carried == 0
        assert verdict_of(
            reviewed, MetricId.UNSUPPORTED_CONCLUSION_RATE, reproduction=reproduction
        ).status is MetricStatus.PASS

    def test_a_reviewers_label_is_a_human_source_that_carries_no_number(self, reviewed):
        label = reviewed.card_id_of(LABEL_REVISION)
        card = reviewed.harness.catalog.get_card(label)
        assert card.field_provenance["candidate_label"].source is FieldSource.HUMAN
        assert card.hypothesis_type is HypothesisType.CELL_STATE
        assert card.label_level is LabelLevel.PROVISIONAL_PROJECT
        assert "candidate_label" not in audited_numeric_paths(card)
        assert all(numeric_provenance_violations(one) == [] for one in reviewed.cards())

    def test_the_rework_row_counts_both_halves_over_the_projects_the_catalog_can_see(
        self, reviewed, reproduction
    ):
        definition = dictionary_row(MetricId.REWORK_RATE)
        one = {
            one.metric_id: one for one in observations(reviewed, reproduction=reproduction)
        }[MetricId.REWORK_RATE]
        assert set(one.segments) == set(definition.requires_segments)
        assert one.numerator == sum(one.segments.values())
        assert one.segments["reruns"] == journal_reruns(reviewed.harness)
        assert one.segments["revisions"] == float(len(revisions(reviewed)[0]))
        assert one.denominator == registered_projects(reviewed.harness)
        assert {row["project_id"] for row in reviewed.harness.catalog.list_runs()} == {F.PROJECT_ID}
        row = verdict_of(reviewed, MetricId.REWORK_RATE, reproduction=reproduction)
        assert row.status is MetricStatus.FAIL
        assert definition.hard_floor is None
        assert row.release_blocking is False

    def test_reading_the_cards_moves_exactly_three_rows(self, unreviewed, reviewed, reproduction):
        before = verdicts(unreviewed, reproduction=reproduction)
        after = verdicts(reviewed, reproduction=reproduction)
        moved = {one for one in before if before[one].status is not after[one].status}
        assert moved == {
            MetricId.EXPERT_REVISION_RATE,
            MetricId.REWORK_RATE,
            MetricId.END_TO_END_LATENCY,
        }


# ------------------------------------------------------------------ §6.8 over the real citations


class TestTheIsolationAttestation:
    def test_marking_an_asset_loses_the_claims_built_only_on_that_asset(self, unreviewed):
        marked = unreviewed.plane.asset(SLOT_CLUSTERS).asset_id
        attestation = attest(unreviewed, [marked])
        assert attestation.masked == [marked]
        assert marked not in attestation.kept
        assert attestation.unscorable_uses == unscorable_uses(unreviewed, [marked])
        assert attestation.unscorable_uses
        assert attestation.clean is False

    def test_the_masked_rows_are_the_recoverable_kind_and_cost_a_person_nothing(self, unreviewed):
        marked = unreviewed.plane.asset(SLOT_CLUSTERS).asset_id
        attestation = attest(unreviewed, [marked])
        rows = [
            one
            for one in run_signals(unreviewed, attestation=attestation)
            if one.trigger is GateTrigger.BENCHMARK_MARKED_CITATION
        ]
        assert sorted(one.subject_id for one in rows) == attestation.unscorable_uses
        assert all(GATE_RULES[one.trigger].auto_recoverable for one in rows)
        assert all(one.blocks_release is False for one in apply_gate_rules(rows))

    def test_one_masked_citation_beside_a_clean_one_costs_no_score(self, unreviewed):
        marked = unreviewed.plane.asset(SLOT_CLUSTERS).asset_id
        attestation = attest(unreviewed, [marked], per_claim=False)
        assert attestation.unscorable_uses == unscorable_uses(unreviewed, [marked], per_claim=False)
        assert attestation.clean
        assert all(
            any(one != marked for one in ids) for ids in uses_by(unreviewed, per_claim=False).values()
        )

    def test_a_mark_that_names_another_evaluation_run_is_orphaned_and_still_masked(self, unreviewed):
        marked = unreviewed.plane.asset(SLOT_CLUSTERS).asset_id
        attestation = attest(unreviewed, [marked], mark_run_id="eval_a_different_set_entirely")
        assert attestation.masked == [marked]
        assert attestation.orphaned == [marked]
        assert attestation.unscorable_uses == unscorable_uses(unreviewed, [marked])

    def test_a_mark_dated_after_the_run_is_refused_rather_than_quietly_applied(self, unreviewed):
        marked = unreviewed.plane.asset(SLOT_CLUSTERS).asset_id
        with pytest.raises(ValueError, match="after this run"):
            isolate(
                BENCHMARK_RUN,
                claim_sources(unreviewed),
                marked=[E.marked(marked, benchmark_run_id=BENCHMARK_RUN, marked_on=RUN_ON + timedelta(days=1))],
                today=RUN_ON,
            )

    def test_a_mark_on_the_data_a_product_was_written_from_reaches_the_product(self, unreviewed):
        clusters = unreviewed.plane.asset(SLOT_CLUSTERS).asset_id
        ancestors = list(cited_inputs(unreviewed, clusters))
        assert ancestors
        assert clusters not in ancestors
        attestation = attest(unreviewed, ancestors, with_lineage=True)

        def reached(asset_id: str) -> bool:
            return asset_id in ancestors or bool(set(ancestors) & set(cited_inputs(unreviewed, asset_id)))

        assert clusters in attestation.masked
        assert attestation.unscorable_uses == unscorable_uses(unreviewed, ancestors, reached=reached)
        assert attestation.unscorable_uses


# ------------------------------------------------------------------ the gate rows this run fires


class TestTheGateRowsThisRunFires:
    def test_every_card_drafted_from_discovery_alone_files_its_own_row(self, unreviewed):
        rows = [
            one
            for one in run_signals(unreviewed)
            if one.trigger is GateTrigger.SUPPORT_FROM_DISCOVERY_ONLY
        ]
        assert sorted(one.subject_id for one in rows) == sorted(unreviewed.status_when_filed)
        for card in unreviewed.cards():
            assert card.supporting_evidence_ids
            assert card.confirmation_evidence_ids == []
            assert {record.partition for record in card.evidence.values()} == {EvidencePartition.DISCOVERY}

    def test_the_adhoc_row_reads_the_plan_the_run_is_pinned_to(self, unreviewed):
        pinned = unreviewed.plane.service.plan
        nodes = support_nodes(unreviewed)
        used = set().union(*nodes.values())
        assert used
        assert {one.node_id for one in pinned.nodes if one.tool_class is ToolClass.PROJECT_ADHOC} == set()
        assert adhoc_support_signals(unreviewed, pinned) == []
        fired = adhoc_support_signals(unreviewed, adhoc_plan(pinned, used))
        assert sorted(one.subject_id for one in fired) == sorted(
            card_id for card_id, sources in nodes.items() if sources <= used
        )
        assert fired

    def test_a_channel_that_supplies_nothing_keeps_the_adhoc_row_shut(self, unreviewed):
        pinned = unreviewed.plane.service.plan
        used = set().union(*support_nodes(unreviewed).values())
        unused = {one.node_id for one in pinned.nodes} - used
        assert unused
        assert adhoc_support_signals(unreviewed, adhoc_plan(pinned, unused)) == []

    def test_an_adhoc_only_support_is_a_row_that_waits_for_a_person(self, unreviewed):
        plan = adhoc_plan(unreviewed.plane.service.plan, set().union(*support_nodes(unreviewed).values()))
        rows = [
            one
            for one in run_signals(unreviewed, plan=plan)
            if one.trigger is GateTrigger.ADHOC_ONLY_SUPPORT
        ]
        assert rows
        assert all(GATE_RULES[one.trigger].blocks_release and not GATE_RULES[one.trigger].auto_recoverable for one in rows)
        assert {one.subject_id for one in apply_gate_rules(rows) if one.requires_human} == {
            one.subject_id for one in rows
        }

    def test_no_number_on_a_filed_card_came_from_a_model(self, unreviewed):
        assert [
            one for one in run_signals(unreviewed) if one.trigger is GateTrigger.MODEL_SOURCED_NUMERIC_FIELD
        ] == []
        assert all(numeric_provenance_violations(card) == [] for card in unreviewed.cards())

    def test_a_model_written_number_that_reached_the_index_is_still_fired_and_still_refused(self, disposable):
        card = disposable.cards()[0]
        path = sorted(audited_numeric_paths(card))[0]
        tampered = card.model_copy(
            update={
                "field_provenance": {
                    **card.field_provenance,
                    path: FieldProvenance(
                        source=FieldSource.MODEL,
                        object_id=card.card_id,
                        note="written into the card by the drafting model",
                    ),
                }
            }
        )
        violations = numeric_provenance_violations(tampered)
        assert violations == [f"{path}: numeric value sourced from {FieldSource.MODEL}"]
        with pytest.raises(GateViolation, match="numeric value sourced from"):
            enforce_card_status(tampered)
        disposable.harness.catalog.update_card(tampered)
        rows = [
            one
            for one in run_signals(disposable)
            if one.trigger is GateTrigger.MODEL_SOURCED_NUMERIC_FIELD
        ]
        assert [one.subject_id for one in rows] == [card.card_id]
        assert [one.detail for one in rows] == numeric_provenance_violations(
            disposable.harness.catalog.get_card(card.card_id)
        )

    def test_the_queue_row_fires_only_past_the_cap_the_thresholds_record(self, unreviewed):
        assert len(unreviewed.plane.drafts) < DEFAULT_THRESHOLDS.expert_queue_card_limit
        assert [
            one for one in run_signals(unreviewed) if one.trigger is GateTrigger.EXPERT_QUEUE_OVERFLOW
        ] == []
        cap = len(unreviewed.plane.drafts) - 2
        queue = build_queue(unreviewed.plane.drafts, limit=cap)
        assert queue.limit == cap
        assert queue.observed
        rows = [
            one
            for one in run_signals(unreviewed, queue_cap=cap)
            if one.trigger is GateTrigger.EXPERT_QUEUE_OVERFLOW
        ]
        assert sorted(one.subject_id for one in rows) == sorted(queue.observed)
        assert all(GATE_RULES[one.trigger].auto_recoverable for one in rows)

    def test_every_signal_that_fired_names_a_subject_a_reviewer_can_open(self, unreviewed):
        cards = {card.card_id for card in unreviewed.cards()}
        signals = run_signals(unreviewed, attestation=attest(unreviewed, [unreviewed.plane.asset(SLOT_CLUSTERS).asset_id]))
        assert signals
        for one in signals:
            assert one.detail
            assert one.subject_id in cards or one.subject_id.partition(":")[0] in cards


# ------------------------------------------------------------------ where the two tables meet


class TestTheReleaseDecision:
    def test_the_unread_run_blocks_once_and_that_failure_names_a_wait_nobody_took(
        self, unreviewed, reproduction
    ):
        made = decision(unreviewed, reproduction=reproduction)
        assert made.allowed is False
        assert len(made.blockers) == 1
        assert MetricId.END_TO_END_LATENCY.value in made.blockers[0]
        assert "review_wait" in made.blockers[0]
        assert len(made.human_actions) == len(unreviewed.cards())
        assert made.noted == [f"low-risk finding: {one}" for one in READING.reported]

    def test_every_unmeasured_row_is_reported_as_a_trend_rather_than_left_out(self, unreviewed, reproduction):
        made = decision(unreviewed, reproduction=reproduction)
        unmeasured = [
            one.metric_id.value
            for one in verdicts(unreviewed, reproduction=reproduction).values()
            if one.status is MetricStatus.UNMEASURED
        ]
        assert unmeasured
        assert len(made.trends) == len(unmeasured)
        for name in unmeasured:
            assert any(one.startswith(f"{name} has no denominator") for one in made.trends)

    def test_readings_that_revised_two_of_five_cards_block_on_those_two_rows(self, reviewed, reproduction):
        made = decision(reviewed, reproduction=reproduction)
        assert made.allowed is False
        assert {one.partition(":")[0] for one in made.blockers} == {
            MetricId.EXPERT_REVISION_RATE.value,
            MetricId.REWORK_RATE.value,
        }

    def test_below_the_size_floors_the_same_two_failures_are_trends(self, reviewed, reproduction):
        assert TOO_SMALL_TO_GATE.gate_allowed is False
        made = decision(reviewed, reproduction=reproduction, truth=TOO_SMALL_TO_GATE)
        assert made.allowed is True
        assert made.blockers == []
        for name in (MetricId.EXPERT_REVISION_RATE.value, MetricId.REWORK_RATE.value):
            assert any(one.startswith(f"{name} failed below section 10.5") for one in made.trends)
        assert [one for one in made.trends if one.startswith("truth set:")] == [
            f"truth set: {one}" for one in TOO_SMALL_TO_GATE.shortfalls
        ]
        assert MetricId.SAFETY_GATES.value not in " ".join(made.trends + made.blockers)

    def test_a_flipped_high_risk_case_blocks_whichever_way_the_floors_fall(
        self, unreviewed, reproduction
    ):
        passing = next(one for one in CORPUS if one.harm_level is HarmLevel.HIGH and one.passed)
        flipped = [
            one.model_copy(update={"passed": False}) if one.case_id == passing.case_id else one
            for one in CORPUS
        ]
        reading = evaluate_red_team(flipped, today=CORPUS_ON)
        assert reading.high_passed == READING.high_passed - 1
        assert reading.high_total == READING.high_total
        assert len(reading.blockers) == len(READING.blockers) + 1
        assert passing.case_id in " ".join(reading.blockers)
        for truth in (None, TOO_SMALL_TO_GATE):
            made = decision(unreviewed, reproduction=reproduction, red_team=reading, truth=truth)
            assert made.allowed is False
            assert any(one.startswith(MetricId.SAFETY_GATES.value) for one in made.blockers)
            assert any(passing.case_id in one for one in made.blockers)
        row = verdict_of(unreviewed, MetricId.SAFETY_GATES, reproduction=reproduction, red_team=reading)
        assert row.status is MetricStatus.FAIL
        assert row.release_blocking is True
        assert row.value < 1.0
        assert row.value == round(reading.high_passed / reading.high_total, 6)

    def test_a_medium_failure_is_carried_only_while_its_fix_deadline_is_live(self, unreviewed, reproduction):
        passing = next(one for one in CORPUS if one.harm_level is HarmLevel.MEDIUM and one.passed)
        assert passing.fix_deadline is None
        for update, blocks in (({"passed": False}, True), ({"passed": False, "fix_deadline": C.FIX_DEADLINE}, False)):
            cases = [
                one.model_copy(update=update) if one.case_id == passing.case_id else one for one in CORPUS
            ]
            reading = evaluate_red_team(cases, today=CORPUS_ON)
            made = decision(unreviewed, reproduction=reproduction, red_team=reading)
            assert any(passing.case_id in one for one in made.blockers) is blocks
            assert any(passing.case_id in one for one in made.noted) is not blocks
        assert C.FIX_DEADLINE >= CORPUS_ON

    def test_an_open_low_risk_assumption_becomes_a_block_only_once_the_operator_dates_it(
        self, unreviewed, reproduction
    ):
        assert sum(any(one in line for line in READING.reported) for one in C.OPEN_ASSUMPTIONS) == len(
            C.OPEN_ASSUMPTIONS
        )
        reading = evaluate_red_team(CORPUS, floors=HarmFloors(low_risk_sla_days=1), today=CORPUS_ON)
        assert len(reading.blockers) == len(C.OPEN_ASSUMPTIONS)
        assert all(any(one in line for line in reading.blockers) for one in C.OPEN_ASSUMPTIONS)
        plain = decision(unreviewed, reproduction=reproduction)
        made = decision(unreviewed, reproduction=reproduction, red_team=reading)
        assert made.allowed is False
        assert made.blockers[: len(plain.blockers)] == plain.blockers
        assert made.blockers[-len(reading.blockers) :] == reading.blockers
        assert any(one.startswith(MetricId.SAFETY_GATES.value) for one in made.blockers)
        assert made.noted == []

    def test_a_release_cannot_be_shipped_without_the_safety_row_at_all(self, unreviewed, reproduction):
        rows = [
            one
            for one in evaluate_all(observations(unreviewed, reproduction=reproduction))
            if one.metric_id is not MetricId.SAFETY_GATES
        ]
        made = release_decision(
            metrics=rows, gates=apply_gate_rules(run_signals(unreviewed)), red_team=None, truth=None
        )
        assert made.allowed is False
        assert any("safety row of section 10.6" in one for one in made.blockers)

    def test_a_masked_citation_costs_the_gate_a_row_but_not_the_release(self, unreviewed, reproduction):
        marked = unreviewed.plane.asset(SLOT_CLUSTERS).asset_id
        attestation = attest(unreviewed, [marked])
        plain = decision(unreviewed, reproduction=reproduction)
        masked = decision(unreviewed, reproduction=reproduction, attestation=attestation)
        assert masked.blockers == plain.blockers
        assert masked.human_actions == plain.human_actions
        assert len(masked.gates) == len(plain.gates) + len(attestation.unscorable_uses)
        assert masked.allowed is False

    def test_a_card_supported_only_through_an_adhoc_channel_stops_the_deliverable(
        self, unreviewed, reproduction
    ):
        plan = adhoc_plan(unreviewed.plane.service.plan, set().union(*support_nodes(unreviewed).values()))
        plain = decision(unreviewed, reproduction=reproduction)
        adhoc = decision(unreviewed, reproduction=reproduction, plan=plan)
        assert adhoc.allowed is False
        assert set(plain.blockers) < set(adhoc.blockers)
        assert all(
            GateTrigger.ADHOC_ONLY_SUPPORT.value in one for one in set(adhoc.blockers) - set(plain.blockers)
        )
        assert set(plain.human_actions) < set(adhoc.human_actions)

    def test_the_decision_is_recomputed_from_the_records_rather_than_remembered(
        self, unreviewed, reviewed, reproduction
    ):
        first = decision(unreviewed, reproduction=reproduction)
        again = decision(unreviewed, reproduction=reproduction)
        assert first.decision_id() == again.decision_id()
        assert first.decision_id() != decision(reviewed, reproduction=reproduction).decision_id()
