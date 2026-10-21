"""Shared factories for the policy-as-code tests."""

from datetime import date

from bioadapt_policies.access import Actor, Resource, Role
from bioadapt_policies.egress import EgressApproval, EgressRequest, FieldCategory, OutgoingField
from bioadapt_policies.model_policy import ModelClass, ModelRoute
from bioadapt_policies.providers import ProviderTerms
from bioadapt_policies.qc_policy import CellFilter, QcMetric, QcPolicy, QcRule, ThresholdSource
from bioadapt_policies.robustness import (
    MINIMUM_FEATURE_FACTORS,
    MINIMUM_RESOLUTION_FACTORS,
    MINIMUM_SEED_COUNT,
    ProtocolDeviation,
    RobustnessProtocol,
)
from bioadapt_schemas.enums import PrivacyTier, UsageScope

TODAY = date(2026, 5, 1)
PROJECT = "prj_001"
PURPOSE = "literature claim extraction"
CLOUD_ENDPOINT = "api.provider-a.example.com:443"
LOCAL_HOSTS = {"sc-node-01.internal:8443"}
OTHER_PROJECT = "prj_002"


def terms(**overrides) -> ProviderTerms:
    fields = {
        "provider": "provider-a",
        "endpoint": CLOUD_ENDPOINT,
        "zero_retention": True,
        "no_training_use": True,
        "subprocessors_disclosed": True,
        "data_region": "cn-north",
        "dpa_signed": True,
        "reviewed_at": date(2026, 1, 1),
        "reviewed_by": "compliance_officer_01",
    }
    fields.update(overrides)
    return ProviderTerms(**fields)


def field(path: str, category: FieldCategory, **overrides) -> OutgoingField:
    return OutgoingField(path=path, category=category, **overrides)


def safe_fields() -> list[OutgoingField]:
    return [
        field("task.objective", FieldCategory.TASK_SEMANTICS),
        field("stats.group_sizes", FieldCategory.AGGREGATE_STATISTICS),
    ]


def request(
    *,
    tier: PrivacyTier = PrivacyTier.RESTRICTED,
    destination: str = CLOUD_ENDPOINT,
    fields: list[OutgoingField] | None = None,
    **overrides,
) -> EgressRequest:
    fields = safe_fields() if fields is None else fields
    return EgressRequest(
        project_id=PROJECT,
        privacy_tier=tier,
        purpose=PURPOSE,
        destination=destination,
        fields=fields,
        **overrides,
    )


def approval(**overrides) -> EgressApproval:
    fields = {
        "approval_id": "egr_0001",
        "approver_id": "compliance_officer_01",
        "project_id": PROJECT,
        "purpose": PURPOSE,
        "approved_on": date(2026, 4, 20),
        "expires_on": date(2026, 6, 30),
    }
    fields.update(overrides)
    return EgressApproval(**fields)


def cloud_context(**overrides) -> dict:
    """Keyword arguments that make an external call policy-valid for the restricted tier."""
    context = {
        "today": TODAY,
        "local_hosts": LOCAL_HOSTS,
        "cloud_allowlist": {CLOUD_ENDPOINT},
        "approval": approval(),
        "provider_terms": terms(),
    }
    context.update(overrides)
    return context


def actor(
    *,
    subject_id: str = "user_analyst_01",
    roles: list[Role] = [Role.ANALYST],
    project_ids: list[str] = [PROJECT],
    **overrides,
) -> Actor:
    fields = {
        "subject_id": subject_id,
        "roles": roles,
        "project_ids": project_ids,
    }
    fields.update(overrides)
    return Actor(**fields)


def service_actor(**overrides) -> Actor:
    fields = {
        "subject_id": "svc_orchestrator",
        "roles": [Role.SERVICE_AGENT],
        "project_ids": [PROJECT],
        "is_service_account": True,
    }
    fields.update(overrides)
    return Actor(**fields)


def resource(
    *,
    resource_id: str = "run_001",
    resource_type: str = "run",
    project_id: str | None = PROJECT,
    **overrides,
) -> Resource:
    fields = {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "project_id": project_id,
    }
    fields.update(overrides)
    return Resource(**fields)


def route(model_class: ModelClass = ModelClass.CLOUD_FLAGSHIP, **overrides) -> ModelRoute:
    fields = {
        "model_id": "flagship-2026-04",
        "model_class": model_class,
        "endpoint": CLOUD_ENDPOINT,
        "benchmark_run_id": "eval_0001",
    }
    fields.update(overrides)
    return ModelRoute(**fields)


def asset(
    project_id: str = PROJECT,
    usage_scope: UsageScope = UsageScope.THIS_PROJECT_ONLY,
    **overrides,
) -> Resource:
    return resource(
        resource_id="as_0001",
        resource_type="asset",
        project_id=project_id,
        usage_scope=usage_scope,
        **overrides,
    )


def qc_rule(name: str = "mito_high", **overrides) -> QcRule:
    fields = {
        "name": name,
        "metric": QcMetric.MITOCHONDRIAL_FRACTION,
        "apply": CellFilter.ABOVE,
        "upper": 0.2,
        "threshold_source": ThresholdSource.DISTRIBUTION_DIAGNOSTIC,
        "source_ref": "quantile_diagnostic_v1",
    }
    fields.update(overrides)
    return QcRule(**fields)


def qc_policy(**overrides) -> QcPolicy:
    fields = {
        "policy_id": "qc_policy_v1",
        "version": "1",
        "species": "human",
        "tissue": "pbmc",
        "platform": "10x_3v3",
        "dissociation": "none_required_blood",
        "expected_artifacts": ["doublets", "ambient_rna"],
        "artifact_screens": ["scrublet"],
        "rules": [qc_rule()],
    }
    fields.update(overrides)
    return QcPolicy(**fields)


def robustness_protocol(**overrides) -> RobustnessProtocol:
    """Section 7.5's minimum protocol: all five classes, at their minimum factor sets."""
    fields = {
        "protocol_id": "robustness_pbmc",
        "version": "1",
        "resolution_factors": list(MINIMUM_RESOLUTION_FACTORS),
        "seed_count": MINIMUM_SEED_COUNT,
        "feature_factors": list(MINIMUM_FEATURE_FACTORS),
        "leave_one_donor": True,
        "subsample_fraction": 0.5,
        "permutation_replicates": 200,
    }
    fields.update(overrides)
    return RobustnessProtocol(**fields)


def approved_deviation(**overrides) -> ProtocolDeviation:
    fields = {
        "approved_by": "project_owner_01",
        "reason": "one donor per project, so leave-one-donor cannot run",
        "note": "candidate reported as provisional",
    }
    fields.update(overrides)
    return ProtocolDeviation(**fields)
