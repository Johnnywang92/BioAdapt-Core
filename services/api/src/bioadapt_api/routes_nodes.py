"""The compute-node registry (plan V2.0 sections 4.2, 4.4, 9.4, 9.6, 11.1).

A deployment runs its runs somewhere. This module is where that somewhere is written down: a node is a
machine, and what the platform holds about it is a *declaration* - what it has, where it can reach, and
who said so. A run is matched against that declaration (section 9.4) rather than against a named
machine, so one node, a laptop plus a server, and a rack of them answer the same question the same way.

The rows are platform-level: a node serves every project, so it is not one project's property and the
registry is read under the global scope, the way the identity directory is.

Nothing here says a machine exists. A registration is a claim, and the row says who made it - the
distinction that keeps "this deployment can run that" from being a promise nobody checked.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field

from bioadapt_policies.access import GLOBAL_SCOPE, Action
from bioadapt_schemas.entities import Contract
from bioadapt_schemas.nodes import ExecutionNode, NodeCapabilities, NodePosition, node_reaches
from bioadapt_scrnaseq.specs import reference_tool_specs
from bioadapt_tool_registry.placement import node_fits
from bioadapt_tool_registry.spec import ToolSpec

from .deps import ApiState, ClaimsDep, Session, StateDep, open_session, request_trace_id
from .errors import conflict, invalid, not_found_in_deployment
from .paging import Page, page_params
from .toolset import reference_deployment

router = APIRouter(tags=["nodes"])


class NodeRegistration(Contract):
    """What a person declares about a machine.

    Who declared it and when are not request fields: they are the session's, the same way a grant's
    approver is the person who pressed the button rather than whoever typed the name. The endpoint is
    a request field because it is a fact about the machine - but the credential that opens it is not
    here: a secret does not belong in a row anybody can read.
    """

    node_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    position: NodePosition
    capabilities: NodeCapabilities
    endpoint: Optional[str] = None
    notes: Optional[str] = None


def _platform_session(request: Request, state: ApiState, claims) -> Session:
    """A session over the registry: the global scope, so the machines are one list."""
    return open_session(state, claims, GLOBAL_SCOPE, request_trace_id(request))


def _registered_specs() -> dict[str, ToolSpec]:
    """The registered reference tools, keyed by name. A node's fitness is asked about a real tool."""
    return reference_tool_specs(reference_deployment(None))


def _named_spec(query: str) -> ToolSpec:
    """The tool a caller named as ``name@version``."""
    name, _, version = query.partition("@")
    if not name or not version:
        raise invalid("can_run", "name a registered tool as tool_name@tool_version")
    for spec in _registered_specs().values():
        if spec.tool_name == name and spec.tool_version == version:
            return spec
    raise not_found_in_deployment("tool", query)


def _node_body(node: ExecutionNode, *, spec: Optional[ToolSpec], location: Optional[str]) -> dict:
    body = node.model_dump(mode="json")
    if spec is not None:
        reasons = node_fits(node, spec)
        body["fits"] = not reasons
        #: The reasons a machine was refused, in the words a person can act on. "This cannot run that"
        #: with no reason is a dead end; a reason is the next step.
        body["unmet_requirements"] = reasons
    if location is not None:
        body["reaches"] = node_reaches(node, location)
    return body


@router.post("/nodes", status_code=201)
def register_node(body: NodeRegistration, request: Request, state: StateDep, claims: ClaimsDep) -> dict:
    """Declare a machine that can run runs.

    A node is a claim about the deployment, so it is somebody's act: a human, with a second factor,
    audited the way every other permission change is. The row records the declarer, and the record
    does not pretend the machine has been seen - only that somebody said so.
    """
    session = _platform_session(request, state, claims)
    session.enforce(Action.NODE_REGISTER, body.node_id, "execution_node")
    try:
        session.catalog.get_node(body.node_id)
    except LookupError:
        pass
    else:
        raise conflict(f"node {body.node_id} is already declared", field="node_id")
    node = ExecutionNode(
        node_id=body.node_id,
        label=body.label,
        position=body.position,
        capabilities=body.capabilities,
        endpoint=body.endpoint,
        registered_by=session.actor.subject_id,
        registered_at=session.clock(),
        notes=body.notes,
    )
    session.catalog.register_node(node)
    session.audit(
        "node:register",
        node.node_id,
        details={
            "label": node.label,
            "position": str(node.position),
            "capabilities": node.capabilities.model_dump(mode="json"),
        },
    )
    return node.model_dump(mode="json")


@router.get("/nodes")
def list_nodes(
    request: Request,
    state: StateDep,
    claims: ClaimsDep,
    can_run: Optional[str] = Query(None, description="only the nodes this registered tool fits on"),
    reaches: Optional[str] = Query(None, description="only the nodes that can fetch these bytes"),
    page: Page = Depends(page_params),
) -> dict:
    """The machines this deployment claims, and what each of them can do.

    The two filters are the two questions that decide where a run goes: does this machine fit the
    tool, and can it reach the data. A filtered list says which ones; the node's own page says why the
    others were not on it.

    The filters run before the page is cut, not after: a page cut first would answer "no machine here
    fits" with an empty page while the machine that fits sits on the next one.
    """
    session = _platform_session(request, state, claims)
    session.enforce(Action.NODE_READ, "execution_node:list", "execution_node")
    spec = _named_spec(can_run) if can_run else None
    nodes = session.catalog.list_nodes()
    if spec is not None:
        nodes = [node for node in nodes if not node_fits(node, spec)]
    if reaches is not None:
        nodes = [node for node in nodes if node_reaches(node, reaches)]
    window = nodes[page.offset : page.offset + page.limit]
    return page.envelope([node.model_dump(mode="json") for node in window])


@router.get("/nodes/{node_id}")
def read_node(
    node_id: str,
    request: Request,
    state: StateDep,
    claims: ClaimsDep,
    can_run: Optional[str] = Query(None, description="say whether this node fits that tool, and why not"),
    reaches: Optional[str] = Query(None, description="say whether this node can fetch these bytes"),
) -> dict:
    """One declared machine, and what it can and cannot do."""
    session = _platform_session(request, state, claims)
    session.enforce(Action.NODE_READ, node_id, "execution_node")
    try:
        node = session.catalog.get_node(node_id)
    except LookupError as exc:
        raise not_found_in_deployment("compute_node", node_id) from exc
    spec = _named_spec(can_run) if can_run else None
    return _node_body(node, spec=spec, location=reaches)
