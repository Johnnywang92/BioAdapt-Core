"""Per-project quotas and the launch-time budget checks (plan V2.0 section 9.7).

Limits are supplied by the deployment: the plan forbids shipping unconfirmed stage-0 numbers as code
constants, so nothing here has a default value. What a run may do *while* it runs is the orchestrator's
decision (section 9.2); these checks only stop an over-large run from being accepted at all.
"""

from __future__ import annotations

from typing import Optional, Protocol

from pydantic import Field

from bioadapt_schemas.entities import ImmutableContract
from bioadapt_schemas.enums import RunState
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.plan import AnalysisPlan

from .errors import problem

#: Run states that still occupy a project slot; anything else has finished.
TERMINAL_RUN_STATES = frozenset({RunState.REPORT_READY, RunState.ARCHIVED, RunState.FAILED})


class ProjectQuotas(ImmutableContract):
    max_storage_bytes: int = Field(gt=0)
    max_active_runs: int = Field(ge=1)
    max_run_compute_minutes: int = Field(ge=1)
    max_run_token_cost_usd: float = Field(gt=0)

    #: The orchestrator's names for the same two ceilings (section 9.2 bounds one run, not one project).
    @property
    def max_compute_minutes(self) -> int:
        return self.max_run_compute_minutes

    @property
    def max_model_cost_usd(self) -> float:
        return self.max_run_token_cost_usd


class QuotaProvider(Protocol):
    def for_project(self, project_id: str) -> ProjectQuotas: ...


class StaticQuotas:
    """Per-project overrides plus a configured default; there is no implicit platform limit."""

    def __init__(self, *, default: ProjectQuotas, overrides: Optional[dict[str, ProjectQuotas]] = None) -> None:
        self.default = default
        self.overrides = dict(overrides or {})

    def for_project(self, project_id: str) -> ProjectQuotas:
        return self.overrides.get(project_id, self.default)


def _resource_exceeded(message: str) -> None:
    raise problem(
        429, ErrorClass.RESOURCE_EXCEEDED, "project_quota_exceeded", message, retryable=True, retry_after=30
    )


def check_storage(quotas: ProjectQuotas, *, used_bytes: int, incoming_bytes: int) -> None:
    if used_bytes + incoming_bytes > quotas.max_storage_bytes:
        _resource_exceeded(
            f"project storage quota {quotas.max_storage_bytes} bytes exceeded by {incoming_bytes} bytes"
        )


def check_active_runs(quotas: ProjectQuotas, *, active_runs: int) -> None:
    if active_runs >= quotas.max_active_runs:
        _resource_exceeded(f"{active_runs} runs already active, quota is {quotas.max_active_runs}")


def check_plan_budget(quotas: ProjectQuotas, plan: AnalysisPlan) -> None:
    budget = plan.loop_budget
    if budget.max_compute_minutes > quotas.max_run_compute_minutes:
        raise problem(
            400,
            ErrorClass.BUDGET_EXCEEDED,
            "compute_budget_above_quota",
            f"plan asks for {budget.max_compute_minutes} compute minutes, the project budget is "
            f"{quotas.max_run_compute_minutes}",
            field="loop_budget.max_compute_minutes",
        )
    if budget.max_token_cost_usd > quotas.max_run_token_cost_usd:
        raise problem(
            400,
            ErrorClass.BUDGET_EXCEEDED,
            "token_budget_above_quota",
            f"plan asks for USD {budget.max_token_cost_usd} of model cost, the project budget is "
            f"{quotas.max_run_token_cost_usd}",
            field="loop_budget.max_token_cost_usd",
        )
