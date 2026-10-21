"""Core entities of plan V2.0 section 5.1."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    AlignmentKind,
    AnnotationStatus,
    AssetRole,
    CandidateType,
    FilteringState,
    GenomeIdSystem,
    LabelLevel,
    PairingMethod,
    PrivacyTier,
    RegulatoryProfile,
    ReplicateType,
    ReproducibilityLevel,
    RetentionClass,
    ReviewDecision,
    ReviewScope,
    Severity,
    UsageScope,
)
from .identifiers import canonical_cell_key, sha256_hex
from .thresholds import DEFAULT_THRESHOLDS, Thresholds


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImmutableContract(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True)


def require_hex64(value: str, label: str) -> str:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.lower()):
        raise ValueError(f"{label} must be a 64-char hex digest")
    return value


def sha256_of_json(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_hex(canonical.encode("utf-8"))


class Genome(ImmutableContract):
    build: str
    annotation_source: str
    version: str
    id_system: GenomeIdSystem


class Project(Contract):
    project_id: str
    study_goal: str
    species: str
    privacy_tier: PrivacyTier
    created_by: str
    regulatory_profile: list[RegulatoryProfile] = Field(default_factory=list)
    annotation_granularity_target: str
    review_load_budget_hours_per_week: float = Field(gt=0)
    thresholds: Thresholds = DEFAULT_THRESHOLDS
    created_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _hgr_implies_strictest_tier(self) -> "Project":
        if RegulatoryProfile.HGR in self.regulatory_profile and self.privacy_tier not in (
            PrivacyTier.HGR_CONTROLLED,
            PrivacyTier.STRICT_LOCAL,
        ):
            raise ValueError("human genetic resource projects cannot use public or restricted tier")
        return self


class Specimen(ImmutableContract):
    specimen_id: str
    project_id: str
    donor_id: str
    tissue: str
    site: Optional[str] = None
    timepoint: Optional[str] = None
    collection_event: Optional[str] = None


class Library(ImmutableContract):
    library_id: str
    specimen_id: str
    batch_id: str
    replicate_type: ReplicateType
    sequencing_run_id: Optional[str] = None


class Assay(ImmutableContract):
    assay_id: str
    library_id: str
    modality: str
    platform: str
    protocol: str
    genome: Genome


class Cell(ImmutableContract):
    specimen_id: str
    barcode: str
    modality_presence: dict[str, bool] = Field(default_factory=dict)
    cell_key: str = ""

    @model_validator(mode="before")
    @classmethod
    def _derive_cell_key(cls, data: Any) -> Any:
        if isinstance(data, dict) and "specimen_id" in data and "barcode" in data:
            data = dict(data)
            data["cell_key"] = data.get("cell_key") or canonical_cell_key(
                data["specimen_id"], data["barcode"], **_cell_key_options(data)
            )
        return data


def _cell_key_options(data: dict[str, Any]) -> dict[str, Any]:
    version = data.pop("barcode_normalization", None)
    return {"barcode_normalization": version} if version else {}


class CellPairing(ImmutableContract):
    cell_pairing_id: str
    run_id: str
    modality_a: str
    modality_b: str
    method: PairingMethod
    confidence: Optional[float] = Field(None, ge=0, le=1)
    failure_reason: Optional[str] = None

    @model_validator(mode="after")
    def _require_evidence_for_inference(self) -> "CellPairing":
        if self.method is PairingMethod.UNPAIRED:
            if not self.failure_reason:
                raise ValueError("unpaired cells require failure_reason")
        elif self.confidence is None:
            raise ValueError(f"{self.method} pairing requires confidence")
        return self


class UpstreamPipeline(ImmutableContract):
    name: str
    version: str
    reference_build: str
    reference_annotation_source: str


class DataAsset(ImmutableContract):
    asset_id: str
    project_id: str
    uri: str
    file_sha256: str
    content_fingerprint: str
    format: str
    schema_version: str
    size_bytes: int = Field(ge=0)
    privacy_tier: PrivacyTier
    usage_scope: UsageScope
    retention_class: RetentionClass
    filtering_state: FilteringState
    created_at: datetime
    upstream_pipeline: Optional[UpstreamPipeline] = None
    genome: Optional[Genome] = None
    created_by_run_id: Optional[str] = None

    @field_validator("file_sha256", "content_fingerprint")
    @classmethod
    def _digests(cls, value: str) -> str:
        return require_hex64(value, "digest")

    @model_validator(mode="after")
    def _require_upstream_metadata(self) -> "DataAsset":
        if self.filtering_state is FilteringState.UNKNOWN and self.upstream_pipeline is None:
            raise ValueError("unknown filtering state requires upstream pipeline metadata")
        return self


class AssetLineage(ImmutableContract):
    run_id: str
    node_id: str
    asset_id: str
    role: AssetRole
    ordinal: int = Field(ge=0)


class AnnotationVersion(ImmutableContract):
    """Section 5.7: the index row only - cell labels live in the immutable annotation asset."""

    version_id: str
    run_id: str
    asset_id: str
    label_column: str
    level: LabelLevel
    created_by: str
    status: AnnotationStatus
    superseded_by: Optional[str] = None

    @model_validator(mode="after")
    def _version_chain(self) -> "AnnotationVersion":
        if self.status is AnnotationStatus.SUPERSEDED and not self.superseded_by:
            raise ValueError("a superseded annotation version must point at its replacement")
        if self.status is AnnotationStatus.ACTIVE and self.superseded_by:
            raise ValueError("an active annotation version cannot point at a replacement")
        return self


class ModelInvocation(ImmutableContract):
    model_id: str
    purpose: str
    provider_snapshot: Optional[str] = None
    sampling_params: dict[str, Any] = Field(default_factory=dict)
    prompt_sha256: str
    output_sha256: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    policy_decision: str

    @field_validator("prompt_sha256", "output_sha256")
    @classmethod
    def _digests(cls, value: str) -> str:
        return require_hex64(value, "digest")


class RunManifest(Contract):
    run_id: str
    project_id: str
    plan_id: str
    plan_version: str
    tool_versions: dict[str, str]
    tool_registry_snapshot: str
    input_hashes: dict[str, str]
    reproducibility_level_target: ReproducibilityLevel
    parameters: dict[str, Any] = Field(default_factory=dict)
    model_invocations: list[ModelInvocation] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @field_validator("input_hashes")
    @classmethod
    def _hashes(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("run manifest requires at least one input hash")
        for asset_id, digest in value.items():
            require_hex64(digest, f"input hash of {asset_id}")
        return value


class AlignmentRecord(ImmutableContract):
    other_candidate_id: str
    other_run_id: str
    kind: AlignmentKind
    jaccard: float = Field(ge=0, le=1)
    carry_over: bool = False
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None


class CandidateSet(Contract):
    candidate_id: str
    run_id: str
    member_fingerprint: str
    size: int = Field(ge=1)
    donor_count: int = Field(ge=1)
    display_name: str
    cluster_origin: dict[str, Any]
    candidate_types: list[CandidateType] = Field(default_factory=list)
    alignment_history: list[AlignmentRecord] = Field(default_factory=list)

    @field_validator("member_fingerprint")
    @classmethod
    def _fingerprint(cls, value: str) -> str:
        return require_hex64(value, "member_fingerprint")

    @model_validator(mode="after")
    def _id_matches_fingerprint_and_run(self) -> "CandidateSet":
        from .identifiers import candidate_id as build_id

        expected = build_id(self.member_fingerprint, self.run_id)
        if self.candidate_id != expected:
            raise ValueError(f"candidate_id must be {expected}")
        if self.donor_count > self.size:
            raise ValueError("donor_count cannot exceed size")
        if not self.candidate_types:
            raise ValueError("a candidate set must declare at least one candidate type")
        return self


class PreflightIssue(ImmutableContract):
    code: str
    severity: Severity
    message: str
    asset_id: Optional[str] = None
    field: Optional[str] = None


class PreflightReport(Contract):
    project_id: str
    run_id: str
    passed: bool
    checked_at: datetime
    issues: list[PreflightIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _blocking_severity(self) -> "PreflightReport":
        blocking = sorted(i.code for i in self.issues if i.severity == "block")
        if blocking and self.passed:
            raise ValueError(f"blocking issues present: {blocking}")
        return self


class ReviewEvent(Contract):
    review_id: str
    object_id: str
    object_type: str
    scope: ReviewScope
    reviewer_id: str
    decision: ReviewDecision
    rationale: str = Field(min_length=1)
    timestamp: datetime
    supersedes_review_id: Optional[str] = None
    carry_over_from_candidate_id: Optional[str] = None
    retraction_reason: Optional[str] = None
    previous_value: Optional[Any] = None
    new_value: Optional[Any] = None

    @model_validator(mode="after")
    def _valid_revision_chain(self) -> "ReviewEvent":
        if self.supersedes_review_id == self.review_id:
            raise ValueError("a review cannot supersede itself")
        if self.decision is ReviewDecision.REVISE and self.new_value is None:
            raise ValueError("revise decisions must record new_value")
        if self.decision is ReviewDecision.REVISE and self.scope is ReviewScope.FIELD and not self.previous_value:
            raise ValueError("field revisions must record the overwritten original value")
        return self
