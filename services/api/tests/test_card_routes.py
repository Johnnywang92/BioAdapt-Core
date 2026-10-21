"""Drafting cards and reading the expert queue (plan V2.0 sections 9.1, 13.4).

Both routes hand the work to the evidence service, so what is tested here is the seam the service does not
own: who may ask for a draft, that the envelope shows the ceiling beside the card instead of on it, that
filing the same run twice adds no second row, and that a card a person has acted on cannot be rewritten
from the drafting path.
"""

import json
from typing import Any, Mapping, Sequence

import pytest
from bioadapt_policies.access import Role
from bioadapt_schemas.entities import AssetLineage, CandidateSet, DataAsset
from bioadapt_schemas.enums import (
    AssetRole,
    CandidateType,
    FilteringState,
    PrivacyTier,
    RetentionClass,
    ReviewDecision,
    ReviewScope,
    UsageScope,
)
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.identifiers import (
    canonical_cell_key,
    candidate_id,
    index_hash,
    member_fingerprint,
    sha256_hex,
)
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS
from bioadapt_storage.objectstore import object_key
from conftest import ANALYST, EXPERT, NOW, PI, PROJECT_ID, RUN_ID, manifest

BASE = f"/v1/projects/{PROJECT_ID}"

#: A run of its own: the shared ``RUN_ID`` manifest names an uploaded input whose bytes no fixture writes,
#: and a card cannot be built from an index row alone - which is the needs-input case tested below.
DRAFT_RUN = "run_project001_09"
DRAFT_BASE = f"{BASE}/runs/{DRAFT_RUN}"
FOCUS = "cluster_7"
COMMON = "cluster_1"
FOCUS_CELLS = 60
PARTITION_CELLS = 6600
DISCOVERY = ("spc_01", "spc_02")


def _block(cluster: str, size: int) -> dict[str, str]:
    return {
        canonical_cell_key(DISCOVERY[index % len(DISCOVERY)], f"{cluster}{index:06d}"): cluster
        for index in range(size)
    }


LABELS: dict[str, str] = {**_block(FOCUS, FOCUS_CELLS), **_block(COMMON, PARTITION_CELLS - FOCUS_CELLS)}
FOCUS_MEMBERS = sorted(key for key, name in LABELS.items() if name == FOCUS)
COMMON_MEMBERS = sorted(key for key, name in LABELS.items() if name == COMMON)


def _candidate(cells: Sequence[str], display_name: str, kinds: Sequence[CandidateType]) -> CandidateSet:
    fingerprint = member_fingerprint(cells)
    return CandidateSet(
        candidate_id=candidate_id(fingerprint, DRAFT_RUN),
        run_id=DRAFT_RUN,
        member_fingerprint=fingerprint,
        size=len(set(cells)),
        donor_count=len({key.partition("\x1f")[0] for key in cells}),
        display_name=display_name,
        cluster_origin={"method": "leiden", "resolution": 1.0},
        candidate_types=list(kinds),
    )


def _clusters_product() -> tuple[str, str, dict[str, Any]]:
    return (
        "clusters",
        "cluster_labels_v1",
        {
            "method": "leiden",
            "resolution": 1.0,
            "seed": 0,
            "modularity": 0.61,
            "graph_digest": "ab" * 32,
            "singleton_share": 0.0,
            "n_clusters": 2,
            "labels": [{"cell_key": key, "cluster": name} for key, name in sorted(LABELS.items())],
        },
    )


def _markers_product() -> tuple[str, str, dict[str, Any]]:
    genes = ("IL7R", "CXCL13", "PDCD1")
    return (
        "markers",
        "markers_v1",
        {
            "method": "rank_genes_groups_wilcoxon",
            "top_n": len(genes),
            "rank_only": True,
            "cluster_digest": "cd" * 32,
            "markers": [
                {
                    "cluster": cluster,
                    "gene": gene,
                    "rank": rank,
                    "mean_in": 1.8,
                    "mean_out": 0.2,
                    "detection_in": 0.82,
                    "detection_out": 0.06,
                    "log2fc": 2.1,
                }
                for cluster in (FOCUS, COMMON)
                for rank, gene in enumerate(genes, start=1)
            ],
        },
    )


