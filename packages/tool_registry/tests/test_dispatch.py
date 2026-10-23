"""Where an attempt is sent (plan V2.0 sections 3.3, 4.2, 9.4).

A deployment's machines are declarations, so where a run goes is a question answered by what each
machine says it has - not by which machine happens to be first in a list a person typed. These cases
hold the two halves of that: the machine that fits takes the work and is named in the answer, and the
one that does not says why, in words the person who can add a machine or change a plan can act on.

One attempt goes to exactly one machine. The second answer for an attempt is the duplicate work
section 3.3's receipt exists to prevent, so the choice is stable and a delivery is never mirrored.
The one machine a dispatcher may move past is one that fails its health check: the check carries no
work, so moving on cannot duplicate anything.
"""

from __future__ import annotations

from typing import Any, Iterator, Optional

import pytest

from bioadapt_schemas.enums import PrivacyTier
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.nodes import NodeCapabilities, NodeNetworkMode, NodePosition
from bioadapt_tool_registry import NodeDispatch
from bioadapt_tool_registry.enums import ToolResultStatus

from node_factories import (
    TOKEN,
    address_of,
    cage,
    declared_node,
    make_agent,
    request,
    serve,
    stop,
)


def dispatch(
    nodes: list[Any],
    *,
    tier: PrivacyTier = PrivacyTier.RESTRICTED,
    credentials: Optional[dict[str, str]] = None,
    **overrides: Any,
) -> NodeDispatch:
    """A dispatcher holding each declared machine's own secret, unless the case says otherwise."""
    held = credentials if credentials is not None else {node.node_id: TOKEN for node in nodes}
    return NodeDispatch(nodes=nodes, privacy_tier=tier, credentials=held, **overrides)


@pytest.fixture
def agent(tmp_path: Any) -> Iterator[Any]:
    """A node listening on a free port, with the stand-in tool behind it."""
    made = make_agent(tmp_path)
    server, thread = serve(made)
    made.server = server
    try:
        yield made
    finally:
        stop(server, thread)


