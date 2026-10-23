"""The backends a node may run on (plan V2.0 sections 3.3, 4.2, 9.6).

Section 3.3 makes the state machine the only truth about a run, and asks every callback to be
idempotent and correlated by ``run_id + node_id + attempt``. The seam that describes such a callback
lives in ``bioadapt_tool_registry.execution``; this module answers it for the case where the work
runs in this process, which is what the platform does today.

The answer travels as bytes. A request that carried references into this process's memory could not
be handed to a laptop or a lab server unchanged, and a product that came back as an index row could
not be stored by a machine that holds no index (§4.2). So the backend hands back the product's own
canonical bytes, and the control plane stores exactly those - the asset's digest is the digest of
the work that ran, not of a second serialization of it (§5.6).
"""

from __future__ import annotations

import importlib.metadata
import platform
from datetime import UTC, datetime
from typing import Callable, Optional

from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_tool_registry.execution import (
    ExecutionInput,
    ExecutionOutcome,
    ExecutionOutput,
    ExecutionRequest,
)

from .implementation import Invocation, ToolInput, execute
from .products import Product, read_product


def worker_runtime() -> str:
    """What this process actually is: the reference worker and the interpreter running it.

    A tool declares the image it is deployed in, but this backend runs the reference steps from an
    installed package, and an answer that named the image would be describing a container that never
    ran (§9.4).
    """
    try:
        version = importlib.metadata.version("bioadapt-scrnaseq")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - every deployment installs it
        version = "unversioned"
    return f"bioadapt-scrnaseq {version} on python {platform.python_version()}"


def asset_id_for_bytes(payload: bytes) -> str:
    """The asset id a payload deserves, named by its own bytes (§5.2).

    The control plane derives the same id from the bytes it stores, so an asset is named the same
    whoever computed it - and a node that recomputed something that already exists resolves to the
    same asset instead of a second copy of it.
    """
    return f"as_{sha256_hex(payload)[:12]}"


class _CollectingSink:
    """Takes what a step produced and keeps it, without deciding where it is stored.

    The runner owns storage, so a product leaves here as bytes rather than as a row in the index: a
    node running on another machine has no catalog to write to, and a control plane that stores what
    a node sent is the only way the two agree on what ran.
    """

    def __init__(self) -> None:
        self.products: list[Product] = []

    def write(self, product: Product) -> str:
        self.products.append(product)
        return asset_id_for_bytes(product.json_bytes())


class InProcessBackend:
    """Runs a node in this process, behind the seam (§3.3).

    The step itself is the reference implementation, so this backend changes where the work is
    *described* and not what it computes: the bytes that come back are the bytes the step wrote, and
    the result says the attempt was made for the run, node and attempt that asked for it.
    """

    def __init__(self, *, clock: Optional[Callable[[], datetime]] = None) -> None:
        self.clock = clock or (lambda: datetime.now(UTC))

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        invocation = Invocation(
            run_id=request.run_id,
            node_id=request.node_id,
            project_id=request.project_id,
            spec=request.spec,
            inputs=tuple(_tool_input(item) for item in request.inputs),
            parameters=dict(request.parameters),
        )
        sink = _CollectingSink()
        started_at = self.clock()
        result = execute(invocation, sink, started_at=started_at, finished_at=self.clock())
        outputs = []
        for product in sink.products:
            payload = product.json_bytes()
            outputs.append(
                ExecutionOutput(
                    slot=product.slot,
                    schema_version=product.schema_version,
                    sha256=sha256_hex(payload),
                    content=payload,
                )
            )
        return ExecutionOutcome(
            run_id=request.run_id,
            node_id=request.node_id,
            attempt=request.attempt,
            status=result.status,
            result=result,
            outputs=outputs,
            started_at=started_at,
            finished_at=self.clock(),
            runtime=worker_runtime(),
        )


def _tool_input(item: ExecutionInput) -> ToolInput:
    """The input as the step reads it, insisting the bytes are the bytes the request named.

    A request that lost bytes on the way here would otherwise be read as a matrix with holes in it.
    The digest named is the one the index holds for the asset, which is not necessarily the one the
    platform's own serializer would produce for it: a file a person uploaded is stored as it arrived
    (§5.2), and the step reads the product out of it rather than out of a re-serialization.
    """
    if sha256_hex(item.content) != item.sha256:
        raise ValueError(f"input {item.slot!r} does not hash to the digest the request named")
    product = read_product(item.schema_version, item.content)
    return ToolInput(slot=item.slot, asset_id=item.asset_id, product=product)
