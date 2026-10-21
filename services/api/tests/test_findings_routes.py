"""Candidates, evidence cards, reviews and sign-off (sections 5.4, 5.6, 9.3, 13.1)."""

from bioadapt_policies.access import Role
from bioadapt_schemas.enums import AlignmentKind, FinalStatus, ReviewDecision, ReviewScope, SignOff
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS
from conftest import ANALYST, COMPLIANCE, EXPERT, EXPERT2, PI, PROJECT_ID, RUN_ID, SERVICE, candidate, card

BASE = f"/v1/projects/{PROJECT_ID}"


def _card(client, auth, seeded):
    return client.get(f"{BASE}/candidates/{seeded['candidate_id']}/evidence", headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])).json()["evidence_card"]


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


def test_alignment_is_reclassified_by_the_thresholds_the_deployment_set(client, state, auth, seeded):
    """§5.6: this read answers against the cut set in force, the same one drafting and the queue use."""
    state.card_thresholds = DEFAULT_THRESHOLDS.model_copy(
        update={"version": "v1-measured", "confirmed": True, "alignment_continuation_min": 0.7}
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


def test_an_approve_review_changes_nothing_but_the_record(client, auth, catalog, seeded):
    response = client.post(
        f"{BASE}/cards/card_001/reviews",
        json={
            "scope": "card",
            "decision": "approve",
            "rationale": "consistent across both donors",
            "carry_over_from_candidate_id": seeded["other_candidate_id"],
        },
        headers=auth(ANALYST, [Role.ANALYST]),
    )
    assert response.status_code == 200
    assert response.json()["final_status"] == str(FinalStatus.UNKNOWN)
    events = catalog.set_scope(PROJECT_ID).reviews("card_001")
    assert events[0].carry_over_from_candidate_id == seeded["other_candidate_id"]


def test_reviews_are_readable_in_order(client, auth, seeded):
    headers = auth(EXPERT, [Role.DOMAIN_EXPERT])
    first = client.post(
        f"{BASE}/cards/card_001/reviews",
        json={"scope": "card", "decision": "mark_unknown", "rationale": "no independent support"},
        headers=headers,
    ).json()["review_id"]
    client.post(
        f"{BASE}/cards/card_001/reviews",
        json={"scope": "card", "decision": "approve", "rationale": "re-read with the curator", "supersedes_review_id": first},
        headers=headers,
    )
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
