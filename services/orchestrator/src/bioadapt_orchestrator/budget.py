"""The bounded targeted-analysis loop and the runaway-task breaker (plan V2.0 sections 9.2, 9.7).

Two ceilings apply at once: what the plan asked for and what the project is allowed to spend. The
smaller one wins, and reaching either stops the loop by handing the run to a person - the machine
never decides on its own to keep spending.
"""

from __future__ import annotations

from dataclasses import dataclass

from bioadapt_schemas.plan import LoopBudget


@dataclass(frozen=True)
class LoopUsage:
    iterations: int = 0
    compute_minutes: float = 0.0
    model_cost_usd: float = 0.0


@dataclass(frozen=True)
class LoopCeiling:
    max_iterations: int
    max_compute_minutes: float
    max_model_cost_usd: float

    @classmethod
    def of(
        cls,
        budget: LoopBudget,
        *,
        quota_compute_minutes: int,
        quota_model_cost_usd: float,
    ) -> "LoopCeiling":
        return cls(
            max_iterations=budget.max_iterations,
            max_compute_minutes=min(budget.max_compute_minutes, quota_compute_minutes),
            max_model_cost_usd=min(budget.max_token_cost_usd, quota_model_cost_usd),
        )

    def exhausted(self, usage: LoopUsage) -> list[str]:
        """Why the loop may not run again; empty means there is room for another pass."""
        reasons: list[str] = []
        if usage.iterations > self.max_iterations:
            reasons.append(f"iterations {usage.iterations} > {self.max_iterations}")
        if usage.compute_minutes > self.max_compute_minutes:
            reasons.append(
                f"compute minutes {usage.compute_minutes:g} of {self.max_compute_minutes:g} allowed"
            )
        if usage.model_cost_usd > self.max_model_cost_usd:
            reasons.append(
                f"model cost {usage.model_cost_usd:.2f} USD of {self.max_model_cost_usd:.2f} allowed"
            )
        return reasons


def invalid_resumption_budget(previous: LoopBudget, approved: LoopBudget) -> list[str]:
    """Section 9.2: resuming an escalated run takes a human approval of a *larger* plan budget.

    Comparing budgets instead of counting resumptions is what keeps the loop bounded: every authorised
    restart consumes part of a finite ceiling, so even a determined human cannot spin the run forever.
    """
    pairs = {
        "max_iterations": (previous.max_iterations, approved.max_iterations),
        "max_compute_minutes": (previous.max_compute_minutes, approved.max_compute_minutes),
        "max_token_cost_usd": (previous.max_token_cost_usd, approved.max_token_cost_usd),
    }
    lowered = sorted(name for name, (before, after) in pairs.items() if after < before)
    reasons: list[str] = []
    if lowered:
        reasons.append(f"an escalation approval may not lower a budget ceiling: {lowered}")
    if not any(after > before for before, after in pairs.values()):
        reasons.append("an escalation approval must raise at least one budget ceiling")
    return reasons
