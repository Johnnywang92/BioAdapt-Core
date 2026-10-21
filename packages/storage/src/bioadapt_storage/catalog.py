"""SQLite reference implementation of the relational index (plan V2.0 sections 5.7-5.9, 9.7).

Postgres with row level security is the production path; this catalog is the single-node and test
path, and it enforces the same two invariants: a session project scope on every statement, and
append-only evidence tables.
"""

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Callable, Optional

from pydantic import BaseModel

from bioadapt_schemas.entities import (
    AnnotationVersion,
    AssetLineage,
    CandidateSet,
    DataAsset,
    ModelInvocation,
    Project,
    ReviewEvent,
    RunManifest,
    sha256_of_json,
)
from bioadapt_schemas.enums import RunEventKind, RunState, UsageScope
from bioadapt_schemas.evidence import EvidenceCard
from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_schemas.orchestration import Approval, RunCheckpoint, RunEvent
from bioadapt_schemas.plan import AnalysisPlan

from .ddl import SCOPE_GUC, render_sqlite
from .schema import APP_SCHEMA, TABLES_BY_NAME

#: Section 5.8: only these scopes make an asset visible to another project's retrieval layer.
REUSABLE_SCOPES = (UsageScope.INTERNAL_IMPROVEMENT, UsageScope.CROSS_PROJECT_ALLOWED)


class ScopeError(RuntimeError):
    pass


#: A compare-and-set that found the row had already moved. The gate exists and is still open; this write
#: simply is not the one that gets to close it.
class ConcurrentWriteError(RuntimeError):
    pass


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _dumps(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), default=str)


class _Result:
    """A statement's rows, read out while the connection lock is still held."""

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._rows = cursor.fetchall()
        self._at = 0
        self.lastrowid = cursor.lastrowid
        self.rowcount = cursor.rowcount

    def fetchone(self) -> Optional[sqlite3.Row]:
        row = self._rows[self._at] if self._at < len(self._rows) else None
        self._at += 1
        return row

    def fetchall(self) -> list[sqlite3.Row]:
        rows = self._rows[self._at :]
        self._at = len(self._rows)
        return rows

    def __iter__(self) -> Iterator[sqlite3.Row]:
        return iter(self.fetchall())


