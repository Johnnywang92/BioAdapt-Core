"""The outbound face of a drafted card (plan V2.0 sections 11.5, 11.6).

Section 11.5 says an outbound context is built from a whitelist instead of trimmed out of a rich payload,
so these tests read the card the way a reviewer would not: which of its fields may be shown at all, and
whether what is left still carries nothing about a cell, a donor or an object. The products below are typed
out as the JSON a node would store, which keeps the seam under test the projection rather than the reader.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping, Optional, Sequence

import pytest

import evidence_factories as E
from bioadapt_evidence.assets import SCHEMA_BY_SLOT
from bioadapt_evidence.cards import CardDraft, EvidenceCardService
from bioadapt_evidence.decision import EvidenceModelService
from bioadapt_evidence.errors import EgressBlocked, ModelOutputRejected
from bioadapt_evidence.outbound import (
    CARD_OUTBOUND,
    CardNarrative,
    card_model_request,
    card_outbound,
    generate_reviewer_memo,
)
from bioadapt_policies.egress import (
    DENIED_ALWAYS,
    OUTBOUND_UNITS,
    TIER_ALLOWLIST,
    EgressApproval,
    EgressRequest,
    OutgoingField,
    build_outbound_context,
    evaluate_egress,
)
from bioadapt_policies.model_policy import ModelClass, ModelRoute, ModelTask
from bioadapt_policies.providers import ProviderTerms
from bioadapt_schemas.enums import PrivacyTier
from bioadapt_schemas.identifiers import CELL_KEY_SEPARATOR
from bioadapt_storage.catalog import Catalog
from bioadapt_storage.objectstore import InMemoryObjectStore
from bioadapt_storage.traces import InMemoryTraceStore

FOCUS = "rare_state"
OTHER = "cd14_monocyte"
SIZES = {FOCUS: 60, OTHER: 4000}
PURPOSE = "describe_a_candidate_to_a_model"

SLOT_NODES = {
    "clusters": "clustering",
    "markers": "markers",
    "de_table": "pseudobulk_de",
    "stability": "robustness",
    "coverage": "coverage",
    "state_axis": "state_axis",
    "qc_flags": "qc",
}

#: Digests the reference payloads store beside their numbers. None of them identifies a person, but every
#: one is a content address of an internal object, so a projection that carried one would show that the
#: context was assembled by copying a payload rather than by reading an allowlist.
INTERNAL_DIGESTS = ("ab" * 32, "cd" * 32, "bc" * 32, "be" * 32, "em" * 32, "rf" * 32, "ex" * 32)


@dataclass
class Focus:
    """One drafted card, plus everything about its run that must not appear in the outbound template."""

    draft: CardDraft
    catalog: Catalog
    cells: list[str]
    specimens: list[str]
    asset_ids: list[str]
    fingerprints: list[str]

    def template(self, *, only: Sequence[str] = ()) -> dict[str, Any]:
        if only:
            return card_outbound(self.draft, only=only)[0]
        return card_outbound(self.draft)[0]

    def text(self) -> str:
        return json.dumps(self.template(), sort_keys=True, default=str)


def build(*, drop: Sequence[str] = (), tier: PrivacyTier = PrivacyTier.RESTRICTED) -> Focus:
    partition = E.partition_labels(*(E.labels_block(name, size) for name, size in SIZES.items()))
    cells = E.members_of(partition, FOCUS)
    products: dict[str, Mapping[str, Any]] = {
        "clusters": E.clusters_payload(partition),
        "markers": E.markers_payload([FOCUS, OTHER]),
        "de_table": E.de_payload(
            E.cluster_contrast(f"focus_vs_{OTHER}", FOCUS, OTHER), [E.de_table(FOCUS, OTHER)]
        ),
        "stability": E.stability_payload(FOCUS, baseline_cells=len(cells)),
        "coverage": E.coverage_payload(cells),
        "state_axis": E.state_axis_payload(cells),
        "qc_flags": E.qc_payload({cell: () for cell in cells}),
    }
    for slot in drop:
        products.pop(slot, None)
    objects = InMemoryObjectStore()
    catalog = E.catalog_with_run(objects, tier=tier)
    assets: dict[str, str] = {}
    for slot, payload in products.items():
        assets[slot] = E.write_product(
            catalog,
            objects,
            slot=slot,
            schema_version=SCHEMA_BY_SLOT[slot],
            payload=payload,
            node_id=SLOT_NODES[slot],
        )
    candidate = E.candidate_for(partition, FOCUS)
    service = EvidenceCardService(catalog=catalog, objects=objects, run_id=E.RUN_ID)
    return Focus(
        draft=service.draft(candidate),
        catalog=catalog,
        cells=cells,
        specimens=list(E.DISCOVERY_SPECIMENS),
        asset_ids=sorted(assets.values()),
        fingerprints=[candidate.member_fingerprint],
    )


@pytest.fixture(scope="module")
def focus() -> Focus:
    made = build()
    yield made
    made.catalog.close()


@pytest.fixture(scope="module")
def uncovered() -> Focus:
    """A card the coverage node never answered for, which still has to project onto every allowed path."""
    made = build(drop=("coverage",))
    yield made
    made.catalog.close()


@pytest.fixture(scope="module")
def local_only() -> Focus:
    """The same card filed at a tier that keeps every field inside the data plane."""
    made = build(tier=PrivacyTier.STRICT_LOCAL)
    yield made
    made.catalog.close()


@pytest.fixture
def single() -> Focus:
    """A run of its own, for a test that reads back the record one model call left behind."""
    made = build()
    yield made
    made.catalog.close()


def request(
    fields: Sequence[OutgoingField], tier: PrivacyTier, *, destination: str = E.LOCAL_ENDPOINT
) -> EgressRequest:
    return EgressRequest(
        project_id=E.PROJECT_ID,
        privacy_tier=tier,
        purpose=PURPOSE,
        destination=destination,
        fields=list(fields),
    )


def approval(*, purpose: str = PURPOSE) -> EgressApproval:
    return EgressApproval(
        approval_id="appr_outbound_01",
        approver_id=E.PI,
        project_id=E.PROJECT_ID,
        purpose=purpose,
        approved_on=E.TODAY - timedelta(days=1),
        expires_on=E.TODAY + timedelta(days=30),
    )


class TestAllowlist:
    def test_the_face_is_the_allowlist_itself(self, focus: Focus) -> None:
        assert sorted(focus.template()) == sorted(CARD_OUTBOUND)

    def test_every_field_could_appear_in_a_public_template(self) -> None:
        """A field the tier table can never allow would make the projection unsendable by construction."""
        for field_name, declared in CARD_OUTBOUND.items():
            assert declared.path == field_name
            assert declared.category not in DENIED_ALWAYS
            assert declared.category in TIER_ALLOWLIST[PrivacyTier.PUBLIC]
            assert declared.unit in OUTBOUND_UNITS

    def test_narrowing_is_allowed_and_exact(self, focus: Focus) -> None:
        template, fields = card_outbound(focus.draft, only=["candidate", "ceiling"])
        assert sorted(template) == ["candidate", "ceiling"]
        assert [declared.path for declared in fields] == ["candidate", "ceiling"]

    def test_a_call_site_cannot_add_a_field(self, focus: Focus) -> None:
        with pytest.raises(EgressBlocked) as raised:
            card_outbound(focus.draft, only=["candidate", "member_fingerprint"])
        assert "cannot add to it" in raised.value.message
        assert "§11.5" in raised.value.message


class TestNothingPersonalLeaves:
    def test_no_cell_key_or_specimen_identifier(self, focus: Focus) -> None:
        text = focus.text()
        assert CELL_KEY_SEPARATOR not in text
        for cell in focus.cells[:5]:
            assert cell not in text
        for specimen in focus.specimens:
            assert specimen not in text

    def test_no_object_identifier_or_digest(self, focus: Focus) -> None:
        text = focus.text()
        for asset_id in focus.asset_ids:
            assert asset_id not in text
        for digest in INTERNAL_DIGESTS:
            assert digest not in text
        for fingerprint in focus.fingerprints:
            assert fingerprint not in text

    def test_composition_is_counts_not_membership(self, focus: Focus) -> None:
        assert sorted(focus.template()["composition"]) == [
            "cluster_method",
            "cluster_resolution",
            "donor_count",
            "partition_cells",
            "rare",
            "rare_rule_reasons",
            "share",
            "size",
            "thresholds_version",
        ]

    def test_marker_rank_travels_with_its_caveat(self, focus: Focus) -> None:
        markers = focus.template()["markers"]
        assert markers["rank_only"] is True
        assert isinstance(markers["genes"], list) and markers["genes"]

    def test_the_drafting_path_never_claims_a_hypothesis(self, focus: Focus) -> None:
        hypothesis = focus.template()["hypothesis"]
        assert hypothesis["type"] == "unknown"
        assert hypothesis["unknown_reason"] is not None


class TestAgainstTheEgressPolicy:
    def test_restricted_tier_sends_exactly_the_whitelist(self, focus: Focus) -> None:
        template, fields = card_outbound(focus.draft)
        decision = evaluate_egress(
            request(fields, PrivacyTier.RESTRICTED),
            today=E.TODAY,
            local_hosts={E.LOCAL_ENDPOINT},
            approval=approval(),
        )
        assert decision.allowed, decision.blocked_reasons
        assert sorted(decision.allowed_paths) == sorted(template)
        assert build_outbound_context(template, decision) == template

    def test_an_approval_for_another_purpose_sends_nothing(self, focus: Focus) -> None:
        template, fields = card_outbound(focus.draft)
        decision = evaluate_egress(
            request(fields, PrivacyTier.RESTRICTED),
            today=E.TODAY,
            local_hosts={E.LOCAL_ENDPOINT},
            approval=approval(purpose="summarise_a_run_for_a_report"),
        )
        assert not decision.allowed
        with pytest.raises(ValueError):
            build_outbound_context(template, decision)

    @pytest.mark.parametrize(
        "tier", [PrivacyTier.STRICT_LOCAL, PrivacyTier.HGR_CONTROLLED], ids=lambda t: t.value
    )
    def test_a_local_only_tier_denies_every_field(self, focus: Focus, tier: PrivacyTier) -> None:
        _, fields = card_outbound(focus.draft)
        decision = evaluate_egress(
            request(fields, tier),
            today=E.TODAY,
            local_hosts={E.LOCAL_ENDPOINT},
            approval=approval(),
        )
        assert not decision.allowed
        assert decision.allowed_paths == []
        assert len(decision.denials) == len(fields)

    def test_the_template_has_no_path_the_approval_cannot_find(self, uncovered: Focus) -> None:
        """A reference the run never measured projects as an empty answer, not as a missing field."""
        template, fields = card_outbound(uncovered.draft)
        assert template["reference_coverage"] == {}
        decision = evaluate_egress(
            request(fields, PrivacyTier.PUBLIC),
            today=E.TODAY,
            local_hosts={E.LOCAL_ENDPOINT},
        )
        assert decision.allowed, decision.blocked_reasons
        assert build_outbound_context(template, decision)["reference_coverage"] == {}


# --------------------------------------------------------------------- the one model path


def route(model_class: ModelClass = ModelClass.LOCAL, **overrides: Any) -> ModelRoute:
    endpoint = overrides.pop(
        "endpoint", E.LOCAL_ENDPOINT if model_class is ModelClass.LOCAL else E.CLOUD_ENDPOINT
    )
    fields: dict[str, Any] = {
        "model_id": f"{model_class.value}-2026-06",
        "model_class": model_class,
        "endpoint": endpoint,
        "sampling_params": {"temperature": 0.0},
        "benchmark_run_id": "eval_outbound_01",
    }
    fields.update(overrides)
    return ModelRoute(**fields)


def provider_terms(**overrides: Any) -> ProviderTerms:
    fields: dict[str, Any] = {
        "provider": "provider-a",
        "endpoint": E.CLOUD_ENDPOINT,
        "zero_retention": True,
        "no_training_use": True,
        "subprocessors_disclosed": True,
        "data_region": "cn-north",
        "dpa_signed": True,
        "reviewed_at": E.TODAY - timedelta(days=30),
        "reviewed_by": E.COMPLIANCE,
    }
    fields.update(overrides)
    return ProviderTerms(**fields)


def model_service(
    made: Focus,
    *,
    gateway: Any,
    model_class: ModelClass = ModelClass.LOCAL,
    **context: Any,
) -> EvidenceModelService:
    settings: dict[str, Any] = {
        "local_hosts": [E.LOCAL_ENDPOINT],
        "cloud_allowlist": [E.CLOUD_ENDPOINT],
        "approval": approval(),
        "today": E.TODAY,
        "agreement_kappa": 0.82,
        "benchmark_passed": True,
    }
    if model_class is not ModelClass.LOCAL:
        settings["provider_terms"] = provider_terms()
    settings.update(context)
    return EvidenceModelService(
        catalog=made.catalog,
        gateway=gateway,
        traces=InMemoryTraceStore(),
        route=route(model_class),
        **settings,
    )


def as_request(made: Focus, *, tier: Optional[PrivacyTier] = None, destination: str = E.LOCAL_ENDPOINT):
    return card_model_request(
        made.draft,
        project_id=made.catalog.get_project().project_id,
        purpose=PURPOSE,
        task=ModelTask.EVIDENCE_SYNTHESIS,
        privacy_tier=tier if tier is not None else made.catalog.get_project().privacy_tier,
        destination=destination,
    )


def rows(catalog: Catalog) -> list[dict[str, Any]]:
    return [dict(row) for row in catalog.conn.execute("select * from model_invocation").fetchall()]


class TestTheOneModelPath:
    """§11.6's controls read against a card: the face is what is sent, the answer is what comes back, and
    an answer that reaches for a reviewer's field is recorded rather than merged."""

    def test_the_request_carries_the_face_and_the_answer_contract(self, focus: Focus) -> None:
        made = as_request(focus)
        assert made.template == focus.template()
        assert [declared.path for declared in made.fields] == list(CARD_OUTBOUND)
        assert made.schema is CardNarrative
        assert made.run_id == focus.draft.card.run_id

    def test_the_answer_contract_names_no_field_a_person_decides(self) -> None:
        """Control 6 is a property of the contract, not a filter: the fields are simply absent from it."""
        assert sorted(CardNarrative.model_fields) == [
            "confidence",
            "confounding_risks",
            "power_warning",
            "recommended_action",
            "stability_verdict",
            "summary",
        ]
        human_fields = {"final_status", "candidate_label", "reviewer_id", "sign_off", "reviewed_at", "review_reason"}
        assert human_fields.isdisjoint(CardNarrative.model_fields)

    def test_a_local_route_is_handed_exactly_the_face(self, single: Focus) -> None:
        capture = E.RecordingGateway(
            [E.response(structured={"summary": "a rare CD14-low state", "confidence": 0.4})]
        )
        call = model_service(single, gateway=capture).execute(as_request(single))
        assert set(call.sent_paths) == set(CARD_OUTBOUND)
        assert call.usable and call.output["summary"] == "a rare CD14-low state"
        assert len(capture.prompts) == 1
        text = json.dumps(capture.prompts, sort_keys=True, default=str)
        for forbidden in single.cells[:5] + single.specimens + single.asset_ids + single.fingerprints:
            assert forbidden not in text, f"{forbidden} reached a prompt"
        made = rows(single.catalog)
        assert [row["invocation_id"] for row in made] == [call.invocation_id]

    def test_an_answer_that_reaches_for_a_reviewers_field_stands_recorded(self, single: Focus) -> None:
        capture = E.RecordingGateway(
            [E.response(structured={"summary": "consistent with a state", "sign_off": "signed"})]
        )
        with pytest.raises(ModelOutputRejected) as raised:
            model_service(single, gateway=capture).execute(as_request(single))
        assert "privileged" in raised.value.message
        assert len(capture.prompts) == 1, "the send happened, so the attempt has to be in the record"
        assert len(rows(single.catalog)) == 1, "recorded once, and not retried until it passed"

    def test_a_strict_local_card_never_reaches_a_cloud_endpoint(self, local_only: Focus) -> None:
        made = as_request(local_only, destination=E.CLOUD_ENDPOINT)
        service = model_service(local_only, gateway=E.UnavailableGateway(), model_class=ModelClass.CLOUD_STANDARD)
        with pytest.raises(EgressBlocked) as raised:
            service.execute(made)
        assert "may not leave the data plane" in raised.value.message

    def test_a_route_that_calls_itself_local_is_still_judged_by_where_it_sends(self, local_only: Focus) -> None:
        """§5.9: a local *label* on a route is not a local destination. The endpoint decides, and an
        external one goes through the allowlist whatever the route says about itself."""
        capture = E.RecordingGateway(
            [E.response(structured={"summary": "a rare CD14-low state", "confidence": 0.4})]
        )
        service = EvidenceModelService(
            catalog=local_only.catalog,
            gateway=capture,
            traces=InMemoryTraceStore(),
            route=route(ModelClass.LOCAL, endpoint=E.CLOUD_ENDPOINT),
            local_hosts=[E.LOCAL_ENDPOINT],
            cloud_allowlist=[E.CLOUD_ENDPOINT],
            approval=approval(),
            today=E.TODAY,
            agreement_kappa=0.82,
            benchmark_passed=True,
        )
        with pytest.raises(EgressBlocked) as raised:
            service.execute(as_request(local_only, destination=E.CLOUD_ENDPOINT))
        assert "may not leave the data plane" in raised.value.message
        assert capture.prompts == [], "nothing may leave while the decision says it may not"
        assert rows(local_only.catalog) == []


