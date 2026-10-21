"""Relational index schema for the platform (plan V2.0 sections 5.1, 5.7-5.9, 9.7, 11.2).

The same spec renders the Postgres migration (production, with row level security) and the SQLite
catalog used by the single-node deployment and the tests, so the two can never drift apart.
"""

from dataclasses import dataclass
from typing import Optional

#: Column types are logical names; each dialect maps them (json -> jsonb on Postgres).
LOGICAL_TYPES = ("text", "int", "float", "bool", "json", "timestamp")

#: Section 11.2: the index never stores key material. Matched on whole name segments so token
#: *counts* (`input_tokens`) stay legal while an `access_token` column does not.
SECRET_SUBSTRINGS = ("secret", "password", "credential", "api_key", "apikey", "private_key", "auth_header")
SECRET_SEGMENTS = frozenset({"secret", "token", "password", "credential", "credentials", "key", "apikey"})


def is_secret_column_name(name: str) -> bool:
    lowered = name.lower()
    if any(bad in lowered for bad in SECRET_SUBSTRINGS):
        return True
    return any(segment in SECRET_SEGMENTS for segment in lowered.split("_"))


@dataclass(frozen=True)
class Col:
    name: str
    type: str
    nullable: bool = True
    primary_key: bool = False

    def __post_init__(self) -> None:
        if self.type not in LOGICAL_TYPES:
            raise ValueError(f"unknown logical type {self.type!r} for column {self.name}")


@dataclass(frozen=True)
class PartialUnique:
    """At most one row matching a condition - used for the 'currently effective' annotation version."""

    columns: tuple[str, ...]
    where_column: str
    where_value: str


@dataclass(frozen=True)
class TableSpec:
    name: str
    cols: tuple[Col, ...]
    project_scoped: bool = True
    append_only: bool = False
    unique_groups: tuple[tuple[str, ...], ...] = ()
    indexes: tuple[tuple[str, ...], ...] = ()
    partial_unique: tuple[PartialUnique, ...] = ()

    def column(self, name: str) -> Optional[Col]:
        return next((c for c in self.cols if c.name == name), None)

    @property
    def primary_key(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.cols if c.primary_key)


C = Col


def _t(name: str, *cols: Col, **kwargs: object) -> TableSpec:
    return TableSpec(name=name, cols=cols, **kwargs)  # type: ignore[arg-type]


