"""Authorization policy: RBAC + attribute rules, default deny (plan V2.0 sections 9.7, 11.2, 11.6).

Every action is denied unless a role grants it *and* every attribute rule attached to that action
passes. Denials carry the rule name so §11.3 acceptance tests can assert on them one by one.
"""

from enum import StrEnum
from typing import Any, Optional

from pydantic import Field, model_validator

from bioadapt_schemas.entities import Contract, ImmutableContract, sha256_of_json
from bioadapt_schemas.enums import PrivacyTier, UsageScope

#: Section 9.7: object storage hands out short-lived signed URLs only.
MAX_PRESIGNED_TTL_SECONDS = 600
#: Section 9.7: list endpoints are paged, default 50 / hard cap 500.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 500
GLOBAL_SCOPE = "*"


class Action(StrEnum):
    PROJECT_READ = "project:read"
    PROJECT_WRITE = "project:write"
    #: Section 1.5 + appendix C1: replacing a project's stage-0 placeholder cuts with measured ones.
    THRESHOLDS_CONFIRM = "thresholds:confirm"
    RUN_LAUNCH = "run:launch"
    RUN_READ = "run:read"
    RUN_CANCEL = "run:cancel"
    #: The workflow engine reports transitions, node retries and spend through this one action.
    RUN_UPDATE = "run:update"
    APPROVAL_REQUEST = "approval:request"
    APPROVAL_READ = "approval:read"
    APPROVAL_DECIDE = "approval:decide"
    APPROVAL_DELEGATE = "approval:delegate"
    ASSET_READ = "asset:read"
    ASSET_WRITE = "asset:write"
    ASSET_EXPORT = "asset:export"
    ASSET_DELETE = "asset:delete"
    CARD_REVIEW = "card:review"
    CARD_SIGN_OFF = "card:sign_off"
    EGRESS_REQUEST = "egress:request"
    EGRESS_APPROVE = "egress:approve"
    TOOL_REGISTER = "tool:register"
    TOOL_GRANT_ADHOC = "tool:grant_adhoc"
    TOOL_PROMOTE = "tool:promote"
    MODEL_CALL = "model:call"
    CASE_LIBRARY_WRITE = "case_library:write"
    TRUTH_FREEZE = "evaluation:freeze_truth"
    BENCHMARK_RUN = "evaluation:run_benchmark"
    ROLE_GRANT = "iam:grant_role"
    POLICY_WRITE = "policy:write"
    AUDIT_READ = "audit:read"


class Role(StrEnum):
    PLATFORM_ADMIN = "platform_admin"
    PRINCIPAL_INVESTIGATOR = "principal_investigator"
    ANALYST = "analyst"
    DOMAIN_EXPERT = "domain_expert"
    KNOWLEDGE_CURATOR = "knowledge_curator"
    EVALUATION_LEAD = "evaluation_lead"
    COMPLIANCE_OFFICER = "compliance_officer"
    SERVICE_AGENT = "service_agent"


ROLE_GRANTS: dict[Role, frozenset[Action]] = {
    Role.PLATFORM_ADMIN: frozenset(
        {
            Action.PROJECT_READ,
            Action.PROJECT_WRITE,
            Action.RUN_READ,
            Action.ASSET_READ,
            Action.TOOL_REGISTER,
            Action.TOOL_PROMOTE,
            Action.ROLE_GRANT,
            Action.POLICY_WRITE,
            Action.AUDIT_READ,
        }
    ),
    Role.PRINCIPAL_INVESTIGATOR: frozenset(
        {
            Action.PROJECT_READ,
            Action.PROJECT_WRITE,
            Action.THRESHOLDS_CONFIRM,
            Action.RUN_LAUNCH,
            Action.RUN_READ,
            Action.RUN_CANCEL,
            Action.RUN_UPDATE,
            Action.APPROVAL_REQUEST,
            Action.APPROVAL_READ,
            Action.APPROVAL_DECIDE,
            Action.APPROVAL_DELEGATE,
            Action.ASSET_READ,
            Action.ASSET_WRITE,
            Action.ASSET_EXPORT,
            Action.ASSET_DELETE,
            Action.EGRESS_REQUEST,
            Action.TOOL_GRANT_ADHOC,
            Action.CARD_REVIEW,
            Action.CARD_SIGN_OFF,
            Action.BENCHMARK_RUN,
            Action.AUDIT_READ,
        }
    ),
    Role.ANALYST: frozenset(
        {
            Action.PROJECT_READ,
            Action.RUN_LAUNCH,
            Action.RUN_READ,
            Action.RUN_UPDATE,
            Action.APPROVAL_REQUEST,
            Action.APPROVAL_READ,
            Action.ASSET_READ,
            Action.ASSET_WRITE,
            Action.EGRESS_REQUEST,
            Action.CARD_REVIEW,
            Action.MODEL_CALL,
        }
    ),
    Role.DOMAIN_EXPERT: frozenset(
        {
            Action.PROJECT_READ,
            Action.RUN_READ,
            Action.RUN_UPDATE,
            Action.APPROVAL_REQUEST,
            Action.APPROVAL_READ,
            Action.ASSET_READ,
            Action.CARD_REVIEW,
            Action.CARD_SIGN_OFF,
        }
    ),
    Role.KNOWLEDGE_CURATOR: frozenset({Action.PROJECT_READ, Action.CASE_LIBRARY_WRITE}),
    Role.EVALUATION_LEAD: frozenset(
        {Action.PROJECT_READ, Action.RUN_READ, Action.ASSET_READ, Action.TRUTH_FREEZE, Action.BENCHMARK_RUN}
    ),
    Role.COMPLIANCE_OFFICER: frozenset(
        {
            Action.PROJECT_READ,
            Action.EGRESS_APPROVE,
            Action.APPROVAL_READ,
            Action.AUDIT_READ,
            Action.CASE_LIBRARY_WRITE,
        }
    ),
    #: Section 9.1: the engine reports what happened to a run - including an approval the platform
    #: already holds. What it may never do is be the approval: that is HUMAN_ONLY_ACTIONS below,
    #: matched with the state machine's own human-only targets.
    Role.SERVICE_AGENT: frozenset(
        {Action.RUN_READ, Action.RUN_UPDATE, Action.ASSET_READ, Action.ASSET_WRITE, Action.MODEL_CALL}
    ),
}