class _LockedConnection:
    """One sqlite connection used by every request thread of a reference deployment.

    sqlite3 hands a shared connection to two threads without ``check_same_thread=False``, and that
    combination deadlocks: a thread inside a statement waits for the GIL to call the scope function
    while another thread holds the GIL and waits for the connection. Every statement is therefore
    run under one re-entrant lock, and its rows are materialised before the lock is released so no
    cursor outlives the statement that made it.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    def execute(self, sql: str, params: Any = ()) -> _Result:
        with self._lock:
            return _Result(self._conn.execute(sql, params))

    def executescript(self, sql: str) -> None:
        with self._lock:
            self._conn.executescript(sql)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class CatalogSession:
    """One session's scope over a connection that several sessions share.

    ``set_scope`` records a *session* property, and a reference deployment answers requests on a
    threadpool - FastAPI runs a route's dependencies and its body in separate threadpool submissions,
    so the two can land on different worker threads. A scope stored on the connection is then either
    another request's (which is how one project reads another's rows) or missing (which fails closed as
    a 403). Binding the scope for the duration of each call makes it a property of the session that
    made the call, which is what the row-level scope was always meant to be.
    """

    def __init__(self, catalog: "Catalog", project_id: str) -> None:
        self._catalog = catalog
        self._project_id = project_id

    @property
    def project_id(self) -> str:
        return self._project_id

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._catalog, name)
        if not callable(attribute):
            return attribute

        def bound(*args: Any, **kwargs: Any) -> Any:
            local = self._catalog._local
            previous = getattr(local, "scope", None)
            local.scope = self._project_id
            try:
                return attribute(*args, **kwargs)
            finally:
                local.scope = previous

        return bound


class Catalog:
    def __init__(
        self,
        path: str = ":memory:",
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        check_same_thread: bool = True,
    ):
        #: A connection stays thread-affine unless the caller opts out; only the single-node
        #: reference deployment (whose API answers on a threadpool) passes False. Opting out shares
        #: one connection across threads, so statements are serialised (see ``_LockedConnection``)
        #: and the project scope is kept per thread - a scope is a session property, not a global one.
        #: Autocommit: the index has no request-scoped transaction, so a write that returns has
        #: landed. Multi-statement steps say so explicitly (see ``record_annotation_version``).
        self._lock = threading.RLock()
        self._local = threading.local()
        raw = sqlite3.connect(path, check_same_thread=check_same_thread, isolation_level=None)
        raw.row_factory = sqlite3.Row
        raw.create_function("current_setting", 2, self._current_setting)
        self.conn = _LockedConnection(raw, self._lock)
        self.clock = clock
        self.conn.executescript(render_sqlite(APP_SCHEMA))

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ session scope

    @property
    def _scope(self) -> Optional[str]:
        return getattr(self._local, "scope", None)

    @property
    def scope(self) -> str:
        if self._scope is None:
            raise ScopeError("no project scope set; statements are denied by default")
        return self._scope

    def set_scope(self, project_id: str) -> "Catalog":
        self._local.scope = project_id
        return self

    def clear_scope(self) -> None:
        self._local.scope = None

    def session(self, project_id: str) -> CatalogSession:
        """A view that re-binds this project's scope on every call; what a request should hold."""
        return CatalogSession(self, project_id)

    def _current_setting(self, key: str, default: str = "") -> str:
        return (self._scope or default) if key == SCOPE_GUC else default

    # ------------------------------------------------------------------ row encoding

    def _encode(self, table: str, column: str, value: Any) -> Any:
        col = TABLES_BY_NAME[table].column(column)
        if col is None:
            raise ValueError(f"{table} has no column {column}")
        if value is None:
            return None
        if col.type == "json":
            return _dumps(value)
        if col.type == "timestamp":
            return value.isoformat() if isinstance(value, datetime) else str(value)
        if col.type == "bool":
            return int(bool(value))
        return value

    def _decode(self, table: str, row: sqlite3.Row) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for column, value in dict(row).items():
            col = TABLES_BY_NAME[table].column(column)
            if value is None or col is None:
                out[column] = value
            elif col.type == "json":
                out[column] = json.loads(value)
            elif col.type == "timestamp":
                out[column] = datetime.fromisoformat(value)
            elif col.type == "bool":
                out[column] = bool(value)
            else:
                out[column] = value
        return out

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Explicit unit of work: autocommit is the default, so multi-statement steps opt in.

        The lock is held for the whole block: on one shared connection a ``begin`` in this thread
        would otherwise let another thread's statement land inside this transaction.
        """
        with self._lock:
            self.conn.execute("begin")
            try:
                yield
            except Exception:
                self.conn.execute("rollback")
                raise
            self.conn.execute("commit")

    def _insert(self, table: str, values: dict[str, Any]) -> None:
        spec = TABLES_BY_NAME[table]
        missing = [c.name for c in spec.cols if not c.nullable and c.name not in values]
        if missing:
            raise ValueError(f"{table}: missing {missing}")
        names = [c.name for c in spec.cols]
        self.conn.execute(
            f'insert into "{table}" ({", ".join(names)}) values ({", ".join("?" for _ in names)})',
            [self._encode(table, name, values.get(name)) for name in names],
        )

    def _contract(self, table: str, row: sqlite3.Row, drop: tuple[str, ...] = ()) -> dict[str, Any]:
        skip = set(drop)
        return {k: v for k, v in self._decode(table, row).items() if k not in skip}

    def _fields_of(self, table: str, *drop: str) -> tuple[str, ...]:
        return tuple(c.name for c in TABLES_BY_NAME[table].cols if c.name not in drop)

    def _scoped(
        self, table: str, where: str = "1=1", *params: Any, limit: Optional[int] = None, offset: int = 0
    ) -> list[sqlite3.Row]:
        sql = f'select * from "{table}" where "project_id" = ? and {where}'
        tail: tuple[Any, ...] = ()
        if limit is not None or offset:
            #: An offset with no limit still has to be applied - silently dropping it would hand a
            #: caller that asked for the tail the whole head of the table. SQLite reads LIMIT -1 as
            #: "no limit".
            sql += " limit ? offset ?"
            tail = (limit if limit is not None else -1, offset)
        return list(self.conn.execute(sql, (self.scope, *params, *tail)))

    def _one(self, table: str, where: str, *params: Any) -> sqlite3.Row:
        rows = self._scoped(table, where, *params)
        if not rows:
            raise LookupError(f"{where} matched nothing in project {self.scope}")
        return rows[0]

    # ------------------------------------------------------------------ project

    def create_project(self, project: Project) -> None:
        self.set_scope(project.project_id)
        values = project.model_dump(mode="python")
        values["created_at"] = project.created_at or self.clock()
        self._insert("project", values)

    def get_project(self) -> Project:
        rows = self._scoped("project", "project_id = ?", self.scope)
        if not rows:
            raise LookupError(f"project {self.scope} is not registered")
        return Project(**self._decode("project", rows[0]))

    # ------------------------------------------------------------------ assets and lineage

    def record_asset(self, asset: DataAsset) -> str:
        """Content-addressed insert: a byte-identical re-upload resolves to the existing asset."""
        row = self.conn.execute(
            "select asset_id from data_asset where project_id = ? and file_sha256 = ?",
            (self.scope, asset.file_sha256),
        ).fetchone()
        if row:
            return row["asset_id"]
        self._insert("data_asset", asset.model_dump(mode="python"))
        return asset.asset_id

    def get_asset(self, asset_id: str) -> DataAsset:
        return DataAsset(**self._contract("data_asset", self._one("data_asset", "asset_id = ?", asset_id)))

    def list_assets(
        self,
        *,
        retention_class: Optional[str] = None,
        run_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[DataAsset]:
        where, params = "1=1", []
        if retention_class:
            where += " and retention_class = ?"
            params.append(retention_class)
        if run_id:
            where += " and created_by_run_id = ?"
            params.append(run_id)
        rows = self._scoped("data_asset", f"{where} order by created_at, asset_id", *params, limit=limit, offset=offset)
        return [DataAsset(**self._decode("data_asset", row)) for row in rows]

    def storage_bytes(self) -> int:
        row = self.conn.execute(
            "select coalesce(sum(size_bytes), 0) as used from data_asset where project_id = ?", (self.scope,)
        ).fetchone()
        return int(row["used"])

    def shared_assets(self, scopes: tuple[UsageScope, ...] = REUSABLE_SCOPES) -> list[DataAsset]:
        """Section 5.8: retrieval may only hit explicitly reusable assets belonging to other projects."""
        marks = ", ".join("?" for _ in scopes)
        rows = self.conn.execute(
            f"select * from data_asset where project_id <> ? and usage_scope in ({marks})",
            (self.scope, *[s.value for s in scopes]),
        )
        return [DataAsset(**self._decode("data_asset", row)) for row in rows]

    def shared_asset(
        self, asset_id: str, scopes: tuple[UsageScope, ...] = REUSABLE_SCOPES
    ) -> Optional[DataAsset]:
        """One foreign asset by id. The same §5.8 filter as ``shared_assets``, without reading the rest."""
        marks = ", ".join("?" for _ in scopes)
        row = self.conn.execute(
            f"select * from data_asset where asset_id = ? and project_id <> ? and usage_scope in ({marks}) limit 1",
            (asset_id, self.scope, *[s.value for s in scopes]),
        ).fetchone()
        return DataAsset(**self._decode("data_asset", row)) if row is not None else None

    def record_lineage(self, lineage: AssetLineage) -> None:
        """Idempotent: a replayed node attempt re-records the same edge instead of colliding with it."""
        exists = self.conn.execute(
            "select 1 from asset_lineage where run_id = ? and node_id = ? and asset_id = ? and role = ?",
            (lineage.run_id, lineage.node_id, lineage.asset_id, lineage.role.value),
        ).fetchone()
        if exists:
            return
        self._insert(
            "asset_lineage",
            {**lineage.model_dump(mode="python"), "project_id": self.scope, "created_at": self.clock()},
        )

    def lineage(self, asset_id: str) -> list[AssetLineage]:
        fields = self._fields_of("asset_lineage", "project_id", "created_at")
        return [
            AssetLineage(**{k: v for k, v in self._decode("asset_lineage", row).items() if k in fields})
            for row in self._scoped("asset_lineage", "asset_id = ? order by ordinal", asset_id)
        ]

    # ------------------------------------------------------------------ plans

    def record_plan(self, plan: AnalysisPlan, *, created_by: str) -> str:
        """Section 9.7: plans are versioned and immutable; runs pin a digest, never a mutable plan."""
        digest = sha256_of_json(plan.model_dump(mode="json"))
        self._insert(
            "analysis_plan",
            {
                "plan_id": plan.plan_id,
                "plan_version": plan.plan_version,
                "project_id": self.scope,
                "objective": plan.objective,
                "plan": plan,
                "plan_sha256": digest,
                "created_by": created_by,
                "created_at": self.clock(),
            },
        )
        return digest

    def get_plan(self, plan_id: str, plan_version: str) -> AnalysisPlan:
        row = self._one("analysis_plan", "plan_id = ? and plan_version = ?", plan_id, plan_version)
        return AnalysisPlan(**self._decode("analysis_plan", row)["plan"])

    def plan_digest(self, plan_id: str, plan_version: str) -> str:
        row = self.conn.execute(
            "select plan_sha256 from analysis_plan where project_id = ? and plan_id = ? and plan_version = ?",
            (self.scope, plan_id, plan_version),
        ).fetchone()
        if row is None:
            raise LookupError(f"plan {plan_id}@{plan_version} not found in project {self.scope}")
        return row["plan_sha256"]

    def plan_author(self, plan_id: str, plan_version: str) -> str:
        """Section 13.1: the subject who authored a plan is needed to police self sign-off."""
        row = self.conn.execute(
            "select created_by from analysis_plan where project_id = ? and plan_id = ? and plan_version = ?",
            (self.scope, plan_id, plan_version),
        ).fetchone()
        if row is None:
            raise LookupError(f"plan {plan_id}@{plan_version} not found in project {self.scope}")
        return row["created_by"]

    # ------------------------------------------------------------------ runs

    def record_run(self, manifest: RunManifest, state: str) -> None:
        values = manifest.model_dump(mode="python")
        values["state"] = state
        self._insert("run_manifest", values)

    def get_run(self, run_id: str) -> RunManifest:
        fields = self._fields_of("run_manifest", "state")
        row = self._one("run_manifest", "run_id = ?", run_id)
        return RunManifest(**{k: v for k, v in self._decode("run_manifest", row).items() if k in fields})

    def set_run_plan(self, run_id: str, *, plan_version: str, tool_versions: dict[str, str]) -> None:
        """Section 9.2: restarting an escalated run re-pins the plan version a person approved."""
        cur = self.conn.execute(
            "update run_manifest set plan_version = ?, tool_versions = ? where project_id = ? and run_id = ?",
            (plan_version, self._encode("run_manifest", "tool_versions", tool_versions), self.scope, run_id),
        )
        if cur.rowcount != 1:
            raise LookupError(f"run {run_id} not found in project {self.scope}")

    def set_run_state(self, run_id: str, state: str) -> None:
        """Only the orchestrator writes here: a state change without its journal line is a bug."""
        cur = self.conn.execute(
            "update run_manifest set state = ? where project_id = ? and run_id = ?",
            (state, self.scope, run_id),
        )
        if cur.rowcount != 1:
            raise LookupError(f"run {run_id} not found in project {self.scope}")

    def run_state(self, run_id: str) -> str:
        row = self.conn.execute(
            "select state from run_manifest where project_id = ? and run_id = ?", (self.scope, run_id)
        ).fetchone()
        if row is None:
            raise LookupError(f"run {run_id} not found in project {self.scope}")
        return row["state"]

    def list_runs(
        self, *, state: Optional[str] = None, limit: Optional[int] = None, offset: int = 0
    ) -> list[dict[str, Any]]:
        where = "state = ?" if state else "1=1"
        params: tuple[Any, ...] = (state,) if state else ()
        rows = self._scoped("run_manifest", f"{where} order by run_id", *params, limit=limit, offset=offset)
        return [self._decode("run_manifest", row) for row in rows]

    def count_runs_not_in(self, states: list[str]) -> int:
        if not states:
            raise ValueError("at least one state must be excluded")
        marks = ", ".join("?" for _ in states)
        row = self.conn.execute(
            f"select count(*) as n from run_manifest where project_id = ? and state not in ({marks})",
            (self.scope, *states),
        ).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------------ orchestration

    def _run_event(self, row: sqlite3.Row) -> RunEvent:
        fields = self._contract("run_event", row, ("project_id", "seq"))
        fields["at"] = fields.pop("created_at")
        return RunEvent(**fields)

    def has_run_event(self, event_id: str) -> bool:
        return self.conn.execute("select 1 from run_event where event_id = ?", (event_id,)).fetchone() is not None

    def append_run_event(self, event: RunEvent) -> bool:
        """Section 9.2: a repeated callback for the same attempt is a no-op, not a second journal line."""
        if self.has_run_event(event.event_id):
            return False
        #: The caller names the run in the event, so the scope has to vouch for it before a line lands;
        #: otherwise a run id from another project becomes a row in this project's history (§11.3 row 1).
        if self.conn.execute(
            "select 1 from run_manifest where project_id = ? and run_id = ?", (self.scope, event.run_id)
        ).fetchone() is None:
            raise LookupError(f"run {event.run_id} not found in project {self.scope}")
        position = self.conn.execute(
            "select coalesce(max(seq), 0) + 1 as seq from run_event where project_id = ? and run_id = ?",
            (self.scope, event.run_id),
        ).fetchone()["seq"]
        self._insert(
            "run_event",
            {
                **event.model_dump(mode="python"),
                "project_id": self.scope,
                "seq": position,
                "created_at": event.at or self.clock(),
            },
        )
        return True

    def apply_transition(self, event: RunEvent, *, finished_at: Optional[datetime] = None) -> bool:
        """The journal line and the run's current state land together, or the transition did not happen."""
        with self._transaction():
            if not self.append_run_event(event):
                return False
            self.set_run_state(event.run_id, str(event.to_state))
            if finished_at is not None:
                self.conn.execute(
                    "update run_manifest set finished_at = ? where project_id = ? and run_id = ?",
                    (finished_at.isoformat(), self.scope, event.run_id),
                )
        return True

    def get_run_event(self, event_id: str) -> RunEvent:
        return self._run_event(self._one("run_event", "event_id = ?", event_id))

    def list_run_events(
        self,
        run_id: str,
        *,
        kind: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[RunEvent]:
        where, params = "run_id = ?", [run_id]
        if kind:
            where += " and kind = ?"
            params.append(kind)
        rows = self._scoped("run_event", f"{where} order by seq", *params, limit=limit, offset=offset)
        return [self._run_event(row) for row in rows]

    def loop_iterations(self, run_id: str) -> int:
        """Section 9.2: an iteration is one entry into the bounded targeted-analysis loop."""
        row = self.conn.execute(
            "select count(*) as n from run_event where project_id = ? and run_id = ? and to_state = ?",
            (self.scope, run_id, str(RunState.TARGETED_ANALYSIS)),
        ).fetchone()
        return int(row["n"])

    def usage_totals(self, run_id: str) -> tuple[float, float]:
        row = self.conn.execute(
            "select coalesce(sum(compute_minutes), 0) as minutes, coalesce(sum(model_cost_usd), 0) as cost"
            " from run_event where project_id = ? and run_id = ? and kind = ?",
            (self.scope, run_id, str(RunEventKind.USAGE)),
        ).fetchone()
        return float(row["minutes"]), float(row["cost"])

    def last_node_event(self, run_id: str, kind: str, node_id: str) -> Optional[RunEvent]:
        row = self.conn.execute(
            "select * from run_event where project_id = ? and run_id = ? and kind = ? and node_id = ?"
            " order by created_at desc, event_id desc limit 1",
            (self.scope, run_id, kind, node_id),
        ).fetchone()
        return self._run_event(row) if row is not None else None

    def last_event_into_state(self, run_id: str, to_state: str, *, kind: Optional[str] = None) -> Optional[RunEvent]:
        """The newest journal line that put the run into ``to_state``, read without walking the journal.

        Sorted on ``seq`` rather than on ``created_at`` because the clock is second-resolution and a
        callback burst ties; ``seq`` is the position the line was appended at.
        """
        where, params = "run_id = ? and to_state = ?", [run_id, to_state]
        if kind:
            where += " and kind = ?"
            params.append(kind)
        row = self.conn.execute(
            f'select * from run_event where "project_id" = ? and {where} order by seq desc limit 1',
            (self.scope, *params),
        ).fetchone()
        return self._run_event(row) if row is not None else None

    # ------------------------------------------------------------------ checkpoints

    def _checkpoint(self, row: sqlite3.Row) -> RunCheckpoint:
        fields = self._contract("run_checkpoint", row, ("project_id",))
        fields["at"] = fields.pop("created_at")
        return RunCheckpoint(**fields)

    def record_checkpoint(self, checkpoint: RunCheckpoint) -> None:
        """§4.1: one checkpoint per execution identity, written when the attempt produced its assets.

        A node whose inputs, parameters or registry snapshot changed is a different identity and lands
        beside the row it displaces, so resuming never overwrites the provenance of assets that are
        still in the index.
        """
        self._insert(
            "run_checkpoint",
            {
                **checkpoint.model_dump(mode="python"),
                "project_id": self.scope,
                "created_at": checkpoint.at or self.clock(),
            },
        )

    def matching_checkpoint(
        self,
        run_id: str,
        node_id: str,
        *,
        tool_name: str,
        tool_version: str,
        input_digest: str,
        parameters_sha256: str,
        registry_snapshot: str,
    ) -> Optional[RunCheckpoint]:
        """§4.1's reuse test is a keyed lookup, not a comparison: the row exists only for exactly this
        tool, these inputs, these parameters and this registry snapshot."""
        rows = self._scoped(
            "run_checkpoint",
            "run_id = ? and node_id = ? and tool_name = ? and tool_version = ? and input_digest = ?"
            " and parameters_sha256 = ? and registry_snapshot = ?",
            run_id,
            node_id,
            tool_name,
            tool_version,
            input_digest,
            parameters_sha256,
            registry_snapshot,
            limit=1,
        )
        return self._checkpoint(rows[0]) if rows else None

    def list_checkpoints(self, run_id: str) -> list[RunCheckpoint]:
        return [
            self._checkpoint(row)
            for row in self._scoped("run_checkpoint", "run_id = ? order by node_id, attempt", run_id)
        ]

    # ------------------------------------------------------------------ approvals

    def _approval(self, row: sqlite3.Row) -> Approval:
        return Approval(**self._contract("approval", row, ("project_id",)))

    def record_approval(self, approval: Approval) -> str:
        """Subject-addressed: asking again for the same digest resolves to the approval already open."""
        existing = self.conn.execute(
            "select 1 from approval where project_id = ? and approval_id = ?", (self.scope, approval.approval_id)
        ).fetchone()
        if existing is None:
            self._insert("approval", {**approval.model_dump(mode="python"), "project_id": self.scope})
        return approval.approval_id

    def get_approval(self, approval_id: str) -> Approval:
        return self._approval(self._one("approval", "approval_id = ?", approval_id))

    def find_approval(
        self, *, run_id: str, kind: str, subject_digest: str, node_id: Optional[str] = None
    ) -> Optional[Approval]:
        where = "run_id = ? and kind = ? and subject_digest = ?"
        params: list[Any] = [run_id, kind, subject_digest]
        if node_id is None:
            where += " and node_id is null"
        else:
            where += " and node_id = ?"
            params.append(node_id)
        rows = self._scoped("approval", where, *params)
        return self._approval(rows[0]) if rows else None

    def list_approvals(
        self,
        *,
        state: Optional[str] = None,
        run_id: Optional[str] = None,
        due_before: Optional[datetime] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[Approval]:
        where, params = "1=1", []
        if state:
            where += " and state = ?"
            params.append(state)
        if run_id:
            where += " and run_id = ?"
            params.append(run_id)
        if due_before is not None:
            where += " and state = 'pending' and sla_at < ?"
            params.append(due_before.isoformat())
        rows = self._scoped(
            "approval", f"{where} order by requested_at, approval_id", *params, limit=limit, offset=offset
        )
        return [self._approval(row) for row in rows]

    def _mutate_approval(
        self, approval_id: str, sets: dict[str, Any], *, read_by: Optional[str] = None, only_pending: bool = True
    ) -> Approval:
        """Compare-and-set on what the caller read: pending, and still held by the person they saw hold it.

        Section 9.2: a decision and a delegation are each a read followed by a write, so without restating
        the holder an interleaving lets a person who has already handed the gate away record the decision
        anyway - the row would say they decided it while the delegate finds it closed by someone else.
        """
        guards = ["project_id = ?", "approval_id = ?"]
        if only_pending:
            guards.append("state = 'pending'")
        params = [self._encode("approval", name, value) for name, value in sets.items()]
        params.extend([self.scope, approval_id])
        if read_by is not None:
            guards.append("assigned_to = ?")
            params.append(read_by)
        clause = ", ".join(f'"{name}" = ?' for name in sets)
        cur = self.conn.execute(f'update approval set {clause} where {" and ".join(guards)}', params)
        if cur.rowcount == 1:
            return self.get_approval(approval_id)
        if read_by is not None and self.conn.execute(
            "select 1 from approval where project_id = ? and approval_id = ? and state = 'pending'",
            (self.scope, approval_id),
        ).fetchone():
            raise ConcurrentWriteError(
                f"approval {approval_id} is no longer held by {read_by}; it moved while this write was in flight"
            )
        raise LookupError(f"no pending approval {approval_id} in project {self.scope}")

    def decide_approval(
        self,
        approval_id: str,
        *,
        state: str,
        decided_by: str,
        decided_at: datetime,
        reason: str,
        read_by: Optional[str] = None,
    ) -> Approval:
        return self._mutate_approval(
            approval_id,
            {"state": state, "decided_by": decided_by, "decided_at": decided_at, "decision_reason": reason},
            read_by=read_by,
        )

    def delegate_approval(
        self,
        approval_id: str,
        *,
        from_subject: str,
        to_subject: str,
        at: datetime,
        read_by: Optional[str] = None,
    ) -> Approval:
        return self._mutate_approval(
            approval_id,
            {"assigned_to": to_subject, "delegated_from": from_subject, "delegated_at": at},
            read_by=from_subject if read_by is None else read_by,
        )

    def record_model_invocation(self, invocation: ModelInvocation, *, run_id: str) -> str:
        """Content addressed: replaying an identical invocation resolves to the existing row."""
        invocation_id = "inv_" + sha256_hex(_dumps(invocation.model_dump(mode="json")).encode())[:12]
        existing = self.conn.execute(
            "select 1 from model_invocation where invocation_id = ?", (invocation_id,)
        ).fetchone()
        if existing is not None:
            return invocation_id
        self._insert(
            "model_invocation",
            {
                **invocation.model_dump(mode="python"),
                "invocation_id": invocation_id,
                "project_id": self.scope,
                "run_id": run_id,
                "created_at": self.clock(),
            },
        )
        return invocation_id

    # ------------------------------------------------------------------ annotation versions

    def record_annotation_version(
        self, version: AnnotationVersion, *, supersedes_version_id: Optional[str] = None
    ) -> None:
        """Section 5.7: the chain moves in one step, so a half-applied replacement cannot leave two active versions."""

        def apply() -> None:
            if supersedes_version_id is not None:
                self._supersede(supersedes_version_id, version.version_id)
            self._insert(
                "annotation_version",
                {**version.model_dump(mode="python"), "project_id": self.scope, "created_at": self.clock()},
            )

        if supersedes_version_id is None:
            apply()
            return
        with self._transaction():
            apply()

    def _supersede(self, version_id: str, replacement_id: str) -> None:
        cur = self.conn.execute(
            "update annotation_version set status = 'superseded', superseded_by = ? "
            "where project_id = ? and version_id = ? and status = 'active'",
            (replacement_id, self.scope, version_id),
        )
        if cur.rowcount != 1:
            raise LookupError(f"no active annotation version {version_id} in project {self.scope}")

    def supersede_annotation_version(self, version_id: str, replacement_id: str) -> None:
        self._supersede(version_id, replacement_id)

    def _annotation(self, row: sqlite3.Row) -> AnnotationVersion:
        fields = self._fields_of("annotation_version", "project_id", "created_at")
        return AnnotationVersion(**{k: v for k, v in self._decode("annotation_version", row).items() if k in fields})

    def get_annotation_version(self, version_id: str) -> AnnotationVersion:
        return self._annotation(self._one("annotation_version", "version_id = ?", version_id))

    def effective_annotation_version(self, run_id: str, label_column: str) -> Optional[AnnotationVersion]:
        rows = self._scoped(
            "annotation_version",
            "run_id = ? and label_column = ? and status = 'active'",
            run_id,
            label_column,
        )
        return self._annotation(rows[0]) if rows else None

    def annotation_chain(self, run_id: str, label_column: str) -> list[AnnotationVersion]:
        rows = self._scoped(
            "annotation_version", "run_id = ? and label_column = ? order by created_at", run_id, label_column
        )
        return [self._annotation(row) for row in rows]

    # ------------------------------------------------------------------ candidates and cards

    def record_candidate(self, candidate: CandidateSet) -> None:
        self._insert("candidate_set", {**candidate.model_dump(mode="python"), "project_id": self.scope})

    def _candidate(self, row: sqlite3.Row) -> CandidateSet:
        fields = self._fields_of("candidate_set", "project_id")
        return CandidateSet(**{k: v for k, v in self._decode("candidate_set", row).items() if k in fields})

    def get_candidate(self, candidate_id: str) -> CandidateSet:
        return self._candidate(self._one("candidate_set", "candidate_id = ?", candidate_id))

    def find_candidate(self, *, run_id: str, display_name: str) -> CandidateSet:
        """Section 5.6: a cluster label is only unique inside the run that produced it."""
        return self._candidate(
            self._one("candidate_set", "run_id = ? and display_name = ?", run_id, display_name)
        )

    def list_candidates(
        self,
        *,
        display_name: Optional[str] = None,
        run_id: Optional[str] = None,
        exclude_candidate_id: Optional[str] = None,
        limit: int,
        offset: int = 0,
    ) -> list[CandidateSet]:
        clauses: list[str] = []
        params: list[Any] = []
        if display_name:
            clauses.append("display_name = ?")
            params.append(display_name)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        #: Excluded in the index, not after the page was cut: a candidate reading its own label group
        #: would otherwise lose one row from every page it is asked for.
        if exclude_candidate_id:
            clauses.append("candidate_id <> ?")
            params.append(exclude_candidate_id)
        where = " and ".join(clauses) if clauses else "1=1"
        rows = self._scoped("candidate_set", f"{where} order by run_id, candidate_id", *params, limit=limit, offset=offset)
        return [self._candidate(row) for row in rows]

    def record_card(self, card: EvidenceCard, *, annotation_version_id: Optional[str] = None) -> None:
        now = self.clock()
        self._insert(
            "evidence_card",
            {
                "card_id": card.card_id,
                "project_id": self.scope,
                "run_id": card.run_id,
                "candidate_id": card.candidate_id,
                "final_status": card.final_status,
                "annotation_version_id": annotation_version_id,
                "card": card,
                "created_at": now,
                "updated_at": now,
            },
        )

    def update_card(self, card: EvidenceCard, *, annotation_version_id: Optional[str] = None) -> None:
        self.conn.execute(
            "update evidence_card set final_status = ?, annotation_version_id = coalesce(?, annotation_version_id), "
            "card = ?, updated_at = ? where project_id = ? and card_id = ?",
            (
                card.final_status,
                annotation_version_id,
                _dumps(card),
                self.clock().isoformat(),
                self.scope,
                card.card_id,
            ),
        )

    def get_card(self, card_id: str) -> EvidenceCard:
        row = self._one("evidence_card", "card_id = ?", card_id)
        return EvidenceCard(**self._decode("evidence_card", row)["card"])

    def get_card_for_candidate(self, candidate_id: str) -> EvidenceCard:
        row = self._one("evidence_card", "candidate_id = ?", candidate_id)
        return EvidenceCard(**self._decode("evidence_card", row)["card"])

    def card_final_status(self, card_id: str) -> Optional[str]:
        row = self.conn.execute(
            "select final_status from evidence_card where project_id = ? and card_id = ?", (self.scope, card_id)
        ).fetchone()
        return row["final_status"] if row else None

    def card_annotation_version(self, card_id: str) -> Optional[str]:
        row = self.conn.execute(
            "select annotation_version_id from evidence_card where project_id = ? and card_id = ?",
            (self.scope, card_id),
        ).fetchone()
        return row["annotation_version_id"] if row else None

    # ------------------------------------------------------------------ idempotency and exports

    def lookup_idempotency(self, idempotency_id: str, *, route: str) -> Optional[dict[str, str]]:
        rows = self._scoped("idempotency_request", "idempotency_id = ? and route = ?", idempotency_id, route)
        if not rows:
            return None
        row = dict(rows[0])
        return {k: row[k] for k in ("request_hash", "resource_kind", "resource_id")}

    def record_idempotency(
        self, *, idempotency_id: str, route: str, request_hash: str, resource_kind: str, resource_id: str
    ) -> None:
        self._insert(
            "idempotency_request",
            {
                "project_id": self.scope,
                "idempotency_id": idempotency_id,
                "route": route,
                "request_hash": request_hash,
                "resource_kind": resource_kind,
                "resource_id": resource_id,
                "created_at": self.clock(),
            },
        )

    def create_export(
        self, *, export_id: str, kind: str, filters: dict[str, Any], requested_by: str
    ) -> None:
        now = self.clock()
        self._insert(
            "export_job",
            {
                "export_id": export_id,
                "project_id": self.scope,
                "kind": kind,
                "status": "QUEUED",
                "filters": filters,
                "requested_by": requested_by,
                "created_at": now,
                "updated_at": now,
            },
        )

    def update_export(
        self, export_id: str, *, status: str, artifact_asset_id: Optional[str] = None, failure_reason: Optional[str] = None
    ) -> None:
        cur = self.conn.execute(
            "update export_job set status = ?, artifact_asset_id = coalesce(?, artifact_asset_id), "
            "failure_reason = ?, updated_at = ? where project_id = ? and export_id = ?",
            (status, artifact_asset_id, failure_reason, self.clock().isoformat(), self.scope, export_id),
        )
        if cur.rowcount != 1:
            raise LookupError(f"export {export_id} not found in project {self.scope}")

    def get_export(self, export_id: str) -> dict[str, Any]:
        return self._decode("export_job", self._one("export_job", "export_id = ?", export_id))

    def list_exports(self, *, status: Optional[str] = None, limit: int, offset: int = 0) -> list[dict[str, Any]]:
        where = "status = ?" if status else "1=1"
        params: tuple[Any, ...] = (status,) if status else ()
        rows = self._scoped("export_job", f"{where} order by created_at", *params, limit=limit, offset=offset)
        return [self._decode("export_job", row) for row in rows]

    # ------------------------------------------------------------------ reviews, decisions, audit

    def append_review(self, event: ReviewEvent) -> None:
        self._insert("review_event", {**event.model_dump(mode="python"), "project_id": self.scope})

    def reviews(self, object_id: str) -> list[ReviewEvent]:
        fields = self._fields_of("review_event", "project_id")
        rows = self._scoped("review_event", "object_id = ? order by timestamp", object_id)
        return [ReviewEvent(**{k: v for k, v in self._decode("review_event", row).items() if k in fields}) for row in rows]

    def record_egress_decision(
        self, decision_id: str, *, request: dict[str, Any], decision: dict[str, Any], allowed: bool
    ) -> None:
        self._insert(
            "egress_decision",
            {
                "decision_id": decision_id,
                "project_id": self.scope,
                "allowed": allowed,
                "request": request,
                "decision": decision,
                "created_at": self.clock(),
            },
        )

    def egress_decision(self, decision_id: str) -> dict[str, Any]:
        return self._decode("egress_decision", self._one("egress_decision", "decision_id = ?", decision_id))

    def record_egress_approval(
        self,
        *,
        approval_id: str,
        approver_id: str,
        purpose: str,
        approved_on: str,
        expires_on: str,
    ) -> None:
        """Section 11.2: an approval is a human act kept as its own append-only record."""
        self._insert(
            "egress_approval",
            {
                "approval_id": approval_id,
                "project_id": self.scope,
                "approver_id": approver_id,
                "purpose": purpose,
                "approved_on": approved_on,
                "expires_on": expires_on,
                "created_at": self.clock(),
            },
        )

    def egress_approval(self, approval_id: str) -> dict[str, Any]:
        row = self._one("egress_approval", "approval_id = ?", approval_id)
        return self._decode("egress_approval", row)

    def audit(
        self,
        *,
        subject_id: str,
        subject_kind: str,
        action: str,
        resource_id: str,
        decision_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> str:
        entry_id = f"aud_{uuid.uuid4().hex[:12]}"
        self._insert(
            "audit_log",
            {
                "entry_id": entry_id,
                "project_id": self.scope,
                "subject_id": subject_id,
                "subject_kind": subject_kind,
                "action": action,
                "resource_id": resource_id,
                "decision_id": decision_id,
                "trace_id": trace_id,
                "details": details or {},
                "created_at": self.clock(),
            },
        )
        return entry_id

    def audit_entries(
        self,
        *,
        subject_kind: Optional[str] = None,
        action: Optional[str] = None,
        trace_id: Optional[str] = None,
        limit: int,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if subject_kind:
            clauses.append("subject_kind = ?")
            params.append(subject_kind)
        if action:
            clauses.append("action = ?")
            params.append(action)
        if trace_id:
            clauses.append("trace_id = ?")
            params.append(trace_id)
        where = (" and ".join(clauses) if clauses else "1=1") + " order by created_at, entry_id"
        rows = self._scoped("audit_log", where, *params, limit=limit, offset=offset)
        return [self._decode("audit_log", row) for row in rows]
