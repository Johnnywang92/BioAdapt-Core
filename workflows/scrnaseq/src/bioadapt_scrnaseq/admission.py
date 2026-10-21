"""From a converted product to an asset a run can be handed (plan V2.0 sections 4.1, 5.2, 9.5).

``scanpy_adapter.read_h5ad`` ends at an ``ExpressionProduct`` in memory, and a run starts at an asset
id in the index: the two halves exist and nothing joined them. This module is the join. It builds the
index row with the driver's own ``asset_for``, so a converted file is labelled exactly as a node's
output is - an id derived from its bytes, ``format="json"``, the workflow's ``expression_v1`` contract -
and it writes the payload next to a manifest, which is what §4.1 asks of a lossy conversion: the record
says which slots the read could not carry and whether the original was kept alongside.

It writes files rather than calling ``write_asset`` because the reference deployment keeps its index
and object plane inside one process (``scripts/serve_workbench.py``): a second process has no rows to
add. The manifest's ``asset`` object is the body ``POST /v1/projects/{project}/assets`` accepts, so
the person holding the file does the registration through the same door as any other upload.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Optional

from bioadapt_schemas.entities import DataAsset
from bioadapt_schemas.enums import FilteringState, PrivacyTier, RetentionClass, UsageScope
from bioadapt_schemas.errors import ErrorClass

from .errors import StepFailure
from .runner import ASSET_FORMAT, AssetDefaults, asset_for
from .scanpy_adapter import AnnDataRead

#: A person's own expression data: the labels §5.8 and §5.9 make the reference build hold for it.
#: Retention and sharing scope are project decisions, so a caller may pass its own ``AssetDefaults``.
DEFAULT_ASSET_DEFAULTS = AssetDefaults(
    privacy_tier=PrivacyTier.RESTRICTED,
    usage_scope=UsageScope.THIS_PROJECT_ONLY,
    retention_class=RetentionClass.RUN_LINKED,
    filtering_state=FilteringState.NONE,
)

#: How many bytes to hash at a time: an h5ad can be larger than memory should assume it is not.
CHUNK = 1 << 20


@dataclass(frozen=True)
class FileNote:
    """The pre-conversion file, named by its own digest so it can be admitted as the other asset (§4.1)."""

    path: str
    file_sha256: str
    size_bytes: int

    def to_payload(self) -> dict[str, Any]:
        return {"path": self.path, "file_sha256": self.file_sha256, "size_bytes": self.size_bytes}


def note_of(path: str) -> FileNote:
    digest = hashlib.sha256()
    size = 0
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        raise StepFailure(
            code="input_missing",
            error_class=ErrorClass.NEEDS_INPUT,
            message=(
                f"{path!r} cannot be read ({error}); the file a conversion was asked for has to be there "
                "before anything can be indexed about it"
            ),
            field="input",
            retryable=False,
        ) from error
    return FileNote(path=str(path), file_sha256=digest.hexdigest(), size_bytes=size)


@dataclass(frozen=True)
class Admission:
    """The bytes to upload, the row to declare, and everything the conversion lost in between."""

    asset: DataAsset
    payload: bytes
    #: The slot the plan wires this asset into; a node reads only the slot it was told to.
    slot: str
    #: None when the caller did not name the file it came from; §4.1 wants it named when it can be.
    original: Optional[FileNote]
    #: The obs column that fed each of the three fields the contract needs per cell.
    obs_keys: Mapping[str, str]
    dropped_slots: tuple[str, ...]
    warnings: tuple[str, ...]
    cells_with_counts: int

    @property
    def asset_id(self) -> str:
        return self.asset.asset_id

    def manifest(self) -> dict[str, Any]:
        """The record a later reader uses to check that this asset answers the question they are asking.

        The upload's digests are not restated here: the asset row carries ``file_sha256`` and
        ``size_bytes`` for the very bytes this writes to disk, and one source each is the point of §5.2.
        """
        return {
            "asset": self.asset.model_dump(mode="json"),
            "upload": {"file": f"{self.asset_id}.{ASSET_FORMAT}", "slot": self.slot},
            "source": {"h5ad": self.original.to_payload() if self.original else None},
            "obs_keys": dict(sorted(self.obs_keys.items())),
            "conversion": {
                "dropped_slots": list(self.dropped_slots),
                "lossy": bool(self.dropped_slots),
                "cells_with_counts": self.cells_with_counts,
                "warnings": list(self.warnings),
            },
        }

    def files(self) -> dict[str, bytes]:
        return {
            f"{self.asset_id}.{ASSET_FORMAT}": self.payload,
            f"{self.asset_id}.manifest.json": (json.dumps(self.manifest(), indent=2, sort_keys=True) + "\n").encode(),
        }


def admit(
    read: AnnDataRead,
    *,
    project_id: str,
    created_at: Optional[datetime] = None,
    defaults: AssetDefaults = DEFAULT_ASSET_DEFAULTS,
    obs_keys: Mapping[str, str],
    original: Optional[FileNote] = None,
) -> Admission:
    """Label a converted product as this project's asset, and keep the loss in the same record."""
    payload = read.product.json_bytes()
    asset = asset_for(
        read.product,
        project_id=project_id,
        defaults=defaults,
        created_at=created_at or datetime.now(UTC),
    )
    return Admission(
        asset=asset,
        payload=payload,
        slot=read.product.slot,
        original=original,
        obs_keys=obs_keys,
        dropped_slots=read.dropped_slots,
        warnings=read.warnings,
        cells_with_counts=read.cells_with_counts,
    )


def write_admission(admission: Admission, directory: Path) -> tuple[Path, ...]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in sorted(admission.files().items()):
        path = directory / name
        path.write_bytes(content)
        written.append(path)
    return tuple(written)


__all__ = [
    "DEFAULT_ASSET_DEFAULTS",
    "Admission",
    "FileNote",
    "admit",
    "note_of",
    "write_admission",
]
