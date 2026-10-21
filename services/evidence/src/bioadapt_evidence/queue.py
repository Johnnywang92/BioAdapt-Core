"""The expert queue a draft list turns into (plan V2.0 sections 1.5, 7.2, 13.4).

Section 7.2's promise to a reviewer is that the queue is short and the reason it is short is written
down. Two numbers decide an order: how much the answer matters (``impact``) and how much the evidence
leaves open (``uncertainty``). Both are read off the draft and its gate verdict, never stored on the
card, so a card rebuilt from the same bytes lands in the same place in the queue - or moves there for a
reason a reviewer can point at.

The cap is the plan's own threshold, ``expert_queue_card_limit``, so a project that raises it raises a
versioned number the cards cite. Cards below the cap are not deleted: ``observed`` is the archive the
report appendix is supposed to carry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from bioadapt_schemas.enums import FinalStatus
from bioadapt_schemas.evidence import EvidenceCard, EvidenceKind
from bioadapt_schemas.gates import expert_queue, priority_score
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS, Thresholds

from .cards import CardDraft

RULE_VERSION = "expert_queue_rule_v1"

#: How much an expert's answer changes what the project does. Three is the case the plan built the
#: platform for: a rare population the reference does not reach. One is a card that already argues with
#: itself, which a reviewer has to settle even when the set is common.
IMPACT_RARE_AND_GAP = 3
IMPACT_RARE_OR_GAP = 2
IMPACT_SELF_CONFLICT = 1

#: How much the evidence leaves open. ``supported`` answers the question; ``provisional`` and below mean
#: a person's judgement is what would move it, and a conflict or an unknown card is the widest gap.
#: ``rejected`` is settled - the numbers say no - so it carries little uncertainty even though it is low.
UNCERTAINTY_BY_CEILING = {
    FinalStatus.SUPPORTED: 0,
    FinalStatus.PROVISIONAL: 2,
    FinalStatus.CONFLICT: 3,
    FinalStatus.UNKNOWN: 3,
    FinalStatus.REJECTED: 1,
}


@dataclass(frozen=True)
class CardScore:
    """One draft, the two numbers it scored, and the sentences that say why."""

    card_id: str
    candidate_id: str
    display_name: str
    impact: int
    uncertainty: int
    client_ask: bool
    reasons: tuple[str, ...]

    @property
    def score(self) -> int:
        return priority_score(self.impact, self.uncertainty, self.client_ask)

    def as_dict(self) -> dict[str, object]:
        return {
            "card_id": self.card_id,
            "candidate_id": self.candidate_id,
            "display_name": self.display_name,
            "impact": self.impact,
            "uncertainty": self.uncertainty,
            "client_ask": self.client_ask,
            "score": self.score,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class ExpertQueue:
    """What a person sees, what was filed, and the rule and numbers that produced the split."""

    queue: tuple[str, ...]
    observed: tuple[str, ...]
    scores: tuple[CardScore, ...]
    limit: int
    thresholds_version: str
    rule_version: str

    def score_of(self, card_id: str) -> CardScore:
        for score in self.scores:
            if score.card_id == card_id:
                return score
        raise LookupError(f"card {card_id} is not in this queue")

    def as_dict(self) -> dict[str, object]:
        return {
            "queue": list(self.queue),
            "observed": list(self.observed),
            "scores": [score.as_dict() for score in self.scores],
            "limit": self.limit,
            "thresholds_version": self.thresholds_version,
            "rule_version": self.rule_version,
        }


def _is_rare(card: EvidenceCard) -> tuple[bool, str]:
    """The rarity reading the card itself carries, from the composition record the clusters asset cites."""
    for record in card.evidence.values():
        if record.kind is EvidenceKind.COMPOSITION:
            rare = bool(record.content.get("rare"))
            return rare, (
                f"{'rare' if rare else 'not rare'} by the composition rule: "
                f"{record.content.get('size')} of {record.content.get('partition_cells')} cells "
                f"across {record.content.get('donor_count')} donors"
            )
    return False, "no composition record on the card, so nothing marks this set as rare"


def score_draft(draft: CardDraft, *, client_ask: bool = False) -> CardScore:
    """Score one draft against its derived ceiling - the queue reads the card, it does not judge it."""
    card = draft.card
    reasons: list[str] = []
    rare, why = _is_rare(card)
    reasons.append(why)
    coverage = card.novelty_vs_coverage
    gap = bool(coverage is not None and coverage.coverage_gap)
    reasons.append(
        "the reference does not reach these cells"
        if gap
        else ("no coverage measurement, so the reference's reach is unknown (§7.6)"
              if coverage is None else "the reference covers these cells")
    )

    if rare and gap:
        impact = IMPACT_RARE_AND_GAP
    elif rare or gap:
        impact = IMPACT_RARE_OR_GAP
    elif card.unresolved_conflict or card.contradicting_evidence_ids:
        impact = IMPACT_SELF_CONFLICT
        reasons.append(
            f"{len(card.contradicting_evidence_ids)} record(s) argue against the ones that support it"
        )
    else:
        impact = 0

    uncertainty = UNCERTAINTY_BY_CEILING[draft.ceiling]
    reasons.append(f"ceiling {draft.ceiling.value}: {'; '.join(draft.gate_reasons) or 'nothing holds it back'}")
    if client_ask:
        reasons.append("the project named this population as one it is asking about")

    return CardScore(
        card_id=card.card_id,
        candidate_id=card.candidate_id,
        display_name=card.display_name,
        impact=impact,
        uncertainty=uncertainty,
        client_ask=client_ask,
        reasons=tuple(reasons),
    )


def build_queue(
    drafts: Sequence[CardDraft],
    *,
    client_ask: Iterable[str] = (),
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    limit: Optional[int] = None,
) -> ExpertQueue:
    """Order drafts for an expert and split them at the plan's cap (section 7.2).

    ``client_ask`` names candidate ids, because that is the form a project objective takes - a population
    the client asked about - and a display name is only unique inside the run that made it.
    """
    asked = set(client_ask)
    scores = [score_draft(draft, client_ask=draft.candidate_id in asked) for draft in drafts]
    queue, observed = _ordered_split(scores, thresholds=thresholds, limit=limit)
    return ExpertQueue(
        queue=tuple(queue),
        observed=tuple(observed),
        scores=tuple(scores),
        limit=thresholds.expert_queue_card_limit if limit is None else limit,
        thresholds_version=thresholds.version,
        rule_version=RULE_VERSION,
    )


def _ordered_split(
    scores: Iterable[CardScore], *, thresholds: Thresholds, limit: Optional[int]
) -> tuple[list[str], list[str]]:
    """The gate's own split, called with the scores so the cap lives in one place (§1.5)."""
    pairs = [(score.card_id, score.score) for score in scores]
    return expert_queue(pairs, limit, thresholds=thresholds)