class TestReviewerMemo:
    def test_generates_valid_narrative_from_draft(self, focus: Focus) -> None:
        memo = generate_reviewer_memo(focus.draft)
        assert isinstance(memo, CardNarrative)
        assert memo.summary
        assert memo.confidence is not None and 0.0 <= memo.confidence <= 1.0
        assert memo.stability_verdict is not None
        assert memo.recommended_action is not None

    def test_generates_valid_narrative_from_card(self, focus: Focus) -> None:
        memo = generate_reviewer_memo(focus.draft.card)
        assert isinstance(memo, CardNarrative)
        assert memo.summary
        assert memo.confidence is not None

    def test_power_warning_flagged_when_underpowered(self, focus: Focus) -> None:
        from dataclasses import replace
        underpowered_draft = replace(focus.draft, underpowered_design=True)
        memo = generate_reviewer_memo(underpowered_draft)
        assert memo.power_warning is not None
        assert "min_n_per_group" in memo.power_warning

    def test_confounding_risk_flagged_for_continuous_tail(self, focus: Focus) -> None:
        from dataclasses import replace
        from bioadapt_schemas.evidence import ContinuousStateCheck, IdentityVsState
        mock_tail = ContinuousStateCheck(method="diffusion_pseudotime", is_axis_tail=True, statistic=0.88)
        card_with_tail = focus.draft.card.model_copy(
            update={"identity_vs_state": IdentityVsState(continuous_state_check=mock_tail)}
        )
        tail_draft = replace(focus.draft, card=card_with_tail)
        memo = generate_reviewer_memo(tail_draft)
        assert any("连续态混杂" in r for r in memo.confounding_risks)
        assert "0.88" in memo.confounding_risks[0]
