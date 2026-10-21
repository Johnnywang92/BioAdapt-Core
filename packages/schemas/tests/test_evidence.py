import pytest
from pydantic import ValidationError

from bioadapt_schemas.enums import (
    CandidateType,
    EvidenceKind,
    EvidencePartition,
    EvidenceRole,
    FieldSource,
    FinalStatus,
    HypothesisType,
    LabelLevel,
    PerturbationKind,
    ScreeningResult,
    SignOff,
    UnknownReason,
)
from bioadapt_schemas.evidence import (
    CompositionCheck,
    ContinuousStateCheck,
    Evidence,
    EvidenceSource,
    FieldProvenance,
    NoveltyVsCoverage,
    OntologyRef,
    PerturbationResult,
    ScreeningItem,
    StabilitySummary,
)
from schemas_factories import make_card, make_screening, make_stability, screen_item


class TestOntologyRef:
    def test_term_id_requires_name_and_version(self):
        with pytest.raises(ValidationError, match="ontology_name and ontology_version"):
            OntologyRef(term_id="CL:0000767")
        assert OntologyRef(
            term_id="CL:0000767", ontology_name="cell ontology", ontology_version="2026-05-01"
        )

    def test_name_without_term_id_is_rejected(self):
        with pytest.raises(ValidationError, match="without term_id"):
            OntologyRef(ontology_name="cell ontology", ontology_version="2026-05-01")

    def test_empty_ref_is_valid_for_unknown_labels(self):
        assert OntologyRef().term_id is None


class TestEvidenceSource:
    def test_at_least_one_identifier_is_required(self):
        with pytest.raises(ValidationError, match="at least one identifier"):
            EvidenceSource(locator="figure 3")

    def test_literature_evidence_requires_a_locator(self):
        with pytest.raises(ValidationError, match="locator"):
            EvidenceSource(doi="10.1000/xyz")
        assert EvidenceSource(doi="10.1000/xyz", locator="fig 3, row 2")

    def test_reference_dataset_evidence_must_pin_a_version(self):
        with pytest.raises(ValidationError, match="dataset version"):
            EvidenceSource(dataset_id="cellxgene_census")
        assert EvidenceSource(dataset_id="cellxgene_census", version="2026-05")

    def test_computed_evidence_points_at_a_run_and_asset(self):
        source = EvidenceSource(run_id="run_1", asset_id="asset_de")
        assert source.license_class is None


