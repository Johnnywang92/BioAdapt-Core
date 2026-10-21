from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from bioadapt_schemas.enums import (
    AlignmentKind,
    AssetRole,
    CandidateType,
    FilteringState,
    GenomeIdSystem,
    PairingMethod,
    PrivacyTier,
    RegulatoryProfile,
    ReplicateType,
    ReproducibilityLevel,
    RetentionClass,
    ReviewDecision,
    ReviewScope,
    Severity,
    UsageScope,
)
from bioadapt_schemas.entities import (
    AlignmentRecord,
    AssetLineage,
    CandidateSet,
    Cell,
    CellPairing,
    DataAsset,
    Genome,
    Library,
    ModelInvocation,
    PreflightIssue,
    PreflightReport,
    Project,
    ReviewEvent,
    RunManifest,
    Specimen,
    UpstreamPipeline,
    sha256_of_json,
)
from bioadapt_schemas.identifiers import derive_candidate

UTC = timezone.utc
D64 = "a1" * 32


def make_project(**overrides):
    fields = {
        "project_id": "prj_001",
        "study_goal": "find rare myeloid subtype",
        "species": "mouse",
        "privacy_tier": PrivacyTier.RESTRICTED,
        "created_by": "user_analyst_01",
        "annotation_granularity_target": "cell_type_broad",
        "review_load_budget_hours_per_week": 4.0,
    }
    fields.update(overrides)
    return Project(**fields)


def make_genome():
    return Genome(
        build="GRCm39", annotation_source="Ensembl", version="111", id_system=GenomeIdSystem.ENSEMBL
    )


def make_asset(**overrides):
    fields = {
        "asset_id": "asset_counts",
        "project_id": "prj_001",
        "uri": "s3://bioadapt/prj_001/counts.h5ad",
        "file_sha256": D64,
        "content_fingerprint": D64,
        "format": "h5ad",
        "schema_version": "v1",
        "size_bytes": 1024,
        "privacy_tier": PrivacyTier.RESTRICTED,
        "usage_scope": UsageScope.THIS_PROJECT_ONLY,
        "retention_class": RetentionClass.RUN_LINKED,
        "filtering_state": FilteringState.NONE,
        "created_at": datetime(2026, 10, 19, tzinfo=UTC),
    }
    fields.update(overrides)
    return DataAsset(**fields)


def make_candidate(**overrides):
    cid, fingerprint, size = derive_candidate(
        [("spc_01", "AAAC"), ("spc_01", "GGGT"), ("spc_02", "TTTT")], "run_prj_01"
    )
    fields = {
        "candidate_id": cid,
        "run_id": "run_prj_01",
        "member_fingerprint": fingerprint,
        "size": size,
        "donor_count": 2,
        "display_name": "cluster_7",
        "cluster_origin": {"tool": "leiden", "tool_version": "0.5.1", "resolution": 1.0},
        "candidate_types": [CandidateType.C3_RARE_STABLE],
    }
    fields.update(overrides)
    return CandidateSet(**fields)


