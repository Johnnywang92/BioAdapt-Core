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

from typing import Any, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, Field

from bioadapt_policies.egress import FieldCategory, OutgoingField
from bioadapt_policies.model_policy import ModelTask
from bioadapt_schemas.enums import EvidenceKind, PrivacyTier, RegulatoryProfile, ScreeningResult
from bioadapt_schemas.evidence import EvidenceCard
from bioadapt_schemas.gates import card_ceiling
from bioadapt_schemas.gates import stability_verdict as stability_verdict_fn

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
    "stability": OutgoingField(
        path="stability",
        category=FieldCategory.AGGREGATE_STATISTICS,
        unit="group",
        note="perturbation pass counts, min ARI and Jaccard across arms; group-level numbers",
    ),
    "artifact_screening": OutgoingField(
        path="artifact_screening",
        category=FieldCategory.AGGREGATE_STATISTICS,
        unit="group",
        note="doublet and ambient screening risk levels and blocked flags; group-level",
    ),
    "continuous_state": OutgoingField(
        path="continuous_state",
        category=FieldCategory.AGGREGATE_STATISTICS,
        unit="group",
        note="continuous state tail and gradient score; group-level",
    ),
    "power_diagnostics": OutgoingField(
        path="power_diagnostics",
        category=FieldCategory.AGGREGATE_STATISTICS,
        unit="group",
        note="whether donor count meets the design min_n_per_group and notes",
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
    power_warning: Optional[str] = Field(default=None, max_length=300)
    confounding_risks: list[str] = Field(default_factory=list)
    stability_verdict: Optional[str] = Field(default=None, max_length=300)
    recommended_action: Optional[str] = Field(default=None, max_length=300)


def _card_content(card: EvidenceCard, kind: EvidenceKind) -> dict[str, Any]:
    """One evidence record's content by kind."""
    found = [entry for entry in card.evidence.values() if entry.kind is kind]
    return {} if not found else dict(found[0].content)


def generate_reviewer_memo(target: Union[CardDraft, EvidenceCard]) -> CardNarrative:
    """Generate a structured, deterministic bioinformatics review memo from a drafted card or EvidenceCard.

    Adheres strictly to plan V2.0 sections 7.2-7.8 & 11.6:
    - Never sets privileged fields (final_status, candidate_label, reviewer_id, sign_off).
    - Checks power sufficiency: flags whether donor count is below min_n_per_group (§7.4).
    - Detects confounding: continuous state activation/differentiation axis tail, doublet & ambient risks.
    - Evaluates robustness across the 5 perturbation classes.
    - Synthesizes recommended action based on gate ceiling and evidence completeness.
    """
    if isinstance(target, CardDraft):
        card = target.card
        ceiling_str = target.ceiling.value
        gate_reasons = list(target.gate_reasons)
        underpowered = target.underpowered_design
        blocking = list(target.blocking)
    else:
        card = target
        verdict = card_ceiling(card)
        ceiling_obj = verdict.detail.get("ceiling")
        ceiling_str = ceiling_obj.value if hasattr(ceiling_obj, "value") else str(ceiling_obj or "unknown")
        gate_reasons = list(verdict.reasons)
        underpowered = False
        blocking = [r.value for r in card.artifact_screening.blocked_by] if card.artifact_screening else []

    # 1. Power diagnostics (§7.4)
    power_warning: Optional[str] = None
    if underpowered:
        power_warning = (
            "样本供体量低于设计矩阵底线 (min_n_per_group)，差异表达与标志物检验力受限，"
            "组间比较仅作描述性推断，不可用于结论性论证 (§7.4)。"
        )

    # 2. Confounding risks (Continuous state, artifacts)
    confounding_risks: list[str] = []
    state = card.identity_vs_state.continuous_state_check
    if state and state.is_axis_tail:
        stat_note = f" (统计量: {state.statistic:.2f})" if state.statistic is not None else ""
        confounding_risks.append(
            f"连续态混杂：候选群被判定为参考梯度轴尾部 (is_axis_tail=True){stat_note}，"
            "疑似活化/分化梯度的生理极值，并非独立新细胞类型 (§7.6)。"
        )

    screening = card.artifact_screening
    if screening:
        for risk, item in screening.items.items():
            if item.result is ScreeningResult.FAIL:
                confounding_risks.append(f"质控伪影未通过：{risk.value} (工具: {item.tool} {item.tool_version})")
            elif item.result is ScreeningResult.WARN:
                confounding_risks.append(f"质控伪影警示：{risk.value} 存在潜在风险标记")

    # 3. Stability verdict (5 perturbation protocol, §7.5)
    stability = card.stability_summary
    stability_verdict: Optional[str] = None
    if stability:
        s_verdict = stability_verdict_fn(stability)
        aris = [r.cc_ari for r in stability.results if r.cc_ari is not None]
        jacs = [r.jaccard for r in stability.results if r.jaccard is not None]
        min_ari = f"{min(aris):.2f}" if aris else "N/A"
        min_jac = f"{min(jacs):.2f}" if jacs else "N/A"
        if s_verdict.passed:
            stability_verdict = f"稳健性达标：扰动评估通过 (ARI 下限: {min_ari}, Jaccard 下限: {min_jac})。"
        else:
            reasons_str = "; ".join(s_verdict.reasons) if s_verdict.reasons else "低于通过阈值"
            stability_verdict = f"稳健性未达标：{reasons_str} (ARI 下限: {min_ari}, Jaccard 下限: {min_jac})。"
    else:
        stability_verdict = "未记录 5 类扰动稳健性评估结果。"

    # 4. Recommended action based on ceiling & gate reasons (§7.2, §7.8)
    recommended_action: Optional[str] = None
    if blocking:
        recommended_action = f"触发严重质控伪影阻塞 ({', '.join(blocking)})，建议专家驳回该候选群 (reject)。"
    elif ceiling_str == "conflict":
        recommended_action = "存在参考源或模态间冲突，建议标记为 conflict 并指派专家裁决独立正交证据。"
    elif ceiling_str == "rejected":
        recommended_action = "证据链严重缺失或质控未过关，建议予以驳回 (reject)。"
    elif ceiling_str == "supported":
        if underpowered:
            recommended_action = "统计指标虽达标但受限于样本量，建议审慎核验后视情况签署 (conditional sign-off)。"
        else:
            recommended_action = "各项证据链（标志物特异性、跨供体一致性、稳健性）完整，建议专家签署确认 (supported)。"
    elif ceiling_str == "provisional":
        if state and state.is_axis_tail:
            recommended_action = "存在明显连续态表型梯度，建议补充流式细胞术 (FACS) 排除生理过渡态，暂定 provisional。"
        else:
            recommended_action = "缺少独立确认集或关键正交验证，建议批准为暂定状态 (provisional) 并列入随访队列。"
    else:
        recommended_action = "证据链尚不完备，建议保留观察 (observed) 或返回重新聚类。"

    # 5. Summary & Calibrated Confidence
    types_str = "/".join(t.value for t in card.candidate_types)
    comp = _card_content(card, EvidenceKind.COMPOSITION)
    size_part = f"包含 {comp['size']} 个细胞" if "size" in comp else ""
    donor_part = f"，覆盖 {comp['donor_count']} 位供体" if "donor_count" in comp else ""
    count_desc = f" ({size_part}{donor_part})" if size_part else ""

    summary_parts = [
        f"候选群 {card.display_name} [{types_str}]{count_desc}。",
    ]
    if gate_reasons:
        summary_parts.append(f"门禁判定上限: {ceiling_str} (原因: {'; '.join(gate_reasons[:2])})。")
    if power_warning:
        summary_parts.append("注意：供体数偏少，组间检验力受限。")
    if confounding_risks:
        summary_parts.append(f"提示 {len(confounding_risks)} 项混杂风险。")
    summary = " ".join(summary_parts)[:600]

    # Calculate confidence (0.0 to 1.0)
    conf = 0.5
    if ceiling_str == "supported":
        conf = 0.85
    elif ceiling_str == "provisional":
        conf = 0.65
    elif ceiling_str in ("conflict", "rejected"):
        conf = 0.3

    if underpowered:
        conf = max(0.1, conf - 0.2)
    if state and state.is_axis_tail:
        conf = max(0.1, conf - 0.2)
    if stability:
        s_v = stability_verdict_fn(stability)
        if not s_v.passed:
            conf = max(0.1, conf - 0.15)
    if blocking:
        conf = 0.1

    return CardNarrative(
        summary=summary,
        confidence=round(conf, 2),
        power_warning=power_warning,
        confounding_risks=confounding_risks,
        stability_verdict=stability_verdict,
        recommended_action=recommended_action,
    )


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
    stability = card.stability_summary
    screening = card.artifact_screening
    state = card.identity_vs_state.continuous_state_check
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
        "stability": (
            {}
            if stability is None
            else {
                "protocol_version": stability.protocol_version,
                "results": [
                    {
                        "kind": r.kind.value,
                        "cc_ari": r.cc_ari,
                        "jaccard": r.jaccard,
                        "replicates": r.replicates,
                        "judgement": r.detail.get("judgement"),
                        "vanished_arms": r.detail.get("vanished_arms", 0),
                    }
                    for r in stability.results
                ],
            }
        ),
        "artifact_screening": (
            {}
            if screening is None
            else {
                "items": {risk.value: item.result.value for risk, item in screening.items.items()},
                "blocked_by": [r.value for r in screening.blocked_by],
            }
        ),
        "continuous_state": (
            {}
            if state is None
            else {
                "method": state.method,
                "is_axis_tail": state.is_axis_tail,
                "statistic": state.statistic,
            }
        ),
        "power_diagnostics": {
            "underpowered_design": draft.underpowered_design,
            "donor_notes": [n for n in draft.notes if "min_n_per_group" in n],
        },
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
