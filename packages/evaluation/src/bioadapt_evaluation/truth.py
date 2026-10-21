"""Frozen ground truth and the annotation a truth claim is allowed to rest on (plan V2.0 section 10.5).

Section 10.5's rule is about provenance rather than size: a label may be used as truth only when two people
blindly produced it and every disagreement between them was settled, and an atlas label nobody curated is
allowed to be a reference but never an answer. So a set stores what the annotators did and the floors the
study agreed to, and every judgement - truth, reference-only, refused, and whether the class is big enough
to gate a threshold on - is derived from those rows by ``TruthSet.reading``. A set that recorded its own
verdict would keep saying "sufficient" after the third dataset had been withdrawn.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Optional

from pydantic import Field, model_validator

from bioadapt_schemas.entities import ImmutableContract, sha256_of_json
from bioadapt_schemas.enums import LabelProvenance, LicenseReviewStatus


class TruthStratum(StrEnum):
    COMMON = "common"
    RARE = "rare"
    COUNTEREXAMPLE = "counterexample"


class AnnotationCall(StrEnum):
    """What one blind annotator said about one candidate in one dataset."""

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    ABSTAINED = "abstained"


class LabelUse(StrEnum):
    """The three things a stored label can be, once section 10.5's provenance rule is applied to it."""

    TRUTH = "truth"
    REFERENCE_ONLY = "reference_only"
    REFUSED = "refused"


class TruthFloors(ImmutableContract):
    """Section 10.5's size minimums, kept beside the set so a later floor cannot silently move.

    Below these numbers the plan still allows a report, but only as a trend with a bootstrap interval - the
    floors exist because a class of forty cells cannot carry a pass/fail threshold.
    """

    version: str = "truth_floors_v1"
    min_common_datasets: int = 3
    min_rare_cells: int = 200
    min_rare_donors: int = 3
    min_counterexample_classes: int = 20
    min_blind_annotations: int = 2
    min_retrieval_pairs: int = 50


class TruthClass(ImmutableContract):
    """One label in one dataset, counted the way the study counted it, with the provenance it came from."""

    label: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    stratum: TruthStratum
    cell_count: int = Field(ge=0)
    donor_count: int = Field(ge=0)
    label_provenance: LabelProvenance
    #: Synthetic classes are the planned way to hold counterexamples and stress cases without letting real
    #: personal data into a benchmark that outlives the study (§10.5).
    synthetic: bool = False
    license_review: LicenseReviewStatus = LicenseReviewStatus.NOT_REQUIRED
    source_ref: Optional[str] = None

    @model_validator(mode="after")
    def _synthetic_needs_a_licence_decision(self) -> "TruthClass":
        if self.synthetic and self.license_review is LicenseReviewStatus.PENDING:
            raise ValueError(
                f"synthetic class {self.label} in {self.dataset_id} has an unreviewed licence; "
                "section 10.5 registers source and licence before a generated set enters the benchmark"
            )
        return self


class BlindAnnotation(ImmutableContract):
    """One annotator's answer, recorded before they were told what anybody else said."""

    annotation_id: str
    dataset_id: str
    label: str
    annotator_id: str
    call: AnnotationCall
    made_at: datetime

    @model_validator(mode="after")
    def _is_blind(self) -> "BlindAnnotation":
        if "model" in self.annotator_id.lower() or self.annotator_id.startswith("svc_"):
            raise ValueError(
                f"annotation {self.annotation_id} was made by {self.annotator_id}: section 10.5's truth is "
                "independent human annotation, so a model or service account cannot be one of its authors"
            )
        return self


class Adjudication(ImmutableContract):
    """The settlement of a disagreement, which is the only way two contradicting annotations become a label."""

    dataset_id: str
    label: str
    annotator_ids: list[str] = Field(min_length=2)
    decided_by: str
    resolution: AnnotationCall
    reason: str = Field(min_length=1)
    decided_at: datetime


class ClassReading(ImmutableContract):
    """A derived answer: what this stored class may be used for, and the rule that decided it."""

    label: str
    dataset_id: str
    use: LabelUse
    reasons: tuple[str, ...]
    #: False when the class falls short of a floor, which is the difference between a trend and a gate.
    threshold_gate_allowed: bool

    @property
    def usable(self) -> bool:
        return self.use is LabelUse.TRUTH and self.threshold_gate_allowed


