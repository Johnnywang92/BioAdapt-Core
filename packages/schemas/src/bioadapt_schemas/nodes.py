"""Compute-node contracts (plan V2.0 sections 4.2, 4.4, 9.4, 9.6, 11.1).

A node is a machine that can run a run. What the platform holds about it is a *declaration*: what the
machine says it has, and where it can reach. The declaration is matched against what a tool needs
(section 9.4) rather than against a named machine, so one node, a laptop plus a server, and a rack of
them all answer "where can this run go" the same way.

Nothing here says the machine exists. A row is a claim about a machine, and the claim records who made
it - the same way a declared tool is a claim until it runs.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Optional
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .entities import ImmutableContract


class NodePosition(StrEnum):
    """Where the machine sits relative to the data it will touch (section 4.2's three domains)."""

    #: Inside the deployment's own perimeter: a laptop, a lab server, an on-premise box.
    LOCAL = "local"
    #: Inside a customer intranet (section 4.6, the fourth domain of section 4.2).
    PRIVATE = "private"
    #: On someone else's cloud, reachable only over the network.
    CLOUD = "cloud"


class NodeNetworkMode(StrEnum):
    """The node's outbound route, which decides whose data may land on it at all."""

    #: No outbound route. A strict-local or human-genetic-resource project can only land here.
    CLOSED = "closed"
    #: Outbound is limited to the hosts the declaration names.
    ALLOWLIST = "allowlist"
    #: Outbound is unrestricted. A node like this can never serve a strict-local project.
    OPEN = "open"


class NodeDataAccess(ImmutableContract):
    """Where this node can reach bytes without the control plane carrying them (section 9.7).

    The control plane holds metadata and digests; the bytes live wherever the data lives. These three
    lists are how a node says which of those places it can reach on its own.
    """

    #: Directories on the node that can hold staged bytes.
    local_paths: list[str] = Field(default_factory=list)
    #: Object-store buckets the node can pull from with credentials it holds itself.
    oss_buckets: list[str] = Field(default_factory=list)
    #: Public sources the node can download from, named by the source (``cellxgene``).
    public_sources: list[str] = Field(default_factory=list)


class NodeCapabilities(ImmutableContract):
    """What the machine has. The upper bound the platform may promise a run on it."""

    cpu: int = Field(ge=1)
    memory_mb: int = Field(ge=1)
    gpu: int = Field(ge=0, default=0)
    #: Whether a container runtime exists. Section 9.6 puts ad-hoc code in a non-privileged container,
    #: so a node without one cannot run an ad-hoc step at all.
    can_run_containers: bool = False
    network_mode: NodeNetworkMode = NodeNetworkMode.CLOSED
    #: The hosts an allowlisted node may reach. Meaningless unless ``network_mode`` is allowlist.
    allowed_hosts: list[str] = Field(default_factory=list)
    data_access: NodeDataAccess = Field(default_factory=NodeDataAccess)

    @model_validator(mode="after")
    def _hosts_belong_to_the_mode(self) -> "NodeCapabilities":
        if self.network_mode is not NodeNetworkMode.ALLOWLIST and self.allowed_hosts:
            raise ValueError("allowed_hosts belong to an allowlisted node")
        return self


class ExecutionNode(ImmutableContract):
    """One declared machine."""

    node_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    position: NodePosition
    capabilities: NodeCapabilities
    #: Where the machine's agent listens. A declaration that says nothing about how to reach the
    #: machine leaves the run with nowhere to go, so the endpoint is part of the claim - and only the
    #: endpoint: the credential that opens it belongs to the deployment, not to a row in the index.
    endpoint: Optional[str] = None
    #: Whether this machine is taking work. A machine that is switched off stays declared - its id,
    #: records and receipts remain resolvable - but no attempt is sent to it, and the refusal says so.
    enabled: bool = True
    #: Who made the claim. The row is not evidence that the machine is there.
    registered_by: str = Field(min_length=1)
    registered_at: datetime
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _endpoint_is_reachable(self) -> "ExecutionNode":
        if self.endpoint is None:
            return self
        problem = endpoint_problem(self.endpoint)
        if problem is not None:
            raise ValueError(problem)
        return self


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def endpoint_problem(endpoint: str) -> Optional[str]:
    """What is wrong with an endpoint address, if anything.

    Plain http carries both the attempt and the credential in the clear, so it is only acceptable on
    the loopback address - a machine talking to itself. A machine reached over a network has to be
    reached over https; a redirect is somebody else choosing the destination, so no address that
    redirects is treated as reachable.
    """
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return "a node endpoint must be an http or https address"
    if parts.scheme not in ("http", "https") or not parts.netloc.strip("/"):
        return "a node endpoint must be an http or https address"
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK_HOSTS:
        return "a node reached over a network must use https; plain http is only for the loopback address"
    return None


def _covers(declared: str, wanted: str) -> bool:
    """Whether a declared path holds a wanted one. A parent directory covers what is under it."""
    declared = declared.rstrip("/")
    wanted = wanted.rstrip("/")
    return wanted == declared or wanted.startswith(declared + "/")


def node_reaches(node: ExecutionNode, location: str) -> bool:
    """Whether this node can fetch the bytes at ``location`` without the control plane carrying them.

    Locations are written the way section 9.7 addresses bytes: ``oss://bucket/key``,
    ``file:///absolute/path`` and ``accession://source/id`` each name a place the node may already be
    able to reach on its own.
    """
    scheme, _, rest = location.partition("://")
    access = node.capabilities.data_access
    if scheme == "oss":
        bucket = rest.split("/", 1)[0]
        return bucket in access.oss_buckets
    if scheme == "file":
        return any(_covers(declared, rest) for declared in access.local_paths)
    if scheme == "accession":
        source = rest.split("/", 1)[0]
        return source in access.public_sources
    return False
