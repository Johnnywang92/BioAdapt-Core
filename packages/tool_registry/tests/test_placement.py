from bioadapt_schemas.enums import PrivacyTier, ToolClass
from bioadapt_schemas.nodes import ExecutionNode, NodeCapabilities, NodeNetworkMode, NodePosition
from bioadapt_tool_registry import NetworkPolicy
from bioadapt_tool_registry.enums import NetworkMode
from bioadapt_tool_registry.placement import node_allows_tier, node_fits, select_nodes

from registry_factories import NOW, adhoc_spec, complete_spec


def machine(**overrides) -> ExecutionNode:
    fields = {
        "node_id": "nod_server_01",
        "label": "lab server",
        "position": NodePosition.LOCAL,
        "capabilities": NodeCapabilities(cpu=16, memory_mb=65536, can_run_containers=True),
        "registered_by": "user_admin_01",
        "registered_at": NOW,
    }
    fields.update(overrides)
    return ExecutionNode(**fields)


class TestResources:
    def test_a_node_with_room_fits(self) -> None:
        assert node_fits(machine(), complete_spec()) == []

    def test_a_node_short_of_memory_says_so(self) -> None:
        reasons = node_fits(machine(capabilities=NodeCapabilities(cpu=16, memory_mb=4096)), complete_spec())
        assert any("16384MB" in reason for reason in reasons)

    def test_a_node_short_of_cpu_says_so(self) -> None:
        reasons = node_fits(machine(capabilities=NodeCapabilities(cpu=1, memory_mb=65536)), complete_spec())
        assert any("cpu" in reason for reason in reasons)


class TestAdhoc:
    def test_an_adhoc_tool_needs_a_container_runtime(self) -> None:
        reasons = node_fits(machine(capabilities=NodeCapabilities(cpu=16, memory_mb=65536)), adhoc_spec())
        assert any("container" in reason for reason in reasons)

    def test_a_registered_tool_does_not(self) -> None:
        reasons = node_fits(
            machine(capabilities=NodeCapabilities(cpu=16, memory_mb=65536)), complete_spec(tool_class=ToolClass.REGISTERED)
        )
        assert not any("container" in reason for reason in reasons)


class TestNetwork:
    def test_a_closed_tool_runs_on_a_closed_node(self) -> None:
        closed = machine(capabilities=NodeCapabilities(cpu=16, memory_mb=65536, network_mode=NodeNetworkMode.CLOSED))
        assert node_fits(closed, complete_spec(network=NetworkPolicy(mode=NetworkMode.CLOSED))) == []

    def test_a_tool_that_needs_a_route_does_not_fit_a_closed_node(self) -> None:
        closed = machine(capabilities=NodeCapabilities(cpu=16, memory_mb=65536, network_mode=NodeNetworkMode.CLOSED))
        reasons = node_fits(closed, complete_spec(network=NetworkPolicy(mode=NetworkMode.ALLOWLIST, allowed_hosts=["x"])))
        assert any("outbound" in reason for reason in reasons)

    def test_an_allowlisted_node_refuses_a_host_it_does_not_allow(self) -> None:
        allowed = machine(
            capabilities=NodeCapabilities(
                cpu=16,
                memory_mb=65536,
                network_mode=NodeNetworkMode.ALLOWLIST,
                allowed_hosts=["oss.example.com"],
            )
        )
        reasons = node_fits(allowed, complete_spec(network=NetworkPolicy(mode=NetworkMode.ALLOWLIST, allowed_hosts=["other.example.com"])))
        assert any("other.example.com" in reason for reason in reasons)

    def test_a_wildcard_covers_the_host_under_it(self) -> None:
        allowed = machine(
            capabilities=NodeCapabilities(
                cpu=16,
                memory_mb=65536,
                network_mode=NodeNetworkMode.ALLOWLIST,
                allowed_hosts=["*.example.com"],
            )
        )
        reasons = node_fits(allowed, complete_spec(network=NetworkPolicy(mode=NetworkMode.ALLOWLIST, allowed_hosts=["oss.example.com"])))
        assert reasons == []

    def test_an_open_node_covers_an_allowlisted_tool(self) -> None:
        open_node = machine(
            capabilities=NodeCapabilities(cpu=16, memory_mb=65536, network_mode=NodeNetworkMode.OPEN)
        )
        reasons = node_fits(open_node, complete_spec(network=NetworkPolicy(mode=NetworkMode.ALLOWLIST, allowed_hosts=["x.example.com"])))
        assert reasons == []


class TestTier:
    """§4.2, §4.4: the project's promise about its bytes is a placement rule, not a note."""

    def test_a_strict_local_project_may_not_land_in_the_cloud(self) -> None:
        reasons = node_allows_tier(machine(position=NodePosition.CLOUD), PrivacyTier.STRICT_LOCAL)
        assert any("cloud" in reason for reason in reasons)

    def test_a_strict_local_project_needs_a_closed_network(self) -> None:
        open_node = machine(
            capabilities=NodeCapabilities(cpu=16, memory_mb=65536, network_mode=NodeNetworkMode.OPEN)
        )
        reasons = node_allows_tier(open_node, PrivacyTier.STRICT_LOCAL)
        assert any("network is closed" in reason for reason in reasons)

    def test_a_strict_local_project_runs_on_a_local_closed_machine(self) -> None:
        assert node_allows_tier(machine(), PrivacyTier.STRICT_LOCAL) == []

    def test_a_strict_local_project_may_use_a_private_closed_machine(self) -> None:
        assert node_allows_tier(machine(position=NodePosition.PRIVATE), PrivacyTier.STRICT_LOCAL) == []

    def test_a_hgr_project_needs_a_closed_network(self) -> None:
        open_node = machine(
            position=NodePosition.PRIVATE,
            capabilities=NodeCapabilities(cpu=16, memory_mb=65536, network_mode=NodeNetworkMode.OPEN),
        )
        reasons = node_allows_tier(open_node, PrivacyTier.HGR_CONTROLLED)
        assert any("human-genetic-resource" in reason for reason in reasons)

    def test_a_hgr_project_runs_on_a_private_closed_machine(self) -> None:
        assert node_allows_tier(machine(position=NodePosition.PRIVATE), PrivacyTier.HGR_CONTROLLED) == []

    def test_restricted_and_public_tiers_are_not_held_to_the_network(self) -> None:
        open_node = machine(
            capabilities=NodeCapabilities(cpu=16, memory_mb=65536, network_mode=NodeNetworkMode.OPEN)
        )
        assert node_allows_tier(open_node, PrivacyTier.RESTRICTED) == []
        assert node_allows_tier(open_node, PrivacyTier.PUBLIC) == []


class TestSelection:
    def test_only_the_nodes_that_fit_are_selected(self) -> None:
        roomy = machine()
        tight = machine(node_id="nod_small_01", capabilities=NodeCapabilities(cpu=1, memory_mb=2048))
        assert [node.node_id for node in select_nodes([tight, roomy], complete_spec())] == ["nod_server_01"]
