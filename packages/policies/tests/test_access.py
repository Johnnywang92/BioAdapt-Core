import pytest
from pydantic import ValidationError

from bioadapt_policies.access import (
    DEFAULT_PAGE_SIZE,
    GLOBAL_SCOPE,
    MAX_PAGE_SIZE,
    MAX_PRESIGNED_TTL_SECONDS,
    Action,
    Actor,
    Resource,
    Role,
    authorize,
    page_size,
    presigned_url_ttl,
    role_conflicts,
)
from bioadapt_schemas.enums import PrivacyTier, UsageScope
from policy_factories import OTHER_PROJECT, PROJECT, actor, asset, resource, service_actor

EXPERT = dict(subject_id="user_expert_07", roles=[Role.DOMAIN_EXPERT])
COMPLIANCE = dict(subject_id="user_compliance_01", roles=[Role.COMPLIANCE_OFFICER], mfa_verified=True)
PI = dict(subject_id="user_pi_01", roles=[Role.PRINCIPAL_INVESTIGATOR])
GATE = dict(resource_id="apr_0001", resource_type="approval")


class TestDefaultDeny:
    def test_an_action_that_no_role_grants_is_denied(self):
        decision = authorize(actor(), Action.CARD_SIGN_OFF, resource(resource_id="card_01", resource_type="card"))
        assert not decision.allowed
        assert "no role of ['analyst'] grants card:sign_off" in decision.denials

    def test_a_domain_expert_may_sign_off_within_their_project(self):
        decision = authorize(actor(**EXPERT), Action.CARD_SIGN_OFF, resource(resource_id="card_01", resource_type="card"))
        assert decision.allowed
        assert decision.denials == []

    def test_service_accounts_may_never_reach_approval_or_permission_writes(self):
        for action, res in (
            (Action.EGRESS_APPROVE, resource(resource_id="egr_0001", resource_type="egress_approval")),
            (Action.TOOL_GRANT_ADHOC, resource(resource_id="adhoc_01", resource_type="tool_grant")),
            (Action.POLICY_WRITE, resource(resource_id="pol_01", resource_type="policy")),
            (Action.TRUTH_FREEZE, resource(resource_id="eval_01", resource_type="evaluation")),
        ):
            svc = service_actor()
            decision = authorize(svc, action, res)
            assert not decision.allowed, action
            assert any("must be performed by a human" in d for d in decision.denials), action

    def test_a_service_agent_can_write_results(self):
        assert authorize(service_actor(), Action.ASSET_WRITE, asset()).allowed


class TestProjectScope:
    def test_actions_outside_the_actor_project_scope_are_denied(self):
        decision = authorize(actor(), Action.RUN_LAUNCH, resource(project_id=OTHER_PROJECT))
        assert not decision.allowed
        assert "run:launch outside the actor project scope" in decision.denials

    def test_platform_admin_holding_the_global_scope_marker_reaches_any_project(self):
        admin = actor(subject_id="user_platform_admin_01", roles=[Role.PLATFORM_ADMIN], project_ids=[GLOBAL_SCOPE])
        assert authorize(admin, Action.PROJECT_READ, resource(project_id=OTHER_PROJECT)).allowed

    def test_cross_project_asset_read_needs_a_reusable_usage_scope(self):
        shared = asset(project_id=OTHER_PROJECT, usage_scope=UsageScope.CROSS_PROJECT_ALLOWED)
        assert authorize(actor(), Action.ASSET_READ, shared).allowed
        locked = asset(project_id=OTHER_PROJECT, usage_scope=UsageScope.THIS_PROJECT_ONLY)
        decision = authorize(actor(), Action.ASSET_READ, locked)
        assert not decision.allowed
        assert "this_project_only and may not be read here" in decision.denials[1]


class TestSeparationOfDuties:
    def test_egress_requester_cannot_approve_their_own_request(self):
        both = actor(
            subject_id="user_pi_01",
            roles=[Role.PLATFORM_ADMIN, Role.COMPLIANCE_OFFICER],
            project_ids=[GLOBAL_SCOPE],
            mfa_verified=True,
        )
        decision = authorize(
            both,
            Action.EGRESS_APPROVE,
            resource(resource_id="egr_0001", resource_type="egress_approval"),
            requesting_subject_id=both.subject_id,
        )
        assert not decision.allowed
        assert "egress requester and approver must be different subjects" in decision.denials

    def test_another_compliance_officer_with_a_second_factor_can_approve(self):
        decision = authorize(
            actor(**COMPLIANCE),
            Action.EGRESS_APPROVE,
            resource(resource_id="egr_0001", resource_type="egress_approval"),
            requesting_subject_id="user_pi_01",
        )
        assert decision.allowed

    def test_approval_without_the_requesting_subject_is_incomplete(self):
        decision = authorize(
            actor(**COMPLIANCE),
            Action.EGRESS_APPROVE,
            resource(resource_id="egr_0001", resource_type="egress_approval"),
        )
        assert not decision.allowed
        assert "needs the requesting subject" in decision.denials[0]

    def test_the_run_author_cannot_sign_off_their_own_conclusion(self):
        card = resource(resource_id="card_01", resource_type="card", attributes={"authored_by": "user_expert_07"})
        decision = authorize(actor(**EXPERT), Action.CARD_SIGN_OFF, card)
        assert not decision.allowed
        assert "may not sign off its own conclusion" in decision.denials[0]

    def test_conflicting_roles_cannot_be_held_at_all(self):
        with pytest.raises(ValidationError, match="may not be held together"):
            Actor(subject_id="user_01", roles=[Role.EVALUATION_LEAD, Role.ANALYST], project_ids=[PROJECT])
        assert role_conflicts({Role.COMPLIANCE_OFFICER, Role.PRINCIPAL_INVESTIGATOR})

    def test_a_service_account_may_only_hold_the_service_role(self):
        with pytest.raises(ValidationError, match="service_agent"):
            Actor(
                subject_id="svc_orchestrator",
                roles=[Role.SERVICE_AGENT, Role.ANALYST],
                project_ids=[PROJECT],
                is_service_account=True,
            )


