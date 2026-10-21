"""Inter-annotator agreement, and the floor below which averaging is not allowed (plan V2.0 section 10.7).

Section 10.7 makes aggregation conditional: three reviewers may be combined into one number only once
Krippendorff's alpha reaches 0.6. That is a statement about a *derived* quantity, so this module computes
it rather than storing it, and it returns the reasons a reader needs when the number is not usable -
an alpha of ``None`` because every reviewer gave the same score is a different finding from an alpha of
0.31 because they disagreed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Mapping, Optional, Sequence, Union

from pydantic import Field

from bioadapt_schemas.entities import Contract

Value = Union[int, float, str, None]


class AgreementLevel(StrEnum):
    """The distance metric alpha is computed with: category mismatch, or numeric distance."""

    NOMINAL = "nominal"
    INTERVAL = "interval"


class Agreement(Contract):
    """One agreement measurement, with the threshold it was judged against and what it means."""

    level: AgreementLevel
    units: int
    observers: int
    #: ``None`` when the data cannot produce a number - fewer than two repliable units, or no variation.
    alpha: Optional[float] = None
    threshold: float = 0.6
    reasons: list[str] = Field(default_factory=list)

    @property
    def usable(self) -> bool:
        """Section 10.7: below the floor the scores are not one number, and the report has to say so."""
        return self.alpha is not None and self.alpha >= self.threshold


def _distance(a: Value, b: Value, level: AgreementLevel) -> float:
    if level is AgreementLevel.NOMINAL:
        return 0.0 if a == b else 1.0
    return float(a) - float(b)  # type: ignore[arg-type]


def krippendorff(
    units: Sequence[Mapping[str, Value]],
    *,
    level: AgreementLevel = AgreementLevel.INTERVAL,
    threshold: float = 0.6,
) -> Agreement:
    """Krippendorff's alpha over ``[{observer: value}]``, with missing answers left out of the pairs.

    The convention is the standard coincidence-matrix one: ``alpha = 1 - Do / De``, where ``Do`` is the mean
    squared distance between values recorded in the same unit and ``De`` the mean squared distance over all
    pairs of values in the pool, each unit weighted by how many answers it carries. An alpha of 1 means the
    observers never differed; 0 means they differed exactly as much as chance allows, and less than that means
    they were worse than random.
    """
    reasons: list[str] = []
    observers = {name for unit in units for name, value in unit.items() if value is not None}
    repliable = [
        {name: value for name, value in unit.items() if value is not None}
        for unit in units
        if sum(1 for value in unit.values() if value is not None) >= 2
    ]
    dropped = len(units) - len(repliable)
    if dropped:
        reasons.append(
            f"{dropped} unit(s) carry fewer than two answers, so they contribute no pair and are not counted"
        )
    if len(repliable) < 2 or len(observers) < 2:
        reasons.append(
            "agreement needs at least two units answered by at least two observers; there is no number here"
        )
        return Agreement(
            level=level, units=len(repliable), observers=len(observers), alpha=None,
            threshold=threshold, reasons=reasons,
        )

    values = sorted(
        {value for unit in repliable for value in unit.values()},
        key=lambda item: (isinstance(item, str), item),
    )
    index = {value: position for position, value in enumerate(values)}
    #: co[c][k] counts how often c and k coincided inside one unit, divided by that unit's answers minus one;
    #: marginals[c] is how many times c was recorded at all. Both are symmetric sums over ordered pairs.
    co: list[list[float]] = [[0.0] * len(values) for _ in values]
    marginals: list[int] = [0] * len(values)
    positions = 0
    for unit in repliable:
        counts: dict[Value, int] = {}
        for value in unit.values():
            counts[value] = counts.get(value, 0) + 1
        answers = len(unit)
        positions += answers
        for value, count in counts.items():
            position = index[value]
            marginals[position] += count
            co[position][position] += count * (count - 1) / (answers - 1)
        held = list(counts.items())
        for first, (value_a, count_a) in enumerate(held):
            for value_b, count_b in held[first + 1:]:
                weight = count_a * count_b / (answers - 1)
                co[index[value_a]][index[value_b]] += weight
                co[index[value_b]][index[value_a]] += weight

    observed = 0.0
    expected = 0.0
    for first, value_a in enumerate(values):
        for second, value_b in enumerate(values):
            if first == second:
                continue
            distance = _distance(value_a, value_b, level) ** 2
            observed += co[first][second] * distance
            expected += marginals[first] * marginals[second] * distance
    observed /= positions
    expected /= positions * (positions - 1)

    if expected == 0.0:
        reasons.append(
            "every recorded answer is the same, so there is no variation for alpha to measure: agreement is "
            "unreported rather than assumed perfect (§10.7)"
        )
        return Agreement(
            level=level, units=len(repliable), observers=len(observers), alpha=None,
            threshold=threshold, reasons=reasons,
        )
    alpha = 1.0 - observed / expected
    if alpha < threshold:
        reasons.append(
            f"alpha {round(alpha, 4)} is below the {threshold} floor, so these scores stay separate and "
            "the disagreement is the finding (§10.7)"
        )
    return Agreement(
        level=level,
        units=len(repliable),
        observers=len(observers),
        alpha=round(alpha, 6),
        threshold=threshold,
        reasons=reasons,
    )
