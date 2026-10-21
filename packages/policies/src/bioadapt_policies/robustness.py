"""The robustness minimum protocol as code (plan V2.0 section 7.5).

Section 7.5 fixes the five perturbation classes a candidate has to survive and says a project may add
to them but may not take any of them away without a human approval on record. That is why the set is a
policy object rather than a loop inside the analysis code: the same reason QC thresholds live in
``QcPolicy`` applies - a number that only exists inside a step cannot be reviewed, cannot be diffed
between projects, and cannot be shown beside the conclusion it was used for.

The pass criteria are deliberately not here. They are section 1.5 thresholds (``stability_cc_ari_min``,
``stability_jaccard_min``, ``stability_pass_ratio``) which the step reads from the project's own
threshold set, so one version of the truth decides both what was perturbed and what counts as stable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator

from bioadapt_schemas.entities import ImmutableContract
from bioadapt_schemas.enums import PerturbationKind

#: Section 7.5's default arms. A project may add factors; removing one is a policy deviation, not a
#: parameter choice, so the protocol refuses to be constructed without an approval that names who
#: accepted the gap.
MINIMUM_RESOLUTION_FACTORS = (0.5, 0.75, 1.5, 2.0)
MINIMUM_FEATURE_FACTORS = (0.5, 1.0, 1.5)
MINIMUM_SEED_COUNT = 5


class ProtocolDeviation(ImmutableContract):
    """The recorded human act behind a protocol weaker than section 7.5's minimum."""

    approved_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    #: What the project gave up, in the reviewer's own words, so the card can show it.
    note: Optional[str] = None
    approved_at: Optional[datetime] = None