def _de_row(gene: str, log2fc: float, padj: float, in_group: float, excluded: float, agreed: int) -> dict:
    return {
        "gene": gene,
        "group_a": FOCUS,
        "group_b": COMMON,
        "log2fc": log2fc,
        "p_value": max(padj / 10, 1e-8),
        "padj": padj,
        "detection_a": in_group,
        "detection_b": excluded,
        "donors_tested": 4,
        "donors_same_direction": agreed,
    }


def _de_product() -> tuple[str, str, dict[str, Any]]:
    """One gene clearing all five section 7.8 cuts, one short of donor consistency, one short of both others."""
    return (
        "de_table",
        "de_table_v1",
        {
            "method": "limit_donor_pseudobulk",
            "multiple_testing": "fdr_bh",
            "thresholds_version": DEFAULT_THRESHOLDS.version,
            "permutation_replicates": 0,
            "seed": 0,
            "unit": "donor",
            "contrasts": [[FOCUS, COMMON]],
            "contrast_details": {
                f"{FOCUS}_vs_{COMMON}": {
                    "arms": "cluster",
                    "group_a": FOCUS,
                    "group_b": COMMON,
                    "cluster": FOCUS,
                    "design": "paired",
                    "donors_a": 4,
                    "donors_b": 4,
                    "paired_donors": 4,
                }
            },
            "donors_per_group": {FOCUS: 4, COMMON: 4},
            "table": [
                _de_row("IL7R", 2.14, 3e-5, 0.91, 0.02, 4),
                _de_row("CXCL13", 1.42, 9e-4, 0.71, 0.05, 3),
                _de_row("MS4A1", 0.31, 0.34, 0.44, 0.31, 2),
            ],
        },
    )


PRODUCTS = (_clusters_product, _markers_product, _de_product)


#: A manifest has to name at least one root input (§5.2), and an upload is indexed without a producing
#: run, so the reader reaches it through the manifest rather than through the run's own outputs.
def _root_product() -> tuple[str, str, dict[str, Any]]:
    return ("counts", "expression_v1", {"matrix": {"shape": [4, 3]}, "transform": "counts", "parameters": {}})


def _store(
    catalog, objects, slot: str, schema_version: str, payload: Mapping[str, Any], *, run_id: str = DRAFT_RUN
) -> str:
    """A finished node's three writes, in the order they have to happen: bytes, index row, lineage."""
    raw = json.dumps({"slot": slot, "schema_version": schema_version, **payload}, sort_keys=True).encode()
    digest = sha256_hex(raw)
    asset_id = f"as_{slot}"
    retention = RetentionClass.RUN_LINKED if run_id else RetentionClass.DELIVERABLE
    key = object_key(
        project_id=PROJECT_ID,
        asset_id=asset_id,
        file_sha256=digest,
        retention_class=retention,
        extension="json",
    )
    objects.put(key, raw)
    catalog.record_asset(
        DataAsset(
            asset_id=asset_id,
            project_id=PROJECT_ID,
            uri="s3://" + key,
            file_sha256=digest,
            content_fingerprint=index_hash(LABELS),
            format="json",
            schema_version=schema_version,
            size_bytes=len(raw),
            privacy_tier=PrivacyTier.RESTRICTED,
            usage_scope=UsageScope.THIS_PROJECT_ONLY,
            retention_class=retention,
            filtering_state=FilteringState.NONE,
            created_at=NOW,
            created_by_run_id=run_id or None,
        )
    )
    if run_id:
        #: The plan the shared fixture pins has a single node, so every product of this run is its output.
        catalog.record_lineage(
            AssetLineage(run_id=run_id, node_id="cluster", asset_id=asset_id, role=AssetRole.OUTPUT, ordinal=0)
        )
    return asset_id


