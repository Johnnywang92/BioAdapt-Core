"""Model provider terms (plan V2.0 sections 4.5, 11.2)."""

from datetime import date, timedelta
from typing import Optional

from pydantic import model_validator

from bioadapt_schemas.entities import ImmutableContract

# Stage-0 placeholder (§11.2): refresh before every contract renewal.
TERMS_VALIDITY_DAYS = 365


class ProviderTerms(ImmutableContract):
    """Contract terms that must be verified before a restricted project may use a cloud model."""

    provider: str
    endpoint: str
    zero_retention: bool
    no_training_use: bool
    subprocessors_disclosed: bool
    data_region: str
    dpa_signed: bool
    reviewed_at: date
    reviewed_by: str
    dedicated_endpoint: Optional[str] = None

    @model_validator(mode="after")
    def _cloud_preconditions(self) -> "ProviderTerms":
        missing = []
        for field in (
            "zero_retention",
            "no_training_use",
            "subprocessors_disclosed",
            "dpa_signed",
        ):
            if not getattr(self, field):
                missing.append(field)
        if missing:
            raise ValueError(f"provider terms incomplete: {missing}")
        return self

    def is_current(self, today: date, validity_days: int = TERMS_VALIDITY_DAYS) -> bool:
        return today - self.reviewed_at <= timedelta(days=validity_days)