class RetrievalQuery(ImmutableContract):
    """One query and the evidence a curator says answers it (§10.5's retrieval set)."""

    query_id: str
    question: str = Field(min_length=1)
    expected_evidence_refs: list[str] = Field(min_length=1)
    #: A query whose expected answer is an abstention is a legitimate case: the plan measures abstention
    #: quality, so "no evidence supports this" is an annotation, not an empty row.
    expects_no_support: bool = False


class TruthSet(ImmutableContract):
    """A frozen evaluation set: its classes, the annotation behind each label, and the floors it was built to."""

    truth_set_id: str
    version: str
    frozen_on: date
    purpose: str = Field(min_length=1)
    floors: TruthFloors = Field(default_factory=TruthFloors)
    classes: list[TruthClass] = Field(min_length=1)
    annotations: list[BlindAnnotation] = Field(default_factory=list)
    adjudications: list[Adjudication] = Field(default_factory=list)
    retrieval_queries: list[RetrievalQuery] = Field(default_factory=list)
    #: The plan's own warning: a set below its floors reports a trend and must not gate a release on it.
    report_only: bool = False

    @model_validator(mode="after")
    def _frozen_means_addressed(self) -> "TruthSet":
        keyed = {(one.label, one.dataset_id) for one in self.classes}
        if len(keyed) != len(self.classes):
            raise ValueError("a truth set holds one row per (label, dataset): a duplicate makes two answers")
        for annotation in self.annotations:
            if (annotation.label, annotation.dataset_id) not in keyed:
                raise ValueError(
                    f"annotation {annotation.annotation_id} answers {annotation.label} in "
                    f"{annotation.dataset_id}, which this set does not hold"
                )
        return self

    def content_fingerprint(self) -> str:
        """The set's address: a frozen version is only useful if the bytes behind it can be re-checked."""
        return sha256_of_json(self.model_dump(mode="json"))

    def annotations_for(self, label: str, dataset_id: str) -> list[BlindAnnotation]:
        return [
            one
            for one in self.annotations
            if one.label == label and one.dataset_id == dataset_id
        ]

    def adjudication_for(self, label: str, dataset_id: str) -> Optional[Adjudication]:
        found = [
            one for one in self.adjudications if one.label == label and one.dataset_id == dataset_id
        ]
        if len(found) > 1:
            raise ValueError(f"{label} in {dataset_id} carries {len(found)} adjudications")
        return found[0] if found else None

    def counterexample_classes(self) -> list[TruthClass]:
        return [one for one in self.classes if one.stratum is TruthStratum.COUNTEREXAMPLE]

    def dataset_count(self, label: str) -> int:
        return len({one.dataset_id for one in self.classes if one.label == label})

    def reading(self) -> "TruthReading":
        """Derive what each class may be used for. Nothing here is stored, so nothing here can go stale."""
        return TruthReading(self)


