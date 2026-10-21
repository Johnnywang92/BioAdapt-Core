"""The blind comparison a release claim has to survive (plan V2.0 section 10.7).

A reviewer who knows which arm came from which model grades the model, not the conclusion. So the delivery
template is fixed - same field order, same figure list, no source identifiers - and the comparison is only
blind if the roster holds at least three people of whom one never worked on the platform, and the baseline
is two or three traditional workflows rather than one analyst on one afternoon. The scores themselves are
stored; whether they may be averaged is derived here, from the agreement computed in ``agreement``.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from pydantic import Field, model_validator

from bioadapt_schemas.entities import Contract, ImmutableContract

from .agreement import Agreement, AgreementLevel, krippendorff

#: The fixed order a delivered packet is written in, so two arms of one comparison differ only in content.
DELIVERY_FIELD_ORDER: tuple[str, ...] = (
    "study_goal",
    "analysis_question",
    "candidate",
    "claim",
    "supporting_evidence",
    "contradicting_evidence",
    "abstention",
    "reviewer_actions_needed",
)
#: The charts every packet must carry, so a comparison cannot be won by omitting the unfavourable one.
REQUIRED_FIGURES: tuple[str, ...] = ("evidence_ladder", "risk_coverage_curve", "per_donor_effect")

MIN_REVIEWERS = 3
MIN_EXTERNAL_REVIEWERS = 1
MIN_BASELINE_ARMS = 2
MAX_BASELINE_ARMS = 3
AGREEMENT_FLOOR = 0.6


class Reviewer(ImmutableContract):
    reviewer_id: str = Field(min_length=1)
    #: Section 10.7 asks for at least one reviewer who did not build the thing being graded.
    involved_in_development: bool = False


class BlindPacket(ImmutableContract):
    """One arm of a comparison, written into the template with its source identifiers withheld."""

    packet_id: str
    comparison_id: str
    fields: dict[str, str] = Field(min_length=1)
    figures: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fixed_order(self) -> "BlindPacket":
        if tuple(self.fields) != DELIVERY_FIELD_ORDER:
            raise ValueError(
                f"packet {self.packet_id} is not written in the delivery template: expected "
                f"{list(DELIVERY_FIELD_ORDER)}, got {list(self.fields)}"
            )
        missing = [name for name in REQUIRED_FIGURES if name not in self.figures]
        if missing:
            raise ValueError(f"packet {self.packet_id} omits the required figures {missing}")
        return self

    def leaks(self, identities: Iterable[str]) -> list[str]:
        """Which known source identifiers this packet still carries, if any.

        The check is a reading rather than a stored flag because the identities are not the packet's
        business: a model id or a colleague's name entered the template through whoever assembled it.
        """
        found: list[str] = []
        for identity in sorted(set(identities)):
            if not identity:
                continue
            for name, value in self.fields.items():
                if identity.lower() in value.lower() or identity.lower() in name.lower():
                    found.append(identity)
                    break
        return found


class BlindScore(ImmutableContract):
    """The three dimensions section 10.7 names, recorded per reviewer and per packet."""

    reviewer_id: str
    packet_id: str
    #: Conclusion credibility, 1-5.
    credibility: int = Field(ge=1, le=5)
    conclusions_to_revise: int = Field(ge=0)
    reading_seconds: int = Field(ge=0)


class BaselineArm(ImmutableContract):
    """One traditional workflow's result, which is what a platform claim is compared against."""

    arm_id: str
    analyst_id: str
    workflow: str = Field(min_length=1)
    conclusions: int = Field(ge=0)
    hours: float = Field(ge=0)


class BlindReview(Contract):
    """A comparison as filed: packets, roster, scores and the baselines it is measured against."""

    comparison_id: str
    packets: list[BlindPacket] = Field(min_length=1)
    reviewers: list[Reviewer] = Field(min_length=1)
    scores: list[BlindScore] = Field(default_factory=list)
    baseline_arms: list[BaselineArm] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_score_per_reviewer_and_packet(self) -> "BlindReview":
        seen = {(one.reviewer_id, one.packet_id) for one in self.scores}
        if len(seen) != len(self.scores):
            raise ValueError("a reviewer scores a packet once; a repeat is a second opinion, not a correction")
        reviewers = {one.reviewer_id for one in self.reviewers}
        packets = {one.packet_id for one in self.packets}
        unknown_reviewers = sorted({one.reviewer_id for one in self.scores} - reviewers)
        if unknown_reviewers:
            raise ValueError(f"scores recorded by reviewers not on the roster: {unknown_reviewers}")
        stray = sorted({one.packet_id for one in self.scores} - packets)
        if stray:
            raise ValueError(f"scores recorded for packets outside this comparison: {stray}")
        for packet in self.packets:
            if packet.comparison_id != self.comparison_id:
                raise ValueError(f"packet {packet.packet_id} belongs to {packet.comparison_id}")
        return self

    def scores_for(self, packet_id: str) -> list[BlindScore]:
        return [one for one in self.scores if one.packet_id == packet_id]

    def reading(self, *, identities: Sequence[str] = (), agreement_floor: float = AGREEMENT_FLOOR) -> "BlindReading":
        return BlindReading(self, identities=identities, agreement_floor=agreement_floor)