#: Section 11.6 control 6: approval and permission state is only reachable by human action.
HUMAN_ONLY_ACTIONS = frozenset(
    {
        Action.CARD_SIGN_OFF,
        Action.EGRESS_APPROVE,
        Action.APPROVAL_DECIDE,
        Action.APPROVAL_DELEGATE,
        Action.TOOL_REGISTER,
        Action.TOOL_GRANT_ADHOC,
        Action.TOOL_PROMOTE,
        Action.TRUTH_FREEZE,
        Action.ROLE_GRANT,
        Action.POLICY_WRITE,
        Action.CASE_LIBRARY_WRITE,
        #: What the confirmed flag unlocks is the conclusion exit, so it is set by a person or not at all.
        Action.THRESHOLDS_CONFIRM,
    }
)


#: Actions that additionally require a second factor.
MFA_REQUIRED_ACTIONS = frozenset(
    {
        Action.EGRESS_APPROVE,
        #: Section 9.6: an ad-hoc grant puts unaudited code into a compute container, so it is the one
        #: promotion-shaped action that a password alone may not carry.
        Action.TOOL_GRANT_ADHOC,
        Action.TOOL_PROMOTE,
        Action.ASSET_DELETE,
        Action.ROLE_GRANT,
        Action.POLICY_WRITE,
        Action.TRUTH_FREEZE,
        Action.THRESHOLDS_CONFIRM,
    }
)

#: Section 13.1: the person who freezes ground truth may not also build the thing measured.
INCOMPATIBLE_ROLES: tuple[frozenset[Role], ...] = (
    frozenset({Role.EVALUATION_LEAD, Role.ANALYST}),
    frozenset({Role.EVALUATION_LEAD, Role.PLATFORM_ADMIN}),
    frozenset({Role.COMPLIANCE_OFFICER, Role.PRINCIPAL_INVESTIGATOR}),
)

LOCAL_ONLY_TIERS = frozenset({PrivacyTier.STRICT_LOCAL, PrivacyTier.HGR_CONTROLLED})
REUSABLE_SCOPES = frozenset({UsageScope.INTERNAL_IMPROVEMENT, UsageScope.CROSS_PROJECT_ALLOWED})


def role_conflicts(roles: set[Role]) -> list[str]:
    return [
        f"roles {sorted(pair)} may not be held together"
        for pair in INCOMPATIBLE_ROLES
        if len(pair & roles) > 1
    ]


class Actor(ImmutableContract):
    subject_id: str = Field(min_length=1)
    roles: list[Role] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    is_service_account: bool = False
    mfa_verified: bool = False

    @model_validator(mode="after")
    def _separation_of_duties(self) -> "Actor":
        conflicts = role_conflicts(set(self.roles))
        if conflicts:
            raise ValueError("; ".join(conflicts))
        if self.is_service_account and not set(self.roles) <= {Role.SERVICE_AGENT}:
            raise ValueError("a service account may only hold the service_agent role")
        return self

    def scope(self) -> set[str]:
        return set(self.project_ids)