@pytest.fixture
def drafted(catalog, state) -> dict[str, Any]:
    catalog.set_scope(PROJECT_ID)
    slot, schema_version, payload = _root_product()
    root = _store(catalog, state.objects, slot, schema_version, payload, run_id="")
    catalog.record_run(
        manifest(run_id=DRAFT_RUN, input_hashes={root: catalog.get_asset(root).file_sha256}),
        state="EVIDENCE_BUILDING",
    )
    for make in PRODUCTS:
        slot, schema_version, payload = make()
        _store(catalog, state.objects, slot, schema_version, payload)
    focus = _candidate(FOCUS_MEMBERS, FOCUS, [CandidateType.C3_RARE_STABLE])
    common = _candidate(COMMON_MEMBERS, COMMON, [CandidateType.C1_UNASSIGNED])
    catalog.record_candidate(focus)
    catalog.record_candidate(common)
    return {"focus_candidate_id": focus.candidate_id, "common_candidate_id": common.candidate_id}


def _draft_for(body: dict, wanted: str) -> dict:
    return next(item for item in body["cards"] if item["candidate_id"] == wanted)


def _scores(body: dict) -> dict[str, dict]:
    return {item["candidate_id"]: item for item in body["scores"]}


def test_drafting_is_addressed_to_a_run_and_needs_a_role_that_holds_asset_write(client, auth, catalog, drafted):
    response = client.post(f"{DRAFT_BASE}/cards/draft", json={}, headers=auth(EXPERT, [Role.DOMAIN_EXPERT]))
    assert response.status_code == 403
    body = response.json()
    assert body["code"] == "authorization_denied"
    assert body["class"] == str(ErrorClass.POLICY_BLOCKED)
    assert f"{DRAFT_RUN}:card_draft" in body["message"]
    with pytest.raises(LookupError):
        catalog.get_card_for_candidate(drafted["focus_candidate_id"])


def test_a_draft_is_filed_as_unknown_and_shows_the_ceiling_beside_the_card(client, auth, catalog, drafted):
    response = client.post(f"{DRAFT_BASE}/cards/draft", json={}, headers=auth(ANALYST, [Role.ANALYST]))
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == DRAFT_RUN
    assert body["thresholds_version"] == DEFAULT_THRESHOLDS.version
    assert [item["candidate_id"] for item in body["cards"]] == sorted(
        [drafted["focus_candidate_id"], drafted["common_candidate_id"]]
    )

    focus = _draft_for(body, drafted["focus_candidate_id"])
    assert focus["ceiling"] == "provisional"
    assert focus["support_met"] is True
    assert focus["underpowered_design"] is False
    assert focus["blocking_risks"] == []
    assert "no independent confirmation evidence" in focus["gate_reasons"]
    assert any("stability is unmeasured" in note for note in focus["notes"])
    assert focus["evidence_count"] == len(focus["card"]["evidence"])

    card = focus["card"]
    assert card["hypothesis_type"] == "unknown"
    assert card["final_status"] == "unknown"
    assert card["candidate_label"] is None
    assert card["sign_off"] == "not_signed"
    assert card["reviewer_id"] is None
    assert card["thresholds_version"] == DEFAULT_THRESHOLDS.version
    assert card["supporting_evidence_ids"], "the gene clearing every section 7.8 cut has to support the card"
    assert card["confirmation_evidence_ids"] == []
    assert card["field_provenance"], "every audited number on a draft names the record it came from"

    filed = catalog.get_card_for_candidate(drafted["focus_candidate_id"])
    assert filed.model_dump(mode="json") == card
    assert catalog.card_final_status(focus["card_id"]) == "unknown"


def test_a_draft_is_filed_under_the_annotation_version_the_caller_names(client, auth, catalog, drafted):
    response = client.post(
        f"{DRAFT_BASE}/cards/draft",
        json={"annotation_version_id": "ann_0001"},
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    )
    assert response.status_code == 200
    row = catalog.conn.execute(
        "select annotation_version_id from evidence_card where card_id = ?", (response.json()["cards"][0]["card_id"],)
    ).fetchone()
    assert row["annotation_version_id"] == "ann_0001"


