"""Where an attempt is sent (plan V2.0 sections 3.3, 4.2, 9.4).

A deployment's machines are declarations, so where a run goes is a question answered by what each
machine says it has - not by which machine happens to be first in a list a person typed. These cases
hold the two halves of that: the machine that fits takes the work and is named in the answer, and the
one that does not says why, in words the person who can add a machine or change a plan can act on.

One attempt goes to exactly one machine. The second answer for an attempt is the duplicate work
section 3.3's receipt exists to prevent, so the choice is stable and a delivery is never mirrored.
"""

from __future__ import annotations

from typing import Any, Iterator

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
        node = declared_node(endpoint=address_of(agent), node_id="nod_declared_01")
        outcome = NodeDispatch(
            nodes=[node], privacy_tier=PrivacyTier.RESTRICTED, token=TOKEN
        ).execute(request())

        assert outcome.status is ToolResultStatus.SUCCESS
        assert [item.content for item in outcome.outputs] == [b'{"slot":"counts"}']

    def test_the_answer_names_the_declared_machine_not_the_agent_label(self, agent: Any) -> None:
        """The receipt has to name a row the registry can resolve; the far side's own label is not one."""
        node = declared_node(endpoint=address_of(agent), node_id="nod_declared_01")
        outcome = NodeDispatch(
            nodes=[node], privacy_tier=PrivacyTier.RESTRICTED, token=TOKEN
        ).execute(request())

        assert outcome.executed_on == "nod_declared_01"

    def test_one_attempt_goes_to_one_machine(self, tmp_path: Any) -> None:
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        second.mkdir()
        chosen, other = make_agent(first, node_name="node_a"), make_agent(second, node_name="node_b")
        servers = [serve(made) for made in (chosen, other)]
        chosen.server, other.server = servers[0][0], servers[1][0]
        try:
            nodes = [
                declared_node(endpoint=address_of(other), node_id="nod_b"),
                declared_node(endpoint=address_of(chosen), node_id="nod_a"),
            ]
            outcome = NodeDispatch(
                nodes=nodes, privacy_tier=PrivacyTier.RESTRICTED, token=TOKEN
            ).execute(request())
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
        node = declared_node(endpoint=address_of(agent), node_id="nod_declared_01")
        dispatch = NodeDispatch(nodes=[node], privacy_tier=PrivacyTier.RESTRICTED, token=TOKEN)

        first = dispatch.execute(request())
        again = dispatch.execute(request())

        assert again.status is ToolResultStatus.SUCCESS
        assert [item.sha256 for item in again.outputs] == [item.sha256 for item in first.outputs]
        assert again.compute_minutes == first.compute_minutes
        assert again.executed_on == "nod_declared_01"

    def test_a_machine_that_is_switched_off_is_a_classified_failure(self, agent: Any) -> None:
        address = address_of(agent)
        agent.server.shutdown()
        agent.server.server_close()
        node = declared_node(endpoint=address, node_id="nod_off_01")
        outcome = NodeDispatch(
            nodes=[node], privacy_tier=PrivacyTier.RESTRICTED, token=TOKEN, timeout=2.0
        ).execute(request())

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "node_unreachable"
        assert outcome.errors[0].error_class is ErrorClass.RETRYABLE_FAILURE
        assert outcome.executed_on == "nod_off_01", "the answer still says where the work was sent"


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
        outcome = NodeDispatch(
            nodes=[cloud], privacy_tier=PrivacyTier.STRICT_LOCAL, token=TOKEN
        ).execute(request())

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "no_node_fits"
        assert "nod_server_01" in outcome.errors[0].message
        assert "strict-local" in outcome.errors[0].message
        assert "cloud" in outcome.errors[0].message

    def test_a_strict_local_project_is_not_sent_to_an_open_network(self) -> None:
        outcome = NodeDispatch(
            nodes=[self.open_node()], privacy_tier=PrivacyTier.STRICT_LOCAL, token=TOKEN
        ).execute(request())

        assert outcome.errors[0].code == "no_node_fits"
        assert "strict-local" in outcome.errors[0].message
        assert "network is closed" in outcome.errors[0].message
        assert "open" in outcome.errors[0].message

    def test_a_strict_local_project_runs_on_a_local_closed_machine(self, agent: Any) -> None:
        node = declared_node(endpoint=address_of(agent), node_id="nod_local_01")
        outcome = NodeDispatch(
            nodes=[node], privacy_tier=PrivacyTier.STRICT_LOCAL, token=TOKEN
        ).execute(request())

        assert outcome.status is ToolResultStatus.SUCCESS

    def test_a_hgr_project_is_not_sent_to_an_open_network(self) -> None:
        outcome = NodeDispatch(
            nodes=[self.open_node(position=NodePosition.PRIVATE)],
            privacy_tier=PrivacyTier.HGR_CONTROLLED,
            token=TOKEN,
        ).execute(request())

        assert outcome.errors[0].code == "no_node_fits"
        assert "human-genetic-resource" in outcome.errors[0].message
        assert "network is closed" in outcome.errors[0].message

    def test_a_hgr_project_runs_where_the_network_is_closed(self, agent: Any) -> None:
        node = declared_node(
            endpoint=address_of(agent), position=NodePosition.PRIVATE, node_id="nod_private_01"
        )
        outcome = NodeDispatch(
            nodes=[node], privacy_tier=PrivacyTier.HGR_CONTROLLED, token=TOKEN
        ).execute(request())

        assert outcome.status is ToolResultStatus.SUCCESS

    def test_a_restricted_project_may_still_use_an_open_machine(self, agent: Any) -> None:
        node = self.open_node(endpoint=address_of(agent), node_id="nod_open_01")
        outcome = NodeDispatch(
            nodes=[node], privacy_tier=PrivacyTier.RESTRICTED, token=TOKEN
        ).execute(request())

        assert outcome.status is ToolResultStatus.SUCCESS


