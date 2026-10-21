from datetime import timedelta

import pytest
from pydantic import ValidationError

from bioadapt_schemas.enums import ReproducibilityLevel, ScreeningResult
from bioadapt_tool_registry import (
    PromotionChecklist,
    PromotionReview,
    RegistryError,
    ToolExecution,
    ToolRegistry,
    ValidationOutcome,
    attest,
    minimal_spec,
)
from bioadapt_tool_registry.enums import ToolResultStatus
from registry_factories import (
    D64,
    DIGEST,
    NOW,
    adhoc_spec,
    approval,
    complete_spec,
    classified_error,
    execution,
    result,
)


class TestToolResult:
    def test_a_complete_result_validates(self):
        res = result()
        assert res.status is ToolResultStatus.SUCCESS
        assert res.metrics["median_genes_after_qc"] == 1420.0

    def test_every_declared_input_needs_a_hash(self):
        with pytest.raises(ValidationError, match="no recorded hash"):
            result(input_asset_ids=["asset_counts", "asset_metadata"])

    def test_hashes_must_be_digests(self):
        with pytest.raises(ValidationError, match="64-char hex"):
            result(input_hashes={"asset_counts": "v3"})

    def test_execution_block_carries_the_pinned_image_and_parameter_hash(self):
        with pytest.raises(ValidationError, match="64-char hex"):
            ToolExecution(container_image_digest="latest", tool_version="1.0.0", parameters_sha256=D64)
        pinned = ToolExecution(
            container_image_digest=DIGEST, tool_version="1.0.0", parameters_sha256=D64
        )
        assert pinned.container_image_digest.startswith("sha256:")

    def test_executed_version_must_match_the_claimed_version(self):
        with pytest.raises(ValidationError, match="must match"):
            result(execution=execution(tool_version="1.11.1"))

    def test_failed_checks_cannot_be_reported_as_success(self):
        with pytest.raises(ValidationError, match="failed checks"):
            result(validation_results=[ValidationOutcome(name="matrix_dimensions", result=ScreeningResult.FAIL)])
        assert result(
            status=ToolResultStatus.PARTIAL,
            validation_results=[ValidationOutcome(name="matrix_dimensions", result=ScreeningResult.WARN)],
        )

    def test_failures_are_classified_not_bare(self):
        with pytest.raises(ValidationError, match="classified error"):
            result(status=ToolResultStatus.FAILED, output_asset_ids=[])
        assert result(status=ToolResultStatus.FAILED, output_asset_ids=[], errors=[classified_error()])

    def test_success_declares_output_assets(self):
        with pytest.raises(ValidationError, match="output assets"):
            result(output_asset_ids=[])

    def test_numeric_metrics_only(self):
        with pytest.raises(ValidationError):
            result(metrics={"note": "looks fine"})

    def test_conclusion_bearing_results_need_reproduction_or_signature(self):
        with pytest.raises(ValidationError, match="registered reproduction"):
            result(used_for_conclusion=True)
        assert result(used_for_conclusion=True, human_signed_by="user_expert_01")
        assert result(used_for_conclusion=True, reproduced_by_run_id="run_prj_001_02")

    def test_reproducibility_level_is_reported_when_achieved(self):
        assert result().reproducibility_level_achieved is ReproducibilityLevel.NUMERIC


class TestAttestation:
    def test_registered_results_attest_cleanly(self):
        registry = ToolRegistry()
        registry.register(complete_spec())
        assert attest(result(), registry, now=NOW).tool_name == "scanpy_qc"

    def test_unknown_or_forbidden_tools_cannot_attest(self):
        registry = ToolRegistry()
        with pytest.raises(RegistryError, match="not registered"):
            attest(result(), registry, now=NOW)

    def test_adhoc_results_must_be_flagged_unregistered(self):
        registry = ToolRegistry()
        registry.grant(adhoc_spec(), approval())
        flagged = result(
            tool_name="local_marker_scan", tool_version="0.0.1", unregistered=True,
            execution=execution(tool_version="0.0.1"),
        )
        assert attest(flagged, registry, now=NOW).tool_name == "local_marker_scan"
        unflagged = flagged.model_copy(update={"unregistered": False})
        with pytest.raises(RegistryError, match="unregistered"):
            attest(unflagged, registry, now=NOW)

    def test_registered_tools_may_not_claim_adhoc_status(self):
        registry = ToolRegistry()
        registry.register(complete_spec())
        claimed = result(
            unregistered=True,
            execution=execution(),
        )
        with pytest.raises(RegistryError, match="must not claim unregistered"):
            attest(claimed, registry, now=NOW)

    def test_adhoc_attestation_respects_grant_scope_and_expiry(self):
        registry = ToolRegistry()
        registry.grant(adhoc_spec(), approval())
        flagged = result(
            tool_name="local_marker_scan", tool_version="0.0.1", unregistered=True,
            execution=execution(tool_version="0.0.1"),
        )
        other_project = flagged.model_copy(update={"project_id": "prj_002"})
        with pytest.raises(RegistryError, match="another project"):
            attest(other_project, registry, now=NOW)
        with pytest.raises(RegistryError, match="expired"):
            attest(flagged, registry, now=NOW + timedelta(days=30))

    def test_promoted_results_stop_being_adhoc(self):
        registry = ToolRegistry()
        registry.grant(adhoc_spec(resources=complete_spec().resources), approval())
        registry.promote(
            "local_marker_scan", "0.0.1",
            PromotionReview(
                reviewer_id="user_platform_admin_01", reviewed_at=NOW, reuse_count=2,
                checklist=PromotionChecklist(**{k: True for k in PromotionChecklist.model_fields}),
            ),
        )
        stale = result(
            tool_name="local_marker_scan", tool_version="0.0.1", unregistered=True,
            execution=execution(tool_version="0.0.1"),
        )
        with pytest.raises(RegistryError, match="must not claim unregistered"):
            attest(stale, registry, now=NOW)

    def test_minimal_specs_stay_documentation_incomplete(self):
        assert minimal_spec().admission_violations()
