"""Object storage layout, signed URLs, retention and deletion (plan V2.0 sections 5.9, 9.7, 11.7).

Keys are content-addressed inside a per-project prefix, so a leaked URL cannot be widened into
another project's namespace, and the same bytes always resolve to one object.
"""

import hashlib
import hmac
import re
from datetime import datetime, timedelta
from typing import Iterable, Optional, Protocol

from pydantic import Field

from bioadapt_policies.access import MAX_PRESIGNED_TTL_SECONDS
from bioadapt_schemas.entities import Contract, DataAsset, ImmutableContract
from bioadapt_schemas.enums import RetentionClass

BUCKET = "bioadapt"
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

#: Section 5.9 stage-0 default, to be replaced by a measured value (Appendix C1).
EPHEMERAL_TTL_DAYS = 30
#: Section 5.9: only ephemeral assets are ever collected automatically.
GC_ELIGIBLE = frozenset({RetentionClass.EPHEMERAL})
COLD_CLASSES = frozenset({RetentionClass.RUN_LINKED, RetentionClass.DELIVERABLE, RetentionClass.ARCHIVE})

#: Section 11.7: every surface a copy of an asset can exist on; a deletion job must walk all of them.
REPLICA_SURFACES = (
    "object_store",
    "postgres_index",
    "container_work_dir",
    "nextflow_cache",
    "vector_index_shard",
    "retrieval_result_cache",
    "export_package",
    "trace_store",
    "backup",
)


def _segment(value: str, label: str) -> str:
    if not SAFE_SEGMENT.match(value):
        raise ValueError(f"{label} {value!r} is not a safe path segment")
    return value


def object_key(
    *,
    project_id: str,
    asset_id: str,
    file_sha256: str,
    retention_class: RetentionClass,
    extension: str,
) -> str:
    _segment(project_id, "project_id")
    _segment(asset_id, "asset_id")
    _segment(retention_class.value, "retention_class")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", file_sha256):
        raise ValueError("file_sha256 must be a 64-char hex digest")
    ext = re.sub(r"[^A-Za-z0-9]", "", extension)[:8]
    return f"{BUCKET}/{project_id}/{retention_class.value}/{asset_id}/{file_sha256[:16]}.{ext}"


def key_project(key: str) -> str:
    parts = key.split("/")
    if len(parts) < 4 or parts[0] != BUCKET:
        raise ValueError(f"{key!r} is not a platform object key")
    return parts[1]


class SignedUrl(ImmutableContract):
    key: str
    project_id: str
    method: str = "GET"
    url: str
    issued_at: datetime
    expires_at: datetime

    @property
    def ttl_seconds(self) -> int:
        return int((self.expires_at - self.issued_at).total_seconds())


def issue_signed_url(
    key: str,
    *,
    project_id: str,
    now: datetime,
    signature: str,
    ttl_seconds: int = MAX_PRESIGNED_TTL_SECONDS,
    method: str = "GET",
) -> SignedUrl:
    """Section 9.7: short-lived signed URLs, and only inside the requesting project's prefix."""
    if ttl_seconds <= 0 or ttl_seconds > MAX_PRESIGNED_TTL_SECONDS:
        raise ValueError(f"signed URLs must expire within {MAX_PRESIGNED_TTL_SECONDS} seconds")
    if method not in ("GET", "PUT"):
        raise ValueError("only GET and PUT pre signatures are handed out")
    _segment(signature, "signature")
    owner = key_project(key)
    if owner != project_id:
        raise PermissionError(f"key belongs to project {owner}, not {project_id}")
    expires_at = now + timedelta(seconds=ttl_seconds)
    return SignedUrl(
        key=key,
        project_id=project_id,
        method=method,
        url=f"https://object.internal/{key}?method={method}&sig={signature}&expires={int(expires_at.timestamp())}",
        issued_at=now,
        expires_at=expires_at,
    )


class Signer(Protocol):
    """The deployment signs with a key from the secret manager - never a repo or request constant."""

    def sign(self, *, key: str, method: str, expires_at: datetime) -> str: ...


class HmacSigner:
    #: Deterministic signer for the single-node deployment and the test suite.
    def __init__(self, secret: bytes) -> None:
        if len(secret) < 16:
            raise ValueError("signing secret too short")
        self._secret = secret

    def sign(self, *, key: str, method: str, expires_at: datetime) -> str:
        message = f"{method}\n{key}\n{int(expires_at.timestamp())}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()[:16]


