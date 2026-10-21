"""One real follow-up run, its registered candidates, and the card drafts its products produce.

Two suites read this: the contract-parity checks in ``test_evidence_card_service.py`` and the metric checks in
``test_evaluation_over_the_real_run.py``. §9.5 keeps the layers apart, and this is the root tree that joins
them - so the wiring is written once, here, and a second copy would be a weaker copy of the run the plan asks
both files to measure.

Nothing in this module decides a verdict. It runs the chain, files what the chain produced, and hands the
result over; the reading is each suite's own business.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import driver_factories as F

from bioadapt_evidence import CardDraft, EvidenceCardService, barcodes_of, specimen_of
from bioadapt_evidence.assets import SLOT_CLUSTERS
from bioadapt_scrnaseq import read_product
from bioadapt_schemas.entities import CandidateSet, DataAsset
from bioadapt_schemas.enums import CandidateType
from bioadapt_schemas.identifiers import derive_candidate

FOCUS = F.CANDIDATE_CLUSTER

#: A cluster the follow-up plan points the robustness, coverage and state-axis nodes away from, so a card
#: drafted for it has questions the run can only answer with "not measured" (§7.5, §13.4).
QUIET_CLUSTER = "c001"


def stored(h: F.Harness, slot: str, assets: dict[str, str]) -> Any:
    """Rebuild one stored product with the workflow's own contract class - the writer's side of the seam."""
    asset = h.asset(assets[slot])
    return read_product(asset.schema_version, h.objects.get(asset.uri[len("s3://") :]))


def candidate_sets(h: F.Harness, assets: dict[str, str]) -> list[CandidateSet]:
    """One candidate set per cluster of the partition the run actually stored.

    Membership is re-derived from the stored labels through the platform's own ``derive_candidate``, so a card
    is drafted against a set the index can reproduce. Which class each set claims (§7.2) is the discovery
    step's act; this file only checks that a card carries the evidence set the claim requires.
    """
    clusters = stored(h, SLOT_CLUSTERS, assets)
    made: list[CandidateSet] = []
    for name, keys in clusters.cells_by_cluster().items():
        pairs = list(zip([specimen_of(key) for key in keys], barcodes_of(keys)))
        candidate_id, fingerprint, size = derive_candidate(pairs, h.run_id)
        made.append(
            CandidateSet(
                candidate_id=candidate_id,
                run_id=h.run_id,
                member_fingerprint=fingerprint,
                size=size,
                donor_count=len({specimen for specimen, _barcode in pairs}),
                display_name=name,
                cluster_origin={"method": clusters.method, "resolution": clusters.resolution},
                candidate_types=[CandidateType.C3_RARE_STABLE],
            )
        )
    return made


@dataclass
class Carded:
    """One real follow-up run, its registered candidates, and the drafts its products produce."""

    harness: F.Harness
    assets: dict[str, str]
    service: EvidenceCardService
    candidates: list[CandidateSet]
    drafts: list[CardDraft]

    def draft(self, display_name: str) -> CardDraft:
        return next(d for d in self.drafts if d.card.display_name == display_name)

    def candidate(self, display_name: str) -> CandidateSet:
        return next(c for c in self.candidates if c.display_name == display_name)

    def measured(self, slot: str) -> Any:
        return stored(self.harness, slot, self.assets)

    def asset(self, slot: str) -> DataAsset:
        return self.harness.asset(self.assets[slot])

    def unmeasured(self) -> str:
        """A candidate the robustness, coverage and state-axis nodes were all pointed away from."""
        names = {FOCUS, QUIET_CLUSTER}
        return next(
            name for name in sorted(c.display_name for c in self.candidates) if name not in names
        )


def carded_run(**overrides: Any) -> Carded:
    """Run the follow-up chain, register one candidate per cluster, and draft a card for each.

    ``overrides`` reaches ``driver_factories.harness``, so a suite that wants a different plan or a different
    registry asks for it instead of rebuilding the run beside this one.
    """
    the_plan = overrides.pop("the_plan", None) or F.plan(nodes=F.follow_up_nodes())
    h = F.harness(the_plan=the_plan, **overrides)
    out = h.run()
    assert out.completed, out.stopped_reason
    candidates = candidate_sets(h, out.assets)
    for candidate in candidates:
        h.catalog.record_candidate(candidate)
    service = EvidenceCardService(catalog=h.catalog, objects=h.objects, run_id=h.run_id)
    return Carded(
        harness=h,
        assets=dict(out.assets),
        service=service,
        candidates=candidates,
        drafts=service.drafts(),
    )


def rerun_of(carded: Carded) -> Carded:
    """The same plan and the same root data through a second control plane (§1.5 byte-level reproducibility).

    The comparison is between two runs, not two reads of one run: the harness is rebuilt so the index, the
    object store and the journal are all fresh, which is what a reproduce-at-byte claim has to survive.
    """
    return carded_run(the_plan=carded.harness.plan, data=carded.harness.data)
