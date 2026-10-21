from datetime import timedelta

import pytest
from pydantic import ValidationError

from bioadapt_schemas.enums import LicenseReviewStatus, ToolClass
from bioadapt_tool_registry import (
    AdhocExhaustedError,
    AdhocSandboxLimits,
    AdmissionError,
    ForbiddenToolError,
    NetworkPolicy,
    PromotionChecklist,
    PromotionReview,
    ResourceProfile,
    Revocation,
    ToolRegistry,
    UnknownToolError,
    minimal_spec,
)
from bioadapt_tool_registry.enums import FailurePolicy, NetworkMode
from registry_factories import NOW, adhoc_spec, approval, complete_spec, tool_grant


@pytest.fixture
def registry():
    return ToolRegistry()


class TestRegistration:
    def test_a_documented_tool_is_admitted(self, registry):
        spec = registry.register(complete_spec())
        assert registry.resolve("scanpy_qc", "1.11.0", now=NOW) is spec

    def test_undocumented_tools_are_refused_with_the_missing_items(self, registry):
        with pytest.raises(AdmissionError, match="assumptions are not documented"):
            registry.register(minimal_spec())

    def test_adhoc_tools_do_not_enter_through_register(self, registry):
        with pytest.raises(AdmissionError, match="grant\\(\\)"):
            registry.register(adhoc_spec())

    def test_the_same_name_and_version_cannot_be_registered_twice(self, registry):
        registry.register(complete_spec())
        with pytest.raises(AdmissionError, match="already registered"):
            registry.register(complete_spec())

    def test_resolving_an_unknown_tool_is_explicit(self, registry):
        with pytest.raises(UnknownToolError, match="not registered"):
            registry.resolve("nope", "1.0.0", now=NOW)


class TestAdhocChannel:
    def test_a_granted_tool_runs_once_within_its_project_and_window(self, registry):
        spec = registry.grant(adhoc_spec(), approval())
        assert registry.consume("local_marker_scan", "0.0.1", now=NOW, project_id="prj_001") is spec
        with pytest.raises(AdhocExhaustedError, match="already used"):
            registry.consume("local_marker_scan", "0.0.1", now=NOW, project_id="prj_001")

    def test_grants_do_not_leak_across_projects(self, registry):
        registry.grant(adhoc_spec(), approval())
        with pytest.raises(AdhocExhaustedError, match="belongs to"):
            registry.consume("local_marker_scan", "0.0.1", now=NOW, project_id="prj_002")

    def test_expired_grants_are_refused(self, registry):
        registry.grant(adhoc_spec(), approval())
        later = NOW + timedelta(days=30)
        with pytest.raises(AdhocExhaustedError, match="expired"):
            registry.consume("local_marker_scan", "0.0.1", now=later, project_id="prj_001")

    def test_approval_is_one_time_and_needs_a_reason(self):
        with pytest.raises(ValidationError, match="max_uses"):
            approval(max_uses=5)
        with pytest.raises(ValidationError):
            approval(reason="")
        with pytest.raises(ValidationError, match="positive"):
            approval(expires_at=NOW)

    def test_the_subject_that_submitted_a_tool_cannot_approve_it(self):
        with pytest.raises(ValidationError, match="other than the subject that submitted"):
            approval(requested_by="user_lead_01")

    def test_an_approval_that_names_no_gate_is_incomplete(self):
        with pytest.raises(ValidationError):
            approval(gate_approval_id="")

    def test_a_stored_grant_hydrates_the_registry_with_its_provenance_intact(self, registry):
        stored = tool_grant()
        spec = registry.admit_grant(stored)
        assert spec.tool_name == "local_marker_scan"
        assert spec is registry.resolve(spec.tool_name, spec.tool_version, now=NOW)
        back = registry.grant_of("local_marker_scan", "0.0.1")
        assert (back.approver_id, back.requested_by, back.gate_approval_id) == (
            stored.approver_id,
            stored.requested_by,
            stored.approval_id,
        )
        registry.consume("local_marker_scan", "0.0.1", now=NOW, project_id=stored.project_id)
        with pytest.raises(AdhocExhaustedError, match="already used"):
            registry.check_adhoc("local_marker_scan", "0.0.1", now=NOW, project_id=stored.project_id)

    def test_a_hydrated_registry_re_runs_the_cage_over_the_stored_bytes(self, registry):
        loose = adhoc_spec(resources=ResourceProfile(cpu=64, memory_mb=8192, max_minutes=60))
        with pytest.raises(AdmissionError, match="ad-hoc sandbox violated"):
            registry.admit_grant(tool_grant(spec=loose.model_dump(mode="json")))

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"resources": ResourceProfile(cpu=64, memory_mb=8192, max_minutes=60)},
            {"resources": ResourceProfile(cpu=2, memory_mb=262144, max_minutes=60)},
            {"resources": ResourceProfile(cpu=2, memory_mb=8192, max_minutes=6000)},
            {"resources": ResourceProfile(cpu=2, memory_mb=8192, max_minutes=60, gpu=1)},
            {"network": NetworkPolicy(mode=NetworkMode.ALLOWLIST, allowed_hosts=["refs.internal"])},
            {"retry_limit": 2},
            {"failure_policy": FailurePolicy.RETRY},
        ],
    )
    def test_sandbox_limits_are_enforced_at_grant_time(self, registry, kwargs):
        with pytest.raises(AdmissionError, match="ad-hoc sandbox violated"):
            registry.grant(adhoc_spec(**kwargs), approval())

    def test_quotas_are_configurable(self, registry):
        tight = ToolRegistry(adhoc_limits=AdhocSandboxLimits(max_cpu=1, max_memory_mb=1024, max_minutes=5))
        with pytest.raises(AdmissionError, match="cpu"):
            tight.grant(adhoc_spec(), approval())

    def test_rejected_licences_never_run(self, registry):
        rejected = adhoc_spec(
            license=adhoc_spec().license.model_copy(update={"review_status": LicenseReviewStatus.REJECTED})
        )
        with pytest.raises(AdmissionError, match="rejected licence"):
            registry.grant(rejected, approval())

    def test_adhoc_tools_are_invisible_unless_explicitly_included(self, registry):
        registry.grant(adhoc_spec(), approval())
        assert registry.select(task="marker_scan") == []
        assert len(registry.select(task="marker_scan", include_adhoc=True, now=NOW)) == 1
        assert registry.select(task="marker_scan", include_adhoc=True, now=NOW + timedelta(days=30)) == []


