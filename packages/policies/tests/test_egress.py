from datetime import date

import pytest
from pydantic import ValidationError

from bioadapt_policies.egress import (
    DENIED_ALWAYS,
    TIER_ALLOWLIST,
    EgressRequest,
    FieldCategory,
    OutgoingField,
    build_outbound_context,
    evaluate_egress,
)
from bioadapt_schemas.enums import PrivacyTier, RegulatoryProfile
from policy_factories import (
    CLOUD_ENDPOINT,
    LOCAL_HOSTS,
    OTHER_PROJECT,
    PURPOSE,
    PROJECT,
    TODAY,
    approval,
    cloud_context,
    field,
    request,
    safe_fields,
    terms,
)

ALLOWED = PrivacyTier.PUBLIC


def evaluate(req: EgressRequest, **overrides):
    context = {
        "today": TODAY,
        "local_hosts": LOCAL_HOSTS,
        "cloud_allowlist": {CLOUD_ENDPOINT},
    }
    context.update(overrides)
    return evaluate_egress(req, **context)


class TestAllowlist:
    def test_public_tier_passes_task_semantics_and_group_aggregates(self):
        decision = evaluate(request(tier=ALLOWED), provider_terms=terms())
        assert decision.allowed
        assert decision.allowed_paths == ["task.objective", "stats.group_sizes"]
        assert decision.denials == []

    def test_every_denied_category_is_recorded_per_field(self):
        units = {FieldCategory.CELL_LEVEL: "cell", FieldCategory.DONOR_LEVEL: "donor"}
        fields = [
            field(f"payload.{c.value}", c, unit=units.get(c, "group"))
            for c in sorted(DENIED_ALWAYS, key=str)
        ]
        decision = evaluate(request(tier=ALLOWED, fields=fields), provider_terms=terms())
        assert not decision.allowed
        assert len(decision.denials) == len(fields)
        assert decision.allowed_paths == []
        assert all("never leaves the data plane" in d.rule for d in decision.denials)

    def test_local_only_tiers_have_an_empty_allowlist(self):
        assert TIER_ALLOWLIST[PrivacyTier.STRICT_LOCAL] == frozenset()
        assert TIER_ALLOWLIST[PrivacyTier.HGR_CONTROLLED] == frozenset()

    def test_cell_level_content_cannot_be_relabelled_as_group_level(self):
        with pytest.raises(ValidationError, match="cannot be declared as group-level"):
            field("counts", FieldCategory.CELL_LEVEL, unit="group")

    def test_subgroup_statistics_are_denied_even_at_public_tier(self):
        fields = [
            field("stats.donor_means", FieldCategory.AGGREGATE_STATISTICS, unit="donor"),
            field("stats.per_cell", FieldCategory.AGGREGATE_STATISTICS, unit="cell"),
        ]
        decision = evaluate(request(tier=ALLOWED, fields=fields), provider_terms=terms())
        assert not decision.allowed
        assert [d.rule for d in decision.denials] == [
            "donor-level statistics may not be exported",
            "cell-level statistics may not be exported",
        ]

    def test_literature_snippets_need_a_cleared_licence(self):
        fields = [field("kb.abstract", FieldCategory.PUBLIC_KNOWLEDGE)]
        assert evaluate(request(tier=ALLOWED, fields=fields), provider_terms=terms()).allowed
        blocked = evaluate(
            request(tier=ALLOWED, fields=fields), provider_terms=terms(), license_cleared=False
        )
        assert not blocked.allowed
        assert blocked.denials[0].rule == "literature licence class not cleared"


class TestApprovals:
    def test_restricted_tier_always_needs_an_approval(self):
        decision = evaluate(request(), provider_terms=terms())
        assert decision.requires_approval
        assert not decision.allowed
        assert "restricted-tier egress needs a field-scoped approval for this purpose" in decision.blocked_reasons

    def test_matching_approval_opens_the_whitelisted_fields(self):
        decision = evaluate(request(), **cloud_context())
        assert decision.allowed
        assert decision.requires_approval

    def test_approval_is_scoped_to_project_and_purpose(self):
        for wrong in (
            approval(project_id=OTHER_PROJECT),
            approval(purpose="model benchmarking"),
            approval(expires_on=date(2026, 4, 30)),
        ):
            decision = evaluate(request(), **cloud_context(approval=wrong))
            assert not decision.allowed, wrong

    def test_restricted_tier_without_fields_does_not_claim_an_approval_gap(self):
        decision = evaluate(request(fields=[]), provider_terms=terms())
        assert not decision.allowed
        assert decision.blocked_reasons == ["no field passed the allowlist"]