class TestRefusals:
    def test_a_machine_that_cannot_hold_the_tool_is_not_chosen(self) -> None:
        tiny = declared_node(capabilities=NodeCapabilities(cpu=2, memory_mb=2048))
        outcome = NodeDispatch(
            nodes=[tiny], privacy_tier=PrivacyTier.RESTRICTED, token=TOKEN
        ).execute(request())

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "no_node_fits"
        #: A refusal parks the run for the person who can add a machine, rather than burning a retry.
        assert outcome.errors[0].error_class is ErrorClass.NEEDS_INPUT
        assert "nod_server_01 (lab server)" in outcome.errors[0].message
        assert "the tool wants 4 cpu, the node has 2" in outcome.errors[0].message

    def test_a_machine_that_cannot_hold_the_cage_is_not_chosen(self) -> None:
        """The plan's budget is what a person approved; a machine that covers only the tool has not
        been promised it can hold the attempt."""
        outcome = NodeDispatch(
            nodes=[declared_node()], privacy_tier=PrivacyTier.RESTRICTED, token=TOKEN
        ).execute(request(cage=cage(cpu=16)))

        assert outcome.errors[0].code == "no_node_fits"
        assert "the attempt is allowed 16 cpu, the node has 8" in outcome.errors[0].message

    def test_a_machine_with_no_endpoint_is_not_chosen(self) -> None:
        outcome = NodeDispatch(
            nodes=[declared_node(endpoint=None)], privacy_tier=PrivacyTier.RESTRICTED, token=TOKEN
        ).execute(request())

        assert outcome.errors[0].code == "no_node_fits"
        assert "declares no endpoint" in outcome.errors[0].message

    def test_every_refused_machine_is_named_with_its_own_reason(self) -> None:
        nodes = [
            declared_node(node_id="nod_tiny", capabilities=NodeCapabilities(cpu=2, memory_mb=2048)),
            declared_node(node_id="nod_unreachable", endpoint=None),
        ]
        message = NodeDispatch(
            nodes=nodes, privacy_tier=PrivacyTier.RESTRICTED, token=TOKEN
        ).execute(request()).errors[0].message

        assert "nod_tiny" in message and "the tool wants 4 cpu" in message
        assert "nod_unreachable" in message and "declares no endpoint" in message

    def test_a_deployment_that_holds_no_credential_refuses_instead_of_running_here(self) -> None:
        outcome = NodeDispatch(
            nodes=[declared_node()], privacy_tier=PrivacyTier.RESTRICTED, token=None
        ).execute(request())

        assert outcome.errors[0].code == "node_credential_missing"
        assert outcome.errors[0].error_class is ErrorClass.NEEDS_INPUT
        assert "no credential" in outcome.errors[0].message

    def test_a_dispatcher_with_no_declared_machine_refuses(self) -> None:
        outcome = NodeDispatch(
            nodes=[], privacy_tier=PrivacyTier.RESTRICTED, token=TOKEN
        ).execute(request())

        assert outcome.errors[0].code == "no_node_declared"
        assert outcome.errors[0].error_class is ErrorClass.NEEDS_INPUT
