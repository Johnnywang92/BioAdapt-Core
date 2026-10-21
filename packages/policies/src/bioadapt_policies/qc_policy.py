"""QC and artefact policy as code (plan V2.0 section 7.7).

``QcPolicy`` is a versioned configuration object, not a set of numbers inside the analysis code:
the plan requires every threshold to name where it came from, and the default action to be
"mark the cell as at risk" rather than "delete it". Deleting cells is a project owner's act, because
a QC filter applied silently is how a rare population disappears before anyone sees it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Optional

from pydantic import Field, model_validator

from bioadapt_schemas.entities import ImmutableContract

#: Tissues where a high mitochondrial share is biology, not damage (§7.7). A policy that screens
#: one of these without saying so turns quality noise into C1/C3 candidate signals.
HIGH_MITOCHONDRIAL_TISSUES = frozenset(
    {"heart", "cardiac", "kidney", "kidney_tubule", "liver", "tumour", "tumor", "glioblastoma"}
)

#: The artefact screens the plan names (§7.7). Which of them is actually installed is a registry
#: question; this is the list a policy is expected to consider.
DOUBLET_TOOLS = ("scrublet", "doubletfinder")
AMBIENT_TOOLS = ("soupx", "decontx", "cellbender")


class QcMetric(StrEnum):
    TOTAL_COUNTS = "total_counts"
    DETECTED_GENES = "detected_genes"
    MITOCHONDRIAL_FRACTION = "mitochondrial_fraction"
    RIBOSOMAL_FRACTION = "ribosomal_fraction"
    DOUBLET_SCORE = "doublet_score"
    AMBIENT_SCORE = "ambient_score"


class ThresholdSource(StrEnum):
    KNOWLEDGE_CLAIM = "knowledge_claim"
    DISTRIBUTION_DIAGNOSTIC = "distribution_diagnostic"
    MANUAL = "manual"


class QcAction(StrEnum):
    #: Section 7.7: the MVP default. The cell stays and carries a risk label.
    MARK = "mark"
    #: Only with a named approver, a recorded reason and a report annotation.
    DROP = "drop"


class CellFilter(StrEnum):
    """How the rule is applied once it is no longer only a label."""

    OUTSIDE = "outside"
    ABOVE = "above"
    BELOW = "below"


class QcRule(ImmutableContract):
    name: str = Field(min_length=1)
    metric: QcMetric
    apply: CellFilter = CellFilter.OUTSIDE
    lower: Optional[float] = None
    upper: Optional[float] = None
    action: QcAction = QcAction.MARK
    threshold_source: ThresholdSource
    #: The claim id, the diagnostic id or the measured quantile the bound came from.
    source_ref: Optional[str] = None
    #: Knowledge-claim thresholds are only usable once the claim has been reviewed (§6.5).
    claim_reviewed: bool = False
    reason: Optional[str] = None
    tissue_override: Optional[str] = None

    @model_validator(mode="after")
    def _source_and_bounds(self) -> "QcRule":
        if self.apply is CellFilter.OUTSIDE and self.lower is None and self.upper is None:
            raise ValueError(f"rule {self.name} filters outside a band but declares no bound")
        if self.apply is CellFilter.ABOVE and self.upper is None:
            raise ValueError(f"rule {self.name} flags cells above a bound but declares none")
        if self.apply is CellFilter.BELOW and self.lower is None:
            raise ValueError(f"rule {self.name} flags cells below a bound but declares none")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError(f"rule {self.name} has a band whose lower edge is above its upper edge")
        if self.threshold_source is ThresholdSource.MANUAL and not self.reason:
            raise ValueError(
                f"rule {self.name} sets a threshold by hand; section 7.7 requires the reason, "
                "which is recorded as an analysis_decision"
            )
        if self.threshold_source is ThresholdSource.KNOWLEDGE_CLAIM:
            if not self.source_ref:
                raise ValueError(f"rule {self.name} cites a knowledge claim but names none")
            if not self.claim_reviewed:
                raise ValueError(f"rule {self.name} cites claim {self.source_ref} which is not reviewed")
        if self.threshold_source is ThresholdSource.DISTRIBUTION_DIAGNOSTIC and not self.source_ref:
            raise ValueError(f"rule {self.name} must name the diagnostic its quantile came from")
        return self

    def bound(self) -> Optional[float]:
        if self.apply is CellFilter.ABOVE:
            return self.upper
        if self.apply is CellFilter.BELOW:
            return self.lower
        return self.lower if self.lower is not None else self.upper

    def flags(self, value: float) -> bool:
        if self.apply is CellFilter.ABOVE:
            return value > self.upper  # type: ignore[operator]
        if self.apply is CellFilter.BELOW:
            return value < self.lower  # type: ignore[operator]
        return (self.lower is not None and value < self.lower) or (
            self.upper is not None and value > self.upper
        )


class QcPolicy(ImmutableContract):
    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    species: str = Field(min_length=1)
    tissue: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    dissociation: Optional[str] = None
    expected_artifacts: list[str] = Field(default_factory=list)
    #: The screens asked for, by tool name; each one must exist in the registry for the plan to run.
    artifact_screens: list[str] = Field(default_factory=list)
    rules: list[QcRule] = Field(default_factory=list)
    #: Section 7.7: a high-mitochondrial tissue needs an explicit biological reading of the metric.
    mitochondrial_interpretation: Optional[str] = None
    #: Cell deletion is a project owner's act, recorded here so the runner can check it.
    cell_deletion_approved_by: Optional[str] = None
    cell_deletion_reason: Optional[str] = None
    deletion_annotated_in_report: bool = False
    policy_kind: str = "qc_policy_v1"

    @model_validator(mode="after")
    def _deletion_approval(self) -> "QcPolicy":
        missing = self.deletion_violations()
        if missing:
            raise ValueError("; ".join(missing))
        return self

    def deletion_violations(self) -> list[str]:
        dropping = [rule.name for rule in self.rules if rule.action is QcAction.DROP]
        if not dropping:
            return []
        found = []
        if not self.cell_deletion_approved_by:
            found.append(f"rules {dropping} delete cells with no approving project owner")
        if not self.cell_deletion_reason:
            found.append(f"rules {dropping} delete cells without a recorded reason")
        if not self.deletion_annotated_in_report:
            found.append(f"rules {dropping} delete cells but the report will not say so")
        return found

    def lint(self) -> list[str]:
        """Everything a reviewer looks at before this policy is used on a real project."""
        found: list[str] = []
        if not self.rules:
            found.append("a QC policy with no rules produces no risk labels; state why nothing is screened")
        names = [rule.name for rule in self.rules]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            found.append(f"rule names repeat: {duplicates}")
        found.extend(self.deletion_violations())
        if self.tissue in HIGH_MITOCHONDRIAL_TISSUES and not self.mitochondrial_interpretation:
            found.append(
                f"tissue {self.tissue!r} has a high mitochondrial baseline; section 7.7 requires an "
                "explicit override before mitochondrial thresholds are trusted"
            )
        if not self.dissociation:
            found.append("dissociation protocol is unknown, so stress artefacts cannot be interpreted")
        if not self.expected_artifacts:
            found.append("expected artefacts are not declared")
        return found

    def rules_for(self, metric: QcMetric) -> list[QcRule]:
        return [rule for rule in self.rules if rule.metric is metric]

    def marks_only(self) -> bool:
        return all(rule.action is QcAction.MARK for rule in self.rules)

    def drop_rules(self) -> list[QcRule]:
        return [rule for rule in self.rules if rule.action is QcAction.DROP]
