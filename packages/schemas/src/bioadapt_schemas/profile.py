"""Project profile contract and rare-signal retention rule (plan V2.0 sections 7.1, 4.5)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import Field, model_validator

from .entities import Contract, ImmutableContract
from .thresholds import DEFAULT_THRESHOLDS, Thresholds


class RetainedGroup(ImmutableContract):
    display_name: str
    size: int = Field(ge=1)
    share: float = Field(ge=0, le=1)
    donor_count: int = Field(ge=1)
    top_markers: list[str] = Field(default_factory=list)
    sample_distribution: dict[str, int] = Field(default_factory=dict)


class MissingInformation(ImmutableContract):
    code: str
    question: str
    blocking: bool = False


class ProjectProfile(Contract):
    project_id: str
    species: str
    tissues: list[str]
    disease_background: Optional[str] = None
    modalities: list[str]
    objective: str
    specimen_count: int = Field(ge=1)
    donor_count: int = Field(ge=1)
    summary_schema_version: str
    groups: list[RetainedGroup] = Field(default_factory=list)
    qc_overview: dict[str, Any] = Field(default_factory=dict)
    missing_information: list[MissingInformation] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    target_populations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _donors_not_exceed_specimens(self) -> "ProjectProfile":
        if self.donor_count > self.specimen_count:
            raise ValueError("donor_count cannot exceed specimen_count")
        return self


def groups_requiring_retention(groups: list[RetainedGroup], thresholds: Thresholds = DEFAULT_THRESHOLDS) -> list[RetainedGroup]:
    return [
        g
        for g in groups
        if g.share < thresholds.summary_rare_share_max and g.size >= thresholds.summary_rare_min_cells
    ]


def missing_retained_groups(
    profile: ProjectProfile,
    observed_groups: list[RetainedGroup],
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> list[str]:
    """Rare groups that must appear in the model context but were dropped by summarisation."""
    required = {g.display_name for g in groups_requiring_retention(observed_groups, thresholds)}
    present = {g.display_name for g in profile.groups if g.display_name in required}
    return sorted(required - present)
