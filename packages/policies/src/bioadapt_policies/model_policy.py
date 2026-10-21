"""Model selection and routing policy (plan V2.0 section 4.5)."""

from enum import StrEnum
from typing import Any, Optional

from pydantic import Field

from bioadapt_schemas.entities import Contract, ImmutableContract
from bioadapt_schemas.enums import PrivacyTier, RegulatoryProfile
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.profile import ProjectProfile, RetainedGroup, missing_retained_groups

from .egress import EgressDecision

# Section 4.5: extraction models are only usable once they reach this agreement with human labels.
EXTRACTION_AGREEMENT_KAPPA_MIN = 0.7


class ModelClass(StrEnum):
    CLOUD_FLAGSHIP = "cloud_flagship"
    CLOUD_STANDARD = "cloud_standard"
    LOCAL = "local"
    NONE = "none"


class ModelTask(StrEnum):
    CLAIM_EXTRACTION = "claim_extraction"
    RETRIEVAL_RERANK = "retrieval_rerank"
    PLAN_DRAFTING = "plan_drafting"
    EVIDENCE_SYNTHESIS = "evidence_synthesis"
    REPORT_WRITING = "report_writing"


class DegradedPath(StrEnum):
    HUMAN_TEMPLATE = "human_template"
    DETERMINISTIC_ONLY = "deterministic_only"


CLOUD_CLASSES = frozenset({ModelClass.CLOUD_FLAGSHIP, ModelClass.CLOUD_STANDARD})
LOCAL_ONLY_TIERS = frozenset({PrivacyTier.STRICT_LOCAL, PrivacyTier.HGR_CONTROLLED})
EXTRACTION_TASKS = frozenset({ModelTask.CLAIM_EXTRACTION})


class ModelRoute(ImmutableContract):
    model_id: str
    model_class: ModelClass
    endpoint: str
    sampling_params: dict[str, Any] = Field(default_factory=dict)
    benchmark_run_id: Optional[str] = None


class ModelDecision(Contract):
    allowed: bool
    project_id: str
    task: ModelTask
    model_id: str
    degraded_to: Optional[DegradedPath] = None
    must_not_write_evidence: bool = False
    error_class: Optional[ErrorClass] = None
    reasons: list[str] = Field(default_factory=list)
    policy_version: str = "model_policy_v1"


def _degrade(task: ModelTask) -> DegradedPath:
    return DegradedPath.HUMAN_TEMPLATE if task in EXTRACTION_TASKS else DegradedPath.DETERMINISTIC_ONLY


def route_model_request(
    *,
    project_id: str,
    privacy_tier: PrivacyTier,
    task: ModelTask,
    route: ModelRoute,
    regulatory_profile: list[RegulatoryProfile] = frozenset(),
    egress: Optional[EgressDecision] = None,
    agreement_kappa: Optional[float] = None,
    benchmark_passed: bool = False,
    budget_usd: Optional[float] = None,
    spent_usd: float = 0.0,
    estimate_usd: float = 0.0,
) -> ModelDecision:
    """Decide whether a model may serve this task on this project, and what happens if it may not."""
    reasons: list[str] = []
    cloud = route.model_class in CLOUD_CLASSES

    if route.model_class is ModelClass.NONE:
        return ModelDecision(
            allowed=True,
            project_id=project_id,
            task=task,
            model_id=route.model_id,
            reasons=["non-model path"],
        )

    if cloud:
        if privacy_tier in LOCAL_ONLY_TIERS:
            reasons.append(f"tier {privacy_tier} forbids cloud model calls")
        elif RegulatoryProfile.HGR in regulatory_profile:
            reasons.append("HGR project: a cloud model call needs the legal assessment on file first")
        elif egress is None or not egress.allowed:
            reasons.append("cloud call requires a passing field-level egress decision")

    if not benchmark_passed:
        reasons.append("model has not passed the stratified regression subset for this task")

    if task in EXTRACTION_TASKS:
        if agreement_kappa is None:
            reasons.append("extraction task has no measured agreement with human labels")
        elif agreement_kappa < EXTRACTION_AGREEMENT_KAPPA_MIN:
            reasons.append(
                f"agreement kappa {agreement_kappa} below {EXTRACTION_AGREEMENT_KAPPA_MIN} for extraction tasks"
            )

    if budget_usd is not None and spent_usd + estimate_usd > budget_usd:
        reasons.append(
            f"project token budget exhausted: {spent_usd} + {estimate_usd} > {budget_usd} USD"
        )
        return ModelDecision(
            allowed=False,
            project_id=project_id,
            task=task,
            model_id=route.model_id,
            degraded_to=_degrade(task),
            must_not_write_evidence=True,
            error_class=ErrorClass.BUDGET_EXCEEDED,
            reasons=reasons,
        )

    if reasons:
        return ModelDecision(
            allowed=False,
            project_id=project_id,
            task=task,
            model_id=route.model_id,
            degraded_to=_degrade(task),
            must_not_write_evidence=True,
            error_class=ErrorClass.POLICY_BLOCKED,
            reasons=reasons,
        )

    return ModelDecision(
        allowed=True, project_id=project_id, task=task, model_id=route.model_id
    )


def context_violations(
    profile: ProjectProfile,
    observed_groups: list[RetainedGroup],
) -> list[str]:
    """Section 4.5 context-compression contract: rare signals may not be summarised away."""
    dropped = missing_retained_groups(profile, observed_groups)
    violations = [f"rare group {name} dropped by the summary" for name in dropped]
    if profile.missing_information and any(m.blocking for m in profile.missing_information):
        blocking = sorted(m.code for m in profile.missing_information if m.blocking)
        violations.append(f"blocking information still missing from the profile: {blocking}")
    return violations
