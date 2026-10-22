"""The tool set this deployment can execute, assembled in one place (sections 4.1, 9.4, 9.6).

A plan names tools by name and version, a run pins a registry snapshot, and the screen that offers both
has to read them from the same construction the executor uses. A second copy of that construction is how
a wizard comes to offer a tool the engine then refuses, with nothing on screen to explain the difference.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Optional

from bioadapt_schemas.enums import ToolClass
from bioadapt_schemas.plan import CONCLUSION_OUTPUTS
from bioadapt_scrnaseq.implementation import TASK_INPUT_SLOTS, TASK_RUNNERS
from bioadapt_scrnaseq.specs import Deployment, reference_tool_specs
from bioadapt_tool_registry.registry import ToolRegistry
from bioadapt_tool_registry.spec import ToolSpec

#: The registration facts the reference steps are deployed under. Appendix C1 keeps these as deployment
#: inputs rather than code defaults, so this is the reference build's own answer - container digest,
#: measured peak and licence review date included - and the only place it is written.
REFERENCE_CONTAINER = "registry.internal/bioadapt/scrnaseq-reference"
REFERENCE_TOOL_VERSION = "1.0.0"
REFERENCE_MEASURED_ON = "synthetic_pbmc_v1"


def reference_deployment(species: Optional[str]) -> Deployment:
    return Deployment(
        container_repository=REFERENCE_CONTAINER,
        container_tag=REFERENCE_TOOL_VERSION,
        container_digest="sha256:" + "ab" * 32,
        tool_version=REFERENCE_TOOL_VERSION,
        species=(species or "human",),
        cpu=2,
        memory_mb=4096,
        max_minutes=60,
        measured_peak_memory_mb=2048,
        measured_on=REFERENCE_MEASURED_ON,
        license_spdx_id="BSD-3-Clause",
        license_display_name="Scanpy reference steps",
        license_reviewed_at=date(2026, 5, 4),
        license_review_note="reviewed against licence matrix",
    )


def reference_registry(catalog: Any) -> ToolRegistry:
    """What the executor hydrates for this project: the registered reference steps plus live grants."""
    registry = ToolRegistry()
    for spec in reference_tool_specs(reference_deployment(catalog.get_project().species)).values():
        registry.register(spec)
    for grant in catalog.list_tool_grants(live_only=True):
        registry.admit_grant(grant)
    return registry


def _minimum_memory_gb(spec: ToolSpec) -> int:
    """The smallest budget that does not kill the step mid-run (§9.4 measured peak)."""
    measured = spec.resources.measured_peak_memory_mb or spec.resources.memory_mb
    return max(1, math.ceil(measured / 1024))


def tool_row(spec: ToolSpec) -> dict[str, Any]:
    runnable = spec.task in TASK_RUNNERS
    return {
        "tool_name": spec.tool_name,
        "tool_version": spec.tool_version,
        "tool_class": str(spec.tool_class),
        "task": spec.task,
        "runnable": runnable,
        "input_slots": [{"slot": slot, "required": required} for slot, required in TASK_INPUT_SLOTS.get(spec.task, ())],
        "output_slots": list(TASK_RUNNERS[spec.task][1]) if runnable else [],
        #: A plan node has to request at least this much, or the driver refuses it as an under-budget (§9.2).
        "min_resources": {
            "cpu": spec.resources.cpu,
            "memory_gb": _minimum_memory_gb(spec),
            "gpu": 1 if spec.gpu_required else spec.resources.gpu,
            "max_minutes": spec.resources.max_minutes,
        },
        "measured_peak_memory_mb": spec.resources.measured_peak_memory_mb,
        "measured_on": spec.resources.measured_on,
        "quality_checks": list(spec.quality_checks),
        "assumptions": list(spec.assumptions),
        "invalid_when": list(spec.invalid_when),
        "input_schema": spec.input_schema.model_dump(mode="json"),
        "output_schema": spec.output_schema.model_dump(mode="json"),
        "reference_compatibility": spec.reference_compatibility.model_dump(mode="json"),
        "network_mode": str(spec.network.mode),
        "egress_allowlist": list(spec.egress_allowlist),
        "failure_policy": str(spec.failure_policy),
        "retry_limit": spec.retry_limit,
        "reproducibility_level_target": str(spec.reproducibility_level_target),
        "license": spec.license.model_dump(mode="json"),
        #: The plan contract refuses an ungated ad-hoc node and an ungated conclusion node (§9.6, §11.6),
        #: so the wizard is shown the same requirement rather than being allowed to build an invalid node.
        "requires_approval": spec.tool_class is ToolClass.PROJECT_ADHOC,
        "privacy_policy": "local_only",
    }


def registry_body(registry: ToolRegistry, *, now: Optional[Any] = None) -> dict[str, Any]:
    return {
        #: The value a launch has to name, and the value execution is checked against (§4.1).
        "snapshot_id": registry.snapshot_id(),
        "tools": [tool_row(spec) for spec in registry.select(include_adhoc=True, now=now)],
        "conclusion_outputs": sorted(CONCLUSION_OUTPUTS),
    }
