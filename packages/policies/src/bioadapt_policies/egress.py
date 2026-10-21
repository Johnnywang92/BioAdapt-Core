"""Field-level egress allowlist (plan V2.0 sections 11.4, 11.5).

The outbound context is assembled from the allowlist (whitelist-driven templates), never by
filtering a rich payload and hoping the bad fields were removed.
"""

from datetime import date
from enum import StrEnum
from typing import Any, Literal, Optional

from pydantic import Field, model_validator

from bioadapt_schemas.entities import Contract, ImmutableContract, sha256_of_json
from bioadapt_schemas.enums import PrivacyTier, RegulatoryProfile

from .providers import ProviderTerms

StatisticalUnit = Literal["project", "group", "specimen", "donor", "cell"]

# Only project- and group-level aggregates may leave the data plane (section 11.5, re-identification row).
OUTBOUND_UNITS = frozenset({"project", "group"})


class FieldCategory(StrEnum):
    TASK_SEMANTICS = "task_semantics"
    AGGREGATE_STATISTICS = "aggregate_statistics"
    SUBJECT_METADATA = "subject_metadata"
    PUBLIC_KNOWLEDGE = "public_knowledge"
    IDENTIFIERS = "identifiers"
    CELL_LEVEL = "cell_level"
    DONOR_LEVEL = "donor_level"
    DERIVED_REPRESENTATION = "derived_representation"
    IMAGES = "images"


#: Categories that never leave the data plane, whatever the tier or approval.
DENIED_ALWAYS = frozenset(
    {
        FieldCategory.IDENTIFIERS,
        FieldCategory.CELL_LEVEL,
        FieldCategory.DONOR_LEVEL,
        FieldCategory.DERIVED_REPRESENTATION,
        FieldCategory.IMAGES,
    }
)

#: Tier -> categories that a whitelist template may contain (section 11.5 table).
TIER_ALLOWLIST: dict[PrivacyTier, frozenset[FieldCategory]] = {
    PrivacyTier.PUBLIC: frozenset(
        {
            FieldCategory.TASK_SEMANTICS,
            FieldCategory.AGGREGATE_STATISTICS,
            FieldCategory.SUBJECT_METADATA,
            FieldCategory.PUBLIC_KNOWLEDGE,
        }
    ),
    PrivacyTier.RESTRICTED: frozenset(
        {
            FieldCategory.TASK_SEMANTICS,
            FieldCategory.AGGREGATE_STATISTICS,
            FieldCategory.SUBJECT_METADATA,
            FieldCategory.PUBLIC_KNOWLEDGE,
        }
    ),
    PrivacyTier.STRICT_LOCAL: frozenset(),
    PrivacyTier.HGR_CONTROLLED: frozenset(),
}


class OutgoingField(ImmutableContract):
    path: str = Field(min_length=1)
    category: FieldCategory
    unit: StatisticalUnit = "group"
    note: Optional[str] = None

    @model_validator(mode="after")
    def _reject_implicit_raw_values(self) -> "OutgoingField":
        if self.category in (FieldCategory.CELL_LEVEL, FieldCategory.DONOR_LEVEL) and self.unit in OUTBOUND_UNITS:
            raise ValueError(f"{self.category} content cannot be declared as {self.unit}-level")
        return self


class EgressApproval(ImmutableContract):
    approval_id: str
    approver_id: str
    project_id: str
    purpose: str
    approved_on: date
    expires_on: date

    @model_validator(mode="after")
    def _window(self) -> "EgressApproval":
        if self.expires_on <= self.approved_on:
            raise ValueError("approval window must be positive")
        return self

    def covers(self, project_id: str, purpose: str, today: date) -> bool:
        return (
            self.project_id == project_id
            and self.purpose == purpose
            and self.approved_on <= today <= self.expires_on
        )


class EgressRequest(Contract):
    project_id: str
    privacy_tier: PrivacyTier
    purpose: str
    destination: str
    fields: list[OutgoingField] = Field(default_factory=list)
    regulatory_profile: list[RegulatoryProfile] = Field(default_factory=list)
    usage_scope_note: Optional[str] = None


