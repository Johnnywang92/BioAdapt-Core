"""Candidates, evidence cards, reviews and sign-off (sections 5.4, 5.6, 9.3, 13.1)."""

import json

import pytest
from bioadapt_policies.access import Role
from bioadapt_schemas.enums import AlignmentKind, FinalStatus, ReviewDecision, ReviewScope, SignOff
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.gates import card_ceiling
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS
from conftest import (
    ANALYST,
    ATTESTED_CUTS,
    COMPLIANCE,
    EXPERT,
    EXPERT2,
    PI,
    PROJECT_ID,
    RUN_ID,
    SERVICE,
    candidate,
    card,
    named_card,
)

BASE = f"/v1/projects/{PROJECT_ID}"


def _card(client, auth, seeded):
    return client.get(f"{BASE}/candidates/{seeded['candidate_id']}/evidence", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])).json()["evidence_card"]


@pytest.fixture
def named(catalog):
    """File a card a reviewer has already named: a hypothesis, a label, and the numbers behind them.

    The card is filed against a candidate of its own, so the reads that resolve a card by its
    candidate keep answering with the draft they were written for.
    """

    def file(card_id: str, **overrides) -> str:
        named = named_card(
            card_id=card_id,
            candidate_id=f"cand_{card_id}",
            display_name="cluster_9",
            **overrides,
        )
        catalog.set_scope(PROJECT_ID).record_card(named)
        return card_id

    return file


def test_a_candidate_id_returns_its_card(client, auth, seeded):
    response = client.get(
        f"{BASE}/candidates/{seeded['candidate_id']}/evidence", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    )
    assert response.status_code == 200
    body = response.json()
    assert body["candidate_id"] == seeded["candidate_id"]
    assert body["run_id"] == RUN_ID
    assert body["evidence_card"]["card_id"] == "card_001"


def test_a_cluster_label_is_only_an_alias_and_the_answer_names_its_run(client, auth, seeded):
    headers = auth(ANALYST, [Role.ANALYST])
    here = client.get(f"{BASE}/evidence?cluster_id=cluster_7&run_id={RUN_ID}", headers=headers)
    assert here.status_code == 200
    assert here.json()["run_id"] == RUN_ID
    assert here.json()["cluster_id"] == "cluster_7"
    there = client.get(f"{BASE}/evidence?cluster_id=cluster_7&run_id={seeded['other_run_id']}", headers=headers)
    assert there.status_code == 200
    assert there.json()["candidate_id"] == seeded["other_candidate_id"]
    assert there.json()["evidence_card"]["card_id"] == "card_002"


def test_a_label_without_its_run_is_not_accepted(client, auth, seeded):
    response = client.get(f"{BASE}/evidence?cluster_id=cluster_7", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    assert response.status_code == 400
    assert response.json()["field"] == "run_id"


def test_an_id_and_a_redundant_run_are_a_client_error(client, auth, seeded):
    response = client.get(
        f"{BASE}/evidence?candidate_id={seeded['candidate_id']}&run_id={RUN_ID}",
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    )
    assert response.status_code == 400
    assert response.json()["field"] == "run_id"


def test_the_deprecated_label_route_still_answers_and_says_so(client, auth, seeded):
    response = client.get(
        f"/v0/projects/{PROJECT_ID}/clusters/cluster_7/evidence?run_id={RUN_ID}",
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    )
    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"
    assert "/v1/projects/prj_001/evidence" in response.headers["Link"]
    assert response.json()["run_id"] == RUN_ID


def test_alignment_reports_the_recorded_kind_and_the_kind_under_current_thresholds(client, auth, seeded):
    response = client.get(
        f"{BASE}/candidates/{seeded['candidate_id']}/alignment", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    )
    assert response.status_code == 200
    body = response.json()
    record = body["alignment_history"][0]
    assert record["kind"] == str(AlignmentKind.UNCERTAIN)
    assert record["kind_under_current_thresholds"] == str(AlignmentKind.CONTINUATION)
    assert record["other_candidate_id"] == seeded["other_candidate_id"]
    assert body["same_label_in_other_runs"] == [
        {
            "candidate_id": seeded["other_candidate_id"],
            "run_id": seeded["other_run_id"],
            "size": candidate(run_id=seeded["other_run_id"]).size,
            "donor_count": 2,
            "aligned": True,
        }
    ]
    assert body["thresholds_version"] == "v0-unconfirmed"


def test_alignment_is_reclassified_by_the_thresholds_the_project_registered(client, catalog, auth, seeded):
    """§5.6: this read answers against the project's own cut set - the one drafting and the queue use."""
    catalog.update_project_thresholds(
        DEFAULT_THRESHOLDS.model_copy(
            update={"version": "v1-measured", "confirmed": True, "alignment_continuation_min": 0.7}
        )
    )
    body = client.get(
        f"{BASE}/candidates/{seeded['candidate_id']}/alignment", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    ).json()
    assert body["thresholds_version"] == "v1-measured"
    assert body["alignment_history"][0]["kind"] == str(AlignmentKind.UNCERTAIN)
    assert body["alignment_history"][0]["kind_under_current_thresholds"] == str(AlignmentKind.UNCERTAIN)


def test_the_label_group_loses_no_row_when_the_reader_stands_on_one_of_it(client, auth, seeded):
    """§5.6: the candidate being read is dropped in the index, not after the page was cut."""
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    body = client.get(
        f"{BASE}/candidates/{seeded['candidate_id']}/alignment?limit=1", headers=headers
    ).json()
    assert [item["candidate_id"] for item in body["same_label_in_other_runs"]] == [seeded["other_candidate_id"]]


def test_candidates_are_listed_by_label_across_runs(client, auth, seeded):
    body = client.get(f"{BASE}/candidates?cluster_id=cluster_7", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])).json()
    assert {item["run_id"] for item in body["items"]} == {RUN_ID, seeded["other_run_id"]}