class TestProject:
    def test_hgr_profile_forces_the_strictest_tier(self):
        with pytest.raises(ValidationError, match="human genetic resource"):
            make_project(
                regulatory_profile=[RegulatoryProfile.HGR], privacy_tier=PrivacyTier.RESTRICTED
            )
        assert make_project(
            regulatory_profile=[RegulatoryProfile.HGR], privacy_tier=PrivacyTier.HGR_CONTROLLED
        )

    def test_review_budget_must_be_positive(self):
        with pytest.raises(ValidationError):
            make_project(review_load_budget_hours_per_week=0)

    def test_unknown_fields_are_rejected(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            make_project(secret_api_key="nope")


class TestCellAndPairing:
    def test_cell_key_is_derived_from_specimen_and_barcode(self):
        cell = Cell(specimen_id="spc_01", barcode="AAACGG")
        assert cell.cell_key == "spc_01\x1faaacgg"

    def test_explicit_cell_key_is_respected(self):
        cell = Cell(specimen_id="spc_01", barcode="AAAC", cell_key="legacy_key")
        assert cell.cell_key == "legacy_key"

    def test_cells_are_immutable(self):
        cell = Cell(specimen_id="spc_01", barcode="AAAC")
        with pytest.raises(ValidationError, match="frozen"):
            cell.barcode = "GGGG"

    def test_barcode_normalization_version_can_be_pinned_per_project(self):
        cell = Cell(specimen_id="spc_01", barcode="AAAC", barcode_normalization="v1-lower")
        assert cell.cell_key.endswith("aaac")

    def test_pairing_method_decides_required_fields(self):
        with pytest.raises(ValidationError, match="confidence"):
            CellPairing(
                cell_pairing_id="pair_1",
                run_id="run_1",
                modality_a="RNA",
                modality_b="ATAC",
                method=PairingMethod.BARCODE_INFERENCE,
            )
        with pytest.raises(ValidationError, match="failure_reason"):
            CellPairing(
                cell_pairing_id="pair_2",
                run_id="run_1",
                modality_a="RNA",
                modality_b="ATAC",
                method=PairingMethod.UNPAIRED,
            )
        assert CellPairing(
            cell_pairing_id="pair_3",
            run_id="run_1",
            modality_a="RNA",
            modality_b="ATAC",
            method=PairingMethod.EXPERIMENTAL_WET_PAIRING,
            confidence=1.0,
        )

    def test_library_records_replicate_type(self):
        lib = Library(
            library_id="lib_01", specimen_id="spc_01", batch_id="batch_1", replicate_type=ReplicateType.BIOLOGICAL
        )
        assert lib.replicate_type is ReplicateType.BIOLOGICAL

    def test_specimen_is_scoped_to_a_project(self):
        spec = Specimen(specimen_id="spc_01", project_id="prj_001", donor_id="donor_1", tissue="lung")
        assert spec.donor_id == "donor_1"


class TestDataAssets:
    def test_both_digest_levels_are_required(self):
        with pytest.raises(ValidationError, match="64-char hex"):
            make_asset(file_sha256="short")
        assert make_asset()

    def test_unknown_filtering_state_requires_upstream_pipeline(self):
        with pytest.raises(ValidationError, match="upstream pipeline"):
            make_asset(filtering_state=FilteringState.UNKNOWN)
        assert make_asset(
            filtering_state=FilteringState.UNKNOWN,
            upstream_pipeline=UpstreamPipeline(
                name="cellranger",
                version="8.0.1",
                reference_build="GRCm39",
                reference_annotation_source="Ensembl",
            ),
        )

    def test_lineage_is_a_dag_edge_not_a_parent_field(self):
        edge = AssetLineage(run_id="run_1", node_id="integrate", asset_id="asset_counts", role=AssetRole.INPUT, ordinal=1)
        assert edge.role is AssetRole.INPUT
        with pytest.raises(ValidationError):
            AssetLineage(run_id="run_1", node_id="n", asset_id="a", role=AssetRole.INPUT, ordinal=-1)


class TestRunManifest:
    def make_manifest(self, **overrides):
        fields = {
            "run_id": "run_prj_001_01",
            "project_id": "prj_001",
            "plan_id": "plan_001",
            "plan_version": "1",
            "tool_versions": {"scanpy": "1.11.0"},
            "tool_registry_snapshot": "registry_2026_10_19",
            "input_hashes": {"asset_counts": D64},
            "reproducibility_level_target": ReproducibilityLevel.CONCLUSION,
        }
        fields.update(overrides)
        return RunManifest(**fields)

    def test_at_least_one_input_hash_is_required(self):
        with pytest.raises(ValidationError, match="at least one input hash"):
            self.make_manifest(input_hashes={})

    def test_input_hashes_must_be_digests(self):
        with pytest.raises(ValidationError, match="input hash of asset_counts"):
            self.make_manifest(input_hashes={"asset_counts": "v1"})

    def test_model_invocations_are_recorded_with_digests(self):
        manifest = self.make_manifest(
            model_invocations=[
                ModelInvocation(
                    model_id="local-llm-1",
                    purpose="plan_drafting",
                    prompt_sha256=D64,
                    output_sha256=D64,
                    input_tokens=1200,
                    output_tokens=800,
                    cost_usd=0.0,
                    policy_decision="allow_local",
                )
            ]
        )
        assert manifest.model_invocations[0].purpose == "plan_drafting"
        with pytest.raises(ValidationError, match="64-char hex"):
            ModelInvocation(
                model_id="m", purpose="p", prompt_sha256="x", output_sha256=D64,
                input_tokens=1, output_tokens=1, cost_usd=0, policy_decision="allow",
            )


class TestCandidateSet:
    def test_id_is_bound_to_fingerprint_and_run(self):
        candidate = make_candidate()
        other = derive_candidate([("spc_01", "CCCC")], "run_prj_01")
        with pytest.raises(ValidationError, match="candidate_id must be"):
            make_candidate(member_fingerprint=other[1])

    def test_donor_count_cannot_exceed_size(self):
        with pytest.raises(ValidationError, match="donor_count"):
            make_candidate(donor_count=99)

    def test_candidate_types_are_mandatory(self):
        with pytest.raises(ValidationError, match="candidate type"):
            make_candidate(candidate_types=[])

    def test_alignment_history_keeps_jaccard_and_decision(self):
        candidate = make_candidate(
            alignment_history=[
                AlignmentRecord(
                    other_candidate_id="cand_other",
                    other_run_id="run_prj_00",
                    kind=AlignmentKind.CONTINUATION,
                    jaccard=0.81,
                    carry_over=True,
                    decided_by="user_expert_01",
                )
            ]
        )
        assert candidate.alignment_history[0].kind is AlignmentKind.CONTINUATION


class TestPreflightAndReview:
    def test_blocking_issue_forces_preflight_failure(self):
        report = PreflightReport(
            project_id="prj_001",
            run_id="run_1",
            passed=False,
            checked_at=datetime(2026, 10, 19, tzinfo=UTC),
            issues=[PreflightIssue(code="missing_donor_id", severity=Severity.BLOCK, message="obs has no donor")],
        )
        assert report.issues[0].severity is Severity.BLOCK
        with pytest.raises(ValidationError, match="blocking issues present"):
            PreflightReport(
                project_id="prj_001",
                run_id="run_1",
                passed=True,
                checked_at=datetime(2026, 10, 19, tzinfo=UTC),
                issues=[PreflightIssue(code="missing_donor_id", severity=Severity.BLOCK, message="x")],
            )

    def make_review(self, **overrides):
        fields = {
            "review_id": "rev_002",
            "object_id": "card_001",
            "object_type": "evidence_card",
            "scope": ReviewScope.CARD,
            "reviewer_id": "user_expert_01",
            "decision": ReviewDecision.APPROVE,
            "rationale": "evidence chain complete",
            "timestamp": datetime(2026, 10, 19, tzinfo=UTC),
        }
        fields.update(overrides)
        return ReviewEvent(**fields)

    def test_a_review_cannot_supersede_itself(self):
        with pytest.raises(ValidationError, match="supersede itself"):
            self.make_review(supersedes_review_id="rev_002")

    def test_revise_records_the_new_value(self):
        with pytest.raises(ValidationError, match="new_value"):
            self.make_review(decision=ReviewDecision.REVISE)
        assert self.make_review(decision=ReviewDecision.REVISE, new_value="unknown")

    def test_field_revision_keeps_the_original_value(self):
        with pytest.raises(ValidationError, match="original value"):
            self.make_review(
                decision=ReviewDecision.REVISE, scope=ReviewScope.FIELD, new_value="supported"
            )
        assert self.make_review(
            decision=ReviewDecision.REVISE,
            scope=ReviewScope.FIELD,
            new_value="supported",
            previous_value="provisional",
        )

    def test_empty_rationale_is_rejected(self):
        with pytest.raises(ValidationError):
            self.make_review(rationale="")


def test_sha256_of_json_is_key_order_independent():
    assert sha256_of_json({"a": 1, "b": {"x": 1, "y": 2}}) == sha256_of_json(
        {"b": {"y": 2, "x": 1}, "a": 1}
    )
    assert sha256_of_json({"a": 1}) != sha256_of_json({"a": 2})
