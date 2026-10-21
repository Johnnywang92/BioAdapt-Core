"""Cross-run candidate set alignment (plan V2.0 section 5.6)."""

from __future__ import annotations

from collections.abc import Iterable

from .enums import AlignmentKind
from .thresholds import Thresholds, DEFAULT_THRESHOLDS


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    set_a, set_b = set(a), set(b)
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    return len(set_a & set_b) / len(union)


def classify_alignment(
    similarity: float,
    *,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    split: bool = False,
    merge: bool = False,
) -> AlignmentKind:
    if split:
        return AlignmentKind.SPLIT
    if merge:
        return AlignmentKind.MERGE
    if similarity >= thresholds.alignment_continuation_min:
        return AlignmentKind.CONTINUATION
    if similarity >= thresholds.alignment_uncertain_min:
        return AlignmentKind.UNCERTAIN
    return AlignmentKind.DISTINCT