class FieldDenial(ImmutableContract):
    path: str
    rule: str


class EgressDecision(Contract):
    allowed: bool
    allowed_paths: list[str] = Field(default_factory=list)
    denials: list[FieldDenial] = Field(default_factory=list)
    requires_approval: bool = False
    blocked_reasons: list[str] = Field(default_factory=list)
    policy_version: str = "egress_v1"

    def decision_id(self) -> str:
        payload = {
            "allowed": self.allowed,
            "allowed_paths": sorted(self.allowed_paths),
            "denials": sorted((d.path, d.rule) for d in self.denials),
            "blocked_reasons": sorted(self.blocked_reasons),
            "policy_version": self.policy_version,
        }
        return sha256_of_json(payload)


def evaluate_egress(
    request: EgressRequest,
    *,
    today: date,
    local_hosts: set[str],
    cloud_allowlist: set[str] = frozenset(),
    approval: Optional[EgressApproval] = None,
    provider_terms: Optional[ProviderTerms] = None,
    license_cleared: bool = True,
) -> EgressDecision:
    """Return the whitelist-built outbound decision; every rejection is recorded per field."""
    denials: list[FieldDenial] = []
    reasons: list[str] = []
    is_local = request.destination in local_hosts

    if request.privacy_tier is PrivacyTier.HGR_CONTROLLED:
        reasons.append("human genetic resource data: egress forbidden until the legal assessment is on file")
    elif request.privacy_tier is PrivacyTier.STRICT_LOCAL and not is_local:
        reasons.append("strict-local projects may not contact external endpoints")

    if not is_local:
        if request.destination not in cloud_allowlist:
            reasons.append(f"destination {request.destination!r} is not in the project cloud allowlist")
        if provider_terms is None:
            reasons.append("cloud endpoint requires verified provider terms (section 11.2)")
        elif provider_terms.endpoint != request.destination:
            reasons.append("verified provider terms cover a different endpoint")
        elif not provider_terms.is_current(today):
            reasons.append("provider terms review has expired")

    allowed_set = TIER_ALLOWLIST[request.privacy_tier]
    requires_approval = False
    survivors: list[str] = []
    for field in request.fields:
        if field.category in DENIED_ALWAYS:
            denials.append(FieldDenial(path=field.path, rule=f"{field.category} never leaves the data plane"))
            continue
        if field.category not in allowed_set:
            denials.append(
                FieldDenial(path=field.path, rule=f"{field.category} is not allowlisted at tier {request.privacy_tier}")
            )
            continue
        if field.category is FieldCategory.AGGREGATE_STATISTICS and field.unit not in OUTBOUND_UNITS:
            denials.append(FieldDenial(path=field.path, rule=f"{field.unit}-level statistics may not be exported"))
            continue
        if field.category is FieldCategory.PUBLIC_KNOWLEDGE and not license_cleared:
            denials.append(FieldDenial(path=field.path, rule="literature licence class not cleared"))
            continue
        survivors.append(field.path)

    if request.privacy_tier is PrivacyTier.RESTRICTED:
        requires_approval = True
        if approval is None or not approval.covers(request.project_id, request.purpose, today):
            if survivors:
                reasons.append("restricted-tier egress needs a field-scoped approval for this purpose")

    allowed = bool(survivors) and not reasons
    if not survivors and not reasons:
        reasons.append("no field passed the allowlist")
    return EgressDecision(
        allowed=allowed,
        allowed_paths=survivors if allowed else [],
        denials=denials,
        requires_approval=requires_approval,
        blocked_reasons=reasons,
    )


def build_outbound_context(payload: dict[str, Any], decision: EgressDecision) -> dict[str, Any]:
    """Project a candidate payload onto the approved field paths (whitelist-driven assembly)."""
    if not decision.allowed:
        raise ValueError("egress decision does not permit sending any content")
    outbound: dict[str, Any] = {}
    for path in decision.allowed_paths:
        node: Any = payload
        for segment in path.split("."):
            if not isinstance(node, dict) or segment not in node:
                raise ValueError(f"approved field {path!r} is missing from the outbound template")
            node = node[segment]
        outbound[path] = node
    return outbound