class TestEvidence:
    def make(self, **overrides):
        fields = {
            "evidence_id": "ev_1",
            "kind": EvidenceKind.DIFFERENTIAL_EXPRESSION,
            "role": EvidenceRole.SUPPORTING,
            "partition": EvidencePartition.DISCOVERY,
            "source": EvidenceSource(run_id="run_1", asset_id="asset_de"),
            "provenance": FieldProvenance(source=FieldSource.TOOL, object_id="asset_de"),
            "independence_group": "pseudobulk_edgeR",
            "content": {"fdr": 0.002, "log2fc": 1.4},
        }
        fields.update(overrides)
        return Evidence(**fields)

    def test_trigger_signals_belong_to_the_discovery_partition(self):
        with pytest.raises(ValidationError, match="discovery partition"):
            self.make(role=EvidenceRole.TRIGGER, partition=EvidencePartition.CONFIRMATION)

    def test_provenance_is_mandatory(self):
        with pytest.raises(ValidationError):
            self.make(provenance=None)

    def test_unknown_evidence_fields_are_rejected(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            self.make(claim_confidence="very high")


class TestStabilityAndScreening:
    def test_results_are_required(self):
        with pytest.raises(ValidationError, match="requires perturbation results"):
            StabilitySummary(protocol_version="v1", results=[])

    def test_duplicate_perturbation_kinds_are_rejected(self):
        results = [
            PerturbationResult(kind=PerturbationKind.SEED, cc_ari=0.8, jaccard=0.7, replicates=2),
            PerturbationResult(kind=PerturbationKind.SEED, cc_ari=0.7, jaccard=0.6, replicates=2),
        ]
        with pytest.raises(ValidationError, match="duplicate perturbation kinds"):
            StabilitySummary(protocol_version="v1", results=results)

    def test_mandatory_protocol_has_five_perturbations(self):
        summary = make_stability()
        assert len(summary.results) == len(PerturbationKind.mandatory_protocol()) == 5

    def test_screened_risk_requires_tool_provenance(self):
        with pytest.raises(ValidationError, match="tool and tool_version"):
            ScreeningItem(result=ScreeningResult.WARN)
        assert screen_item(ScreeningResult.WARN).tool == "scrublet"

    def test_not_run_items_may_have_no_tool(self):
        item = ScreeningItem(result=ScreeningResult.NOT_RUN)
        assert item.tool is None

    def test_blocked_and_unscreened_risks_are_enumerable(self):
        screening = make_screening(
            doublet=screen_item(ScreeningResult.FAIL),
            ambient=ScreeningItem(result=ScreeningResult.NOT_RUN),
        )
        assert [str(r) for r in screening.blocked_by] == ["doublet"]
        assert [str(r) for r in screening.not_screened] == ["ambient"]


class TestDerivedCheckModels:
    def test_knn_must_be_positive(self):
        with pytest.raises(ValidationError):
            NoveltyVsCoverage(knn=0)

    def test_composition_p_values_are_bounded(self):
        with pytest.raises(ValidationError):
            CompositionCheck(p_value=1.5)
        assert CompositionCheck(p_value=0.02, multiplicity_strategy="benjamini-hochberg")

    def test_continuous_state_check_keeps_the_method(self):
        check = ContinuousStateCheck(method="diffusion_pseudotime", is_axis_tail=True, statistic=0.91)
        assert check.is_axis_tail


class TestEvidenceCard:
    def test_unknown_hypothesis_needs_a_reason_and_no_label(self):
        with pytest.raises(ValidationError, match="unknown_reason"):
            make_card(hypothesis_type=HypothesisType.UNKNOWN, unknown_reason=None)
        with pytest.raises(ValidationError, match="candidate label"):
            make_card(hypothesis_type=HypothesisType.UNKNOWN, unknown_reason=UnknownReason.QUALITY_LIMITED)

    def test_supported_and_provisional_cards_require_a_label(self):
        with pytest.raises(ValidationError, match="candidate_label"):
            make_card(candidate_label=None)

    def test_provisional_labels_may_not_borrow_an_ontology_id(self):
        with pytest.raises(ValidationError, match="ontology id"):
            make_card(
                ontology=OntologyRef(
                    term_id="CL:0000767", ontology_name="cell ontology", ontology_version="2026-05-01"
                )
            )

    def test_cell_type_labels_require_an_ontology_id(self):
        with pytest.raises(ValidationError, match="Cell Ontology"):
            make_card(label_level=LabelLevel.CELL_TYPE_BROAD)

    def test_referenced_evidence_ids_must_exist_on_the_card(self):
        with pytest.raises(ValidationError, match="not present in card.evidence"):
            make_card(supporting_evidence_ids=["ev_ghost"])

    def test_confirmation_evidence_is_also_supporting(self):
        with pytest.raises(ValidationError, match="listed as supporting"):
            make_card(FinalStatus.SUPPORTED, supporting_evidence_ids=["ev_support"])

    def test_confirmation_ids_must_point_at_the_confirmation_partition(self):
        with pytest.raises(ValidationError, match="partition is"):
            make_card(FinalStatus.SUPPORTED, confirmation_evidence_ids=["ev_support", "ev_confirm"])

    def test_trigger_ids_must_have_the_trigger_role(self):
        with pytest.raises(ValidationError, match="role=trigger"):
            make_card(trigger_signal_ids=["ev_support"])

    def test_trigger_signals_are_never_supporting_evidence(self):
        with pytest.raises(ValidationError, match="cannot be listed as supporting"):
            make_card(supporting_evidence_ids=["ev_support", "ev_trigger"])

    def test_supported_cards_require_a_signed_review(self):
        card = make_card(FinalStatus.SUPPORTED)
        assert card.sign_off is SignOff.FULL
        with pytest.raises(ValidationError, match="reviewer_id, reviewed_at"):
            make_card(FinalStatus.SUPPORTED, reviewer_id=None)

    def test_conflict_cards_must_show_the_conflict(self):
        with pytest.raises(ValidationError, match="contradicting evidence"):
            make_card(FinalStatus.CONFLICT, contradicting_evidence_ids=[])

    def test_candidate_types_are_recorded_on_the_card(self):
        card = make_card(candidate_types=[CandidateType.C2_REFERENCE_CONFLICT])
        assert card.candidate_types[0] is CandidateType.C2_REFERENCE_CONFLICT

    def test_cards_are_assembled_mutably_but_tool_summaries_stay_frozen(self):
        card = make_card()
        card.status_reason = "assembled by the evidence service"
        assert card.status_reason == "assembled by the evidence service"
        with pytest.raises(ValidationError, match="frozen"):
            card.stability_summary.protocol_version = "tampered"