def test_drafting_the_same_run_twice_files_no_second_card(client, auth, catalog, drafted):
    headers = auth(ANALYST, [Role.ANALYST])
    first = client.post(f"{DRAFT_BASE}/cards/draft", json={}, headers=headers)
    again = client.post(f"{DRAFT_BASE}/cards/draft", json={}, headers=headers)
    assert again.status_code == 200
    assert again.json() == first.json()
    assert catalog.conn.execute("select count(*) as n from evidence_card").fetchone()["n"] == len(first.json()["cards"])


def test_drafting_records_which_cards_it_filed_in_the_audit_trail(client, auth, catalog, drafted):
    response = client.post(f"{DRAFT_BASE}/cards/draft", json={}, headers=auth(ANALYST, [Role.ANALYST]))
    assert response.status_code == 200
    entries = catalog.audit_entries(action="card:draft", limit=10)
    assert len(entries) == 1
    details = entries[0]["details"]
    if isinstance(details, str):
        details = json.loads(details)
    cards = response.json()["cards"]
    assert details["card_ids"] == [item["card_id"] for item in cards]
    assert details["ceilings"] == {item["card_id"]: item["ceiling"] for item in cards}


def test_a_named_candidate_still_has_to_belong_to_the_run_being_drafted(client, auth, seeded, drafted):
    foreign = seeded["candidate_id"]
    response = client.post(
        f"{DRAFT_BASE}/cards/draft",
        json={"candidate_ids": [foreign]},
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    )
    assert response.status_code == 400
    body = response.json()
    assert body["field"] == "candidate_ids"
    assert foreign in body["message"] and RUN_ID in body["message"]


def test_a_candidate_that_was_never_registered_is_not_drafted(client, auth, drafted):
    response = client.post(
        f"{DRAFT_BASE}/cards/draft",
        json={"candidate_ids": ["cand_absent_00000000"]},
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    )
    assert response.status_code == 404
    assert response.json()["class"] == str(ErrorClass.NOT_FOUND)


def test_only_the_candidates_the_caller_names_are_drafted(client, auth, catalog, drafted):
    response = client.post(
        f"{DRAFT_BASE}/cards/draft",
        json={"candidate_ids": [drafted["focus_candidate_id"]]},
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    )
    assert response.status_code == 200
    cards = response.json()["cards"]
    assert [item["candidate_id"] for item in cards] == [drafted["focus_candidate_id"]]
    assert catalog.card_final_status(cards[0]["card_id"]) == "unknown"
    with pytest.raises(LookupError):
        catalog.get_card_for_candidate(drafted["common_candidate_id"])


def test_a_run_whose_indexed_input_has_no_bytes_is_refused_as_needing_input(client, auth):
    response = client.post(f"{BASE}/runs/{RUN_ID}/cards/draft", json={}, headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]))
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "missing_product"
    assert body["class"] == str(ErrorClass.NEEDS_INPUT)
    assert body["field"] == "asset_id"
    assert "as_0001" in body["message"]


def test_a_filed_draft_answers_on_the_read_routes(client, auth, drafted):
    headers = auth(ANALYST, [Role.ANALYST])
    drafted_body = client.post(f"{DRAFT_BASE}/cards/draft", json={}, headers=headers).json()
    focus = _draft_for(drafted_body, drafted["focus_candidate_id"])

    by_id = client.get(f"{BASE}/candidates/{focus['candidate_id']}/evidence", headers=auth(EXPERT, [Role.DOMAIN_EXPERT]))
    assert by_id.status_code == 200
    assert by_id.json()["evidence_card"]["card_id"] == focus["card_id"]

    by_label = client.get(f"{BASE}/evidence?cluster_id={FOCUS}&run_id={DRAFT_RUN}", headers=headers)
    assert by_label.status_code == 200
    assert by_label.json()["candidate_id"] == focus["candidate_id"]
    assert by_label.json()["run_id"] == DRAFT_RUN