class TruthReading:
    """The derived view of a truth set: per-class use, the set's floors, and what may gate a decision."""

    def __init__(self, the_set: TruthSet) -> None:
        self.the_set = the_set
        self.classes: tuple[ClassReading, ...] = tuple(
            self._read(one) for one in sorted(the_set.classes, key=lambda c: (c.dataset_id, c.label))
        )
        self.shortfalls: tuple[str, ...] = self._shortfalls()

    def for_class(self, label: str, dataset_id: str) -> ClassReading:
        found = [one for one in self.classes if one.label == label and one.dataset_id == dataset_id]
        if not found:
            raise LookupError(f"{label} in {dataset_id} is not a class of truth set {self.the_set.version}")
        return found[0]

    @property
    def truth_labels(self) -> tuple[str, ...]:
        return tuple(sorted({one.label for one in self.classes if one.use is LabelUse.TRUTH}))

    @property
    def gate_allowed(self) -> bool:
        """A release may be gated on this set only if every class it claims as truth clears its floors."""
        return not self.shortfalls and any(one.threshold_gate_allowed for one in self.classes)

    def _read(self, one: TruthClass) -> ClassReading:
        reasons: list[str] = []
        if one.label_provenance is not LabelProvenance.CURATED_EXPERT:
            reasons.append(
                f"label provenance is {one.label_provenance}: section 10.5 allows an atlas label as a "
                "reference, never as truth"
            )
        made = self.the_set.annotations_for(one.label, one.dataset_id)
        distinct = {item.annotator_id for item in made if item.call is not AnnotationCall.ABSTAINED}
        adjudicated = self.the_set.adjudication_for(one.label, one.dataset_id)
        if len(made) < self.the_set.floors.min_blind_annotations:
            reasons.append(
                f"{len(made)} of {self.the_set.floors.min_blind_annotations} blind annotations recorded"
            )
        elif len(distinct) < self.the_set.floors.min_blind_annotations and adjudicated is None:
            reasons.append(
                "every answer comes from the same hands: independent annotation needs "
                f"{self.the_set.floors.min_blind_annotations} distinct annotators or an adjudication (§10.5)"
            )
        calls = {item.call for item in made}
        if len(calls) > 1 and adjudicated is None:
            reasons.append(
                f"the blind annotations disagree ({sorted(str(c) for c in calls)}) and no adjudication settles it"
            )
        if adjudicated is not None and adjudicated.reason.strip() == "":
            reasons.append("an adjudication without a reason is not a decision")
        if one.synthetic and one.license_review is not LicenseReviewStatus.APPROVED:
            reasons.append(
                f"synthetic class licence is {one.license_review}: generated counterexamples are registered "
                "before they enter the benchmark (§10.5)"
            )
        use = LabelUse.TRUTH if not reasons else (
            LabelUse.REFERENCE_ONLY
            if one.label_provenance is not LabelProvenance.CURATED_EXPERT
            else LabelUse.REFUSED
        )
        gate = use is LabelUse.TRUTH and self._meets_size_floors(one)
        if use is LabelUse.TRUTH and not gate:
            reasons.append(self._size_shortfall(one))
        return ClassReading(
            label=one.label,
            dataset_id=one.dataset_id,
            use=use,
            reasons=tuple(reasons),
            threshold_gate_allowed=gate,
        )

    def _meets_size_floors(self, one: TruthClass) -> bool:
        return self._size_shortfall(one) == ""

    def _size_shortfall(self, one: TruthClass) -> str:
        floors = self.the_set.floors
        if one.stratum is TruthStratum.COMMON:
            datasets = self.the_set.dataset_count(one.label)
            if datasets < floors.min_common_datasets:
                return (
                    f"a common class is measured across {datasets} dataset(s) of the {floors.min_common_datasets} "
                    "the floor requires, so this is a trend, not a gate (§10.5)"
                )
            return ""
        if one.stratum is TruthStratum.RARE:
            if one.cell_count < floors.min_rare_cells or one.donor_count < floors.min_rare_donors:
                return (
                    f"{one.cell_count} cells from {one.donor_count} donor(s) is below the "
                    f"{floors.min_rare_cells}-cell, {floors.min_rare_donors}-donor floor for a rare class, "
                    "so report a bootstrap interval and do not gate on it (§10.5)"
                )
            return ""
        return ""

    def _shortfalls(self) -> tuple[str, ...]:
        floors = self.the_set.floors
        found: list[str] = []
        if len(self.the_set.counterexample_classes()) < floors.min_counterexample_classes:
            found.append(
                f"the set holds {len(self.the_set.counterexample_classes())} counterexample classes against a "
                f"floor of {floors.min_counterexample_classes}: doublets, ambient RNA and known artefact history "
                "are under-covered (§10.5)"
            )
        if len(self.the_set.retrieval_queries) < floors.min_retrieval_pairs:
            found.append(
                f"{len(self.the_set.retrieval_queries)} retrieval queries against a floor of "
                f"{floors.min_retrieval_pairs} (§10.5)"
            )
        if self.the_set.report_only:
            found.append("the set is marked report-only, so nothing may gate a release on it (§10.4)")
        if all(one.use is not LabelUse.TRUTH for one in self.classes):
            found.append("no class of this set survives section 10.5's provenance rule as truth")
        return tuple(found)