class TestChoosing:
    def test_the_declared_machine_takes_the_work(self, agent: Any) -> None:
        node = declared_node(endpoint=address_of(agent))
        outcome = dispatch([node]).execute(request())

        assert outcome.status is ToolResultStatus.SUCCESS
        assert [item.content for item in outcome.outputs] == [b'{"slot":"counts"}']

    def test_the_answer_names_the_declared_machine_not_the_agent_label(self, agent: Any) -> None:
        """The receipt has to name a row the registry can resolve; the far side's own label is not one."""
        node = declared_node(endpoint=address_of(agent))
        outcome = dispatch([node]).execute(request())

        assert outcome.executed_on == "nod_server_01"

    def test_one_attempt_goes_to_one_machine(self, tmp_path: Any) -> None:
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        second.mkdir()
        chosen = make_agent(first, node_name="nod_a")
        other = make_agent(second, node_name="nod_b")
        servers = [serve(made) for made in (chosen, other)]
        chosen.server, other.server = servers[0][0], servers[1][0]
        try:
            nodes = [
                declared_node(endpoint=address_of(other), node_id="nod_b"),
                declared_node(endpoint=address_of(chosen), node_id="nod_a"),
            ]
            outcome = dispatch(nodes).execute(request())
        finally:
            for server, thread in reversed(servers):
                stop(server, thread)

        #: Declared order decides, not the order the list happened to arrive in, so a redelivery of
        #: the attempt lands where the first delivery went.
        assert outcome.executed_on == "nod_a"
        assert (first / "work" / "run_prj_001_01" / "qc_001").is_dir()
        assert not (second / "work" / "run_prj_001_01").exists(), "the other machine was never asked"

    def test_redelivering_the_same_attempt_gets_the_answer_that_was_kept(self, agent: Any) -> None:
        """§3.3: a lost answer is fetched again, not computed again on the far machine."""
        node = declared_node(endpoint=address_of(agent))
        dispatcher = dispatch([node])

        first = dispatcher.execute(request())
        again = dispatcher.execute(request())

        assert again.status is ToolResultStatus.SUCCESS
        assert [item.sha256 for item in again.outputs] == [item.sha256 for item in first.outputs]
        assert again.compute_minutes == first.compute_minutes
        assert again.executed_on == "nod_server_01"

    def test_a_machine_that_does_not_answer_is_a_classified_failure(self, agent: Any) -> None:
        address = address_of(agent)
        agent.server.shutdown()
        agent.server.server_close()
        node = declared_node(endpoint=address, node_id="nod_off_01")
        outcome = dispatch([node], timeout=2.0).execute(request())

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "no_node_reachable"
        assert outcome.errors[0].error_class is ErrorClass.NEEDS_INPUT
        assert "nod_off_01" in outcome.errors[0].message
        assert "did not answer its health check" in outcome.errors[0].message

    def test_a_silent_machine_is_skipped_for_one_that_answers(self, tmp_path: Any) -> None:
        """A health check carries no work, so moving past a silent machine cannot duplicate any."""
        live = make_agent(tmp_path, node_name="nod_live_01")
        server, thread = serve(live)
        live.server = server
        try:
            nodes = [
                declared_node(endpoint="http://127.0.0.1:9", node_id="nod_dead_01"),
                declared_node(endpoint=address_of(live), node_id="nod_live_01"),
            ]
            outcome = dispatch(nodes, timeout=2.0).execute(request())
        finally:
            stop(server, thread)

        assert outcome.status is ToolResultStatus.SUCCESS
        assert outcome.executed_on == "nod_live_01"

    def test_a_switched_off_machine_is_skipped_for_one_that_is_on(self, tmp_path: Any) -> None:
        live = make_agent(tmp_path, node_name="nod_live_01")
        server, thread = serve(live)
        live.server = server
        try:
            nodes = [
                declared_node(endpoint=None, node_id="nod_off_01").model_copy(
                    update={"enabled": False}
                ),
                declared_node(endpoint=address_of(live), node_id="nod_live_01"),
            ]
            outcome = dispatch(nodes).execute(request())
        finally:
            stop(server, thread)

        assert outcome.status is ToolResultStatus.SUCCESS
        assert outcome.executed_on == "nod_live_01"


class TestPrivacy:
    """§4.2, §4.4: the project's promise decides whether a machine may take its work at all."""

    @staticmethod
    def open_node(**overrides: Any) -> Any:
        fields: dict[str, Any] = {
            "capabilities": NodeCapabilities(
                cpu=8,
                memory_mb=16384,
                can_run_containers=True,
                network_mode=NodeNetworkMode.OPEN,
            )
        }
        fields.update(overrides)
        return declared_node(**fields)

    def test_a_strict_local_project_is_not_sent_to_a_cloud_machine(self) -> None:
        cloud = declared_node(position=NodePosition.CLOUD)
        outcome = dispatch([cloud], tier=PrivacyTier.STRICT_LOCAL).execute(request())

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "no_node_fits"
        assert "nod_server_01" in outcome.errors[0].message
        assert "strict-local" in outcome.errors[0].message
        assert "cloud" in outcome.errors[0].message

    def test_a_strict_local_project_is_not_sent_to_an_open_network(self) -> None:
        outcome = dispatch([self.open_node()], tier=PrivacyTier.STRICT_LOCAL).execute(request())

        assert outcome.errors[0].code == "no_node_fits"
        assert "strict-local" in outcome.errors[0].message
        assert "network is closed" in outcome.errors[0].message
        assert "open" in outcome.errors[0].message

    def test_a_strict_local_project_runs_on_a_local_closed_machine(self, agent: Any) -> None:
        node = declared_node(endpoint=address_of(agent))
        outcome = dispatch([node], tier=PrivacyTier.STRICT_LOCAL).execute(request())

        assert outcome.status is ToolResultStatus.SUCCESS

    def test_a_hgr_project_is_not_sent_to_an_open_network(self) -> None:
        outcome = dispatch(
            [self.open_node(position=NodePosition.PRIVATE)], tier=PrivacyTier.HGR_CONTROLLED
        ).execute(request())

        assert outcome.errors[0].code == "no_node_fits"
        assert "human-genetic-resource" in outcome.errors[0].message
        assert "network is closed" in outcome.errors[0].message

    def test_a_hgr_project_runs_where_the_network_is_closed(self, agent: Any) -> None:
        node = declared_node(endpoint=address_of(agent), position=NodePosition.PRIVATE)
        outcome = dispatch([node], tier=PrivacyTier.HGR_CONTROLLED).execute(request())

        assert outcome.status is ToolResultStatus.SUCCESS

    def test_a_restricted_project_may_still_use_an_open_machine(self, agent: Any) -> None:
        node = self.open_node(endpoint=address_of(agent))
        outcome = dispatch([node], tier=PrivacyTier.RESTRICTED).execute(request())

        assert outcome.status is ToolResultStatus.SUCCESS


