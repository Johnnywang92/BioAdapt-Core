"""The candidate search behind §5.6: each filter the screen offers is a fact the run wrote.

``app/projects/[projectId]/runs/[runId]/candidates/page.tsx`` puts population size, donor span and
"nobody has acted on this yet" in the address bar, so a filtered page can be handed to a colleague as
a link. Every one of those has to be answered by the index and resolved *before* the page is cut: a
screen that filtered after fetching would print a count of what it happened to fetch, and §5.6 makes
that count the thing a reviewer decides where to spend the week on.
"""

from bioadapt_policies.access import Role
from bioadapt_schemas.enums import FinalStatus
from conftest import EXPERT, PI, PROJECT_ID, RUN_ID

BASE = f"/v1/projects/{PROJECT_ID}"


def rows(client, auth, **params) -> list[dict]:
    response = client.get(
        f"{BASE}/candidates", params={"limit": 100, **params}, headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def ids(client, auth, **params) -> list[str]:
    return [row["candidate_id"] for row in rows(client, auth, **params)]


def test_the_size_filter_is_answered_from_the_stored_numbers(client, auth, seeded):
    every = rows(client, auth)
    sizes = {row["candidate_id"]: row["size"] for row in every}
    biggest = max(sizes.values())

    assert ids(client, auth, min_size=biggest) == [
        candidate_id for candidate_id, size in sizes.items() if size >= biggest
    ]
    #: one cell above the largest population is a page with nothing on it, not an error
    assert ids(client, auth, min_size=biggest + 1) == []
    assert ids(client, auth, max_size=biggest - 1) == [
        candidate_id for candidate_id, size in sizes.items() if size <= biggest - 1
    ]


def test_the_donor_filter_asks_for_the_span_a_population_has_to_cover(client, auth, seeded):
    donors = {row["candidate_id"]: row["donor_count"] for row in rows(client, auth)}
    needed = max(donors.values())
    assert ids(client, auth, min_donors=needed) == [
        candidate_id for candidate_id, have in donors.items() if have >= needed
    ]
    assert ids(client, auth, min_donors=needed + 1) == []


def test_a_filter_is_applied_before_the_page_is_cut(client, auth, seeded):
    """Paging a filtered set must not lose a row on every page - the same rule as the label group."""
    smallest = min(row["size"] for row in rows(client, auth))
    unfiltered = ids(client, auth, min_size=smallest)
    assert len(unfiltered) > 1, "the fixture has to leave more than one row to page over"
    first = ids(client, auth, min_size=smallest, limit=1, offset=0)
    second = ids(client, auth, min_size=smallest, limit=1, offset=1)
    assert first + second == unfiltered
    assert len(set(first + second)) == len(unfiltered)


def test_reviewing_a_card_moves_its_candidate_out_of_the_unreviewed_filter(client, auth, seeded):
    unreviewed = ids(client, auth, reviewed=False)
    assert seeded["candidate_id"] in unreviewed, "a draft card is still an unknown, so it reads as unreviewed"
    assert ids(client, auth, reviewed=True) == []

    decided = client.post(
        f"{BASE}/cards/card_001/reviews",
        json={"scope": "card", "decision": "reject", "rationale": "cluster is a doublet artefact"},
        headers=auth(EXPERT, [Role.DOMAIN_EXPERT]),
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["final_status"] == str(FinalStatus.REJECTED)

    #: a rejection is an act: the candidate is no longer something nobody has looked at
    assert ids(client, auth, reviewed=True) == [seeded["candidate_id"]]
    assert ids(client, auth, reviewed=False) == [
        one for one in unreviewed if one != seeded["candidate_id"]
    ]


def test_the_run_filter_keeps_a_cluster_label_inside_the_run_that_made_it(client, auth, seeded):
    """§5.6: the same label in two runs is two candidates, and a filter that names one run answers one."""
    same_label = ids(client, auth, cluster_id=seeded["cluster_id"])
    assert set(same_label) == {seeded["candidate_id"], seeded["other_candidate_id"]}
    assert ids(client, auth, cluster_id=seeded["cluster_id"], run_id=RUN_ID) == [seeded["candidate_id"]]


def test_an_impossible_window_is_refused_with_the_servers_own_sentence(client, auth, seeded):
    refused = client.get(
        f"{BASE}/candidates",
        params={"min_size": 1000, "max_size": 10},
        headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR]),
    )
    assert refused.status_code == 400
    assert "min_size" in refused.json()["message"]

    zero = client.get(
        f"{BASE}/candidates", params={"min_size": 0}, headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    )
    assert zero.status_code == 400
    assert "min_size" in zero.text

    not_a_number = client.get(
        f"{BASE}/candidates", params={"min_donors": "two"}, headers=auth(PI, [Role.PRINCIPAL_INVESTIGATOR])
    )
    assert not_a_number.status_code == 400


def test_a_filtered_row_still_claims_the_same_things_an_unfiltered_one_does(client, auth, seeded):
    """A filter narrows which rows come back; it never changes what a row says about itself."""
    every = rows(client, auth)
    narrowed = rows(client, auth, min_size=min(row["size"] for row in every), run_id=RUN_ID)
    by_id = {row["candidate_id"]: row for row in every}
    for row in narrowed:
        assert row == by_id[row["candidate_id"]]
