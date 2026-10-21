"""Vocabulary for tool admission and execution results (plan V2.0 sections 9.4, 9.6)."""

from enum import StrEnum


class FailurePolicy(StrEnum):
    HALT = "halt"
    RETRY = "retry"
    FALLBACK = "fallback"
    ESCALATE_TO_HUMAN = "escalate_to_human"


class NetworkMode(StrEnum):
    CLOSED = "closed"
    ALLOWLIST = "allowlist"


class AssetScope(StrEnum):
    PROJECT = "project"
    PUBLIC = "public"
    NONE = "none"


class ToolResultStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    POLICY_BLOCKED = "policy_blocked"


UNPINNED_VERSIONS = frozenset({"latest", "main", "master", "dev", "develop", "stable", "current"})