class TestRefusals:
    def test_a_machine_that_cannot_hold_the_tool_is_not_chosen(self) -> None:
        tiny = declared_node(capabilities=NodeCapabilities(cpu=2, memory_mb=2048))
        outcome = dispatch([tiny]).execute(request())

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "no_node_fits"
        #: A refusal parks the run for the person who can add a machine, rather than burning a retry.
        assert outcome.errors[0].error_class is ErrorClass.NEEDS_INPUT
        assert "nod_server_01 (lab server)" in outcome.errors[0].message
        assert "the tool wants 4 cpu, the node has 2" in outcome.errors[0].message

    def test_a_machine_that_cannot_hold_the_cage_is_not_chosen(self) -> None:
        """The plan's budget is what a person approved; a machine that covers only the tool has not
        been promised it can hold the attempt."""
        outcome = dispatch([declared_node()]).execute(request(cage=cage(cpu=16)))

        assert outcome.errors[0].code == "no_node_fits"
        assert "the attempt is allowed 16 cpu, the node has 8" in outcome.errors[0].message

    def test_a_machine_with_no_endpoint_is_not_chosen(self) -> None:
        outcome = dispatch([declared_node(endpoint=None)]).execute(request())

        assert outcome.errors[0].code == "no_node_fits"
        assert "declares no endpoint" in outcome.errors[0].message

    def test_every_refused_machine_is_named_with_its_own_reason(self) -> None:
        nodes = [
            declared_node(node_id="nod_tiny", capabilities=NodeCapabilities(cpu=2, memory_mb=2048)),
            declared_node(node_id="nod_unreachable", endpoint=None),
        ]
        message = dispatch(nodes).execute(request()).errors[0].message

        assert "nod_tiny" in message and "the tool wants 4 cpu" in message
        assert "nod_unreachable" in message and "declares no endpoint" in message

    def test_a_deployment_that_holds_no_credential_refuses_instead_of_running_here(self) -> None:
        outcome = dispatch([declared_node()], credentials={}).execute(request())

        assert outcome.errors[0].code == "node_credential_missing"
        assert outcome.errors[0].error_class is ErrorClass.NEEDS_INPUT
        assert "no credential" in outcome.errors[0].message

    def test_a_machine_without_its_own_credential_is_not_opened_with_anothers(self) -> None:
        """One node's secret does not open another; a map naming a different node is no credential."""
        outcome = dispatch(
            [declared_node(node_id="nod_other")], credentials={"nod_known": TOKEN}
        ).execute(request())

        assert outcome.errors[0].code == "no_node_fits"
        assert "nod_other" in outcome.errors[0].message
        assert "no credential for this node" in outcome.errors[0].message

    def test_a_wrong_credential_does_not_fall_back_to_the_next_machine(self, agent: Any) -> None:
        node = declared_node(endpoint=address_of(agent))
        outcome = dispatch([node], credentials={"nod_server_01": "not the secret"}).execute(request())

        assert outcome.errors[0].code == "no_node_reachable"
        assert "refused the control plane's credential" in outcome.errors[0].message

    def test_a_dispatcher_with_no_declared_machine_refuses(self) -> None:
        outcome = dispatch([]).execute(request())

        assert outcome.errors[0].code == "no_node_declared"
        assert outcome.errors[0].error_class is ErrorClass.NEEDS_INPUT
