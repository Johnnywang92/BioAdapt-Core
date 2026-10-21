"""Who may retry what (plan V2.0 section 9.2, Q-G-03).

The state machine owns business retries - count and backoff come from the plan node's
``retry_policy``. The workflow engine keeps its own node-level fault tolerance, but every attempt
must be reported here, and the same ceiling bounds both: an engine that retries past the plan's
limit has stopped being fault-tolerant and started hiding a failure.
"""

from __future__ import annotations

from datetime import datetime

from bioadapt_schemas.plan import RetryPolicy

from .errors import BackoffPending, RetryRefused


def assert_attempt_within_policy(policy: RetryPolicy, attempt: int, *, kind: str) -> None:
    if attempt > policy.max_attempts:
        raise RetryRefused(
            f"{kind} attempt {attempt} exceeds the plan's retry_policy.max_attempts "
            f"{policy.max_attempts}; the plan, not the caller, sets how often this is retried",
            field="attempt",
        )


def seconds_until_retry(policy: RetryPolicy, last_attempt_at: datetime | None, now: datetime) -> int:
    if last_attempt_at is None or policy.backoff_seconds == 0:
        return 0
    remaining = policy.backoff_seconds - int((now - last_attempt_at).total_seconds())
    return max(remaining, 0)


def assert_backoff_elapsed(
    policy: RetryPolicy, last_attempt_at: datetime | None, now: datetime, *, kind: str
) -> None:
    remaining = seconds_until_retry(policy, last_attempt_at, now)
    if remaining:
        raise BackoffPending(
            f"{kind} must wait {remaining}s: the plan sets a {policy.backoff_seconds}s backoff "
            "between retries",
            retry_after=remaining,
        )