APP_SCHEMA: tuple[TableSpec, ...] = (
    _t(
        "project",
        C("project_id", "text", primary_key=True),
        C("study_goal", "text", nullable=False),
        C("species", "text", nullable=False),
        C("privacy_tier", "text", nullable=False),
        C("created_by", "text", nullable=False),
        C("regulatory_profile", "json", nullable=False),
        C("annotation_granularity_target", "text", nullable=False),
        C("review_load_budget_hours_per_week", "float", nullable=False),
        C("thresholds", "json", nullable=False),
        C("created_at", "timestamp", nullable=False),
        project_scoped=False,
    ),
    _t(
        "data_asset",
        C("asset_id", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("uri", "text", nullable=False),
        C("file_sha256", "text", nullable=False),
        C("content_fingerprint", "text", nullable=False),
        C("format", "text", nullable=False),
        C("schema_version", "text", nullable=False),
        C("size_bytes", "int", nullable=False),
        C("privacy_tier", "text", nullable=False),
        C("usage_scope", "text", nullable=False),
        C("retention_class", "text", nullable=False),
        C("filtering_state", "text", nullable=False),
        C("upstream_pipeline", "json"),
        C("genome", "json"),
        C("created_by_run_id", "text"),
        C("created_at", "timestamp", nullable=False),
        unique_groups=(("project_id", "file_sha256"),),
        indexes=(("project_id",), ("content_fingerprint",), ("created_by_run_id",), ("retention_class",)),
    ),
    _t(
        "asset_lineage",
        C("run_id", "text", primary_key=True),
        C("node_id", "text", primary_key=True),
        C("asset_id", "text", primary_key=True),
        C("role", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("ordinal", "int", nullable=False),
        C("created_at", "timestamp", nullable=False),
        indexes=(("asset_id",), ("project_id",)),
        append_only=True,
    ),
    _t(
        "analysis_plan",
        C("plan_id", "text", primary_key=True),
        C("plan_version", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("objective", "text", nullable=False),
        C("plan", "json", nullable=False),
        C("plan_sha256", "text", nullable=False),
        C("created_by", "text", nullable=False),
        C("created_at", "timestamp", nullable=False),
        indexes=(("project_id",), ("plan_sha256",)),
        append_only=True,
    ),
    _t(
        "run_manifest",
        C("run_id", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("plan_id", "text", nullable=False),
        C("plan_version", "text", nullable=False),
        C("tool_versions", "json", nullable=False),
        C("tool_registry_snapshot", "text", nullable=False),
        C("input_hashes", "json", nullable=False),
        C("reproducibility_level_target", "text", nullable=False),
        C("parameters", "json", nullable=False),
        C("state", "text", nullable=False),
        C("started_at", "timestamp"),
        C("finished_at", "timestamp"),
        indexes=(("project_id",), ("state",)),
    ),
    _t(
        "model_invocation",
        C("invocation_id", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("run_id", "text", nullable=False),
        C("model_id", "text", nullable=False),
        C("purpose", "text", nullable=False),
        C("provider_snapshot", "text"),
        C("sampling_params", "json", nullable=False),
        C("prompt_sha256", "text", nullable=False),
        C("output_sha256", "text", nullable=False),
        C("input_tokens", "int", nullable=False),
        C("output_tokens", "int", nullable=False),
        C("cost_usd", "float", nullable=False),
        C("policy_decision", "text", nullable=False),
        C("created_at", "timestamp", nullable=False),
        indexes=(("run_id",), ("project_id",)),
        append_only=True,
    ),
    _t(
        "annotation_version",
        C("version_id", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("run_id", "text", nullable=False),
        C("asset_id", "text", nullable=False),
        C("label_column", "text", nullable=False),
        C("level", "text", nullable=False),
        C("created_by", "text", nullable=False),
        C("status", "text", nullable=False),
        C("superseded_by", "text"),
        C("created_at", "timestamp", nullable=False),
        unique_groups=(("asset_id",),),
        indexes=(("run_id",), ("project_id",)),
        partial_unique=(PartialUnique(("run_id", "label_column"), "status", "active"),),
    ),
    _t(
        "candidate_set",
        C("candidate_id", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("run_id", "text", nullable=False),
        C("member_fingerprint", "text", nullable=False),
        C("size", "int", nullable=False),
        C("donor_count", "int", nullable=False),
        C("display_name", "text", nullable=False),
        C("cluster_origin", "json", nullable=False),
        C("candidate_types", "json", nullable=False),
        #: Section 5.6: an alignment decision is recorded on the candidate it was made for.
        C("alignment_history", "json", nullable=False),
        indexes=(("run_id",), ("project_id",), ("member_fingerprint",), ("display_name",)),
    ),
    _t(
        "evidence_card",
        C("card_id", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("run_id", "text", nullable=False),
        C("candidate_id", "text", nullable=False),
        C("final_status", "text", nullable=False),
        C("annotation_version_id", "text"),
        C("card", "json", nullable=False),
        C("created_at", "timestamp", nullable=False),
        C("updated_at", "timestamp", nullable=False),
        indexes=(("candidate_id",), ("run_id",), ("project_id",)),
    ),
    _t(
        "review_event",
        C("review_id", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("object_id", "text", nullable=False),
        C("object_type", "text", nullable=False),
        C("scope", "text", nullable=False),
        C("reviewer_id", "text", nullable=False),
        C("decision", "text", nullable=False),
        C("rationale", "text", nullable=False),
        C("supersedes_review_id", "text"),
        C("carry_over_from_candidate_id", "text"),
        C("retraction_reason", "text"),
        C("previous_value", "json"),
        C("new_value", "json"),
        C("timestamp", "timestamp", nullable=False),
        indexes=(("object_id",), ("project_id",)),
        append_only=True,
    ),
    _t(
        "run_event",
        C("event_id", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("run_id", "text", nullable=False),
        #: Journal order. Timestamps tie (the clock is second-resolution and a callback burst is
        #: normal), so reads sort on the position the event was appended at, assigned per run at
        #: insert time. On Postgres the same value comes from the run row lock, not from a race.
        C("seq", "int", nullable=False),
        C("kind", "text", nullable=False),
        C("from_state", "text", nullable=False),
        C("to_state", "text", nullable=False),
        C("actor_id", "text", nullable=False),
        C("actor_kind", "text", nullable=False),
        #: Section 9.2: no transition without a stated reason.
        C("reason", "text", nullable=False),
        C("input_hash", "text", nullable=False),
        C("versions", "json", nullable=False),
        C("attempt", "int", nullable=False),
        C("node_id", "text"),
        C("loop_iteration", "int", nullable=False),
        C("compute_minutes", "float", nullable=False),
        C("model_cost_usd", "float", nullable=False),
        C("created_at", "timestamp", nullable=False),
        indexes=(("run_id", "seq"), ("project_id",), ("run_id", "kind")),
        append_only=True,
    ),
    _t(
        "approval",
        C("approval_id", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("run_id", "text", nullable=False),
        C("kind", "text", nullable=False),
        C("node_id", "text"),
        #: What was signed off: a plan or node digest. Change the subject and the approval no longer applies.
        C("subject_digest", "text", nullable=False),
        C("requested_by", "text", nullable=False),
        #: Section 9.2: an approval is accountable to someone, and that is who may delegate it.
        C("assigned_to", "text", nullable=False),
        C("requested_at", "timestamp", nullable=False),
        C("sla_at", "timestamp", nullable=False),
        C("state", "text", nullable=False),
        C("decided_by", "text"),
        C("decided_at", "timestamp"),
        C("decision_reason", "text"),
        C("delegated_from", "text"),
        C("delegated_at", "timestamp"),
        indexes=(("run_id",), ("project_id",), ("state",)),
    ),
    _t(
        "tool_grant",
        C("grant_id", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("run_id", "text", nullable=False),
        C("node_id", "text", nullable=False),
        C("plan_id", "text", nullable=False),
        C("plan_version", "text", nullable=False),
        #: Section 9.6: the ad-hoc gate whose decision this grant executes, and the key that makes a
        #: second issue of the same permission a collision rather than another execution.
        C("approval_id", "text", nullable=False),
        C("tool_name", "text", nullable=False),
        C("tool_version", "text", nullable=False),
        C("spec", "json", nullable=False),
        C("spec_sha256", "text", nullable=False),
        C("requested_by", "text", nullable=False),
        C("approver_id", "text", nullable=False),
        C("reason", "text", nullable=False),
        C("approved_at", "timestamp", nullable=False),
        C("expires_at", "timestamp", nullable=False),
        C("max_uses", "int", nullable=False),
        #: Spending is a write on the row that grants, not a second row: one gate buys one execution, and
        #: a nullable stamp is the whole of that promise once the process that held the registry is gone.
        C("spent_at", "timestamp"),
        unique_groups=(("project_id", "approval_id"),),
        indexes=(("project_id",), ("run_id",), ("tool_name", "tool_version"), ("approval_id",)),
    ),
    _t(
        "run_checkpoint",
        C("project_id", "text", primary_key=True),
        C("run_id", "text", primary_key=True),
        C("node_id", "text", primary_key=True),
        #: Section 4.1: a checkpoint is reusable for exactly one execution identity - this tool at this
        #: version, over these inputs, under these parameters and this registry snapshot. Keying on the
        #: identity rather than on an attempt counter means a node re-run after a plan edit is a second
        #: row beside the first, so resuming never overwrites the provenance of what is still stored.
        C("tool_name", "text", primary_key=True),
        C("tool_version", "text", primary_key=True),
        C("input_digest", "text", primary_key=True),
        C("parameters_sha256", "text", primary_key=True),
        C("registry_snapshot", "text", primary_key=True),
        #: Which attempt of its pass produced this, as the journal's usage and retry events number it.
        C("attempt", "int", nullable=False),
        C("output_asset_ids", "json", nullable=False),
        C("output_digests", "json", nullable=False),
        C("compute_minutes", "float", nullable=False),
        C("created_at", "timestamp", nullable=False),
        indexes=(("run_id",), ("project_id",), ("run_id", "node_id")),
        append_only=True,
    ),
    _t(
        "egress_decision",
        C("decision_id", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("allowed", "bool", nullable=False),
        C("request", "json", nullable=False),
        C("decision", "json", nullable=False),
        C("created_at", "timestamp", nullable=False),
        indexes=(("project_id",)),
        append_only=True,
    ),
    _t(
        "idempotency_request",
        C("project_id", "text", primary_key=True),
        C("idempotency_id", "text", primary_key=True),
        C("route", "text", nullable=False),
        C("request_hash", "text", nullable=False),
        C("resource_kind", "text", nullable=False),
        C("resource_id", "text", nullable=False),
        C("created_at", "timestamp", nullable=False),
        indexes=(("project_id",), ("resource_id",)),
        append_only=True,
    ),
    _t(
        "export_job",
        C("export_id", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("kind", "text", nullable=False),
        C("status", "text", nullable=False),
        C("filters", "json", nullable=False),
        C("requested_by", "text", nullable=False),
        C("artifact_asset_id", "text"),
        C("failure_reason", "text"),
        C("created_at", "timestamp", nullable=False),
        C("updated_at", "timestamp", nullable=False),
        indexes=(("project_id",), ("status",)),
    ),
    _t(
        "egress_approval",
        C("approval_id", "text", primary_key=True),
        C("project_id", "text", nullable=False),
        C("approver_id", "text", nullable=False),
        C("purpose", "text", nullable=False),
        C("approved_on", "text", nullable=False),
        C("expires_on", "text", nullable=False),
        C("created_at", "timestamp", nullable=False),
        indexes=(("project_id",), ("purpose",)),
        append_only=True,
    ),
    _t(
        "audit_log",
        C("entry_id", "text", primary_key=True),
        C("project_id", "text"),
        C("subject_id", "text", nullable=False),
        C("subject_kind", "text", nullable=False),
        C("action", "text", nullable=False),
        C("resource_id", "text", nullable=False),
        C("decision_id", "text"),
        C("trace_id", "text"),
        C("details", "json", nullable=False),
        C("created_at", "timestamp", nullable=False),
        indexes=(("project_id",), ("subject_id",), ("trace_id",)),
        append_only=True,
    ),
)

TABLES_BY_NAME = {t.name: t for t in APP_SCHEMA}


def lint(specs: tuple[TableSpec, ...] = APP_SCHEMA) -> list[str]:
    """Structural invariants the platform depends on; each maps to a plan requirement."""
    violations: list[str] = []
    for table in specs:
        for col in table.cols:
            if is_secret_column_name(col.name):
                violations.append(f"{table.name}.{col.name}: key material must not be stored in the index")
        if table.name == "project":
            continue
        if not table.project_scoped:
            violations.append(f"{table.name}: every non-project table must be project scoped")
        if table.project_scoped and table.column("project_id") is None:
            violations.append(f"{table.name}: project scoped tables need a project_id column")
        if not table.primary_key:
            violations.append(f"{table.name}: no primary key")
        if table.append_only:
            time_col = next((c for c in table.cols if c.type == "timestamp"), None)
            if time_col is None:
                violations.append(f"{table.name}: append-only tables must record when the row was written")
    if not any(t.name == "project" for t in specs):
        violations.append("the schema must declare the project table")
    return violations
