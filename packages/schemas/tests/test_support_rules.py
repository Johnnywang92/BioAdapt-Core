import pytest
from pydantic import ValidationError

from bioadapt_schemas.alignment import classify_alignment, jaccard
from bioadapt_schemas.enums import AlignmentKind
from bioadapt_schemas.profile import (
    MissingInformation,
    ProjectProfile,
    RetainedGroup,
    groups_requiring_retention,
    missing_retained_groups,
)
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS, Thresholds


class TestAlignment:
    def test_jaccard_bounds(self):
        assert jaccard(["a", "b"], ["a", "b"]) == 1.0
        assert jaccard(["a"], ["b"]) == 0.0
        assert jaccard([], []) == 1.0
        assert jaccard(["a", "b"], ["b", "c"]) == pytest.approx(1 / 3)

    def test_continuation_uncertain_and_distinct_bands(self):
        assert classify_alignment(0.8) is AlignmentKind.CONTINUATION
        assert classify_alignment(0.6) is AlignmentKind.CONTINUATION
        assert classify_alignment(0.5) is AlignmentKind.UNCERTAIN
        assert classify_alignment(0.2) is AlignmentKind.DISTINCT

    def test_structural_split_and_merge_beat_similarity(self):
        assert classify_alignment(0.95, split=True) is AlignmentKind.SPLIT
        assert classify_alignment(0.95, merge=True) is AlignmentKind.MERGE
        assert classify_alignment(0.1, split=True) is AlignmentKind.SPLIT

    def test_bands_are_configurable(self):
        thresholds = Thresholds(alignment_continuation_min=0.9, alignment_uncertain_min=0.8)
        assert classify_alignment(0.85, thresholds=thresholds) is AlignmentKind.UNCERTAIN
        with pytest.raises(ValidationError):
            Thresholds(alignment_continuation_min=0.4, alignment_uncertain_min=0.6)


class TestThresholds:
    def test_thresholds_are_immutable_shared_objects(self):
        with pytest.raises(ValidationError):
            DEFAULT_THRESHOLDS.rare_min_cells = 1
        assert DEFAULT_THRESHOLDS.stability_cc_ari_min == 0.6

    def test_marker_detection_bands_are_ordered(self):
        with pytest.raises(ValidationError, match="excluded detection frequency"):
            Thresholds(detection_frequency_in_min=0.1, detection_frequency_excluded_max=0.4)

    def test_confirmed_thresholds_leave_the_placeholder_state(self):
        confirmed = Thresholds(version="stage0_measured", confirmed=True)
        assert not confirmed.is_unconfirmed_default_set
        assert confirmed.de_fdr_max == DEFAULT_THRESHOLDS.de_fdr_max


class TestProjectProfileRetention:
    def profile(self, groups):
        return ProjectProfile(
            project_id="prj_001",
            species="human",
            tissues=["lung"],
            modalities=["RNA"],
            objective="find special cells",
            specimen_count=6,
            donor_count=4,
            summary_schema_version="summary_v1",
            groups=groups,
            missing_information=[
                MissingInformation(code="timepoint_missing", question="When were samples taken?", blocking=True)
            ],
        )

    def test_donor_count_cannot_exceed_specimen_count(self):
        with pytest.raises(ValidationError, match="specimen_count"):
            ProjectProfile(
                project_id="p", species="human", tissues=["x"], modalities=["RNA"], objective="o",
                specimen_count=2, donor_count=5, summary_schema_version="v1",
            )

    def test_rare_groups_are_flagged_for_retention(self):
        rare = RetainedGroup(display_name="candidate_7", size=800, share=0.004, donor_count=2)
        common = RetainedGroup(display_name="t_cells", size=40_000, share=0.4, donor_count=4)
        tiny = RetainedGroup(display_name="debris", size=20, share=0.001, donor_count=1)
        required = groups_requiring_retention([rare, common, tiny])
        assert [g.display_name for g in required] == ["candidate_7"]

    def test_summarisation_may_not_drop_a_rare_group(self):
        rare = RetainedGroup(display_name="candidate_7", size=800, share=0.004, donor_count=2)
        profile = self.profile(groups=[])
        assert missing_retained_groups(profile, [rare]) == ["candidate_7"]
        assert missing_retained_groups(self.profile(groups=[rare]), [rare]) == []

    def test_blocking_missing_information_is_visible_to_the_planner(self):
        profile = self.profile(groups=[])
        assert profile.missing_information[0].blocking