class Resource(ImmutableContract):
    resource_id: str = Field(min_length=1)
    resource_type: str = Field(min_length=1)
    project_id: Optional[str] = None
    privacy_tier: Optional[PrivacyTier] = None
    usage_scope: UsageScope = UsageScope.THIS_PROJECT_ONLY
    attributes: dict[str, Any] = Field(default_factory=dict)


class AccessDecision(Contract):
    allowed: bool
    subject_id: str
    action: Action
    resource_id: str
    project_id: Optional[str] = None
    denials: list[str] = Field(default_factory=list)
    policy_version: str = "access_v1"

    def decision_id(self) -> str:
        return sha256_of_json(
            {
                "allowed": self.allowed,
                "subject_id": self.subject_id,
                "action": self.action,
                "resource_id": self.resource_id,
                "denials": sorted(self.denials),
                "policy_version": self.policy_version,
            }
        )


def authorize(
    actor: Actor,
    action: Action,
    resource: Resource,
    *,
    requesting_subject_id: Optional[str] = None,
) -> AccessDecision:
    """Default-deny check; the returned decision is what §11.2 requires in the audit log."""
    denials: list[str] = []
    projects = actor.scope()
    granted = any(action in ROLE_GRANTS[role] for role in actor.roles)
    #: Section 5.8: content shared into the case library is readable across projects by scope, not by grant.
    reusable_read = action is Action.ASSET_READ and resource.usage_scope in REUSABLE_SCOPES

    if not granted:
        denials.append(
            f"no role of {sorted(str(role) for role in actor.roles)} grants {action}"
        )

    if GLOBAL_SCOPE not in projects and not reusable_read:
        if resource.project_id is not None and resource.project_id not in projects:
            denials.append(f"{action} outside the actor project scope")

    if actor.is_service_account and action in HUMAN_ONLY_ACTIONS:
        denials.append(f"{action} must be performed by a human, not a service account")

    if action in MFA_REQUIRED_ACTIONS and not actor.mfa_verified:
        denials.append(f"{action} requires a second factor")

    if action is Action.EGRESS_APPROVE:
        if requesting_subject_id is None:
            denials.append("egress approval needs the requesting subject for separation of duties")
        elif requesting_subject_id == actor.subject_id:
            denials.append("egress requester and approver must be different subjects")

    if action is Action.APPROVAL_DECIDE:
        if requesting_subject_id is None:
            denials.append("approval:decide needs the subject that asked for the gate, for separation of duties")
        elif requesting_subject_id == actor.subject_id:
            denials.append("approval:decide: the subject that requested a gate may not open it")

    if action is Action.TOOL_GRANT_ADHOC:
        #: Section 9.6 + 11.6: the one-time approval is the PI's, and the analyst or agent that submitted
        #: the tool is not a PI wearing a second hat. The grant's approver is the authenticated actor, so
        #: this is where that pair stops being something the caller asserts about itself.
        if requesting_subject_id is None:
            denials.append("tool:grant_adhoc needs the subject that submitted the tool, for separation of duties")
        elif requesting_subject_id == actor.subject_id:
            denials.append("tool:grant_adhoc: the subject that submitted an ad-hoc tool may not grant it")

    if action is Action.CARD_SIGN_OFF:
        authored = resource.attributes.get("authored_by")
        if authored == actor.subject_id:
            denials.append("an analysis run author may not sign off its own conclusion")

    if action is Action.ASSET_READ and resource.project_id is not None:
        if resource.project_id not in projects and resource.usage_scope not in REUSABLE_SCOPES:
            denials.append(
                f"asset from another project is {resource.usage_scope} and may not be read here"
            )

    if action in (Action.ASSET_EXPORT, Action.EGRESS_REQUEST) and resource.privacy_tier in LOCAL_ONLY_TIERS:
        denials.append(f"tier {resource.privacy_tier} keeps all content inside the data plane")

    return AccessDecision(
        allowed=not denials,
        subject_id=actor.subject_id,
        action=action,
        resource_id=resource.resource_id,
        project_id=resource.project_id,
        denials=denials,
    )


def presigned_url_ttl(seconds: int) -> int:
    """Clamp a signed-URL lifetime to the policy ceiling instead of refusing it silently."""
    if seconds <= 0:
        raise ValueError("signed URL ttl must be positive")
    return min(seconds, MAX_PRESIGNED_TTL_SECONDS)


def page_size(limit: Optional[int] = None) -> int:
    if limit is None:
        return DEFAULT_PAGE_SIZE
    if limit <= 0:
        raise ValueError("page size must be positive")
    return min(limit, MAX_PAGE_SIZE)
