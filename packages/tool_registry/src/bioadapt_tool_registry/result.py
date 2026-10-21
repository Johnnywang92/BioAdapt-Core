"""ToolResult contract (plan V2.0 sections 9.4, 9.6, 10.1-L2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field, model_validator

from bioadapt_schemas.entities import ImmutableContract, require_hex64
from bioadapt_schemas.enums import ReproducibilityLevel, ScreeningResult, ToolClass
from bioadapt_schemas.errors import ApiError

from .enums import ToolResultStatus
from .registry import RegistryError, ToolRegistry
from .spec import ToolSpec


class ValidationOutcome(ImmutableContract):
    name: str
    result: ScreeningResult
    detail: Optional[str] = None


class ToolExecution(ImmutableContract):
    container_image_digest: str
    tool_version: str
    parameters_sha256: str
    seed: Optional[int] = None

    @model_validator(mode="after")
    def _digests(self) -> "ToolExecution":
        require_hex64(self.container_image_digest.removeprefix("sha256:"), "container image digest")
        require_hex64(self.parameters_sha256, "parameters hash")
        return self


class ToolResult(ImmutableContract):
    run_id: str
    node_id: str
    project_id: str
    tool_name: str
    tool_version: str
    status: ToolResultStatus
    execution: ToolExecution
    input_asset_ids: list[str] = Field(min_length=1)
    input_hashes: dict[str, str]
    parameters: dict[str, Any] = Field(default_factory=dict)
    output_asset_ids: list[str] = Field(default_factory=list)
    metrics: dict[str, float | int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[ApiError] = Field(default_factory=list)
    validation_results: list[ValidationOutcome] = Field(default_factory=list)
    reproducibility_level_achieved: Optional[ReproducibilityLevel] = None
    unregistered: bool = False
    used_for_conclusion: bool = False
    reproduced_by_run_id: Optional[str] = None
    human_signed_by: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _consistent_result(self) -> "ToolResult":
        for asset_id in self.input_asset_ids:
            if asset_id not in self.input_hashes:
                raise ValueError(f"input {asset_id} has no recorded hash")
        for asset_id, digest in self.input_hashes.items():
            require_hex64(digest, f"input hash of {asset_id}")
        if self.execution.tool_version != self.tool_version:
            raise ValueError("execution.tool_version must match the result tool_version")
        failed_checks = [o.name for o in self.validation_results if o.result is ScreeningResult.FAIL]
        if self.status is ToolResultStatus.SUCCESS and failed_checks:
            raise ValueError(f"success result carries failed checks: {failed_checks}")
        if self.status in (ToolResultStatus.FAILED, ToolResultStatus.POLICY_BLOCKED) and not self.errors:
            raise ValueError("failed or blocked results must record at least one classified error")
        if self.status in (ToolResultStatus.SUCCESS, ToolResultStatus.PARTIAL) and not self.output_asset_ids:
            raise ValueError("a successful result must declare its output assets")
        if self.used_for_conclusion and not (self.reproduced_by_run_id or self.human_signed_by):
            raise ValueError("conclusion-bearing results need a registered reproduction or a human signature")
        return self

    def key(self) -> tuple[str, str]:
        return self.tool_name, self.tool_version


def attest(result: ToolResult, registry: ToolRegistry, *, now: datetime) -> ToolSpec:
    """Verify a result against the registry before its assets may be referenced by an evidence card."""
    spec = registry.resolve(result.tool_name, result.tool_version, now=now)
    if result.unregistered and spec.tool_class is not ToolClass.PROJECT_ADHOC:
        raise RegistryError(f"{spec.tool_name} {spec.tool_version} is registered; results must not claim unregistered")
    if not result.unregistered and spec.tool_class is ToolClass.PROJECT_ADHOC:
        raise RegistryError("ad-hoc results must be marked unregistered and flagged in the report")
    grant = registry.grant_of(result.tool_name, result.tool_version)
    if grant is not None:
        if grant.project_id != result.project_id:
            raise RegistryError("ad-hoc grant belongs to another project")
        if not grant.is_live(now):
            raise RegistryError("ad-hoc grant expired before this run")
    return spec
