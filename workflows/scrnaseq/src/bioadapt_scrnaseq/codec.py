"""Serialization shared by the matrix and the step products (plan V2.0 sections 5.6, 10.1).

Byte-level reproducibility is claimed only for the platform's own canonical form: keys sorted,
compact separators, and floats quantized to a fixed precision. Quantizing at serialization rather
than at computation keeps full precision inside a run while making the stored bytes independent of
the last unit in a libm transcendental.
"""

from __future__ import annotations

import json
import math
from typing import Any

#: Six decimals matches the quantization the content fingerprint already applies.
FLOAT_PRECISION = 6


def round_floats(value: Any, ndigits: int = FLOAT_PRECISION) -> Any:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("a stored product may not contain NaN or infinity")
        rounded = round(value, ndigits)
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, (list, tuple)):
        return [round_floats(item, ndigits) for item in value]
    if isinstance(value, dict):
        return {key: round_floats(item, ndigits) for key, item in value.items()}
    return value


def canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        round_floats(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def quantize(value: float, ndigits: int = FLOAT_PRECISION) -> float:
    """One number as the stored bytes hold it.

    Fingerprints that aggregate floats must sum these rather than the in-memory values: the stored
    asset is rounded, so a digest taken from full precision could not be recomputed by anyone reading
    the object back - and section 5.6 exists so that a reviewer can.
    """
    return round_floats(value, ndigits)
