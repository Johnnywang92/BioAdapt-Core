"""ToolSpec contract (plan V2.0 section 9.4)."""

from __future__ import annotations

import re
from datetime import date
from typing import Optional

from pydantic import Field, model_validator

from bioadapt_schemas.entities import ImmutableContract
from bioadapt_schemas.enums import GenomeIdSystem, LicenseReviewStatus, ReproducibilityLevel, ToolClass

from .enums import UNPINNED_VERSIONS, AssetScope, FailurePolicy, NetworkMode

REQUIRED_PROVENANCE_FIELDS = ("input_hashes", "parameters", "container_image_digest", "tool_version")
_VERSION_LIKE = re.compile(r"[0-9]")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class SchemaRef(ImmutableContract):
    name: str
    version: str


class ContainerImage(ImmutableContract):
    repository: str
    tag: str
    digest: str

    @model_validator(mode="after")
    def _pinned_by_digest(self) -> "ContainerImage":
        if not _DIGEST.match(self.digest):
            raise ValueError("container image must be pinned by a sha256 digest")
        if self.tag.lower() in UNPINNED_VERSIONS:
            raise ValueError(f"floating image tag {self.tag!r} is not allowed")
        return self


class NetworkPolicy(ImmutableContract):
    mode: NetworkMode = NetworkMode.CLOSED
    allowed_hosts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent_hosts(self) -> "NetworkPolicy":
        if self.mode is NetworkMode.CLOSED:
            if self.allowed_hosts:
                raise ValueError("closed network policy cannot declare allowed hosts")
            return self
        if not self.allowed_hosts:
            raise ValueError("allowlist network policy requires at least one host")
        for host in self.allowed_hosts:
            if host == "*" or "://" in host or "/" in host or "*" in host:
                raise ValueError(f"network allowlist entry must be an exact host, got {host!r}")
        return self


class DataAccessPolicy(ImmutableContract):
    asset_scope: AssetScope = AssetScope.PROJECT
    reads_only_declared_inputs: bool = True
    writes_only_declared_outputs: bool = True
    secrets_readable: bool = False

    @model_validator(mode="after")
    def _isolation_invariants(self) -> "DataAccessPolicy":
        if not self.reads_only_declared_inputs:
            raise ValueError("a tool may not read beyond its declared inputs")
        if not self.writes_only_declared_outputs:
            raise ValueError("a tool may not write beyond its declared outputs")
        if self.secrets_readable:
            raise ValueError("a tool may never read platform credentials or secrets")
        if self.asset_scope is not AssetScope.PROJECT:
            raise ValueError("tool asset scope is project-scoped; cross-project reuse needs a usage_scope decision")
        return self


class ResourceProfile(ImmutableContract):
    cpu: int = Field(ge=1)
    memory_mb: int = Field(ge=1)
    gpu: int = Field(ge=0, default=0)
    max_minutes: int = Field(ge=1)
    measured_peak_memory_mb: Optional[int] = Field(None, ge=1)
    measured_on: Optional[str] = None


class LicenseInfo(ImmutableContract):
    spdx_id: str
    display_name: str
    review_status: LicenseReviewStatus = LicenseReviewStatus.PENDING
    reviewed_at: Optional[date] = None
    note: Optional[str] = None

    @model_validator(mode="after")
    def _review_evidence(self) -> "LicenseInfo":
        if self.review_status is LicenseReviewStatus.APPROVED and self.reviewed_at is None:
            raise ValueError("approved licence needs a review date")
        return self


class ReferenceCompatibility(ImmutableContract):
    requires_counts: Optional[bool] = None
    id_system: Optional[GenomeIdSystem] = None
    needs_log: Optional[bool] = None
    embedding_source: Optional[str] = None


class ToolSpec(ImmutableContract):
    tool_name: str
    tool_version: str
    task: str
    tool_class: ToolClass
    supported_modalities: list[str]
    supported_species: list[str]
    input_schema: SchemaRef
    output_schema: SchemaRef
    container_image: ContainerImage
    resources: ResourceProfile
    gpu_required: bool = False
    data_access: DataAccessPolicy = Field(default_factory=DataAccessPolicy)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    egress_allowlist: list[str] = Field(default_factory=list)
    quality_checks: list[str] = Field(default_factory=list)
    failure_policy: FailurePolicy = FailurePolicy.HALT
    retry_limit: int = Field(ge=0, default=0)
    provenance_fields: list[str] = Field(default_factory=list)
    reproducibility_level_target: ReproducibilityLevel = ReproducibilityLevel.CONCLUSION
    license: LicenseInfo
    assumptions: list[str] = Field(default_factory=list)
    invalid_when: list[str] = Field(default_factory=list)
    supported_tissue_notes: Optional[str] = None
    reference_compatibility: ReferenceCompatibility = Field(default_factory=ReferenceCompatibility)
    upstream_reference_sources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _version_and_egress(self) -> "ToolSpec":
        if self.tool_version.lower() in UNPINNED_VERSIONS or not _VERSION_LIKE.search(self.tool_version):
            raise ValueError(f"tool_version {self.tool_version!r} is not a pinned version")
        if self.gpu_required and self.resources.gpu < 1:
            raise ValueError("gpu_required is inconsistent with a zero-GPU resource profile")
        if self.network.mode is NetworkMode.CLOSED and self.egress_allowlist:
            raise ValueError("a closed-network tool cannot declare an egress allowlist")
        unknown_egress = sorted(set(self.egress_allowlist) - set(self.network.allowed_hosts))
        if unknown_egress:
            raise ValueError(f"egress destinations outside the network allowlist: {unknown_egress}")
        if not self.supported_modalities:
            raise ValueError("a tool must declare the modalities it supports")
        return self

    def admission_violations(self) -> list[str]:
        """Checks that only a reviewed tool may be registered (sections 9.4, 9.6, 11.4)."""
        violations: list[str] = []
        if not self.assumptions:
            violations.append("assumptions are not documented")
        if not self.invalid_when:
            violations.append("failure scenarios (invalid_when) are not documented")
        if not self.quality_checks:
            violations.append("no quality checks declared")
        missing_provenance = sorted(set(REQUIRED_PROVENANCE_FIELDS) - set(self.provenance_fields))
        if missing_provenance:
            violations.append(f"provenance fields missing: {missing_provenance}")
        if self.license.review_status not in (LicenseReviewStatus.APPROVED, LicenseReviewStatus.NOT_REQUIRED):
            violations.append(f"licence review is {self.license.review_status}")
        if self.resources.measured_peak_memory_mb is None or not self.resources.measured_on:
            violations.append("peak memory has not been measured on a named dataset")
        compat = self.reference_compatibility
        if compat.requires_counts is None:
            violations.append("input state (counts vs normalised) is not declared")
        if compat.id_system is None:
            violations.append("gene id system is not declared")
        if not self.supported_species:
            violations.append("supported species is empty")
        if "*" in self.supported_species and not self.supported_tissue_notes:
            violations.append("a wildcard species claim needs a scope note")
        return violations
