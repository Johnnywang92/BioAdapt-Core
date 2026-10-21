"""Plan V2.0 section 1.5 thresholds.

Every default here is a stage-0 placeholder (appendix C1) and must be replaced by
measured values before it is used to gate a real project.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Thresholds(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = "v0-unconfirmed"
    confirmed: bool = False

    rare_share_max: float = Field(0.01, ge=0, le=1)
    rare_min_cells: int = Field(50, ge=1)
    rare_min_donors: int = Field(2, ge=1)

    low_confidence_percentile: float = Field(10.0, gt=0, lt=100)
    classifier_margin_min: float = Field(0.15, ge=0, le=1)
    coverage_knn: int = Field(20, ge=1)
    coverage_distance_ratio_min: float = Field(2.0, ge=1)

    stability_cc_ari_min: float = Field(0.6, ge=-1, le=1)
    stability_jaccard_min: float = Field(0.5, ge=0, le=1)
    stability_pass_ratio: float = Field(0.6, ge=0, le=1)

    alignment_continuation_min: float = Field(0.6, ge=0, le=1)
    alignment_uncertain_min: float = Field(0.4, ge=0, le=1)

    min_n_per_group: int = Field(3, ge=1)

    de_fdr_max: float = Field(0.05, gt=0, le=1)
    de_min_abs_log2fc: float = Field(1.0, ge=0)
    detection_frequency_in_min: float = Field(0.6, ge=0, le=1)
    detection_frequency_excluded_max: float = Field(0.1, ge=0, le=1)
    donor_consistency_min: float = Field(0.8, ge=0, le=1)

    numeric_tolerance: float = Field(1e-6, gt=0)
    label_agreement_min: float = Field(0.99, ge=0, le=1)

    expert_queue_card_limit: int = Field(10, ge=1)

    summary_rare_share_max: float = Field(0.02, gt=0, le=1)
    summary_rare_min_cells: int = Field(50, ge=1)

    @model_validator(mode="after")
    def _check_bands(self) -> "Thresholds":
        if self.alignment_uncertain_min > self.alignment_continuation_min:
            raise ValueError("alignment_uncertain_min must be <= alignment_continuation_min")
        if self.detection_frequency_excluded_max >= self.detection_frequency_in_min:
            raise ValueError("excluded detection frequency ceiling must be below in-group floor")
        return self

    @property
    def is_unconfirmed_default_set(self) -> bool:
        return not self.confirmed


DEFAULT_THRESHOLDS = Thresholds()
