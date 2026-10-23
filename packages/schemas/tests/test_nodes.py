from datetime import datetime

import pytest
from pydantic import ValidationError

from bioadapt_schemas.nodes import (
    ExecutionNode,
    NodeCapabilities,
    NodeDataAccess,
    NodeNetworkMode,
    NodePosition,
    node_reaches,
)

NOW = datetime(2026, 10, 23, 9, 0, 0)


def capabilities(**overrides) -> NodeCapabilities:
    fields = {"cpu": 8, "memory_mb": 16384, "can_run_containers": True}
    fields.update(overrides)
    return NodeCapabilities(**fields)


def node(**overrides) -> ExecutionNode:
    fields = {
        "node_id": "nod_laptop_01",
        "label": "analyst laptop",
        "position": NodePosition.LOCAL,
        "capabilities": capabilities(),
        "registered_by": "user_admin_01",
        "registered_at": NOW,
    }
    fields.update(overrides)
    return ExecutionNode(**fields)


class TestCapabilities:
    def test_hosts_belong_to_an_allowlisted_node(self) -> None:
        with pytest.raises(ValidationError):
            capabilities(network_mode=NodeNetworkMode.CLOSED, allowed_hosts=["oss.example.com"])

    def test_a_closed_node_may_name_no_hosts(self) -> None:
        assert capabilities(network_mode=NodeNetworkMode.CLOSED).allowed_hosts == []


class TestReaches:
    def test_an_oss_bucket_is_reached_by_bucket_not_by_key(self) -> None:
        machine = node(capabilities=capabilities(data_access=NodeDataAccess(oss_buckets=["seq-raw"])))
        assert node_reaches(machine, "oss://seq-raw/run1/matrix.h5ad")
        assert not node_reaches(machine, "oss://other-bucket/run1/matrix.h5ad")

    def test_a_local_path_is_reached_by_the_directory_that_holds_it(self) -> None:
        machine = node(capabilities=capabilities(data_access=NodeDataAccess(local_paths=["/data/seq"])))
        assert node_reaches(machine, "file:///data/seq/run1/matrix.h5ad")
        assert not node_reaches(machine, "file:///data/other/run1/matrix.h5ad")

    def test_a_public_source_is_reached_by_its_name(self) -> None:
        machine = node(capabilities=capabilities(data_access=NodeDataAccess(public_sources=["cellxgene"])))
        assert node_reaches(machine, "accession://cellxgene/8f2c1d")
        assert not node_reaches(machine, "accession://geo/GSE12345")

    def test_a_location_the_node_cannot_reach_is_not_reached(self) -> None:
        assert not node_reaches(node(), "oss://seq-raw/run1/matrix.h5ad")
