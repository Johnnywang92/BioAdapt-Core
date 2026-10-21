"""QC policy as code (plan V2.0 section 7.7).

The tests here are about where a threshold's authority comes from: a hand-set number is only
usable with a recorded reason, a knowledge claim only once it is reviewed, and deleting cells only
with a named approver who is not the analysis.
"""

import pytest
from pydantic import ValidationError
from policy_factories import qc_policy, qc_rule

from bioadapt_policies.qc_policy import (
    CellFilter,
    QcAction,
    QcMetric,
    ThresholdSource,
)


def test_a_hand_set_threshold_must_say_why():
    with pytest.raises(ValidationError, match="requires the reason"):
        qc_rule(threshold_source=ThresholdSource.MANUAL)


def test_a_knowledge_claim_threshold_is_usable_only_once_reviewed():
    with pytest.raises(ValidationError, match="not reviewed"):
        qc_rule(threshold_source=ThresholdSource.KNOWLEDGE_CLAIM, source_ref="clm_0001")
    with pytest.raises(ValidationError, match="names none"):
        qc_rule(threshold_source=ThresholdSource.KNOWLEDGE_CLAIM, claim_reviewed=True, source_ref=None)
    cited = qc_rule(
        threshold_source=ThresholdSource.KNOWLEDGE_CLAIM, source_ref="clm_0001", claim_reviewed=True
    )
    assert cited.source_ref == "clm_0001"


def test_an_empirical_bound_names_the_diagnostic_it_came_from():
    with pytest.raises(ValidationError, match="diagnostic"):
        qc_rule(source_ref=None)


def test_a_rule_without_the_bounds_its_direction_needs_is_not_built():
    for apply_ in (CellFilter.ABOVE, CellFilter.BELOW, CellFilter.OUTSIDE):
        with pytest.raises(ValidationError):
            qc_rule(apply=apply_, upper=None, lower=None)
    with pytest.raises(ValidationError, match="lower edge is above"):
        qc_rule(apply=CellFilter.OUTSIDE, lower=0.5, upper=0.2)


def test_the_default_action_marks_and_deletes_cells_only_with_a_person_behind_it():
    marking = qc_rule(action=QcAction.MARK)
    assert qc_policy(rules=[marking]).marks_only() is True

    dropping = qc_rule(action=QcAction.DROP)
    with pytest.raises(ValidationError, match="no approving project owner"):
        qc_policy(rules=[dropping])
    with pytest.raises(ValidationError, match="without a recorded reason"):
        qc_policy(rules=[dropping], cell_deletion_approved_by="user_pi_01")
    with pytest.raises(ValidationError, match="will not say so"):
        qc_policy(
            rules=[dropping],
            cell_deletion_approved_by="user_pi_01",
            cell_deletion_reason="empty droplets carry no cells",
        )
    allowed = qc_policy(
        rules=[dropping],
        cell_deletion_approved_by="user_pi_01",
        cell_deletion_reason="empty droplets carry no cells",
        deletion_annotated_in_report=True,
    )
    assert [rule.name for rule in allowed.drop_rules()] == ["mito_high"]


def test_a_tissue_with_a_high_mitochondrial_baseline_has_to_say_so():
    heart = qc_policy(tissue="heart")
    assert any("high mitochondrial baseline" in problem for problem in heart.lint())
    covered = qc_policy(
        tissue="heart", mitochondrial_interpretation="cardiomyocytes carry mtRNA by function"
    )
    assert covered.lint() == []


def test_a_policy_that_declares_nothing_is_a_review_problem_not_a_pass():
    bare = qc_policy(rules=[], dissociation=None, expected_artifacts=[])
    problems = bare.lint()
    assert any("no rules" in problem for problem in problems)
    assert any("dissociation protocol is unknown" in problem for problem in problems)
    assert any("expected artefacts are not declared" in problem for problem in problems)

    repeated = qc_policy(rules=[qc_rule(), qc_rule()])
    assert any("rule names repeat" in problem for problem in repeated.lint())


def test_a_cell_is_flagged_against_the_band_the_policy_declares():
    band = qc_rule(apply=CellFilter.OUTSIDE, upper=0.2, lower=None)
    assert band.flags(0.21) is True and band.flags(0.05) is False
    assert band.bound() == 0.2

    floor = qc_rule(metric=QcMetric.DETECTED_GENES, apply=CellFilter.BELOW, upper=None, lower=200)
    assert floor.flags(150) is True and floor.flags(201) is False
    assert floor.bound() == 200


def test_the_policy_reads_back_the_rules_for_one_metric():
    policy = qc_policy(rules=[qc_rule(), qc_rule(name="genes_low", metric=QcMetric.DETECTED_GENES)])
    assert [rule.name for rule in policy.rules_for(QcMetric.DETECTED_GENES)] == ["genes_low"]
    assert [rule.name for rule in policy.rules_for(QcMetric.DOUBLET_SCORE)] == []
