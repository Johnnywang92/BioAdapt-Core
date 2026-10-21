"""The outbound face of one evidence card (plan V2.0 sections 11.5, 11.6).

Section 11.5's rule is that an outbound context is *built* from a whitelist rather than filtered out of a
rich payload. A card is where that distinction bites: the same objects hold group-level numbers a model may
read and cell keys, donor-level rows and content digests it may not. So the paths allowed to leave, with the
category and statistical unit that authorises each one, are fixed here - and a call site may narrow the list
but cannot add to it. The alternative is to remember, at every place a prompt is assembled, which field of
the card happened to be the dangerous one.

``card_model_request`` is the other half: section 11.6 pairs a field allowlist with an answer contract, so
the projection and the schema a permitted reply has to fit leave together or not at all.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from pydantic import BaseModel, Field

from bioadapt_policies.egress import FieldCategory, OutgoingField
from bioadapt_policies.model_policy import ModelTask
from bioadapt_schemas.enums import EvidenceKind, PrivacyTier, RegulatoryProfile

from .cards import CardDraft
from .decision import SYSTEM_INSTRUCTIONS, ModelCallRequest
from .errors import EgressBlocked

#: What a card may be shown for, as section 11.5's table reads it for this modality.
CARD_OUTBOUND: Mapping[str, OutgoingField] = {
    "candidate": OutgoingField(
        path="candidate",
        category=FieldCategory.AGGREGATE_STATISTICS,
        unit="group",
        note="the cluster label: it names a population of cells, not a person",
    ),
    "candidate_types": OutgoingField(
        path="candidate_types",
        category=FieldCategory.TASK_SEMANTICS,
        unit="group",
        note="which section 7.2 claim a reviewer is being asked to settle",
    ),
    "hypothesis": OutgoingField(
        path="hypothesis",
        category=FieldCategory.TASK_SEMANTICS,
        unit="group",
        note="the drafting path always files an unknown hypothesis, and says why",
    ),
    "ceiling": OutgoingField(
        path="ceiling",
        category=FieldCategory.AGGREGATE_STATISTICS,
        unit="group",
        note="the gate's reading of this run's numbers, which is not a label of anybody's",
    ),
    "gate_reasons": OutgoingField(
        path="gate_reasons", category=FieldCategory.AGGREGATE_STATISTICS, unit="group"
    ),
    "composition": OutgoingField(
        path="composition",
        category=FieldCategory.AGGREGATE_STATISTICS,
        unit="group",
        note="set size, donor count and share: counts of a group, carrying no specimen identifier",
    ),
    "markers": OutgoingField(
        path="markers",
        category=FieldCategory.AGGREGATE_STATISTICS,
        unit="group",
        note="the rank-cut marker list section 11.5 allows; ``rank_only`` travels with it so a model "
        "cannot read an ordering as an identity claim",
    ),
    "reference_coverage": OutgoingField(
        path="reference_coverage", category=FieldCategory.AGGREGATE_STATISTICS, unit="group"
    ),
    "rule_versions": OutgoingField(
        path="rule_versions",
        category=FieldCategory.TASK_SEMANTICS,
        unit="group",
        note="which versioned cuts the numbers were read against, so an answer can be re-checked",
    ),
}


def _content(draft: CardDraft, kind: EvidenceKind) -> dict[str, Any]:
    """One evidence record's content, which is a set of group-level numbers for the kinds listed above."""
    found = [entry for entry in draft.card.evidence.values() if entry.kind is kind]
    return {} if not found else dict(found[0].content)


class CardNarrative(BaseModel):
    """What one call is allowed to answer about a card: a sentence for a reviewer, and nothing else.

    Section 11.6 control 3 makes the answer contract the place where the plan's permission boundary is
    enforced, so the fields a person alone may write - ``final_status``, ``candidate_label``,
    ``reviewer_id``, ``sign_off`` - are absent from it rather than filtered out of it later. The answer
    carries no citation handle either: an evidence id is derived from the set's membership, so the card's
    provenance stays the reader's machine-derived record and a model sentence is filed as prose.
    """

    summary: str = Field(min_length=1, max_length=600)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


def card_outbound(
    draft: CardDraft, *, only: Sequence[str] = ()
) -> tuple[dict[str, Any], tuple[OutgoingField, ...]]:
    """The template and the field declarations for showing one drafted card to a model.

    ``only`` narrows the allowlist. Anything outside it is refused here, at the place the fields are named,
    rather than trimmed later and noticed by nobody (§11.5).
    """
    wanted = tuple(only) or tuple(CARD_OUTBOUND)
    extra = sorted(set(wanted) - set(CARD_OUTBOUND))
    if extra:
        raise EgressBlocked(
            f"card fields {extra} are not on the outbound allowlist; a call site may narrow "
            f"{sorted(CARD_OUTBOUND)} but cannot add to it (§11.5)",
            field="fields",
        )
    card = draft.card
    coverage = card.novelty_vs_coverage
    values: dict[str, Any] = {
        "candidate": card.display_name,
        "candidate_types": sorted(str(kind.value) for kind in card.candidate_types),
        "hypothesis": {
            "type": card.hypothesis_type.value,
            "unknown_reason": None if card.unknown_reason is None else card.unknown_reason.value,
        },
        "ceiling": draft.ceiling.value,
        "gate_reasons": list(draft.gate_reasons),
        "composition": _content(draft, EvidenceKind.COMPOSITION),
        "markers": _content(draft, EvidenceKind.MARKER_SPECIFICITY),
        "reference_coverage": (
            {}
            if coverage is None
            else {
                "knn": coverage.knn,
                "distance_ratio": coverage.distance_ratio,
                "coverage_gap": coverage.coverage_gap,
                "novelty_signal": coverage.novelty_signal,
                "reference_coverage_score": coverage.reference_coverage_score,
            }
        ),
        "rule_versions": {
            "support_rule_version": card.support_rule_version,
            "thresholds_version": card.thresholds_version,
        },
    }
    return {path: values[path] for path in wanted}, tuple(CARD_OUTBOUND[path] for path in wanted)


def card_model_request(
    draft: CardDraft,
    *,
    project_id: str,
    purpose: str,
    task: ModelTask,
    privacy_tier: PrivacyTier,
    destination: str,
    only: Sequence[str] = (),
    regulatory_profile: Sequence[RegulatoryProfile] = (),
) -> ModelCallRequest:
    """The one way a drafted card becomes a model request (sections 11.5, 11.6).

    The allowlist and the answer contract travel together from here, so no call site can hand a gateway a
    card's stored payload: what it can hand over is this projection, whose fields each name the category
    and statistical unit that authorises them, read against the schema a permitted answer has to fit.
    """
    template, fields = card_outbound(draft, only=only)
    return ModelCallRequest(
        project_id=project_id,
        run_id=draft.card.run_id,
        purpose=purpose,
        task=task,
        privacy_tier=privacy_tier,
        destination=destination,
        template=template,
        fields=fields,
        schema=CardNarrative,
        system_instructions=SYSTEM_INSTRUCTIONS,
        regulatory_profile=list(regulatory_profile),
    )
