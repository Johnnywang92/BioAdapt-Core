"""Tool registry, ad-hoc promotion channel and revocation (plan V2.0 section 9.6)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator

from bioadapt_schemas.entities import ImmutableContract, sha256_of_json
from bioadapt_schemas.enums import LicenseReviewStatus, ToolClass
from bioadapt_schemas.orchestration import AdhocToolGrant

from .enums import FailurePolicy, NetworkMode
from .spec import ContainerImage, LicenseInfo, ResourceProfile, SchemaRef, ToolSpec


class RegistryError(ValueError):
    pass


class AdmissionError(RegistryError):
    pass


class UnknownToolError(RegistryError):
    pass


class ForbiddenToolError(RegistryError):
    pass


class AdhocExhaustedError(RegistryError):
    pass


class AdhocSandboxLimits(ImmutableContract):
    """Stage-0 caps (§4.4); confirm against the local compute inventory before real projects."""

    max_cpu: int = Field(ge=1, default=8)
    max_memory_mb: int = Field(ge=1, default=32768)
    max_minutes: int = Field(ge=1, default=240)

    def violations(self, spec: ToolSpec) -> list[str]:
        res = spec.resources
        found = []
        if res.cpu > self.max_cpu:
            found.append(f"cpu {res.cpu} exceeds ad-hoc cap {self.max_cpu}")
        if res.memory_mb > self.max_memory_mb:
            found.append(f"memory {res.memory_mb}MB exceeds ad-hoc cap {self.max_memory_mb}MB")
        if res.max_minutes > self.max_minutes:
            found.append(f"runtime {res.max_minutes}min exceeds ad-hoc cap {self.max_minutes}min")
        if res.gpu > 0:
            found.append("ad-hoc tools may not request GPUs")
        if spec.network.mode is not NetworkMode.CLOSED:
            found.append("ad-hoc tools must run without network access")
        if spec.egress_allowlist:
            found.append("ad-hoc tools must not declare egress destinations")
        if spec.retry_limit != 0:
            found.append("ad-hoc retries would bypass the one-time approval")
        if spec.failure_policy not in (FailurePolicy.HALT, FailurePolicy.ESCALATE_TO_HUMAN):
            found.append("ad-hoc failure policy must halt or escalate to a human")
        return found


DEFAULT_ADHOC_LIMITS = AdhocSandboxLimits()


class AdhocApproval(ImmutableContract):
    """One-time approval of a single ad-hoc tool submission (section 9.6).

    The two provenance fields are what make this more than a permission flag: a grant that does not name
    the gate a person decided, and the subject that submitted the tool, cannot be told apart from one a
    process made up for itself (§11.6).
    """

    approver_id: str
    #: The subject that asked for the tool. Section 9.6 gives the approval to the PI, so this is not them.
    requested_by: str = Field(min_length=1)
    #: The ad-hoc gate whose decision this grant executes (§9.4).
    gate_approval_id: str = Field(min_length=1)
    project_id: str
    plan_id: str
    node_id: str
    reason: str = Field(min_length=1)
    approved_at: datetime
    expires_at: datetime
    max_uses: int = Field(ge=1, default=1)

    @model_validator(mode="after")
    def _one_time(self) -> "AdhocApproval":
        if self.max_uses != 1:
            raise ValueError("ad-hoc approval is granted per submission: max_uses must be 1")
        if self.expires_at <= self.approved_at:
            raise ValueError("ad-hoc approval window must be positive")
        if self.approver_id == self.requested_by:
            raise ValueError("ad-hoc approval must come from someone other than the subject that submitted the tool")
        return self

    def is_live(self, now: datetime) -> bool:
        return now < self.expires_at

    @classmethod
    def of_grant(cls, grant: AdhocToolGrant) -> "AdhocApproval":
        """The approval a stored grant stands for, read back by whatever process is about to run it (§9.6).

        This is the only path from the index row to the object the registry holds, so the approver, the
        submitter and the gate all come off the record a person wrote rather than from the caller.
        """
        return cls(
            approver_id=grant.approver_id,
            requested_by=grant.requested_by,
            gate_approval_id=grant.approval_id,
            project_id=grant.project_id,
            plan_id=grant.plan_id,
            node_id=grant.node_id,
            reason=grant.reason,
            approved_at=grant.approved_at,
            expires_at=grant.expires_at,
            max_uses=grant.max_uses,
        )


class PromotionChecklist(ImmutableContract):
    assumptions_documented: bool = False
    failure_modes_documented: bool = False
    resources_measured: bool = False
    licence_reviewed: bool = False
    unit_tests_present: bool = False
    benchmark_case_present: bool = False
    output_contract_present: bool = False

    def incomplete(self) -> list[str]:
        return sorted(name for name, done in self.model_dump().items() if not done)


class PromotionReview(ImmutableContract):
    reviewer_id: str
    reviewed_at: datetime
    reuse_count: int = Field(ge=0)
    feeds_conclusion: bool = False
    checklist: PromotionChecklist = Field(default_factory=PromotionChecklist)

    @model_validator(mode="after")
    def _complete_or_nothing(self) -> "PromotionReview":
        incomplete = self.checklist.incomplete()
        if incomplete:
            raise ValueError(f"promotion checklist incomplete: {incomplete}")
        if self.reuse_count < 2 and not self.feeds_conclusion:
            raise ValueError("promotion needs either >=2 reuses or a conclusion-bearing result")
        return self


class Revocation(ImmutableContract):
    tool_name: str
    tool_version: str
    reason: str = Field(min_length=1)
    revoked_by: str
    revoked_at: datetime


def _key(spec: ToolSpec) -> tuple[str, str]:
    return spec.tool_name, spec.tool_version


class ToolRegistry:
    def __init__(self, adhoc_limits: AdhocSandboxLimits = DEFAULT_ADHOC_LIMITS):
        self._specs: dict[tuple[str, str], ToolSpec] = {}
        self._grants: dict[tuple[str, str], AdhocApproval] = {}
        self._consumed: set[tuple[str, str]] = set()
        self._forbidden: dict[tuple[str, str], Revocation] = {}
        self._reviews: dict[tuple[str, str], PromotionReview] = {}
        self._adhoc_limits = adhoc_limits

    def register(self, spec: ToolSpec) -> ToolSpec:
        key = _key(spec)
        if spec.tool_class is not ToolClass.REGISTERED:
            raise AdmissionError("ad-hoc tools enter through grant(), not register()")
        if key in self._specs:
            raise AdmissionError(f"{key} is already registered; publish a new version instead")
        self._admit(spec)
        self._specs[key] = spec
        return spec

    def _admit(self, spec: ToolSpec) -> None:
        key = _key(spec)
        if key in self._forbidden:
            raise AdmissionError(f"{key} is forbidden; revocation must be lifted first")
        violations = spec.admission_violations()
        if violations:
            raise AdmissionError(f"{spec.tool_name} {spec.tool_version}: " + "; ".join(violations))

    def grant(self, spec: ToolSpec, approval: AdhocApproval) -> ToolSpec:
        key = _key(spec)
        if spec.tool_class is not ToolClass.PROJECT_ADHOC:
            raise AdmissionError("registered tools do not need an ad-hoc grant")
        if key in self._forbidden:
            raise AdmissionError(f"{key} is forbidden")
        if key in self._specs:
            raise AdmissionError(f"{key} already exists in the registry")
        if spec.license.review_status is LicenseReviewStatus.REJECTED:
            raise AdmissionError("a rejected licence cannot run, even ad-hoc")
        violations = self._adhoc_limits.violations(spec)
        if violations:
            raise AdmissionError("ad-hoc sandbox violated: " + "; ".join(violations))
        self._specs[key] = spec
        self._grants[key] = approval
        return spec

    def admit_grant(self, grant: AdhocToolGrant) -> ToolSpec:
        """Put one stored ad-hoc grant (§9.6) into this registry, as an engine does when it starts.

        The spec comes from the bytes the grant recorded, and ``grant`` re-runs the sandbox checks over
        them, so a hydrated registry cannot end up holding a looser tool than the API agreed to write.
        A spent or expired row is the caller's to filter: this admits the permission, not the question of
        whether it is still live.
        """
        return self.grant(ToolSpec(**grant.spec), AdhocApproval.of_grant(grant))

    def check_adhoc(self, name: str, version: str, *, now: datetime, project_id: str) -> AdhocApproval:
        """Ask whether the grant would still let this tool run, without spending it (§9.6).

        A driver that resumes a run has to be able to ask that question before it knows whether the
        node will actually execute: an output already checkpointed was produced by the spend, and a
        grant cannot be spent twice.
        """
        grant = self._grants.get((name, version))
        if grant is None:
            raise AdhocExhaustedError(f"{name} {version} is ad-hoc and no grant covers it")
        if not grant.is_live(now):
            raise AdhocExhaustedError(f"ad-hoc grant for {name} {version} expired")
        if grant.project_id != project_id:
            raise AdhocExhaustedError(f"ad-hoc grant for {name} {version} belongs to {grant.project_id}")
        if (name, version) in self._consumed:
            raise AdhocExhaustedError(f"ad-hoc grant for {name} {version} was already used")
        return grant

    def consume(self, name: str, version: str, *, now: datetime, project_id: str) -> ToolSpec:
        spec = self.resolve(name, version, now=now)
        grant = self._grants.get((name, version))
        if grant is None:
            return spec
        self.check_adhoc(name, version, now=now, project_id=project_id)
        self._consumed.add((name, version))
        return spec

    def resolve(self, name: str, version: str, *, now: Optional[datetime] = None) -> ToolSpec:
        key = (name, version)
        if key in self._forbidden:
            raise ForbiddenToolError(f"{name} {version} is forbidden: {self._forbidden[key].reason}")
        if key not in self._specs:
            raise UnknownToolError(f"{name} {version} is not registered")
        return self._specs[key]

    def promote(self, name: str, version: str, review: PromotionReview) -> ToolSpec:
        key = (name, version)
        spec = self._specs.get(key)
        if spec is None or key not in self._grants:
            raise UnknownToolError(f"{name} {version} is not an ad-hoc tool to promote")
        promoted = spec.model_copy(update={"tool_class": ToolClass.REGISTERED})
        self._admit(promoted)
        self._specs[key] = promoted
        del self._grants[key]
        self._reviews[key] = review
        return promoted

    def forbid(self, revocation: Revocation) -> None:
        key = (revocation.tool_name, revocation.tool_version)
        self._forbidden[key] = revocation
        self._specs.pop(key, None)
        self._grants.pop(key, None)

    def lift_forbidden(self, name: str, version: str) -> None:
        self._forbidden.pop((name, version), None)

    def select(
        self,
        *,
        task: Optional[str] = None,
        modality: Optional[str] = None,
        species: Optional[str] = None,
        include_adhoc: bool = False,
        now: Optional[datetime] = None,
    ) -> list[ToolSpec]:
        found = []
        for key, spec in sorted(self._specs.items()):
            if key in self._forbidden:
                continue
            if spec.tool_class is ToolClass.PROJECT_ADHOC:
                grant = self._grants.get(key)
                if not include_adhoc or grant is None or (now and not grant.is_live(now)):
                    continue
            if task and spec.task != task:
                continue
            if modality and modality not in spec.supported_modalities:
                continue
            if species and species not in spec.supported_species and "*" not in spec.supported_species:
                continue
            found.append(spec)
        return found

    def grant_of(self, name: str, version: str) -> Optional[AdhocApproval]:
        return self._grants.get((name, version))

    def review_of(self, name: str, version: str) -> Optional[PromotionReview]:
        return self._reviews.get((name, version))

    def is_forbidden(self, name: str, version: str) -> bool:
        return (name, version) in self._forbidden

    def snapshot_id(self) -> str:
        payload = {
            "specs": [
                [name, version, spec.model_dump(mode="json", warnings=False)]
                for (name, version), spec in sorted(self._specs.items())
            ],
            "grants": [[name, version] for (name, version) in sorted(self._grants)],
            "forbidden": [f"{r.tool_name}@{r.tool_version}" for r in self._forbidden.values()],
        }
        return sha256_of_json(payload)


def minimal_spec(**overrides):
    """A spec skeleton used by tests and by the registration UI form defaults."""
    fields = {
        "tool_name": "scanpy_qc",
        "tool_version": "1.11.0",
        "task": "qc",
        "tool_class": ToolClass.REGISTERED,
        "supported_modalities": ["RNA"],
        "supported_species": ["mouse"],
        "input_schema": SchemaRef(name="anndata_counts", version="v1"),
        "output_schema": SchemaRef(name="anndata_qc", version="v1"),
        "container_image": ContainerImage(
            repository="registry.internal/bioadapt/scanpy",
            tag="1.11.0",
            digest="sha256:" + "ab" * 32,
        ),
        "resources": ResourceProfile(cpu=4, memory_mb=16384, max_minutes=60),
        "license": LicenseInfo(spdx_id="BSD-3-Clause", display_name="Scanpy"),
    }
    fields.update(overrides)
    return ToolSpec(**fields)