class RobustnessProtocol(ImmutableContract):
    protocol_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    #: Multipliers applied to the plan's own resolution: section 7.5 reads stability across scales.
    resolution_factors: list[float] = Field(default_factory=list)
    #: Perturbed seeds through the dimensionality-reduction and clustering steps. ``seed_count`` includes
    #: the baseline's own seed, so the arm runs ``seed_count - 1`` times.
    seed_count: int = Field(ge=1)
    #: Multipliers applied to ``n_top_features``, the HVG-count arm of the feature-subset perturbation.
    feature_factors: list[float] = Field(default_factory=list)
    #: One replicate per donor, that donor's cells removed before the chain re-runs.
    leave_one_donor: bool = False
    #: Fraction of cells kept when the subsampling arm runs; None means the arm is absent.
    subsample_fraction: Optional[float] = Field(default=None, gt=0.0, lt=1.0)
    #: Small clusters are judged against a re-dealt labelling rather than against the raw index (§7.5).
    permutation_replicates: int = Field(ge=1)
    deviation: Optional[ProtocolDeviation] = None
    policy_kind: str = "robustness_protocol_v1"

    @model_validator(mode="after")
    def _arms(self) -> "RobustnessProtocol":
        for name, factors in (
            ("resolution_factors", self.resolution_factors),
            ("feature_factors", self.feature_factors),
        ):
            if any(factor <= 0.0 for factor in factors):
                raise ValueError(f"{name} must hold positive multipliers, got {sorted(factors)}")
            if len(factors) != len(set(factors)):
                raise ValueError(f"{name} repeats a factor: {sorted(factors)}")
        missing = self.missing_arms()
        if missing and self.deviation is None:
            raise ValueError(
                f"protocol {self.protocol_id} omits {missing}; section 7.5 lets a project add "
                "perturbations but not remove them without a recorded approval"
            )
        if self.deviation is not None and not missing:
            raise ValueError(
                f"protocol {self.protocol_id} records a deviation while holding all five arms"
            )
        return self

    def missing_arms(self) -> list[str]:
        """Which of the five mandatory classes, or their minimum factor sets, are not held."""
        found: list[str] = []
        if not set(MINIMUM_RESOLUTION_FACTORS) <= set(self.resolution_factors):
            found.append(f"resolution factors {sorted(set(MINIMUM_RESOLUTION_FACTORS) - set(self.resolution_factors))}")
        if self.seed_count < MINIMUM_SEED_COUNT:
            found.append(f"{MINIMUM_SEED_COUNT - self.seed_count} fewer than the five seeds")
        if not set(MINIMUM_FEATURE_FACTORS) <= set(self.feature_factors):
            found.append(f"feature factors {sorted(set(MINIMUM_FEATURE_FACTORS) - set(self.feature_factors))}")
        if not self.leave_one_donor:
            found.append("the leave-one-donor arm")
        if self.subsample_fraction is None:
            found.append("the subsampling arm")
        return found

    def covered_arms(self) -> tuple[PerturbationKind, ...]:
        """The classes this protocol actually runs, in the plan's own order."""
        held = {
            PerturbationKind.RESOLUTION: bool(self.perturbing_factors(PerturbationKind.RESOLUTION)),
            PerturbationKind.SEED: self.seed_count >= 2,
            PerturbationKind.FEATURE_SUBSET: bool(self.perturbing_factors(PerturbationKind.FEATURE_SUBSET)),
            PerturbationKind.LEAVE_ONE_DONOR: self.leave_one_donor,
            PerturbationKind.SUBSAMPLING: self.subsample_fraction is not None,
        }
        return tuple(kind for kind in PerturbationKind.mandatory_protocol() if held[kind])

    def perturbing_factors(self, kind: PerturbationKind) -> tuple[float, ...]:
        """The multipliers that move the run, dropping the reference point.

        ``1.0`` is what the factors are applied *to*, not a perturbation of it: an arm that re-runs the
        chain unchanged agrees with itself by construction. Kept in, it would decide which of the two
        real arms a three-arm median throws away, the same way the baseline seed would (§7.5). The
        reference is still verified - the node refuses to run at all unless the chain it is handed
        reproduces the clustering it was given.
        """
        if kind is PerturbationKind.RESOLUTION:
            factors = self.resolution_factors
        elif kind is PerturbationKind.FEATURE_SUBSET:
            factors = self.feature_factors
        else:
            raise ValueError(f"{kind} is not a factor-shaped perturbation")
        return tuple(sorted(factor for factor in factors if factor != 1.0))

    def seed_offsets(self) -> tuple[int, ...]:
        """The perturbed seeds only. The baseline's own seed is not a replicate of itself: recording it
        would add an arm whose agreement is 1.0 by construction and lift every median with it."""
        return tuple(range(1, self.seed_count))

    def arms_for(self, donors: int) -> dict[PerturbationKind, tuple[str, ...]]:
        """Every replicate this protocol will run, named the way the product records it.

        Leave-one-donor is data-shaped rather than a constant: the plan names the perturbation, the
        project's own design says how many times it has to be applied.
        """
        arms: dict[PerturbationKind, tuple[str, ...]] = {}
        resolution = self.perturbing_factors(PerturbationKind.RESOLUTION)
        if resolution:
            arms[PerturbationKind.RESOLUTION] = tuple(f"resolution_x{factor:g}" for factor in resolution)
        if self.seed_count >= 2:
            arms[PerturbationKind.SEED] = tuple(f"seed+{offset}" for offset in self.seed_offsets())
        features = self.perturbing_factors(PerturbationKind.FEATURE_SUBSET)
        if features:
            arms[PerturbationKind.FEATURE_SUBSET] = tuple(f"features_x{factor:g}" for factor in features)
        if self.leave_one_donor:
            if donors < 2:
                raise ValueError(
                    "the leave-one-donor arm needs at least two donors: with one donor every replicate "
                    "removes the only source of the cells and stability cannot mean anything"
                )
            arms[PerturbationKind.LEAVE_ONE_DONOR] = tuple(f"without_donor_{index}" for index in range(donors))
        if self.subsample_fraction is not None:
            arms[PerturbationKind.SUBSAMPLING] = (f"subsample_{self.subsample_fraction:g}",)
        return arms

    def deviation_note(self) -> Optional[str]:
        if self.deviation is None:
            return None
        return (
            f"{self.deviation.approved_by} accepted the missing arms {self.missing_arms()}: "
            f"{self.deviation.reason}"
        )