class BlindReading:
    """The derived verdict on a comparison: is it blind, is it complete, may its scores be aggregated?"""

    def __init__(self, review: BlindReview, *, identities: Sequence[str], agreement_floor: float) -> None:
        self.review = review
        self.reasons: list[str] = []
        self.leaks: dict[str, list[str]] = {}
        for packet in review.packets:
            found = packet.leaks(identities)
            if found:
                self.leaks[packet.packet_id] = found
        if self.leaks:
            self.reasons.append(
                f"packets carry source identifiers and are not blind: {sorted(self.leaks)} (§10.7)"
            )
        external = [one for one in review.reviewers if not one.involved_in_development]
        if len(review.reviewers) < MIN_REVIEWERS:
            self.reasons.append(
                f"{len(review.reviewers)} reviewers against the floor of {MIN_REVIEWERS} (§10.7)"
            )
        if len(external) < MIN_EXTERNAL_REVIEWERS:
            self.reasons.append(
                "every reviewer worked on the platform, so nobody is reading the conclusion cold (§10.7)"
            )
        unscored = [
            packet.packet_id
            for packet in review.packets
            if len(self.review.scores_for(packet.packet_id)) != len(review.reviewers)
        ]
        if unscored:
            self.reasons.append(f"packets not scored by the whole roster: {sorted(unscored)}")
        if not MIN_BASELINE_ARMS <= len(review.baseline_arms) <= MAX_BASELINE_ARMS:
            self.reasons.append(
                f"{len(review.baseline_arms)} traditional baseline arm(s): section 10.7 compares against "
                f"{MIN_BASELINE_ARMS}-{MAX_BASELINE_ARMS} workflows run by different analysts, not one result"
            )
        if len({one.analyst_id for one in review.baseline_arms}) != len(review.baseline_arms):
            self.reasons.append("two baseline arms share an analyst, so the baseline is one person's day")
        self.agreement = self._agreement(agreement_floor)

    def _agreement(self, floor: float) -> Optional[Agreement]:
        units: list[dict[str, Optional[int]]] = []
        for packet in self.review.packets:
            scored = {one.reviewer_id: one.credibility for one in self.review.scores_for(packet.packet_id)}
            if len(scored) >= 2:
                units.append(scored)
        if not units:
            return None
        return krippendorff(units, level=AgreementLevel.INTERVAL, threshold=floor)

    @property
    def blind(self) -> bool:
        return not self.leaks

    @property
    def comparable(self) -> bool:
        """Aggregating is allowed only with a complete blind roster, full scores and agreement above the floor."""
        return not self.reasons and bool(self.agreement and self.agreement.usable)

    def aggregate(self, packet_id: str) -> dict[str, float]:
        if self.agreement is None or not self.agreement.usable:
            raise ValueError(
                f"packet {packet_id} cannot be aggregated: "
                + (
                    "no agreement was measurable"
                    if self.agreement is None
                    else "; ".join(self.agreement.reasons) or f"alpha {self.agreement.alpha} below floor"
                )
            )
        scored = self.review.scores_for(packet_id)
        if not scored:
            raise LookupError(f"packet {packet_id} carries no scores")
        seconds = sorted(one.reading_seconds for one in scored)
        middle = len(seconds) // 2
        median = (
            seconds[middle]
            if len(seconds) % 2
            else (seconds[middle - 1] + seconds[middle]) / 2
        )
        return {
            "mean_credibility": round(sum(one.credibility for one in scored) / len(scored), 4),
            "conclusions_to_revise": float(sum(one.conclusions_to_revise for one in scored)),
            "median_reading_seconds": float(median),
            "alpha": self.agreement.alpha,
        }
