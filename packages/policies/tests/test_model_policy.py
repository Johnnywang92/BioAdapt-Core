import pytest
from pydantic import ValidationError

from bioadapt_policies.egress import evaluate_egress
from bioadapt_policies.model_policy import (
    EXTRACTION_AGREEMENT_KAPPA_MIN,
    DegradedPath,
    ModelClass,
    ModelRoute,
    ModelTask,
    context_violations,
    route_model_request,
)
from bioadapt_schemas.enums import PrivacyTier, RegulatoryProfile
from bioadapt_schemas.profile import MissingInformation, ProjectProfile, RetainedGroup
from policy_factories import CLOUD_ENDPOINT, PROJECT, cloud_context, request

LOCAL_ROUTE = dict(model_id="local-7b", model_class=ModelClass.LOCAL, endpoint="sc-node-01.internal:8443")


def profile(**overrides) -> ProjectProfile:
    fields = {
        "project_id": PROJECT,
        "species": "human",
        "tissues": ["peripheral blood"],
        "modalities": ["scrna-seq"],
        "objective": "find rare immune states",
        "specimen_count": 6,
        "donor_count": 4,
        "summary_schema_version": "1",
    }
    fields.update(overrides)
    return ProjectProfile(**fields)


def passing_egress(**overrides):
    return evaluate_egress(request(**overrides), **cloud_context())


def route_to(class_: ModelClass, endpoint: str) -> ModelRoute:
    return ModelRoute(model_id=f"{class_}-model", model_class=class_, endpoint=endpoint)


def decide(**overrides):
    fields = {
        "project_id": PROJECT,
        "privacy_tier": PrivacyTier.RESTRICTED,
        "task": ModelTask.RETRIEVAL_RERANK,
        "route": route_to(ModelClass.CLOUD_STANDARD, CLOUD_ENDPOINT),
        "egress": passing_egress(),
        "benchmark_passed": True,
    }
    fields.update(overrides)
    return route_model_request(**fields)


class TestRouting:
    def test_local_route_needs_no_egress_decision(self):
        decision = decide(
            privacy_tier=PrivacyTier.STRICT_LOCAL,
            route=ModelRoute(**LOCAL_ROUTE),
            egress=None,
        )
        assert decision.allowed
        assert decision.project_id == PROJECT

    def test_cloud_call_on_a_strict_local_project_is_blocked(self):
        decision = decide(
            privacy_tier=PrivacyTier.STRICT_LOCAL, egress=None, route=route_to(ModelClass.CLOUD_FLAGSHIP, CLOUD_ENDPOINT)
        )
        assert not decision.allowed
        assert "tier strict_local forbids cloud model calls" in decision.reasons
        assert decision.error_class.value == "policy_blocked"
        assert decision.must_not_write_evidence

    def test_hgr_profile_blocks_cloud_even_when_the_tier_looks_permissive(self):
        decision = decide(
            privacy_tier=PrivacyTier.PUBLIC,
            regulatory_profile=[RegulatoryProfile.HGR],
            route=route_to(ModelClass.CLOUD_STANDARD, CLOUD_ENDPOINT),
        )
        assert not decision.allowed
        assert any("legal assessment" in r for r in decision.reasons)

    def test_cloud_call_without_a_passing_egress_decision_is_blocked(self):
        blocked = passing_egress(tier=PrivacyTier.STRICT_LOCAL)
        decision = decide(egress=blocked, privacy_tier=PrivacyTier.PUBLIC)
        assert not decision.allowed
        assert "cloud call requires a passing field-level egress decision" in decision.reasons

    def test_unbenchmarked_model_is_blocked(self):
        decision = decide(benchmark_passed=False)
        assert not decision.allowed
        assert "stratified regression subset" in decision.reasons[0]

    def test_non_model_path_always_passes(self):
        decision = decide(route=ModelRoute(model_id="rule_engine", model_class=ModelClass.NONE, endpoint=""))
        assert decision.allowed
        assert decision.reasons == ["non-model path"]


class TestExtractionAgreement:
    def test_extraction_needs_a_measured_agreement(self):
        without = decide(task=ModelTask.CLAIM_EXTRACTION, agreement_kappa=None)
        below = decide(
            task=ModelTask.CLAIM_EXTRACTION,
            agreement_kappa=EXTRACTION_AGREEMENT_KAPPA_MIN - 0.05,
        )
        at_floor = decide(task=ModelTask.CLAIM_EXTRACTION, agreement_kappa=EXTRACTION_AGREEMENT_KAPPA_MIN)
        assert not without.allowed and not below.allowed
        assert "no measured agreement" in without.reasons[0]
        assert f"below {EXTRACTION_AGREEMENT_KAPPA_MIN}" in below.reasons[0]
        assert at_floor.allowed

    def test_agreement_is_not_required_for_writing_or_ranking_tasks(self):
        assert decide(task=ModelTask.REPORT_WRITING, agreement_kappa=None).allowed


class TestBudget:
    def test_exhausted_budget_degrades_instead_of_failing_silently(self):
        decision = decide(budget_usd=12.0, spent_usd=11.5, estimate_usd=1.0)
        assert not decision.allowed
        assert decision.error_class.value == "budget_exceeded"
        assert decision.degraded_to is DegradedPath.DETERMINISTIC_ONLY
        assert any("token budget exhausted" in r for r in decision.reasons)

    def test_extraction_tasks_degrade_to_the_human_template(self):
        decision = decide(
            task=ModelTask.CLAIM_EXTRACTION,
            agreement_kappa=0.9,
            budget_usd=1.0,
            spent_usd=1.0,
            estimate_usd=0.5,
        )
        assert decision.degraded_to is DegradedPath.HUMAN_TEMPLATE

    def test_no_budget_set_means_no_cost_gate(self):
        assert decide(budget_usd=None, spent_usd=9_999.0).allowed


class TestContextCompression:
    def test_rare_group_dropped_by_the_summary_is_a_violation(self):
        rare = RetainedGroup(display_name="rare_dc3", size=400, share=0.004, donor_count=3)
        assert context_violations(profile(groups=[]), [rare]) == [
            "rare group rare_dc3 dropped by the summary"
        ]

    def test_retained_group_present_in_the_summary_passes(self):
        rare = RetainedGroup(display_name="rare_dc3", size=400, share=0.004, donor_count=3)
        assert context_violations(profile(groups=[rare]), [rare]) == []

    def test_blocking_missing_information_is_reported(self):
        summary = profile(
            missing_information=[MissingInformation(code="pairing_method", question="如何配对？", blocking=True)]
        )
        assert context_violations(summary, []) == [
            "blocking information still missing from the profile: ['pairing_method']"
        ]

    def test_non_blocking_gaps_do_not_block_the_context(self):
        summary = profile(missing_information=[MissingInformation(code="age_band", question="年龄段？")])
        assert context_violations(summary, []) == []


class TestRouteContract:
    def test_route_requires_an_endpoint_and_a_class(self):
        with pytest.raises(ValidationError, match="model_class"):
            ModelRoute(model_id="x", endpoint=CLOUD_ENDPOINT)

    def test_unknown_model_class_is_rejected(self):
        with pytest.raises(ValidationError, match="model_class"):
            ModelRoute(model_id="x", model_class="gpt-omni", endpoint=CLOUD_ENDPOINT)