class ObjectHead(ImmutableContract):
    key: str
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)


class ObjectStore(Protocol):
    """Object plane the API talks to; the platform only ever addresses platform-shaped keys."""

    def put(self, key: str, payload: bytes) -> ObjectHead: ...

    def get(self, key: str) -> bytes: ...

    def head(self, key: str) -> Optional[ObjectHead]: ...

    def delete(self, key: str) -> None: ...

    def keys(self) -> list[str]: ...


class InMemoryObjectStore:
    """Reference object plane used by the single-node deployment, the tests and the GC drills."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        #: Bytes under a key are written once and never edited, so the head made at put time is the
        #: head the object has always had. Recomputing the digest turned every existence check into a
        #: full pass over the payload.
        self._heads: dict[str, ObjectHead] = {}

    def put(self, key: str, payload: bytes) -> ObjectHead:
        head = ObjectHead(key=key, sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload))
        self._objects[key] = payload
        self._heads[key] = head
        return head

    def get(self, key: str) -> bytes:
        try:
            return self._objects[key]
        except KeyError:
            raise KeyError(key) from None

    def head(self, key: str) -> Optional[ObjectHead]:
        return self._heads.get(key)

    def delete(self, key: str) -> None:
        self._objects.pop(key, None)
        self._heads.pop(key, None)

    def keys(self) -> list[str]:
        return sorted(self._objects)


class GcRecord(ImmutableContract):
    asset_id: str
    reason: str


class GcPlan(Contract):
    deletable: list[GcRecord] = []
    blocked: list[GcRecord] = []
    cold_tiered: list[GcRecord] = []


def plan_gc(
    assets: Iterable[DataAsset],
    *,
    today: datetime,
    referenced_by_deliverable: set[str] = frozenset(),
    ttl_days: int = EPHEMERAL_TTL_DAYS,
) -> GcPlan:
    """Section 5.9: collect only expired ephemeral assets that nothing deliverable depends on."""
    plan = GcPlan()
    for asset in assets:
        if asset.asset_id in referenced_by_deliverable:
            plan.blocked.append(
                GcRecord(asset_id=asset.asset_id, reason="referenced by a deliverable asset (AssetLineage)")
            )
        elif asset.retention_class in GC_ELIGIBLE:
            age = (today - asset.created_at).days
            if age > ttl_days:
                plan.deletable.append(GcRecord(asset_id=asset.asset_id, reason=f"ephemeral ttl {ttl_days}d exceeded"))
            else:
                plan.blocked.append(GcRecord(asset_id=asset.asset_id, reason=f"ephemeral asset is {age}d old"))
        elif asset.retention_class in COLD_CLASSES:
            plan.cold_tiered.append(
                GcRecord(asset_id=asset.asset_id, reason=f"{asset.retention_class.value} moves to the cold tier")
            )
    return plan


class DeletionCertificate(Contract):
    """Section 11.7: the deletion job produces a certificate listing surfaces, time and operator."""

    asset_id: str
    project_id: str
    surfaces: list[str] = []
    deleted_at: datetime
    operator_id: str
    cross_project_sharing: list[str] = []
    verified_no_cross_project_sharing: bool = False

    @property
    def complete(self) -> bool:
        return (
            set(self.surfaces) == set(REPLICA_SURFACES)
            and self.verified_no_cross_project_sharing
            and not self.cross_project_sharing
        )


def deletion_certificate(
    asset: DataAsset,
    *,
    deleted_at: datetime,
    operator_id: str,
    surfaces_cleared: Iterable[str],
    shared_with_projects: Iterable[str] = (),
) -> DeletionCertificate:
    remaining = sorted(set(REPLICA_SURFACES) - set(surfaces_cleared))
    if remaining:
        raise ValueError(f"deletion incomplete, copies remain on: {remaining}")
    shared = sorted(set(shared_with_projects))
    return DeletionCertificate(
        asset_id=asset.asset_id,
        project_id=asset.project_id,
        surfaces=list(REPLICA_SURFACES),
        deleted_at=deleted_at,
        operator_id=operator_id,
        cross_project_sharing=shared,
        verified_no_cross_project_sharing=not shared,
    )