def test_a_card_somebody_has_acted_on_is_not_rewritten_by_a_rebuild(client, auth, catalog, drafted):
    headers = auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    body = client.post(f"{DRAFT_BASE}/cards/draft", json={}, headers=headers).json()
    card_id = _draft_for(body, drafted["focus_candidate_id"])["card_id"]
    reviewed = client.post(
        f"{BASE}/cards/{card_id}/reviews",
        json={
            "scope": str(ReviewScope.CARD),
            "decision": str(ReviewDecision.MARK_UNKNOWN),
            "rationale": "the set needs a second atlas before it can be called anything",
        },
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert reviewed.status_code == 200

    refused = client.post(f"{DRAFT_BASE}/cards/draft", json={}, headers=headers)
    assert refused.status_code == 403
    body = refused.json()
    assert body["code"] == "card_under_review"
    assert body["class"] == str(ErrorClass.POLICY_BLOCKED)
    assert body["field"] == "final_status"
    assert EXPERT in body["message"]
    assert catalog.get_card(card_id).reviewer_id == EXPERT


def test_the_queue_is_ordered_by_the_rule_with_a_reason_for_every_place(client, auth, drafted):
    response = client.get(f"{DRAFT_BASE}/cards/queue", headers=auth(EXPERT, [Role.DOMAIN_EXPERT]))
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == DRAFT_RUN
    assert body["drafted"] == 2
    assert body["limit"] == DEFAULT_THRESHOLDS.expert_queue_card_limit
    assert body["thresholds_version"] == DEFAULT_THRESHOLDS.version
    assert body["rule_version"] == "expert_queue_rule_v1"
    assert set(body["queue"]) | set(body["observed"]) == {item["card_id"] for item in body["scores"]}
    assert body["observed"] == []

    scores = _scores(body)
    focus, common = scores[drafted["focus_candidate_id"]], scores[drafted["common_candidate_id"]]
    assert (focus["impact"], focus["uncertainty"]) == (2, 2)
    assert (common["impact"], common["uncertainty"]) == (0, 3)
    assert body["queue"] == [focus["card_id"], common["card_id"]]
    assert focus["display_name"] == FOCUS and common["display_name"] == COMMON
    assert any("rare by the composition rule" in reason for reason in focus["reasons"])
    assert any("not rare by the composition rule" in reason for reason in common["reasons"])
    assert any("ceiling provisional" in reason for reason in focus["reasons"])


def test_the_project_can_ask_for_a_population_it_is_interested_in(client, auth, drafted):
    def queue_of(ask: str) -> dict:
        return client.get(f"{DRAFT_BASE}/cards/queue?client_ask={ask}", headers=auth(EXPERT, [Role.DOMAIN_EXPERT])).json()

    plain = client.get(f"{DRAFT_BASE}/cards/queue", headers=auth(EXPERT, [Role.DOMAIN_EXPERT])).json()
    asked = queue_of(f"{drafted['common_candidate_id']},")
    common = _scores(asked)[drafted["common_candidate_id"]]
    assert common["client_ask"] is True
    assert common["score"] == common["impact"] * common["uncertainty"] + 2
    assert any("named this population" in reason for reason in common["reasons"])
    assert asked["queue"] == plain["queue"], "a bump of two does not overtake the rare set's four points"
    assert _scores(plain)[drafted["common_candidate_id"]]["client_ask"] is False


def test_the_queue_cap_is_a_number_the_caller_can_lower_but_never_to_zero(client, auth, drafted):
    body = client.get(f"{DRAFT_BASE}/cards/queue?limit=1", headers=auth(EXPERT, [Role.DOMAIN_EXPERT])).json()
    assert body["limit"] == 1
    scores = _scores(body)
    assert body["queue"] == [scores[drafted["focus_candidate_id"]]["card_id"]]
    assert body["observed"] == [scores[drafted["common_candidate_id"]]["card_id"]]

    refused = client.get(f"{DRAFT_BASE}/cards/queue?limit=0", headers=auth(EXPERT, [Role.DOMAIN_EXPERT]))
    assert refused.status_code == 400
    assert refused.json()["field"] == "limit"


def test_reading_the_queue_needs_a_role_that_can_read_the_run(client, auth, drafted):
    response = client.get(f"{DRAFT_BASE}/cards/queue", headers=auth("user_curator_01", [Role.KNOWLEDGE_CURATOR]))
    assert response.status_code == 403
    assert f"{DRAFT_RUN}:card_queue" in response.json()["message"]
