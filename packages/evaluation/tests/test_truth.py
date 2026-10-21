"""Frozen truth sets and the rule about who is allowed to have made a label (plan V2.0 section 10.5).

A truth set is where the plan puts its foot down on provenance: a class is an answer only when at least two
people labelled it without seeing each other, any disagreement between them was settled, and nobody has since
withdrawn the dataset behind it. So nothing here stores a verdict. The set stores the annotations, the
adjudications and the floors the study agreed to, and every use - truth, reference-only, refused, gate or trend
- is derived from those rows when somebody reads them. A set that wrote down "sufficient" would go on saying it
after the second annotator had been deleted.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import evaluation_factories as E
from bioadapt_evaluation.truth import (
    AnnotationCall,
    LabelUse,
    TruthClass,
    TruthFloors,
    TruthStratum,
)
from bioadapt_schemas.enums import LabelProvenance, LicenseReviewStatus


class TestProvenance:
    def test_two_blind_confirmations_make_a_class_an_answer(self) -> None:
        reading = E.truth_set().reading()
        one = reading.for_class(E.RARE, E.DATASET_A)
        assert one.use is LabelUse.TRUTH
        assert one.threshold_gate_allowed
        assert one.usable

    def test_an_atlas_label_is_a_reference_and_never_an_answer(self) -> None:
        frozen = E.truth_set()
        made = E.truth_set(
            classes=[
                E.truth_class(label_provenance=LabelProvenance.PREDICTED),
                *frozen.classes[1:],
            ]
        )
        one = made.reading().for_class(E.RARE, E.DATASET_A)
        assert one.use is LabelUse.REFERENCE_ONLY
        assert not one.threshold_gate_allowed
        assert any("as a reference, never as truth" in reason for reason in one.reasons)

    def test_one_annotator_alone_is_not_independent_annotation(self) -> None:
        made = E.truth_set(annotations=E.blind_pair()[:1])
        one = made.reading().for_class(E.RARE, E.DATASET_A)
        assert one.use is LabelUse.REFUSED
        assert any("1 of 2 blind annotations recorded" in reason for reason in one.reasons)

    def test_two_answers_from_the_same_hands_are_not_independent(self) -> None:
        made = E.truth_set(
            annotations=[
                E.annotation(E.EXPERT_1),
                E.annotation(E.EXPERT_1, annotation_id="ann_expert_01_rare_state_ds_pbmc_01_again"),
            ]
        )
        one = made.reading().for_class(E.RARE, E.DATASET_A)
        assert one.use is LabelUse.REFUSED
        assert any("same hands" in reason for reason in one.reasons)

    def test_a_model_or_a_service_account_cannot_be_an_annotator(self) -> None:
        for annotator_id in ("model_labeler", "svc_orchestrator"):
            with pytest.raises(ValidationError, match="independent human annotation"):
                E.annotation(annotator_id)

    def test_a_disagreement_without_an_adjudication_is_not_an_answer(self) -> None:
        made = E.truth_set(
            annotations=[
                E.annotation(E.EXPERT_1),
                E.annotation(E.EXPERT_2, call=AnnotationCall.REJECTED),
            ]
        )
        one = made.reading().for_class(E.RARE, E.DATASET_A)
        assert one.use is LabelUse.REFUSED
        assert any("no adjudication settles it" in reason for reason in one.reasons)

    def test_an_adjudication_makes_two_contradicting_answers_one_label(self) -> None:
        made = E.truth_set(
            annotations=[
                E.annotation(E.EXPERT_1),
                E.annotation(E.EXPERT_2, call=AnnotationCall.REJECTED),
            ],
            adjudications=[E.adjudication(resolution=AnnotationCall.REJECTED)],
        )
        one = made.reading().for_class(E.RARE, E.DATASET_A)
        assert one.use is LabelUse.TRUTH
        assert one.threshold_gate_allowed

    def test_an_adjudication_without_a_reason_is_not_a_decision(self) -> None:
        made = E.truth_set(
            annotations=[
                E.annotation(E.EXPERT_1),
                E.annotation(E.EXPERT_2, call=AnnotationCall.REJECTED),
            ],
            adjudications=[E.adjudication(reason="   ")],
        )
        one = made.reading().for_class(E.RARE, E.DATASET_A)
        assert any("adjudication without a reason" in reason for reason in one.reasons)
        assert one.use is LabelUse.REFUSED


class TestFreezeIntegrity:
    def test_one_row_per_label_and_dataset(self) -> None:
        with pytest.raises(ValidationError, match="one row per"):
            E.truth_set(classes=[E.truth_class(), E.truth_class()])

    def test_an_annotation_cannot_name_a_class_the_set_does_not_hold(self) -> None:
        with pytest.raises(ValidationError, match="which this set does not hold"):
            E.truth_set(annotations=[E.annotation(label="unknown_population")])

    def test_a_class_cannot_carry_two_adjudications(self) -> None:
        made = E.truth_set(adjudications=[E.adjudication(), E.adjudication()])
        with pytest.raises(ValueError, match="carries 2 adjudications"):
            made.adjudication_for(E.RARE, E.DATASET_A)

    def test_a_synthetic_class_with_an_unreviewed_licence_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="unreviewed licence"):
            TruthClass(
                label="doublet",
                dataset_id=E.DATASET_A,
                stratum=TruthStratum.COUNTEREXAMPLE,
                cell_count=300,
                donor_count=3,
                label_provenance=LabelProvenance.CURATED_EXPERT,
                synthetic=True,
                license_review=LicenseReviewStatus.PENDING,
            )

    def test_the_fingerprint_moves_when_a_dataset_is_withdrawn(self) -> None:
        made = E.truth_set()
        withdrawn = E.truth_set(
            classes=[one for one in made.classes if one.dataset_id != E.DATASET_C],
            annotations=[
                one for one in made.annotations if one.dataset_id != E.DATASET_C
            ],
        )
        assert made.content_fingerprint() != withdrawn.content_fingerprint()

    def test_the_fingerprint_is_the_content_not_the_clock(self) -> None:
        assert E.truth_set().content_fingerprint() == E.truth_set().content_fingerprint()

    def test_a_class_removed_from_the_freeze_is_not_silently_answered(self) -> None:
        made = E.truth_set(
            classes=[one for one in E.truth_set().classes if one.label != E.RARE],
            annotations=[one for one in E.truth_set().annotations if one.label != E.RARE],
        )
        with pytest.raises(LookupError, match="rare_state"):
            made.reading().for_class(E.RARE, E.DATASET_A)

    def test_only_classes_that_clear_the_rules_are_truth_labels(self) -> None:
        labels = E.truth_set().reading().truth_labels
        assert E.RARE in labels
        assert all(one in (E.AMBIENT, E.COMMON, E.DOUBLET, E.RARE) for one in labels)


class TestFloors:
    def test_a_rare_class_below_the_cell_floor_is_a_trend_not_a_gate(self) -> None:
        made = E.truth_set(classes=[E.truth_class(cell_count=120), *E.truth_set().classes[1:]])
        one = made.reading().for_class(E.RARE, E.DATASET_A)
        assert one.use is LabelUse.TRUTH
        assert not one.threshold_gate_allowed
        assert any("200-cell" in reason for reason in one.reasons)

    def test_a_rare_class_below_the_donor_floor_is_a_trend_too(self) -> None:
        made = E.truth_set(classes=[E.truth_class(donor_count=2), *E.truth_set().classes[1:]])
        one = made.reading().for_class(E.RARE, E.DATASET_A)
        assert not one.threshold_gate_allowed
        assert any("3-donor floor" in reason for reason in one.reasons)

    def test_a_common_class_needs_three_datasets_not_three_rows(self) -> None:
        made = E.truth_set(
            classes=[E.common_class(E.DATASET_A), E.common_class(E.DATASET_B)],
            annotations=[
                *E.blind_pair(E.COMMON, E.DATASET_A),
                *E.blind_pair(E.COMMON, E.DATASET_B),
            ],
        )
        one = made.reading().for_class(E.COMMON, E.DATASET_A)
        assert not one.threshold_gate_allowed
        assert any("2 dataset(s) of the 3" in reason for reason in one.reasons)

    def test_the_counterexample_floor_is_a_shortfall_of_the_set(self) -> None:
        made = E.truth_set(
            classes=[one for one in E.truth_set().classes if one.stratum is not TruthStratum.COUNTEREXAMPLE],
            annotations=[
                one
                for one in E.truth_set().annotations
                if one.label not in (E.DOUBLET, E.AMBIENT)
            ],
        )
        reading = made.reading()
        assert any("counterexample" in reason for reason in reading.shortfalls)
        assert not reading.gate_allowed

    def test_the_plan_s_floor_is_twenty_counterexample_classes(self) -> None:
        assert TruthFloors().min_counterexample_classes == 20
        assert TruthFloors().min_rare_cells == 200
        assert TruthFloors().min_rare_donors == 3
        assert TruthFloors().min_common_datasets == 3
        assert TruthFloors().min_retrieval_pairs == 50

    def test_a_report_only_set_cannot_gate_even_when_it_meets_every_floor(self) -> None:
        assert not E.truth_set(report_only=True).reading().gate_allowed

    def test_a_set_whose_every_class_is_refused_cannot_gate(self) -> None:
        made = E.truth_set(annotations=[])
        reading = made.reading()
        assert not reading.gate_allowed
        assert any("no class" in reason for reason in reading.shortfalls)


class TestRetrievalQueriesArePartOfTheFreeze:
    def test_a_query_names_at_least_one_expected_answer(self) -> None:
        with pytest.raises(ValidationError):
            E.retrieval_query(expected_evidence_refs=[])

    def test_an_expected_abstention_is_a_legitimate_annotation(self) -> None:
        query = E.retrieval_query(expects_no_support=True, expected_evidence_refs=["none"])
        assert query.expects_no_support
