"""The robustness minimum protocol as code (plan V2.0 section 7.5).

Section 7.5 fixes five classes of perturbation and says a project may add to them but may not subtract
without a named human approval. These tests are about exactly that: what makes a protocol constructable,
and what a reduced protocol has to be able to show when it is read back.
"""

import pytest
from policy_factories import approved_deviation, robustness_protocol
from pydantic import ValidationError

from bioadapt_policies.robustness import MINIMUM_SEED_COUNT
from bioadapt_schemas.enums import PerturbationKind


def test_the_minimum_protocol_holds_every_class_and_omits_nothing():
    protocol = robustness_protocol()
    assert protocol.covered_arms() == PerturbationKind.mandatory_protocol()
    assert protocol.missing_arms() == []
    assert protocol.deviation_note() is None


@pytest.mark.parametrize(
    "weakened",
    [
        {"resolution_factors": [0.5, 0.75]},
        {"seed_count": MINIMUM_SEED_COUNT - 1},
        {"feature_factors": [1.0]},
        {"leave_one_donor": False},
        {"subsample_fraction": None},
    ],
)
def test_a_reduced_protocol_is_not_constructable_without_an_approval(weakened):
    with pytest.raises(ValidationError, match="without a recorded approval"):
        robustness_protocol(**weakened)
    approved = robustness_protocol(deviation=approved_deviation(), **weakened)
    assert approved.missing_arms()
    assert approved.deviation_note().startswith("project_owner_01")


def test_an_approval_that_gave_up_nothing_is_not_a_record_of_a_decision():
    with pytest.raises(ValidationError, match="while holding all five arms"):
        robustness_protocol(deviation=approved_deviation())


def test_a_multiplier_is_positive_and_appears_once():
    for factors in ([0.0], [-1.0], [0.5, 0.5]):
        with pytest.raises(ValidationError):
            robustness_protocol(resolution_factors=factors + [0.75, 1.5, 2.0])


def test_the_subsampling_fraction_is_a_fraction():
    for fraction in (0.0, 1.0, 1.5):
        with pytest.raises(ValidationError):
            robustness_protocol(subsample_fraction=fraction)


def test_the_baseline_seed_is_not_a_replicate_of_itself():
    protocol = robustness_protocol(seed_count=MINIMUM_SEED_COUNT)
    assert protocol.seed_offsets() == (1, 2, 3, 4)
    assert protocol.arms_for(2)[PerturbationKind.SEED] == ("seed+1", "seed+2", "seed+3", "seed+4")


def test_a_multiplier_of_one_is_the_reference_the_arms_are_measured_against_not_an_arm():
    """§7.5 lists HVG x {0.5, 1.0, 1.5}: the 1.0 is the current value, so it cannot also be a replicate."""
    protocol = robustness_protocol()
    assert protocol.feature_factors == [0.5, 1.0, 1.5]
    assert protocol.perturbing_factors(PerturbationKind.FEATURE_SUBSET) == (0.5, 1.5)
    assert protocol.arms_for(2)[PerturbationKind.FEATURE_SUBSET] == ("features_x0.5", "features_x1.5")
    assert PerturbationKind.FEATURE_SUBSET in protocol.covered_arms()

    reference_only = robustness_protocol(
        feature_factors=[1.0], resolution_factors=[1.0], deviation=approved_deviation()
    )
    assert reference_only.perturbing_factors(PerturbationKind.FEATURE_SUBSET) == ()
    assert reference_only.covered_arms() == (
        PerturbationKind.SEED,
        PerturbationKind.LEAVE_ONE_DONOR,
        PerturbationKind.SUBSAMPLING,
    )

    with pytest.raises(ValueError, match="not a factor-shaped perturbation"):
        protocol.perturbing_factors(PerturbationKind.LEAVE_ONE_DONOR)


def test_a_protocol_holding_only_the_baseline_seed_has_no_seed_arm():
    protocol = robustness_protocol(seed_count=1, leave_one_donor=False, deviation=approved_deviation())
    assert PerturbationKind.SEED not in protocol.covered_arms()
    assert PerturbationKind.SEED not in protocol.arms_for(3)


def test_leave_one_donor_is_data_shaped_and_refuses_a_one_donor_design():
    protocol = robustness_protocol()
    assert protocol.arms_for(3)[PerturbationKind.LEAVE_ONE_DONOR] == (
        "without_donor_0",
        "without_donor_1",
        "without_donor_2",
    )
    with pytest.raises(ValueError, match="at least two donors"):
        protocol.arms_for(1)


def test_the_plan_order_is_kept_so_a_card_lists_the_classes_the_way_section_7_5_does():
    protocol = robustness_protocol(leave_one_donor=False, deviation=approved_deviation())
    assert protocol.covered_arms() == (
        PerturbationKind.RESOLUTION,
        PerturbationKind.SEED,
        PerturbationKind.FEATURE_SUBSET,
        PerturbationKind.SUBSAMPLING,
    )