class TestPromotion:
    def review(self, **overrides):
        fields = {
            "reviewer_id": "user_platform_admin_01",
            "reviewed_at": NOW,
            "reuse_count": 2,
            "checklist": PromotionChecklist(
                assumptions_documented=True,
                failure_modes_documented=True,
                resources_measured=True,
                licence_reviewed=True,
                unit_tests_present=True,
                benchmark_case_present=True,
                output_contract_present=True,
            ),
        }
        fields.update(overrides)
        return PromotionReview(**fields)

    def test_promotion_requires_a_complete_checklist(self):
        with pytest.raises(ValidationError, match="checklist incomplete"):
            self.review(checklist=PromotionChecklist(assumptions_documented=True))

    def test_promotion_needs_reuse_or_a_conclusion_claim(self):
        with pytest.raises(ValidationError, match="reuses"):
            self.review(reuse_count=1)

    def test_adhoc_becomes_registered_after_review(self, registry):
        registry.grant(adhoc_spec(resources=ResourceProfile(
            cpu=2, memory_mb=8192, max_minutes=60, measured_peak_memory_mb=7000, measured_on="run_prj_001_01"
        )), approval())
        promoted = registry.promote("local_marker_scan", "0.0.1", self.review())
        assert promoted.tool_class is ToolClass.REGISTERED
        assert registry.grant_of("local_marker_scan", "0.0.1") is None
        assert registry.review_of("local_marker_scan", "0.0.1").reuse_count == 2
        with pytest.raises(UnknownToolError, match="not an ad-hoc tool"):
            registry.promote("local_marker_scan", "0.0.1", self.review())

    def test_promotion_still_needs_full_admission_evidence(self, registry):
        registry.grant(adhoc_spec(assumptions=[], invalid_when=[]), approval())
        with pytest.raises(AdmissionError, match="assumptions are not documented"):
            registry.promote("local_marker_scan", "0.0.1", self.review())


class TestRevocation:
    def revoke(self, registry):
        registry.forbid(
            Revocation(
                tool_name="scanpy_qc",
                tool_version="1.11.0",
                reason="image digest compromised",
                revoked_by="user_platform_admin_01",
                revoked_at=NOW,
            )
        )

    def test_forbidden_tools_cannot_be_resolved_or_reregistered(self, registry):
        registry.register(complete_spec())
        self.revoke(registry)
        with pytest.raises(ForbiddenToolError, match="compromised"):
            registry.resolve("scanpy_qc", "1.11.0", now=NOW)
        with pytest.raises(AdmissionError, match="forbidden"):
            registry.register(complete_spec())

    def test_lifting_a_revocation_allows_a_re_review(self, registry):
        registry.register(complete_spec())
        self.revoke(registry)
        registry.lift_forbidden("scanpy_qc", "1.11.0")
        assert registry.register(complete_spec())

    def test_forbidden_tools_drop_out_of_selection(self, registry):
        registry.register(complete_spec())
        registry.register(complete_spec(tool_name="scanpy_cluster", task="cluster"))
        self.revoke(registry)
        assert [s.tool_name for s in registry.select()] == ["scanpy_cluster"]


class TestSnapshot:
    def test_snapshot_id_is_stable_for_identical_registries(self):
        first, second = ToolRegistry(), ToolRegistry()
        for registry in (first, second):
            registry.register(complete_spec())
            registry.grant(adhoc_spec(), approval())
        assert first.snapshot_id() == second.snapshot_id()

    def test_snapshot_id_changes_with_content(self):
        base = ToolRegistry()
        base.register(complete_spec())
        changed = ToolRegistry()
        changed.register(complete_spec(tool_version="1.11.1"))
        assert base.snapshot_id() != changed.snapshot_id()

        revoked = ToolRegistry()
        revoked.register(complete_spec())
        revoked.forbid(
            Revocation(
                tool_name="scanpy_qc", tool_version="1.11.0", reason="recall",
                revoked_by="user_platform_admin_01", revoked_at=NOW,
            )
        )
        assert revoked.snapshot_id() != base.snapshot_id()