class TestEndpointsAndTerms:
    def test_destination_outside_the_project_allowlist_is_blocked(self):
        decision = evaluate(request(destination="api.provider-b.example.com:443"), **cloud_context())
        assert not decision.allowed
        assert any("not in the project cloud allowlist" in r for r in decision.blocked_reasons)

    def test_cloud_call_without_verified_terms_is_blocked(self):
        decision = evaluate(request(), today=TODAY, local_hosts=LOCAL_HOSTS, cloud_allowlist={CLOUD_ENDPOINT}, approval=approval())
        assert not decision.allowed
        assert "cloud endpoint requires verified provider terms (section 11.2)" in decision.blocked_reasons

    def test_terms_must_cover_the_exact_endpoint(self):
        decision = evaluate(request(), **cloud_context(provider_terms=terms(endpoint="api.provider-b.example.com:443")))
        assert not decision.allowed
        assert "verified provider terms cover a different endpoint" in decision.blocked_reasons

    def test_expired_terms_review_blocks_the_call(self):
        decision = evaluate(
            request(), **cloud_context(provider_terms=terms(reviewed_at=date(2019, 1, 1)))
        )
        assert not decision.allowed
        assert "provider terms review has expired" in decision.blocked_reasons

    def test_incomplete_provider_terms_cannot_be_constructed(self):
        with pytest.raises(ValidationError, match="provider terms incomplete"):
            terms(zero_retention=False)

    def test_strict_local_may_not_reach_external_endpoints(self):
        decision = evaluate(
            request(tier=PrivacyTier.STRICT_LOCAL), **cloud_context()
        )
        assert not decision.allowed
        assert "strict-local projects may not contact external endpoints" in decision.blocked_reasons

    def test_hgr_projects_are_blocked_everywhere(self):
        decision = evaluate(
            request(tier=PrivacyTier.HGR_CONTROLLED, destination="sc-node-01.internal:8443"),
            today=TODAY,
            local_hosts=LOCAL_HOSTS,
        )
        assert not decision.allowed
        assert any("human genetic resource" in r for r in decision.blocked_reasons)
        assert all(d.rule for d in decision.denials)

    def test_local_destination_still_respects_the_tier_allowlist(self):
        decision = evaluate(
            request(tier=PrivacyTier.STRICT_LOCAL, destination="sc-node-01.internal:8443"),
            today=TODAY,
            local_hosts=LOCAL_HOSTS,
        )
        assert not decision.allowed
        assert "strict-local" not in " ".join(decision.blocked_reasons)
        assert len(decision.denials) == len(safe_fields())


class TestOutboundAssembly:
    def test_context_is_built_from_the_whitelist_not_by_filtering_a_rich_payload(self):
        decision = evaluate(request(tier=ALLOWED), provider_terms=terms())
        payload = {
            "task": {"objective": "find rare cell type"},
            "stats": {"group_sizes": {"g1": 120}},
            "donor_id": "donor_07",
            "barcodes": ["AAACGG", "TTGGCA"],
        }
        assert build_outbound_context(payload, decision) == {
            "task.objective": "find rare cell type",
            "stats.group_sizes": {"g1": 120},
        }

    def test_projection_refuses_a_non_allowing_decision(self):
        decision = evaluate(request(), today=TODAY, local_hosts=LOCAL_HOSTS)
        with pytest.raises(ValueError, match="does not permit"):
            build_outbound_context({"task": {"objective": "x"}}, decision)

    def test_missing_approved_field_fails_the_assembly(self):
        decision = evaluate(request(tier=ALLOWED), provider_terms=terms())
        with pytest.raises(ValueError, match="missing from the outbound template"):
            build_outbound_context({"task": {"objective": "x"}}, decision)

    def test_decision_id_is_stable_and_records_the_denials(self):
        ok = evaluate(request(tier=ALLOWED), provider_terms=terms())
        same = evaluate(request(tier=ALLOWED, fields=list(reversed(safe_fields()))), provider_terms=terms())
        blocked = evaluate(request(tier=ALLOWED, fields=[field("x", FieldCategory.IDENTIFIERS)]), provider_terms=terms())
        assert ok.decision_id() == same.decision_id()
        assert ok.decision_id() != blocked.decision_id()

    def test_regulatory_profile_is_carried_by_the_request(self):
        req = request(tier=ALLOWED, regulatory_profile=[RegulatoryProfile.HGR])
        assert RegulatoryProfile.HGR in req.regulatory_profile

    def test_outgoing_field_paths_must_be_named(self):
        with pytest.raises(ValidationError, match="at least 1 character"):
            EgressRequest(
                project_id=PROJECT,
                privacy_tier=ALLOWED,
                purpose=PURPOSE,
                destination=CLOUD_ENDPOINT,
                fields=[OutgoingField(path="", category=FieldCategory.TASK_SEMANTICS)],
            )
