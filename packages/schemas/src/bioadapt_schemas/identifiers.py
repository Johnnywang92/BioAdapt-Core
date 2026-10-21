"""Identifier and fingerprint rules (plan V2.0 section 5.6)."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterable, Sequence
from decimal import Decimal
from pathlib import Path

CELL_KEY_SEPARATOR = "\x1f"
_UNSAFE = {CELL_KEY_SEPARATOR, "\n", "\r"}

BarcodeNormalizationVersion = str

_BARCODE_NORMALIZATIONS: dict[BarcodeNormalizationVersion, str] = {"v1-lower": "lower"}


class IdentifierError(ValueError):
    pass


def _check_safe(value: str, label: str) -> str:
    if not value:
        raise IdentifierError(f"{label} must not be empty")
    for ch in _UNSAFE:
        if ch in value:
            raise IdentifierError(f"{label} must not contain {ch!r}: {value!r}")
    return value


def normalize_barcode(barcode: str, version: BarcodeNormalizationVersion = "v1-lower") -> str:
    rule = _BARCODE_NORMALIZATIONS.get(version)
    if rule is None:
        raise IdentifierError(f"unknown barcode normalization version {version!r}")
    return barcode.lower() if rule == "lower" else barcode


def canonical_cell_key(
    specimen_id: str,
    barcode: str,
    *,
    barcode_normalization: BarcodeNormalizationVersion = "v1-lower",
) -> str:
    specimen = _check_safe(specimen_id, "specimen_id")
    normalized = normalize_barcode(_check_safe(barcode, "barcode"), barcode_normalization)
    return f"{specimen}{CELL_KEY_SEPARATOR}{normalized}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def member_fingerprint(canonical_cell_keys: Iterable[str]) -> str:
    keys = sorted(set(canonical_cell_keys))
    if not keys:
        raise IdentifierError("member fingerprint requires at least one cell key")
    payload = "\n".join(keys) + "\n"
    return sha256_hex(payload.encode("utf-8"))


def index_hash(values: Iterable[str]) -> str:
    return sha256_hex("\n".join(sorted(set(values))).encode("utf-8"))


def short_run_id(run_id: str) -> str:
    _check_safe(run_id, "run_id")
    return sha256_hex(run_id.encode("utf-8"))[:8]


def candidate_id(fingerprint: str, run_id: str) -> str:
    if len(fingerprint) != 64:
        raise IdentifierError("candidate_id requires a sha256 member fingerprint")
    digest = base64.b32encode(bytes.fromhex(fingerprint)).decode("ascii").rstrip("=")[:12].lower()
    return f"cand_{digest}_{short_run_id(run_id)}"


def _quantize(value: float | int, decimals: int) -> str:
    return str(Decimal(str(value)).quantize(Decimal(1).scaleb(-decimals)))


def content_fingerprint(
    *,
    n_rows: int,
    n_cols: int,
    obs_index_hash: str,
    var_index_hash: str,
    nnz: int,
    numeric_summaries: Sequence[tuple[str, float | int]] = (),
    decimals: int = 6,
) -> str:
    """Order-independent structural fingerprint; survives re-serialization, changes with content."""
    if n_rows <= 0 or n_cols <= 0:
        raise IdentifierError("matrix dimensions must be positive")
    parts = [
        f"rows={n_rows}",
        f"cols={n_cols}",
        f"obs={obs_index_hash}",
        f"var={var_index_hash}",
        f"nnz={nnz}",
    ]
    for name, value in sorted(numeric_summaries):
        parts.append(f"summary[{name}]={_quantize(value, decimals)}")
    return sha256_hex("\n".join(parts).encode("utf-8"))


def file_sha256(path: str | Path, *, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def derive_candidate(
    specimen_barcodes: Iterable[tuple[str, str]],
    run_id: str,
    *,
    barcode_normalization: BarcodeNormalizationVersion = "v1-lower",
) -> tuple[str, str, int]:
    """Returns (candidate_id, member_fingerprint, size) from (specimen_id, barcode) pairs."""
    keys = [canonical_cell_key(s, b, barcode_normalization=barcode_normalization) for s, b in specimen_barcodes]
    fingerprint = member_fingerprint(keys)
    return candidate_id(fingerprint, run_id), fingerprint, len(set(keys))