def test_a_field_review_records_the_value_it_overwrote_as_human_sourced(client, auth, catalog, seeded):
    response = client.post(
        f"{BASE}/cards/card_001/reviews",
        json={
            "scope": "field",
            "decision": "revise",
            "rationale": "the label column was produced by the wrong run",
            "new_value": {"status_reason": "corrected after donor level check"},
        },
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert response.status_code == 200
    card = response.json()["card"]
    assert card["status_reason"] == "corrected after donor level check"
    assert card["field_provenance"]["status_reason"]["source"] == "human"
    events = catalog.set_scope(PROJECT_ID).reviews("card_001")
    assert events[0].previous_value == {"status_reason": "awaiting independent confirmation"}
    assert events[0].new_value == {"status_reason": "corrected after donor level check"}


def test_reviewing_against_a_stale_value_is_refused(client, auth, seeded):
    response = client.post(
        f"{BASE}/cards/card_001/reviews",
        json={
            "scope": "field",
            "decision": "revise",
            "rationale": "wrong assumption about the current value",
            "new_value": {"status_reason": "x"},
            "previous_value": {"status_reason": "something else entirely"},
        },
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert response.status_code == 409
    assert response.json()["field"] == "previous_value"
    assert _card(client, auth, seeded)["status_reason"] == "awaiting independent confirmation"


def test_a_card_level_reject_rewrites_the_status_and_clears_the_sign_off(client, auth, seeded):
    response = client.post(
        f"{BASE}/cards/card_001/reviews",
        json={"scope": "card", "decision": "reject", "rationale": "cluster is a doublet artefact"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert response.status_code == 200
    assert response.json()["final_status"] == str(FinalStatus.REJECTED)
    assert response.json()["card"]["sign_off"] == str(SignOff.NOT_SIGNED)


def test_marking_a_conflict_without_contradicting_evidence_is_a_contract_error(client, auth, seeded):
    response = client.post(
        f"{BASE}/cards/card_001/reviews",
        json={"scope": "card", "decision": "mark_conflict", "rationale": "the markers disagree"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert response.status_code == 400
    assert "conflict cards must list contradicting evidence" in response.json()["message"]


def test_the_status_gate_blocks_a_write_above_the_ceiling(client, auth, seeded):
    response = client.post(
        f"{BASE}/cards/card_001/reviews",
        json={
            "scope": "field",
            "decision": "revise",
            "rationale": "trying to promote the card",
            "new_value": {
                "final_status": "supported",
                "reviewer_id": EXPERT,
                "reviewed_at": "2026-05-01T00:00:00+00:00",
                "sign_off": "full",
            },
        },
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert response.status_code == 422
    assert response.json()["class"] == str(ErrorClass.GATE_BLOCKED)
    assert "ceiling" in response.json()["message"]
    assert _card(client, auth, seeded)["final_status"] == str(FinalStatus.UNKNOWN)


def test_a_membership_note_is_recorded_for_the_next_run(client, auth, catalog, seeded):
    """Section 9.3: a membership decision is a record about the next run, not a change to this card."""
    response = client.post(
        f"{BASE}/cards/card_001/reviews",
        json={
            "scope": "membership",
            "decision": "approve",
            "rationale": "the same set, carried over from the other run",
            "carry_over_from_candidate_id": seeded["other_candidate_id"],
        },
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    assert response.status_code == 200
    assert response.json()["final_status"] == str(FinalStatus.UNKNOWN)
    events = catalog.set_scope(PROJECT_ID).reviews("card_001")
    assert events[0].carry_over_from_candidate_id == seeded["other_candidate_id"]


def test_an_approval_on_a_card_that_states_no_conclusion_is_refused(client, auth, catalog, seeded):
    """An approval adopts a conclusion; a draft states none, so there is nothing to adopt."""
    response = client.post(
        f"{BASE}/cards/card_001/reviews",
        json={"scope": "card", "decision": "approve", "rationale": "the analysis looks right"},
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    assert response.status_code == 409
    body = response.json()
    assert body["class"] == str(ErrorClass.CONFLICT)
    assert body["field"] == "hypothesis_type"
    assert "field review" in body["message"]
    #: Nothing was recorded and nothing moved: a refused review leaves the card as it was.
    assert catalog.set_scope(PROJECT_ID).reviews("card_001") == []
    assert _card(client, auth, seeded)["final_status"] == str(FinalStatus.UNKNOWN)


def test_reviews_are_readable_in_order(client, auth, seeded):
    headers = auth(EXPERT, [Role.DOMAIN_EXPERT])
    first = client.post(
        f"{BASE}/cards/card_001/reviews",
        json={"scope": "card", "decision": "mark_unknown", "rationale": "no independent support"},
        headers=headers,
    ).json()["review_id"]
    r2 = client.post(
        f"{BASE}/cards/card_001/reviews",
        json={
            "scope": "card",
            "decision": "reject",
            "rationale": "re-read with the curator",
            "supersedes_review_id": first,
        },
        headers=headers,
    )
    assert r2.status_code == 200, f"r2 failed: {r2.status_code} {r2.text}"
    listing = client.get(f"{BASE}/cards/card_001/reviews", headers=headers).json()["reviews"]
    assert len(listing) == 2
    assert listing[1]["supersedes_review_id"] == first


def test_a_role_without_review_rights_cannot_review(client, auth, seeded):
    response = client.post(
        f"{BASE}/cards/card_001/reviews",
        json={"scope": "card", "decision": "approve", "rationale": "not my call"},
        headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER]),
    )
    assert response.status_code == 403


def test_the_plan_author_may_not_sign_off_their_own_conclusion(client, auth, seeded):
    response = client.post(
        f"{BASE}/cards/card_001/sign_off",
        json={"sign_off": "full"},
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    assert response.status_code == 403
    assert "own conclusion" in response.json()["message"]


def test_solo_mode_allows_author_to_sign_off(client, auth, seeded):
    headers = auth(
        ANALYST,
        [Role.ANALYST, Role.DOMAIN_EXPERT],
        solo_mode=True,
    )
    response = client.post(
        f"{BASE}/cards/card_001/sign_off",
        json={"sign_off": "conditional", "reason": "solo researcher attestation"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["sign_off"] == str(SignOff.CONDITIONAL)


def test_an_independent_expert_signs_off_and_the_card_records_it(client, auth, seeded):
    response = client.post(
        f"{BASE}/cards/card_001/sign_off", json={"sign_off": "conditional", "reason": "awaiting the second atlas"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert response.status_code == 200
    assert response.json()["sign_off"] == str(SignOff.CONDITIONAL)
    card = _card(client, auth, seeded)
    assert card["sign_off"] == str(SignOff.CONDITIONAL)
    assert card["reviewed_at"]


def test_withdrawing_a_sign_off_must_say_why(client, auth, seeded):
    client.post(
        f"{BASE}/cards/card_001/sign_off", json={"sign_off": "full"}, headers=auth(EXPERT, [Role.DOMAIN_EXPERT])
    )
    response = client.post(
        f"{BASE}/cards/card_001/sign_off",
        json={"sign_off": "not_signed"},
        headers=auth(EXPERT2, [Role.DOMAIN_EXPERT]),
    )
    assert response.status_code == 400
    assert response.json()["field"] == "reason"
    assert _card(client, auth, seeded)["sign_off"] == str(SignOff.FULL)
    withdrew = client.post(
        f"{BASE}/cards/card_001/sign_off",
        json={"sign_off": "not_signed", "reason": "the second atlas contradicts the markers"},
        headers=auth(EXPERT2, [Role.DOMAIN_EXPERT]),
    )
    assert withdrew.status_code == 200
    assert withdrew.json()["sign_off"] == str(SignOff.NOT_SIGNED)
    assert _card(client, auth, seeded)["review_reason"] == "the second atlas contradicts the markers"


def test_an_expert_cannot_withdraw_their_own_sign_off(client, auth, seeded):
    headers = auth(EXPERT, [Role.DOMAIN_EXPERT])
    client.post(f"{BASE}/cards/card_001/sign_off", json={"sign_off": "full"}, headers=headers)
    response = client.post(
        f"{BASE}/cards/card_001/sign_off",
        json={"sign_off": "not_signed", "reason": "changing my own mind"},
        headers=headers,
    )
    assert response.status_code == 403
    assert "own conclusion" in response.json()["message"]
    assert _card(client, auth, seeded)["sign_off"] == str(SignOff.FULL)


def test_a_service_account_cannot_sign_off(client, auth, seeded):
    response = client.post(
        f"{BASE}/cards/card_001/sign_off",
        json={"sign_off": "full"},
        headers=auth(SERVICE, [Role.SERVICE_AGENT], service=True),
    )
    assert response.status_code == 403
    assert "human" in response.json()["message"]


def test_signing_off_is_closed_until_the_project_has_attested_its_cut_set(client, auth, catalog, seeded):
    """§1.5 + §14.4: a finding enters the record only over numbers somebody measured.

    Stage 0 may still compute, draft and queue over the appendix C1 placeholders - running that baseline is
    how the placeholders get replaced - but a sign-off turns a draft into a conclusion, so it names the set
    nobody attested and refuses.
    """
    catalog.update_project_thresholds(DEFAULT_THRESHOLDS)
    refused = client.post(
        f"{BASE}/cards/card_001/sign_off",
        json={"sign_off": "full"},
        headers=auth(EXPERT2, [Role.DOMAIN_EXPERT]),
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["class"] == str(ErrorClass.POLICY_BLOCKED)
    assert "v0-unconfirmed" in refused.json()["message"]
    assert _card(client, auth, seeded)["sign_off"] == str(SignOff.NOT_SIGNED)
    denial = [row for row in catalog.audit_entries(action="card:sign_off", limit=20) if "thresholds_version" in row["details"]]
    assert [row["details"]["thresholds_version"] for row in denial] == ["v0-unconfirmed"]
    assert "card:sign_off is closed" in denial[0]["details"]["denials"][0]

    catalog.update_project_thresholds(ATTESTED_CUTS)
    signed = client.post(
        f"{BASE}/cards/card_001/sign_off",
        json={"sign_off": "full"},
        headers=auth(EXPERT2, [Role.DOMAIN_EXPERT]),
    )
    assert signed.status_code == 200, signed.text
    assert _card(client, auth, seeded)["sign_off"] == str(SignOff.FULL)


def test_withdrawing_a_sign_off_stays_open_over_an_unconfirmed_cut_set(client, auth, catalog, seeded):
    """The gate closes on the way in only: a conclusion has to stay withdrawable while stage 0 measures."""
    headers = auth(EXPERT2, [Role.DOMAIN_EXPERT])
    client.post(f"{BASE}/cards/card_001/sign_off", json={"sign_off": "full"}, headers=headers)
    catalog.update_project_thresholds(DEFAULT_THRESHOLDS)
    withdrew = client.post(
        f"{BASE}/cards/card_001/sign_off",
        json={"sign_off": "not_signed", "reason": "the cut set behind this card was never measured"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert withdrew.status_code == 200, withdrew.text
    assert _card(client, auth, seeded)["sign_off"] == str(SignOff.NOT_SIGNED)


def test_a_card_whose_run_cannot_be_resolved_still_needs_the_sign_off_role(client, auth, catalog, seeded):
    #: Section 13.1: the author list is what the separation-of-duties test reads. When the run behind a
    #: card is gone there is no author list at all, and that has to mean "refuse", not "no objection".
    catalog.set_scope(PROJECT_ID).record_card(card(card_id="card_orphan", run_id="run_gone"))
    response = client.post(
        f"{BASE}/cards/card_orphan/sign_off",
        json={"sign_off": "full"},
        headers=auth(COMPLIANCE, [Role.COMPLIANCE_OFFICER]),
    )
    assert response.status_code == 403
    assert "card:sign_off" in response.json()["message"]
    assert catalog.set_scope(PROJECT_ID).get_card("card_orphan").sign_off is SignOff.NOT_SIGNED


def test_a_card_with_no_resolvable_provenance_is_signed_on_the_role_alone(client, auth, catalog, seeded):
    """Section 13.1, and the boundary of what it can say: with no author list, only the role test is left.

    The separation-of-duties rule reads the people who produced the conclusion. When a card's run row is
    gone, that list is empty, so the fallback call in the route carries the role test and nothing more -
    a DOMAIN_EXPERT who is not on the list is not on it, and cannot be proved to be. This pins the
    behaviour rather than wishing it: the fail-closed alternative (refuse any card whose provenance cannot
    be resolved) is written up in 审查报告 section 四 ⑨ as a deliberate choice, not an oversight.
    """
    catalog.set_scope(PROJECT_ID).record_card(card(card_id="card_orphan_2", run_id="run_gone"))
    signed = client.post(
        f"{BASE}/cards/card_orphan_2/sign_off",
        json={"sign_off": "full"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert signed.status_code == 200, signed.text
    assert catalog.set_scope(PROJECT_ID).get_card("card_orphan_2").sign_off is SignOff.FULL

    #: What is *not* waived is the person: an agent still cannot sign, and the analyst who is named as the
    #: card's reviewer is still barred by the author test that the fallback does carry.
    refused = client.post(
        f"{BASE}/cards/card_orphan_2/sign_off",
        json={"sign_off": "full"},
        headers=auth(SERVICE, [Role.SERVICE_AGENT], service=True),
    )
    assert refused.status_code == 403
    assert "human" in refused.json()["message"]
    catalog.set_scope(PROJECT_ID).record_card(card(card_id="card_orphan_3", run_id="run_gone", reviewer_id=EXPERT))
    own = client.post(
        f"{BASE}/cards/card_orphan_3/sign_off",
        json={"sign_off": "full"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert own.status_code == 403
    assert "own conclusion" in own.json()["message"]
    assert catalog.set_scope(PROJECT_ID).get_card("card_orphan_3").sign_off is SignOff.NOT_SIGNED


def test_an_approval_adopts_the_ceiling_and_stops_at_provisional(client, auth, catalog, named):
    """Section 1.5: an approval is the reviewer's own conclusion, so it reaches provisional at the most."""
    named("card_named")
    response = client.post(
        f"{BASE}/cards/card_named/reviews",
        json={"scope": "card", "decision": "approve", "rationale": "the markers hold in both atlases"},
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["final_status"] == str(FinalStatus.PROVISIONAL)
    assert body["ceiling"] == str(FinalStatus.PROVISIONAL)
    assert any("no independent confirmation evidence" in reason for reason in body["gate_reasons"])
    card = body["card"]
    assert card["status_reason"] == "the markers hold in both atlases"
    assert card["reviewer_id"] == ANALYST
    assert card["sign_off"] == str(SignOff.NOT_SIGNED)
    assert catalog.set_scope(PROJECT_ID).card_final_status("card_named") == str(FinalStatus.PROVISIONAL)


def test_a_confirmed_card_reaches_supported_only_through_a_full_signature(client, auth, named):
    """Section 1.5: supported needs the numbers, an independent confirmation and the expert's signature."""
    named("card_confirmed", confirmed=True)
    approved = client.post(
        f"{BASE}/cards/card_confirmed/reviews",
        json={"scope": "card", "decision": "approve", "rationale": "the numbers hold"},
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["final_status"] == str(FinalStatus.PROVISIONAL)
    assert approved.json()["ceiling"] == str(FinalStatus.SUPPORTED)

    signed = client.post(
        f"{BASE}/cards/card_confirmed/sign_off",
        json={"sign_off": "full", "reason": "the hold-out donors agree"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["final_status"] == str(FinalStatus.SUPPORTED)
    assert signed.json()["ceiling"] == str(FinalStatus.SUPPORTED)


def test_a_conditional_signature_stops_at_provisional(client, auth, named):
    """A signature with a reservation is not the expert's full attestation, so it does not reach supported."""
    named("card_conditional", confirmed=True)
    signed = client.post(
        f"{BASE}/cards/card_conditional/sign_off",
        json={"sign_off": "conditional", "reason": "the second atlas is still running"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["sign_off"] == str(SignOff.CONDITIONAL)
    assert signed.json()["final_status"] == str(FinalStatus.PROVISIONAL)
    assert signed.json()["ceiling"] == str(FinalStatus.SUPPORTED)


def test_a_signature_cannot_carry_a_card_above_what_its_numbers_allow(client, auth, named):
    """The ceiling is the cap, whatever act is used: an unconfirmed card stops at provisional."""
    named("card_unconfirmed")
    signed = client.post(
        f"{BASE}/cards/card_unconfirmed/sign_off",
        json={"sign_off": "full", "reason": "I have read the markers"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["final_status"] == str(FinalStatus.PROVISIONAL)
    assert signed.json()["ceiling"] == str(FinalStatus.PROVISIONAL)
    assert any("no independent confirmation evidence" in reason for reason in signed.json()["gate_reasons"])


def test_withdrawing_a_signature_puts_a_supported_card_back_to_provisional(client, auth, named):
    """A supported card rests on the signature, so withdrawing it takes the status down with it."""
    named("card_withdrawn", confirmed=True)
    signed = client.post(
        f"{BASE}/cards/card_withdrawn/sign_off",
        json={"sign_off": "full", "reason": "the hold-out donors agree"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert signed.json()["final_status"] == str(FinalStatus.SUPPORTED)
    withdrew = client.post(
        f"{BASE}/cards/card_withdrawn/sign_off",
        json={"sign_off": "not_signed", "reason": "the hold-out donors disagree after all"},
        headers=auth(EXPERT2, [Role.DOMAIN_EXPERT]),
    )
    assert withdrew.status_code == 200, withdrew.text
    assert withdrew.json()["sign_off"] == str(SignOff.NOT_SIGNED)
    assert withdrew.json()["final_status"] == str(FinalStatus.PROVISIONAL)


def test_a_decision_that_withdraws_the_conclusion_takes_the_signature_with_it(client, auth, named):
    named("card_recalled", confirmed=True)
    client.post(
        f"{BASE}/cards/card_recalled/sign_off",
        json={"sign_off": "full", "reason": "the hold-out donors agree"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    recalled = client.post(
        f"{BASE}/cards/card_recalled/reviews",
        json={"scope": "card", "decision": "mark_unknown", "rationale": "the second atlas contradicts it"},
        headers=auth(EXPERT2, [Role.DOMAIN_EXPERT]),
    )
    assert recalled.status_code == 200, recalled.text
    assert recalled.json()["final_status"] == str(FinalStatus.UNKNOWN)
    assert recalled.json()["card"]["sign_off"] == str(SignOff.NOT_SIGNED)


def test_the_ceiling_the_reviewer_is_shown_is_the_one_the_gate_reads(client, auth, catalog, named):
    """The ceiling is a reading of the card's own numbers, so the route and the gate cannot disagree."""
    named("card_ceiling")
    response = client.post(
        f"{BASE}/cards/card_ceiling/reviews",
        json={"scope": "card", "decision": "approve", "rationale": "the numbers hold"},
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    assert response.status_code == 200, response.text
    scoped = catalog.set_scope(PROJECT_ID)
    verdict = card_ceiling(scoped.get_card("card_ceiling"), thresholds=scoped.get_project().thresholds)
    assert response.json()["ceiling"] == str(verdict.detail["ceiling"])


def test_the_approval_is_audited_with_the_status_it_moved_from_and_to(client, auth, catalog, named):
    named("card_audited")
    client.post(
        f"{BASE}/cards/card_audited/reviews",
        json={"scope": "card", "decision": "approve", "rationale": "the numbers hold"},
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    rows = catalog.audit_entries(action="card:review:approve", limit=10)
    assert len(rows) == 1
    details = rows[0]["details"]
    if isinstance(details, str):
        details = json.loads(details)
    assert details["from_status"] == str(FinalStatus.UNKNOWN)
    assert details["to_status"] == str(FinalStatus.PROVISIONAL)
    assert details["ceiling"] == str(FinalStatus.PROVISIONAL)