class TestRunGates:
    """Section 9.1 and 9.2: reporting what happened to a run is the engine's job; opening a gate is not."""

    def test_the_engine_reports_transitions_but_never_decides_a_gate(self):
        svc = service_actor()
        assert authorize(svc, Action.RUN_UPDATE, resource()).allowed
        for action in (Action.APPROVAL_DECIDE, Action.APPROVAL_DELEGATE):
            decision = authorize(svc, action, resource(**GATE))
            assert not decision.allowed, action
            assert any("must be performed by a human" in d for d in decision.denials), action

    def test_an_analyst_may_ask_for_a_gate_but_not_open_it(self):
        assert authorize(actor(), Action.APPROVAL_REQUEST, resource(**GATE)).allowed
        decision = authorize(
            actor(), Action.APPROVAL_DECIDE, resource(**GATE), requesting_subject_id="user_expert_02"
        )
        assert not decision.allowed
        assert "no role of ['analyst'] grants approval:decide" in decision.denials

    def test_a_pi_who_asked_for_the_gate_may_not_be_the_one_opening_it(self):
        pi = actor(**PI)
        assert authorize(pi, Action.APPROVAL_DECIDE, resource(**GATE), requesting_subject_id="user_analyst_01").allowed
        decision = authorize(pi, Action.APPROVAL_DECIDE, resource(**GATE), requesting_subject_id=pi.subject_id)
        assert not decision.allowed
        assert "the subject that requested a gate may not open it" in decision.denials[0]

    def test_deciding_a_gate_without_naming_the_requester_is_incomplete(self):
        decision = authorize(actor(**PI), Action.APPROVAL_DECIDE, resource(**GATE))
        assert not decision.allowed
        assert "needs the subject that asked for the gate" in decision.denials[0]

    def test_a_gate_is_accountable_to_one_person_who_may_delegate_it(self):
        pi = actor(**PI)
        assert authorize(pi, Action.APPROVAL_DELEGATE, resource(**GATE)).allowed
        assert authorize(actor(**EXPERT), Action.APPROVAL_DELEGATE, resource(**GATE)).allowed is False

    def test_a_person_reviewing_the_deliverable_may_report_its_final_state(self):
        assert authorize(actor(**EXPERT), Action.RUN_UPDATE, resource()).allowed


class TestSecondFactorAndTier:
    @pytest.mark.parametrize(
        "action", [Action.ROLE_GRANT, Action.TOOL_PROMOTE, Action.ASSET_DELETE, Action.TRUTH_FREEZE]
    )
    def test_privileged_actions_require_mfa(self, action):
        without = actor(subject_id="user_admin_01", roles=[Role.PLATFORM_ADMIN], project_ids=[GLOBAL_SCOPE])
        decision = authorize(without, action, resource(resource_id="x_01", resource_type="x"))
        assert not decision.allowed
        assert f"{action} requires a second factor" in decision.denials

    def test_exporting_from_a_local_only_tier_is_denied(self):
        for tier in (PrivacyTier.STRICT_LOCAL, PrivacyTier.HGR_CONTROLLED):
            decision = authorize(
                actor(subject_id="user_pi_01", roles=[Role.PRINCIPAL_INVESTIGATOR]),
                Action.ASSET_EXPORT,
                asset(project_id=PROJECT, privacy_tier=tier),
            )
            assert not decision.allowed, tier
            assert any("keeps all content inside the data plane" in d for d in decision.denials), tier

    def test_exporting_restricted_data_is_allowed_to_the_pi(self):
        decision = authorize(
            actor(subject_id="user_pi_01", roles=[Role.PRINCIPAL_INVESTIGATOR]),
            Action.ASSET_EXPORT,
            asset(project_id=PROJECT, privacy_tier=PrivacyTier.RESTRICTED),
        )
        assert decision.allowed


class TestAuditAndLimits:
    def test_decision_carries_the_subject_action_and_reasons_for_the_audit_log(self):
        decision = authorize(actor(), Action.CARD_SIGN_OFF, resource(resource_id="card_01", resource_type="card"))
        assert decision.subject_id == "user_analyst_01"
        assert decision.action is Action.CARD_SIGN_OFF
        assert decision.project_id == PROJECT
        assert decision.policy_version == "access_v1"
        assert decision.decision_id() == decision.model_copy(update={"denials": list(reversed(decision.denials))}).decision_id()

    def test_signed_urls_are_capped_at_ten_minutes(self):
        assert presigned_url_ttl(3600) == MAX_PRESIGNED_TTL_SECONDS
        assert presigned_url_ttl(120) == 120
        with pytest.raises(ValueError, match="positive"):
            presigned_url_ttl(0)

    def test_list_endpoints_are_paged_with_a_hard_cap(self):
        assert page_size() == DEFAULT_PAGE_SIZE
        assert page_size(5_000) == MAX_PAGE_SIZE
        with pytest.raises(ValueError, match="positive"):
            page_size(-1)

    def test_unknown_resource_ids_still_produce_a_recordable_decision(self):
        decision = authorize(actor(), Action.PROJECT_READ, Resource(resource_id="prj_x", resource_type="project", project_id="prj_x"))
        assert not decision.allowed
        assert "outside the actor project scope" in decision.denials[0]
