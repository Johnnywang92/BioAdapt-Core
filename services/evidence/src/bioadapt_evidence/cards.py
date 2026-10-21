"""Draft evidence cards from stored products (plan V2.0 sections 7.2-7.8, 9.1, 5.4).

Two rules shape this module.

**Measurements in, verdicts out.** The stored products hold numbers plus the criteria they were measured
against, and whether a candidate is stable, covered, rare or an axis tail is derived here from those
numbers every time a card is built. A driver - and a model - cannot write a conclusion into an asset and
have it come back as evidence: a reviewer re-reads the same bytes and gets the same verdict.

**A draft is not a decision.** Section 9.1 gives the agent no write permission, so what leaves this module
is a draft whose hypothesis type is ``unknown``. It carries the evidence set, the derived ceiling and the
gate reasons a reviewer needs, but the label, the ontology term and the final status are human acts
recorded through the review routes.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from bioadapt_policies.qc_policy import QcMetric
from bioadapt_schemas.entities import CandidateSet
from bioadapt_schemas.enums import (
    ArtifactRisk,
    CrossModalStatus,
    EvidenceKind,
    EvidencePartition,
    EvidenceRole,
    FieldSource,
    FinalStatus,
    HypothesisType,
    LabelLevel,
    PerturbationKind,
    ScreeningResult,
    SignOff,
    UnknownReason,
)
from bioadapt_schemas.evidence import (
    ArtifactScreening,
    CompositionCheck,
    ContinuousStateCheck,
    Evidence,
    EvidenceCard,
    EvidenceSource,
    FieldProvenance,
    IdentityVsState,
    NoveltyVsCoverage,
    PerturbationResult,
    ScreeningItem,
    StabilitySummary,
)
from bioadapt_schemas.gates import (
    GateViolation,
    MarkerStatistics,
    audited_numeric_paths,
    artifact_verdict,
    enforce_card_status,
    novelty_verdict,
    rare_verdict,
    stability_verdict,
    support_rule_verdict,
)
from bioadapt_schemas.identifiers import member_fingerprint, sha256_hex
from bioadapt_schemas.plan import AnalysisPlan
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS, Thresholds
from bioadapt_storage.catalog import Catalog
from bioadapt_storage.objectstore import ObjectStore

from .assets import (
    SLOT_CLUSTERS,
    SLOT_COVERAGE,
    SLOT_DE_TABLE,
    SLOT_MARKERS,
    SLOT_QC_FLAGS,
    SLOT_STABILITY,
    SLOT_STATE_AXIS,
    AssetReader,
    StoredProduct,
    specimen_of,
)
from .errors import CardUnderReview, ContractDrift, TriggerOnHeldOutCells

#: The stored floats are quantized to this precision by the writer, so a median re-read here is the same
#: number the step fingerprinted (§5.6). Re-rounded, a verdict could move on a re-read.
PRECISION = 6

#: Section 7.7: which QC metric can screen which artefact risk. A risk whose screen is not installed is
#: recorded NOT_RUN rather than assumed clear - an absent tool is a finding, not a zero.
RISK_METRICS: Mapping[ArtifactRisk, tuple[QcMetric, ...]] = {
    ArtifactRisk.DOUBLET: (QcMetric.DOUBLET_SCORE,),
    ArtifactRisk.AMBIENT: (QcMetric.AMBIENT_SCORE,),
    ArtifactRisk.LOW_QUALITY: (
        QcMetric.MITOCHONDRIAL_FRACTION,
        QcMetric.RIBOSOMAL_FRACTION,
        QcMetric.TOTAL_COUNTS,
        QcMetric.DETECTED_GENES,
    ),
    ArtifactRisk.BATCH_SPECIFIC: (),
    ArtifactRisk.STRESS_CYCLE: (),
    ArtifactRisk.OVER_SPLIT: (),
}

RISK_EVIDENCE_KIND: Mapping[ArtifactRisk, EvidenceKind] = {
    ArtifactRisk.DOUBLET: EvidenceKind.DOUBLET_SCREENING,
    ArtifactRisk.AMBIENT: EvidenceKind.AMBIENT_SCREENING,
    ArtifactRisk.LOW_QUALITY: EvidenceKind.QUALITY_SCREENING,
    ArtifactRisk.BATCH_SPECIFIC: EvidenceKind.BATCH_SCREENING,
    ArtifactRisk.STRESS_CYCLE: EvidenceKind.STRESS_CYCLE_SCREENING,
    ArtifactRisk.OVER_SPLIT: EvidenceKind.OVER_SPLIT_SCREENING,
}

#: Section 7.5: the perturbation class that stands in for each of the two artefact risks no QC metric
#: measures. A donor-dropout arm is a proxy for a batch screen, and the card detail says so rather than
#: letting the proxy read as the real thing.
RISK_PERTURBATION: Mapping[ArtifactRisk, PerturbationKind] = {
    ArtifactRisk.BATCH_SPECIFIC: PerturbationKind.LEAVE_ONE_DONOR,
    ArtifactRisk.OVER_SPLIT: PerturbationKind.RESOLUTION,
}

#: A screen reads FAIL once the flagged share of the candidate reaches this, and WARN below it. Stage-0
#: number (§1.5, appendix C1): a majority of a population carrying the same risk label is what makes the
#: population the artefact rather than the artefact a minority of it.
DEFAULT_BLOCKING_SHARE = 0.5

#: How many per-gene rows a card reproduces. The table itself is cited by asset id, so a card stays
#: readable without losing the measurement it points at.
MAX_CARD_GENES = 25

SCREEN_ROLE: Mapping[ScreeningResult, Optional[EvidenceRole]] = {
    ScreeningResult.PASS: EvidenceRole.SUPPORTING,
    ScreeningResult.WARN: EvidenceRole.TRIGGER,
    ScreeningResult.FAIL: EvidenceRole.CONTRADICTING,
    ScreeningResult.NOT_RUN: None,
}


def card_id_for(candidate_id: str) -> str:
    """Deterministic, so rebuilding a card from the same bytes addresses the same row."""
    return "card_" + sha256_hex(candidate_id.encode("utf-8"))[:12]


def _evidence_id(candidate_id: str, kind: EvidenceKind, role: EvidenceRole, slug: str) -> str:
    digest = sha256_hex(f"{candidate_id}|{kind}|{role}|{slug}".encode("utf-8"))
    return f"ev_{digest[:12]}"


def _median(values: Iterable[Optional[float]]) -> Optional[float]:
    numbers = [round(float(value), PRECISION) for value in values if value is not None]
    return statistics.median(numbers) if numbers else None


def _share(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, PRECISION) if denominator else 0.0


def _product_citation(product: StoredProduct, slot_note: str) -> FieldProvenance:
    """The citation an aggregate card field carries.

    The numbers are the tool's and the derivation is this reader's, which is what ``note`` says: a
    reviewer who disagrees with the derivation can read the same asset and do it differently, but nobody
    can read the field as a stored answer.
    """
    return FieldProvenance(
        source=FieldSource.TOOL,
        object_id=product.asset_id,
        note=(
            f"{slot_note}, read from the {product.slot} asset {product.asset_id} "
            f"({product.schema_version}) and derived here"
        ),
    )


def _criteria_of(product: StoredProduct, name: str, required: Sequence[str]) -> dict[str, float]:
    value = product.get(name)
    if not isinstance(value, Mapping):
        raise ContractDrift(f"{product.slot} asset {product.asset_id} stores {name!r} as {type(value).__name__}")
    criteria = {str(key): float(item) for key, item in value.items()}
    missing = sorted(key for key in required if key not in criteria)
    if missing:
        raise ContractDrift(
            f"{product.slot} asset {product.asset_id} records no criteria for {missing}; a card may only "
            "judge a measurement against the cuts the step wrote beside it"
        )
    return criteria


def _kind_judgement(arms: Sequence[Mapping[str, Any]], criteria: Mapping[str, float]) -> str:
    """Section 7.5's verdict for one perturbation class, recomputed from the stored arm rows.

    This is the reader-side mirror of ``StabilityProduct.judgement`` and the root seam test pins the two
    against each other: if the two ever disagree, a card would carry a stability answer the asset does
    not support, which is exactly what storing measurements instead of verdicts is meant to prevent.
    """
    if any(int(row["cells_compared"]) == 0 for row in arms):
        return "fail"
    agreement = _median(row.get("cc_ari") for row in arms)
    overlap = _median(row.get("jaccard") for row in arms)
    if agreement is None or overlap is None:
        return "unmeasurable"
    if agreement < float(criteria["cc_ari_min"]) or overlap < float(criteria["jaccard_min"]):
        return "fail"
    small = [row for row in arms if int(row["baseline_cells"]) <= int(criteria["small_cluster_max_cells"])]
    if small:
        null = _median(row.get("permutation_jaccard") for row in small)
        if null is None:
            return "unmeasurable"
        if (_median(row.get("jaccard") for row in small) or 0.0) <= null:
            return "fail"
    return "pass"


def _rule_threshold_source(product: StoredProduct, names: Sequence[str]) -> str:
    """Where each QC bound came from, named the way the policy recorded it (section 7.7)."""
    wanted = set(names)
    parts = sorted(
        f"{rule['name']}={rule['threshold_source']}"
        + (f":{rule['source_ref']}" if rule.get("source_ref") else "")
        for rule in product.rows("rules")
        if str(rule["name"]) in wanted
    )
    return "; ".join(parts) or (
        f"QC policy {product.get('policy_id')}@{product.get('policy_version')} records no source for {sorted(wanted)}"
    )


@dataclass(frozen=True)
class SupportRule:
    """Section 7.8's identity criteria, versioned, because a card must say which rule read its numbers."""

    version: str = "support_rule_v1"
    #: How many markers have to meet every criterion before the marker set counts as support.
    min_qualifying_markers: int = 1


@dataclass(frozen=True)
class ScreeningRule:
    version: str = "screening_rule_v1"
    blocking_share: float = DEFAULT_BLOCKING_SHARE


@dataclass(frozen=True)
class CardDraft:
    """The draft plus what the gates made of it - the reviewer is shown the ceiling, not told a verdict."""

    card: EvidenceCard
    ceiling: FinalStatus
    gate_reasons: tuple[str, ...]
    notes: tuple[str, ...]
    support_met: Optional[bool]
    underpowered_design: bool

    @property
    def card_id(self) -> str:
        return self.card.card_id

    @property
    def candidate_id(self) -> str:
        return self.card.candidate_id

    @property
    def blocking(self) -> tuple[str, ...]:
        screening = self.card.artifact_screening
        return tuple(risk.value for risk in screening.blocked_by) if screening else ()


class _Citations:
    """Numeric provenance, resolved by the longest registered path prefix.

    Section 7.3 requires every number on a card to name where it came from, and a card assembled from six
    assets cannot get that by hand-typing paths. Each record registers the prefix it owns, and the leaf
    paths the gate enumerates are then looked up - so an unregistered leaf is a bug the builder raises on
    rather than a field that quietly arrives without provenance.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[str, FieldProvenance]] = []

    def register(self, prefix: str, entry: FieldProvenance) -> None:
        self._entries.append((prefix, entry))

    def resolve(self, path: str) -> FieldProvenance:
        matches = [
            (len(prefix), entry)
            for prefix, entry in self._entries
            if path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[")
        ]
        if not matches:
            raise ContractDrift(
                f"numeric card field {path!r} has no registered provenance; every number on a draft card "
                "must name the tool record it came from (section 7.3)"
            )
        return max(matches, key=lambda item: item[0])[1]

    def for_card(self, card: EvidenceCard) -> dict[str, FieldProvenance]:
        return {path: self.resolve(path) for path in audited_numeric_paths(card)}


@dataclass
class _Assembly:
    """One candidate's card under construction."""

    candidate: CandidateSet
    reader: AssetReader
    plan: AnalysisPlan
    thresholds: Thresholds
    support_rule: SupportRule
    screening_rule: ScreeningRule
    cells: tuple[str, ...] = ()
    partition_cells: tuple[str, ...] = ()
    evidence: dict[str, Evidence] = field(default_factory=dict)
    citations: _Citations = field(default_factory=_Citations)
    notes: list[str] = field(default_factory=list)
    screening: dict[ArtifactRisk, ScreeningItem] = field(default_factory=dict)
    marker_support_attempted: bool = False
    #: Every population-vs-population contrast that named this candidate as its in-group arm has to meet
    #: section 7.8's criteria: a gene that only separates it from one of two other populations is not a
    #: marker of it, so the strict reading across contrasts is the honest one.
    marker_support_met: bool = True
    underpowered: bool = False

    # ------------------------------------------------------------------ shared pieces

    def cluster_of(self) -> dict[str, str]:
        product = self.reader.product(SLOT_CLUSTERS)
        return {str(row["cell_key"]): str(row["cluster"]) for row in product.rows("labels")}

    def partition_for(self, used_cells: Sequence[str]) -> tuple[EvidencePartition, Optional[str]]:
        confirmation = set(self.plan.data_partitions.confirmation_specimens)
        discovery = set(self.plan.data_partitions.discovery_specimens)
        specimens = {specimen_of(key) for key in used_cells}
        if not specimens:
            return EvidencePartition.DISCOVERY, "the measurement covers no cell of this candidate"
        in_confirmation = specimens & confirmation
        in_discovery = specimens & discovery
        unlisted = sorted(specimens - confirmation - discovery)
        if in_confirmation and not in_discovery:
            return EvidencePartition.CONFIRMATION, None
        if in_discovery and not in_confirmation:
            return EvidencePartition.DISCOVERY, f"specimens in neither partition: {unlisted}" if unlisted else None
        note = "the measurement's cells span both partitions, so it confirms nothing (§7.4)"
        if unlisted:
            note = f"{note}; specimens outside both declared partitions: {unlisted}"
        return EvidencePartition.DISCOVERY, note

    def add(
        self,
        *,
        kind: EvidenceKind,
        role: EvidenceRole,
        slug: str,
        product: StoredProduct,
        content: dict[str, Any],
        used_cells: Sequence[str],
        cite_overrides: Mapping[str, FieldProvenance] = {},
    ) -> Evidence:
        evidence_id = _evidence_id(self.candidate.candidate_id, kind, role, slug)
        if evidence_id in self.evidence:
            raise ContractDrift(f"evidence record {evidence_id} is produced twice for one candidate")
        partition, note = self.partition_for(used_cells)
        if role is EvidenceRole.TRIGGER and partition is EvidencePartition.CONFIRMATION:
            #: Section 7.4: a trigger found inside the held-out partition is the circular inference the
            #: discovery/confirmation split exists to stop, so the card refuses to be built rather than
            #: relabelling the partition to make the record fit.
            raise TriggerOnHeldOutCells(
                f"trigger signal {kind} / {slug} for candidate {self.candidate.display_name} was measured "
                "only on confirmation specimens; move the measurement to the discovery partition",
                field="data_partitions",
            )
        record = Evidence(
            evidence_id=evidence_id,
            kind=kind,
            role=role,
            partition=partition,
            source=EvidenceSource(run_id=self.reader.run_id, asset_id=product.asset_id),
            provenance=FieldProvenance(
                source=FieldSource.TOOL,
                object_id=product.asset_id,
                note=f"read from the {product.slot} asset {product.asset_id} ({product.schema_version})",
            ),
            independence_group=product.asset.content_fingerprint[:12],
            used_cell_set_fingerprint=(
                member_fingerprint(used_cells) if used_cells else None
            ),
            content=content,
        )
        self.evidence[evidence_id] = record
        self.citations.register(f"evidence.{evidence_id}", record.provenance)
        for prefix, entry in cite_overrides.items():
            self.citations.register(f"evidence.{evidence_id}.content.{prefix}", entry)
        if note:
            self.notes.append(note)
        return record


class CandidateCardBuilder:
    """Turns one run's stored products into a draft card per candidate set."""

    def __init__(
        self,
        *,
        reader: AssetReader,
        plan: AnalysisPlan,
        thresholds: Thresholds = DEFAULT_THRESHOLDS,
        support_rule: Optional[SupportRule] = None,
        screening_rule: Optional[ScreeningRule] = None,
    ) -> None:
        self.reader = reader
        self.plan = plan
        self.thresholds = thresholds
        self.support_rule = support_rule or SupportRule()
        self.screening_rule = screening_rule or ScreeningRule()

    # ------------------------------------------------------------------ entry

    def draft(self, candidate: CandidateSet) -> CardDraft:
        if candidate.run_id != self.reader.run_id:
            raise ContractDrift(
                f"candidate {candidate.candidate_id} belongs to run {candidate.run_id}, not {self.reader.run_id}"
            )
        assembly = _Assembly(
            candidate=candidate,
            reader=self.reader,
            plan=self.plan,
            thresholds=self.thresholds,
            support_rule=self.support_rule,
            screening_rule=self.screening_rule,
        )
        assembly.cells = self._membership(candidate)
        assembly.partition_cells = tuple(sorted(assembly.cluster_of()))

        self._composition(assembly)
        self._markers(assembly)
        self._differential_expression(assembly)
        stability = self._stability(assembly)
        screening = self._artifact_screening(assembly, stability)
        continuous = self._continuous_state(assembly)
        coverage = self._coverage(assembly, stability=stability, screening=screening, continuous=continuous)

        card = self._card(assembly, stability=stability, screening=screening, coverage=coverage, continuous=continuous)
        return self._gate(card, assembly)

    # ------------------------------------------------------------------ membership

    def _membership(self, candidate: CandidateSet) -> tuple[str, ...]:
        """The candidate's cells, recovered from the stored labels and checked against its fingerprint."""
        product = self.reader.product(SLOT_CLUSTERS)
        cells = tuple(
            sorted(
                str(row["cell_key"])
                for row in product.rows("labels")
                if str(row["cluster"]) == candidate.display_name
            )
        )
        if not cells:
            raise ContractDrift(
                f"the clusters asset of run {self.reader.run_id} has no cells labelled "
                f"{candidate.display_name!r}, so candidate {candidate.candidate_id} has no membership"
            )
        fingerprint = member_fingerprint(cells)
        if fingerprint != candidate.member_fingerprint:
            raise ContractDrift(
                f"candidate {candidate.candidate_id} records member_fingerprint "
                f"{candidate.member_fingerprint}, but the stored labels for {candidate.display_name!r} "
                f"hash to {fingerprint}: the candidate and the partition are not the same set of cells"
            )
        if len(cells) != candidate.size:
            raise ContractDrift(
                f"candidate {candidate.candidate_id} declares {candidate.size} cells, the stored labels "
                f"give {len(cells)}"
            )
        return cells

    # ------------------------------------------------------------------ sections

    def _composition(self, a: _Assembly) -> None:
        """Section 1.5's rare-signal rule, measured: how big is this set, and does it survive a donor drop."""
        product = a.reader.product(SLOT_CLUSTERS)
        total = len(a.partition_cells)
        verdict = rare_verdict(
            a.candidate.size,
            total,
            a.candidate.donor_count,
            a.thresholds,
        )
        share = verdict.detail["share"]
        origin = a.candidate.cluster_origin
        content = {
            "size": a.candidate.size,
            "donor_count": a.candidate.donor_count,
            "partition_cells": total,
            "share": round(float(share), PRECISION),
            "rare": verdict.passed,
            "rare_rule_reasons": list(verdict.reasons),
            "cluster_method": str(origin.get("method", "")),
            "cluster_resolution": round(float(origin.get("resolution", 0.0)), PRECISION),
            "thresholds_version": a.thresholds.version,
        }
        #: The size and share come from the stored partition; the donor count is the discovery node's
        #: index row, and is cited as such rather than folded in with the rest.
        a.add(
            kind=EvidenceKind.COMPOSITION,
            role=EvidenceRole.TRIGGER,
            slug="rarity",
            product=product,
            content=content,
            used_cells=a.partition_cells,
            cite_overrides={
                "donor_count": FieldProvenance(
                    source=FieldSource.TOOL,
                    object_id=a.candidate.candidate_id,
                    note=f"donor count recorded by the discovery node on candidate set {a.candidate.candidate_id}",
                )
            },
        )

    def _markers(self, a: _Assembly) -> None:
        """Section 7.8 item 5: the rank-sum ordering is an aid to reading the table, never identity evidence."""
        product = a.reader.product(SLOT_MARKERS)
        rows = sorted(
            (row for row in product.rows("markers") if str(row["cluster"]) == a.candidate.display_name),
            key=lambda row: int(row["rank"]),
        )
        if not rows:
            a.notes.append(
                f"the marker table of asset {product.asset_id} names no gene for {a.candidate.display_name}"
            )
            return
        a.add(
            kind=EvidenceKind.MARKER_SPECIFICITY,
            role=EvidenceRole.TRIGGER,
            slug="rank-order",
            product=product,
            content={
                "method": str(product.get("method")),
                "rank_only": bool(product.get("rank_only")),
                "top_n": int(product.get("top_n")),
                "genes": [str(row["gene"]) for row in rows],
                "thresholds_version": a.thresholds.version,
            },
            used_cells=a.partition_cells,
        )

    def _differential_expression(self, a: _Assembly) -> None:
        product = a.reader.product(SLOT_DE_TABLE)
        details: Mapping[str, Any] = dict(product.get("contrast_details"))
        table = product.rows("table")
        multiple_testing = str(product.get("multiple_testing"))
        thresholds_version = str(product.get("thresholds_version"))
        donors_per_group = {str(k): int(v) for k, v in dict(product.get("donors_per_group")).items()}
        floor = a.plan.design_matrix.min_n_per_group
        if any(n < floor for n in donors_per_group.values()):
            #: Section 7.4: under the design's own power floor, a group comparison is descriptive, and the
            #: card says so in the same breath as it reports the numbers.
            a.underpowered = True
            a.notes.append(
                f"donors per group {donors_per_group} is below the design's min_n_per_group={floor}, "
                "so group comparisons on this run are descriptive only (§7.4)"
            )

        for name in sorted(details):
            detail = dict(details[name])
            arms = str(detail.get("arms", "condition"))
            group_a = str(detail.get("group_a", ""))
            group_b = str(detail.get("group_b", ""))
            cluster = detail.get("cluster")
            design = str(detail.get("design", ""))
            common = {
                "contrast": name,
                "arms": arms,
                "group_a": group_a,
                "group_b": group_b,
                "cluster": cluster,
                "design": design,
                "donors_a": int(detail.get("donors_a", 0)),
                "donors_b": int(detail.get("donors_b", 0)),
                "paired_donors": int(detail.get("paired_donors", 0)),
                "multiplicity_strategy": multiple_testing,
                "thresholds_version": thresholds_version,
            }
            rows = [
                row
                for row in table
                if str(row["group_a"]) == group_a and str(row["group_b"]) == group_b
            ]

            if arms != "cluster":
                #: A condition contrast says this set moves between sample groups. That is a reason to look
                #: (§7.2b), not an identity claim - §7.8's criteria read one arm as the population.
                a.add(
                    kind=EvidenceKind.DIFFERENTIAL_EXPRESSION,
                    role=EvidenceRole.TRIGGER,
                    slug=f"condition/{name}",
                    product=product,
                    content={**common, "genes_tested": len(rows)},
                    used_cells=a.partition_cells if cluster in (None, "", "all_cells") else a.cells,
                )
                continue

            if group_a != a.candidate.display_name:
                #: The recorded criteria read arm A as "in-group" and arm B as "excluded", so a contrast
                #: aimed the other way measures the other population's identity. Reading it as support for
                #: this candidate would be a sign error, so it stays a trigger with the reason attached.
                a.add(
                    kind=EvidenceKind.MARKER_SPECIFICITY,
                    role=EvidenceRole.TRIGGER,
                    slug=f"cluster/{name}",
                    product=product,
                    content={
                        **common,
                        "genes_tested": len(rows),
                        "orientation": (
                            "this candidate is arm B, while section 7.8's criteria are recorded against "
                            "arm A; the table tests the other population's identity markers"
                        ),
                    },
                    used_cells=a.cells,
                )
                continue

            stats = [self._marker_statistics(product, row, multiple_testing) for row in rows]
            a.marker_support_attempted = True
            verdict = support_rule_verdict(
                stats,
                a.thresholds,
                require_min_markers=a.support_rule.min_qualifying_markers,
            )
            a.marker_support_met = a.marker_support_met and verdict.passed
            qualifying = set(verdict.detail["qualifying"])
            role = EvidenceRole.SUPPORTING if verdict.passed else EvidenceRole.CONTRADICTING
            shown = [row for row in rows if str(row["gene"]) in qualifying][:MAX_CARD_GENES]
            content = {
                **common,
                "excluded_population": group_b,
                "support_rule_version": a.support_rule.version,
                "min_qualifying_markers": a.support_rule.min_qualifying_markers,
                "qualifying_genes": sorted(qualifying),
                "support_rule_reasons": list(verdict.reasons),
                "genes": [
                    {
                        "gene": str(row["gene"]),
                        "log2fc": round(float(row["log2fc"]), PRECISION),
                        "fdr": round(float(row["padj"]), PRECISION),
                        "detection_in_candidate": round(float(row["detection_a"]), PRECISION),
                        "detection_in_excluded": round(float(row["detection_b"]), PRECISION),
                        "donors_tested": int(row["donors_tested"]),
                        "donors_same_direction": int(row["donors_same_direction"]),
                    }
                    for row in shown
                ],
            }
            if design != "paired":
                content["design_limit"] = (
                    "arms do not share every donor, so the unpaired test was used (§7.8 item 1)"
                )
            #: The measurement covers the two named populations; the candidate's own cells are arm A.
            used = tuple(
                key
                for key, cluster_name in a.cluster_of().items()
                if cluster_name in {group_a, group_b}
            )
            a.add(
                kind=EvidenceKind.MARKER_SPECIFICITY,
                role=role,
                slug=f"cluster/{name}",
                product=product,
                content=content,
                used_cells=used,
            )

    def _marker_statistics(
        self, product: StoredProduct, row: Mapping[str, Any], multiple_testing: str
    ) -> MarkerStatistics:
        tested = int(row["donors_tested"])
        same = int(row["donors_same_direction"])
        return MarkerStatistics(
            gene=str(row["gene"]),
            fdr=float(row["padj"]),
            log2fc=float(row["log2fc"]),
            detection_frequency_in=float(row["detection_a"]),
            detection_frequency_excluded=float(row["detection_b"]),
            donor_direction_consistency=0.0 if tested <= 0 else _share(same, tested),
            #: Recorded, never implied (§7.8 item 4). The product states one strategy for the whole table.
            multiplicity_strategy=multiple_testing,
        )

    def _stability(self, a: _Assembly) -> Optional[StabilitySummary]:
        """Section 7.5: the numbers each perturbation class produced, and this reader's judgement of them."""
        product = a.reader.optional(SLOT_STABILITY)
        if product is None:
            a.notes.append("no robustness product is stored for this run, so stability is unmeasured (§7.5)")
            return None
        rows = [
            row for row in product.rows("arms") if str(row["baseline_cluster"]) == a.candidate.display_name
        ]
        if not rows:
            a.notes.append(
                f"the robustness product {product.asset_id} measured no perturbation arm for "
                f"{a.candidate.display_name}, so stability is unmeasured for this candidate"
            )
            return None
        criteria = _criteria_of(
            product, "criteria", ("cc_ari_min", "jaccard_min", "small_cluster_max_cells")
        )
        omitted_arms = [str(arm) for arm in product.get("omitted_arms")]
        deviation = product.get("deviation")
        approved_deviation = str(deviation) if (deviation and omitted_arms) else None

        results: list[PerturbationResult] = []
        for kind_value in sorted({str(row["kind"]) for row in rows}):
            try:
                kind = PerturbationKind(kind_value)
            except ValueError:
                raise ContractDrift(
                    f"stability product {product.asset_id} records perturbation kind {kind_value!r}, which "
                    "is not a section 7.5 class"
                ) from None
            arms = [row for row in rows if str(row["kind"]) == kind_value]
            judgement = _kind_judgement(arms, criteria)
            vanished = sum(1 for row in arms if int(row["cells_compared"]) == 0)
            a.add(
                kind=EvidenceKind.STABILITY,
                role=EvidenceRole.SUPPORTING if judgement == "pass" else EvidenceRole.CONTRADICTING,
                slug=f"perturbation/{kind_value}",
                product=product,
                content={
                    "protocol_id": str(product.get("protocol_id")),
                    "protocol_version": str(product.get("protocol_version")),
                    "thresholds_version": str(product.get("thresholds_version")),
                    "candidate": a.candidate.display_name,
                    "kind": kind_value,
                    "judgement": judgement,
                    "replicates": len(arms),
                    "arms": sorted(str(row["arm"]) for row in arms),
                    "vanished_arms": vanished,
                    "criteria": {key: round(value, PRECISION) for key, value in sorted(criteria.items())},
                    "cc_ari_median": _median(row.get("cc_ari") for row in arms),
                    "jaccard_median": _median(row.get("jaccard") for row in arms),
                    "permutation_jaccard_median": _median(row.get("permutation_jaccard") for row in arms),
                    "marker_overlap_median": _median(row.get("marker_overlap") for row in arms),
                    "baseline_cells": int(arms[0]["baseline_cells"]),
                    "omitted_arms": omitted_arms,
                    "deviation": str(deviation) if deviation else None,
                },
                used_cells=a.cells,
            )
            results.append(
                PerturbationResult(
                    kind=kind,
                    cc_ari=_median(row.get("cc_ari") for row in arms),
                    jaccard=_median(row.get("jaccard") for row in arms),
                    replicates=len(arms),
                    detail={"judgement": judgement, "vanished_arms": vanished},
                )
            )
        #: The medians below are this reader's derivation of the stored arm rows, so the summary's numbers
        #: cite the robustness asset rather than arriving with no provenance at all (§7.3).
        a.citations.register(
            "stability_summary",
            _product_citation(product, "the agreement each perturbation class showed against its baseline"),
        )
        return StabilitySummary(
            protocol_version=str(product.get("protocol_version")),
            results=results,
            approved_deviation=approved_deviation,
        )

    def _artifact_screening(self, a: _Assembly, stability: Optional[StabilitySummary]) -> Optional[ArtifactScreening]:
        """Section 7.7: one item per named risk, with the tool that ran it or the reason it did not run."""
        product = a.reader.optional(SLOT_QC_FLAGS)
        if product is None:
            a.notes.append("no QC product is stored, so no artefact risk was screened (§7.7)")
            return None
        node_id, tool_name, tool_version = a.reader.tool_of(product.asset_id)
        flags_by_cell = {
            str(row["cell_key"]): {str(flag) for flag in row["flags"]} for row in product.rows("cells")
        }
        rule_names_by_metric: dict[str, list[str]] = {}
        for rule in product.rows("rules"):
            rule_names_by_metric.setdefault(str(rule["metric"]), []).append(str(rule["name"]))

        scored = [cell for cell in a.cells if cell in flags_by_cell]
        if not scored:
            a.notes.append(
                f"the QC product {product.asset_id} scored none of this candidate's cells, so no risk was "
                "screened for it (§7.7)"
            )
            return None

        results = {str(result.kind): result for result in stability.results} if stability else {}
        stability_product = a.reader.optional(SLOT_STABILITY)
        items: dict[ArtifactRisk, ScreeningItem] = {}
        for risk, metrics in RISK_METRICS.items():
            names = sorted({name for metric in metrics for name in rule_names_by_metric.get(metric.value, [])})
            if not names:
                items[risk] = self._proxy_or_unscreened(a, risk, results, stability_product)
                continue
            flagged = sum(1 for cell in scored if flags_by_cell[cell] & set(names))
            share = _share(flagged, len(scored))
            if flagged == 0:
                result = ScreeningResult.PASS
            elif share >= a.screening_rule.blocking_share:
                result = ScreeningResult.FAIL
            else:
                result = ScreeningResult.WARN
            detail = (
                f"{flagged} of {len(scored)} candidate cells carry a QC label from rules {names} "
                f"(share {share:.4f}, blocking at {a.screening_rule.blocking_share}); policy "
                f"{product.get('policy_id')}@{product.get('policy_version')}, action {product.get('actions')}"
            )
            items[risk] = ScreeningItem(
                result=result,
                tool=tool_name,
                tool_version=tool_version,
                threshold_source=_rule_threshold_source(product, names),
                detail=detail,
            )
            role = SCREEN_ROLE[result]
            if role is None:
                continue
            a.add(
                kind=RISK_EVIDENCE_KIND[risk],
                role=role,
                slug=f"screen/{risk.value}",
                product=product,
                content={
                    "risk": risk.value,
                    "rules": names,
                    "flagged_cells": flagged,
                    "scored_cells": len(scored),
                    "candidate_cells": len(a.cells),
                    "flagged_share": round(share, PRECISION),
                    "unscored_cells": len(a.cells) - len(scored),
                    "blocking_share": round(a.screening_rule.blocking_share, PRECISION),
                    "screening_rule_version": a.screening_rule.version,
                    "policy_id": str(product.get("policy_id")),
                    "policy_version": str(product.get("policy_version")),
                    "qc_node": node_id,
                    "thresholds_version": str(product.get("thresholds_version")),
                },
                used_cells=a.cells,
            )
        #: Section 7.7 asks for one item per named risk, so a risk the plan never installed a screen for is
        #: written as NOT_RUN with the reason: an absent tool is a finding, not a clean bill of health.
        missing = sorted((risk.value for risk in RISK_EVIDENCE_KIND if risk not in items), key=str)
        if missing:
            raise ContractDrift(f"the screening table answered no item for {missing}")
        return ArtifactScreening(
            doublet=items[ArtifactRisk.DOUBLET],
            ambient=items[ArtifactRisk.AMBIENT],
            low_quality=items[ArtifactRisk.LOW_QUALITY],
            batch_specific=items[ArtifactRisk.BATCH_SPECIFIC],
            stress_cycle=items[ArtifactRisk.STRESS_CYCLE],
            over_split=items[ArtifactRisk.OVER_SPLIT],
        )

    def _proxy_or_unscreened(
        self,
        a: _Assembly,
        risk: ArtifactRisk,
        results: Mapping[str, PerturbationResult],
        stability_product: Optional[StoredProduct],
    ) -> ScreeningItem:
        """Section 7.7's unscreened risks: read from a perturbation arm as a proxy, or recorded as not run.

        Two of the risks are properties of the partition rather than of a cell, so no QC metric measures
        them: batch-specificity is what a donor-dropout arm shows, and over-splitting is what a resolution
        arm shows. The proxy is labelled as a proxy in its own detail line, because a reviewer who reads
        "batch_specific: pass" needs to know it came from leave-one-donor and not from a batch screen.
        """
        kind = RISK_PERTURBATION.get(risk)
        if kind is None:
            return ScreeningItem(
                result=ScreeningResult.NOT_RUN,
                detail=(
                    f"no {risk.value} screen is installed for this protocol (§7.7): the dissociation and "
                    "collection records describe what was done to the cells, which is not a measurement of "
                    "what it did to them, so the risk is left unscreened rather than assumed clear"
                ),
            )
        if stability_product is None:
            return ScreeningItem(
                result=ScreeningResult.NOT_RUN,
                detail=(
                    f"{risk.value} has no {kind.value} perturbation arm to read from, because this run "
                    "stored no robustness product (§7.7)"
                ),
            )
        if not results:
            #: A run can hold a robustness product that measured a different candidate. The reason a
            #: reviewer reads has to be the one the numbers give: nothing was measured for this set.
            return ScreeningItem(
                result=ScreeningResult.NOT_RUN,
                detail=(
                    f"the robustness product {stability_product.asset_id} measured no {kind.value} arm for "
                    f"{a.candidate.display_name}, so {risk.value} is not screened even indirectly (§7.7)"
                ),
            )
        result = results.get(kind.value)
        if result is None:
            return ScreeningItem(
                result=ScreeningResult.NOT_RUN,
                detail=(
                    f"the robustness product {stability_product.asset_id} ran no {kind.value} arm, so "
                    f"{risk.value} is not screened even indirectly (§7.7)"
                ),
            )
        judgement = str(result.detail.get("judgement", "unmeasurable"))
        screened = {
            "pass": ScreeningResult.PASS,
            "fail": ScreeningResult.FAIL,
        }.get(judgement, ScreeningResult.NOT_RUN)
        node_id, tool, tool_version = a.reader.tool_of(stability_product.asset_id)
        item = ScreeningItem(
            result=screened,
            tool=tool,
            tool_version=tool_version,
            threshold_source=(
                f"robustness protocol {stability_product.get('protocol_id')}@"
                f"{stability_product.get('protocol_version')}, criteria recorded under thresholds "
                f"{stability_product.get('thresholds_version')} (§7.5)"
            ),
            detail=(
                f"proxy for a {risk.value} screen: the {kind.value} arm over {result.replicates} "
                f"replicates gave chance-corrected agreement {result.cc_ari} and overlap {result.jaccard}, "
                f"judged {judgement} against the recorded criteria (§7.7)"
            ),
        )
        role = SCREEN_ROLE[screened]
        if role is not None:
            a.add(
                kind=RISK_EVIDENCE_KIND[risk],
                role=role,
                slug=f"proxy/{risk.value}",
                product=stability_product,
                content={
                    "risk": risk.value,
                    "proxy_kind": kind.value,
                    "judgement": judgement,
                    "replicates": result.replicates,
                    "cc_ari": (None if result.cc_ari is None else round(float(result.cc_ari), PRECISION)),
                    "jaccard": (None if result.jaccard is None else round(float(result.jaccard), PRECISION)),
                    "vanished_arms": int(result.detail.get("vanished_arms", 0)),
                    "protocol_id": str(stability_product.get("protocol_id")),
                    "protocol_version": str(stability_product.get("protocol_version")),
                    "thresholds_version": str(stability_product.get("thresholds_version")),
                    "robustness_node": node_id,
                    "screening_rule_version": a.screening_rule.version,
                    "is_proxy": True,
                },
                used_cells=a.cells,
            )
        return item

    # ------------------------------------------------------------------ coverage / state

    def _coverage(
        self,
        a: _Assembly,
        *,
        stability: Optional[StabilitySummary],
        screening: Optional[ArtifactScreening],
        continuous: Optional[ContinuousStateCheck],
    ) -> Optional[NoveltyVsCoverage]:
        """Section 7.6: how far this candidate sits from the reference, and what the four conditions say.

        Coverage and novelty stay two separate answers. The distances come from the stored product, the
        ratio and the gap are recomputed here from the cuts the step wrote beside them, and novelty is the
        gate's verdict over coverage *and* stability *and* artefacts *and* the axis - so a card can say
        "the reference does not reach these cells" without ever saying they are a new type.
        """
        product = a.reader.optional(SLOT_COVERAGE)
        if product is None:
            a.notes.append(
                "no coverage product is stored for this run, so how far this candidate sits from the "
                "reference is unknown (§7.6)"
            )
            return None
        wanted = set(a.cells)
        rows = [row for row in product.rows("rows") if str(row["cell_key"]) in wanted]
        if not rows:
            a.notes.append(
                f"the coverage product {product.asset_id} measured no cell of {a.candidate.display_name}: "
                "the node was pointed at another candidate, so this set's distance to the reference is "
                "unknown (§7.6)"
            )
            return None
        criteria = _criteria_of(product, "criteria", ("knn", "distance_ratio_min"))
        calibration = round(float(product.get("median_calibration_distance")), PRECISION)
        median_distance = _median(row.get("distance") for row in rows)
        percentile = _median(row.get("calibration_percentile") for row in rows)
        beyond = _share(sum(1 for row in rows if float(row["calibration_percentile"]) >= 1.0), len(rows))

        if calibration <= 0.0:
            #: A calibration median of zero means the reference cells measured as each other's own distance;
            #: dividing by it would call every candidate a gap, so the ratio is left unanswerable.
            ratio, gap = None, False
            a.notes.append(
                f"the calibration set of asset {product.asset_id} has median distance zero, so no ratio "
                "against it means anything and no coverage gap is claimed (§7.6)"
            )
        else:
            ratio = round((median_distance or 0.0) / calibration, PRECISION)
            gap = ratio >= criteria["distance_ratio_min"]

        coverage = NoveltyVsCoverage(
            knn=int(criteria["knn"]),
            #: The product stores a distance distribution, not a score, so none is invented for the card.
            reference_coverage_score=None,
            distance_ratio=ratio,
            coverage_gap=gap,
            novelty_signal=False,
        )
        verdict = novelty_verdict(
            coverage,
            stability_verdict(stability, a.thresholds),
            artifact_verdict(screening),
            continuous,
        )
        note = (
            f"measured over {len(rows)} of this candidate's {len(a.cells)} cells in a {int(criteria['knn'])}"
            f"-neighbour read: median distance {median_distance} against a calibration median of "
            f"{calibration}, ratio {ratio} against a cut of {criteria['distance_ratio_min']}, and "
            f"{beyond} of the cells sit beyond every calibration cell. No coverage score is defined for "
            f"this product, so none is claimed. Novelty as section 7.6's four conditions read it: "
            f"{'all four hold' if verdict.passed else '; '.join(verdict.reasons)}"
        )
        coverage = coverage.model_copy(update={"novelty_signal": verdict.passed, "note": note})
        a.citations.register(
            "novelty_vs_coverage",
            _product_citation(product, "the candidate's distances and the calibration distribution"),
        )
        a.add(
            #: A gap is a reason to look (§7.2b); a covered set is evidence against the claim that it is
            #: nothing the reference knows.
            kind=EvidenceKind.REFERENCE_COVERAGE,
            role=EvidenceRole.TRIGGER if gap else EvidenceRole.CONTRADICTING,
            slug="reference-distance",
            product=product,
            content={
                "method": str(product.get("method")),
                "knn": int(criteria["knn"]),
                "distance_ratio_min": round(criteria["distance_ratio_min"], PRECISION),
                "thresholds_version": str(product.get("thresholds_version")),
                "embedding_digest": str(product.get("embedding_digest")),
                "reference_digest": str(product.get("reference_digest")),
                "reference_cells": int(product.get("reference_cells")),
                "calibration_cells": int(product.get("calibration_cells")),
                "cells_measured": len(rows),
                "candidate_cells": len(a.cells),
                "median_candidate_distance": median_distance,
                "median_calibration_distance": calibration,
                "median_calibration_percentile": percentile,
                "beyond_calibration_share": round(beyond, PRECISION),
                "distance_ratio": ratio,
                "coverage_gap": gap,
                "candidate_quantiles": {
                    str(key): round(float(value), PRECISION)
                    for key, value in sorted(dict(product.get("candidate_quantiles")).items())
                },
                "calibration_quantiles": {
                    str(key): round(float(value), PRECISION)
                    for key, value in sorted(dict(product.get("calibration_quantiles")).items())
                },
                "nearest_references": sorted({str(row["nearest_reference"]) for row in rows})[:MAX_CARD_GENES],
                "novelty_reasons": list(verdict.reasons),
            },
            used_cells=[str(row["cell_key"]) for row in rows],
        )
        return coverage

    def _continuous_state(self, a: _Assembly) -> Optional[ContinuousStateCheck]:
        """Section 7.6's other question: is this set only the end of a gradient the data already has?

        The cosine the step reports is a property of the population it was handed. When that population is
        not exactly this candidate, the answer belongs to the pooled set, so the card records the
        candidate's own tail share as a trigger and leaves the check unanswered rather than borrowing a
        number measured over somebody else's cells.
        """
        product = a.reader.optional(SLOT_STATE_AXIS)
        if product is None:
            a.notes.append(
                "no continuous-state product is stored, so the axis question was never asked for this run (§7.6)"
            )
            return None
        measured = product.rows("rows")
        wanted = set(a.cells)
        rows = [row for row in measured if str(row["cell_key"]) in wanted]
        if not rows:
            a.notes.append(
                f"the continuous-state product {product.asset_id} measured no cell of "
                f"{a.candidate.display_name}, so its position on the declared axis is unknown (§7.6)"
            )
            return None
        criteria = _criteria_of(product, "criteria", ("tail_quantile", "tail_share_min", "axis_explained_min"))
        tail = _share(sum(1 for row in rows if bool(row["beyond_reference_tail"])), len(rows))
        explained = round(float(product.get("axis_explained")), PRECISION)
        attributable = len(rows) == len(measured)
        is_tail = (
            tail >= criteria["tail_share_min"] and explained >= criteria["axis_explained_min"]
            if attributable
            else None
        )
        if attributable:
            role = EvidenceRole.CONTRADICTING if is_tail else EvidenceRole.SUPPORTING
        else:
            role = EvidenceRole.TRIGGER
            a.notes.append(
                f"the axis of asset {product.asset_id} was drawn over {len(measured)} cells of which "
                f"{len(rows)} are this candidate's, so the shared deviation is not attributable to it (§7.6)"
            )
        a.add(
            kind=EvidenceKind.CONTINUOUS_STATE,
            role=role,
            slug="axis-projection",
            product=product,
            content={
                "method": str(product.get("method")),
                "axis": {str(gene): round(float(weight), PRECISION) for gene, weight in sorted(dict(product.get("axis")).items())},
                "tail_quantile": round(criteria["tail_quantile"], PRECISION),
                "tail_share_min": round(criteria["tail_share_min"], PRECISION),
                "axis_explained_min": round(criteria["axis_explained_min"], PRECISION),
                "cells_measured": len(rows),
                "candidate_cells": len(a.cells),
                "measured_cells": len(measured),
                "attributable_to_candidate": attributable,
                "tail_share": round(tail, PRECISION),
                "axis_explained": explained,
                "reference_median": round(float(product.get("reference_median")), PRECISION),
                "reference_stdev": round(float(product.get("reference_stdev")), PRECISION),
                "max_abs_z": round(float(product.get("max_abs_z")), PRECISION),
                "reference_cells": int(product.get("reference_cells")),
                "is_axis_tail": is_tail,
                "expression_digest": str(product.get("expression_digest")),
                "cluster_digest": str(product.get("cluster_digest")),
            },
            used_cells=[str(row["cell_key"]) for row in rows],
        )
        if not attributable:
            return None
        a.citations.register(
            "identity_vs_state",
            _product_citation(product, "the share of cells past the reference's own tail on the declared axis"),
        )
        return ContinuousStateCheck(method=str(product.get("method")), is_axis_tail=is_tail, statistic=tail)

    # ------------------------------------------------------------------ card and gate

    def _card(
        self,
        a: _Assembly,
        *,
        stability: Optional[StabilitySummary],
        screening: Optional[ArtifactScreening],
        coverage: Optional[NoveltyVsCoverage],
        continuous: Optional[ContinuousStateCheck],
    ) -> EvidenceCard:
        supporting = sorted(eid for eid, record in a.evidence.items() if record.role is EvidenceRole.SUPPORTING)
        contradicting = sorted(
            eid for eid, record in a.evidence.items() if record.role is EvidenceRole.CONTRADICTING
        )
        triggers = sorted(eid for eid, record in a.evidence.items() if record.role is EvidenceRole.TRIGGER)
        #: Section 7.4: confirmation is support measured where the model of this candidate was not built.
        confirmation = sorted(
            eid for eid in supporting if a.evidence[eid].partition is EvidencePartition.CONFIRMATION
        )
        backed = {a.evidence[eid].kind for eid in supporting}
        opposed = {a.evidence[eid].kind for eid in contradicting}
        unresolved = bool(backed & opposed)

        artifacts = artifact_verdict(screening)
        if not artifacts.passed:
            reason = UnknownReason.QUALITY_LIMITED
        elif unresolved:
            reason = UnknownReason.UNRESOLVED_CONFLICT
        elif coverage is None or coverage.coverage_gap:
            reason = UnknownReason.REFERENCE_NOT_COVERED
        else:
            reason = UnknownReason.INSUFFICIENT_EVIDENCE

        card = EvidenceCard(
            card_id=card_id_for(a.candidate.candidate_id),
            candidate_id=a.candidate.candidate_id,
            run_id=a.reader.run_id,
            display_name=a.candidate.display_name,
            candidate_types=list(a.candidate.candidate_types),
            #: Section 9.1: the drafting agent holds no write permission, so what it files is a hypothesis
            #: of unknown type with the numbers beside it. The label is a person's act.
            hypothesis_type=HypothesisType.UNKNOWN,
            unknown_reason=reason,
            candidate_label=None,
            label_level=LabelLevel.UNKNOWN_COARSE,
            identity_vs_state=IdentityVsState(continuous_state_check=continuous),
            evidence=dict(a.evidence),
            supporting_evidence_ids=supporting,
            confirmation_evidence_ids=confirmation,
            contradicting_evidence_ids=contradicting,
            trigger_signal_ids=triggers,
            unresolved_conflict=unresolved,
            artifact_screening=screening,
            novelty_vs_coverage=coverage,
            stability_summary=stability,
            #: No composition test is part of this workflow, so the card reports that none was run rather
            #: than leaving the field blank: a C5 claim then reads as unearned, which is the truth (§7.2).
            composition_check=CompositionCheck(
                method=None,
                result="not run: the plan wires no composition test for this modality",
            ),
            cross_modal_status=CrossModalStatus.NOT_YET_VALIDATED,
            support_rule_version=a.support_rule.version,
            thresholds_version=a.thresholds.version,
            status_reason=(
                f"filed by the evidence service as a draft from {len(a.evidence)} records read off the "
                f"products of run {a.reader.run_id}; {len(supporting)} support, {len(contradicting)} "
                f"contradict and {len(triggers)} only trigger a look (§7.2b). Artefacts: "
                f"{'excluded' if artifacts.passed else '; '.join(artifacts.reasons)}. Reference coverage: "
                f"{'no measurement' if coverage is None else ('gap at ratio ' + str(coverage.distance_ratio) if coverage.coverage_gap else 'covered')}. "
                f"Continuous state: "
                f"{'not attributable to this candidate' if continuous is None else ('axis tail' if continuous.is_axis_tail else 'not an axis tail')}. "
                "A label, an ontology term and a final status are recorded by a reviewer, never here."
            ),
            final_status=FinalStatus.UNKNOWN,
            sign_off=SignOff.NOT_SIGNED,
        )
        return card.model_copy(update={"field_provenance": a.citations.for_card(card)})

    def _gate(self, card: EvidenceCard, a: _Assembly) -> CardDraft:
        """Read the card against the plan's gates, and hand the reviewer what the gates said.

        The ceiling is returned, never stored: a stored ceiling is a conclusion that outlives the numbers
        it was read from, and a card rebuilt from the same bytes must be able to say something different
        if the thresholds it cites were themselves revised.
        """
        support_met = None if not a.marker_support_attempted else a.marker_support_met
        try:
            verdict = enforce_card_status(
                card,
                thresholds=a.thresholds,
                support_met=support_met,
                underpowered_design=a.underpowered,
            )
        except GateViolation as error:
            raise ContractDrift(
                f"the draft card for {a.candidate.candidate_id} fails the gates its own numbers set: {error}",
                field="field_provenance",
            ) from None
        return CardDraft(
            card=card,
            ceiling=verdict.detail["ceiling"],
            gate_reasons=tuple(verdict.reasons),
            notes=tuple(dict.fromkeys(a.notes)),
            support_met=support_met,
            underpowered_design=a.underpowered,
        )


class EvidenceCardService:
    """Files draft cards for one run's candidate sets (plan V2.0 sections 5.4, 7.2-7.8, 9.1).

    The service reads a run's stored products, derives what they mean, and writes an ``EvidenceCard`` whose
    hypothesis type is unknown. Nothing it writes can be read as a decision: the label, the ontology term
    and the final status are set through the review routes, and ``persist`` refuses to touch a card a
    person has already started to act on.
    """

    def __init__(
        self,
        *,
        catalog: Catalog,
        objects: ObjectStore,
        run_id: str,
        plan: Optional[AnalysisPlan] = None,
        thresholds: Thresholds = DEFAULT_THRESHOLDS,
        support_rule: Optional[SupportRule] = None,
        screening_rule: Optional[ScreeningRule] = None,
        extra_asset_ids: Sequence[str] = (),
    ) -> None:
        manifest = catalog.get_run(run_id)
        self.catalog = catalog
        self.run_id = run_id
        self.reader = AssetReader(catalog, objects, run_id=run_id, extra_asset_ids=extra_asset_ids)
        self.plan = plan or catalog.get_plan(manifest.plan_id, manifest.plan_version)
        if self.plan.project_id != manifest.project_id:
            raise ContractDrift(
                f"plan {self.plan.plan_id}@{self.plan.plan_version} belongs to project "
                f"{self.plan.project_id}, not to run {run_id}'s project {manifest.project_id}"
            )
        mismatch = self.thresholds_version_mismatch(thresholds)
        if mismatch is not None:
            raise ContractDrift(
                f"the run's products cite threshold set {mismatch!r} but this service was handed "
                f"{thresholds.version!r}; a card may only judge numbers against the cuts in force when "
                "they were measured (§7.3)"
            )
        self.thresholds = thresholds
        self.builder = CandidateCardBuilder(
            reader=self.reader,
            plan=self.plan,
            thresholds=thresholds,
            support_rule=support_rule,
            screening_rule=screening_rule,
        )

    def thresholds_version_mismatch(self, thresholds: Thresholds) -> Optional[str]:
        """The threshold set the stored products name, when it is not the one this service reads against.

        Every product that applies a cut records which version supplied it, so a card built against a
        revised set is refused before it can be filed: the alternative is a card whose verdict quietly
        changes because somebody edited a number the measurement never saw (§7.3).
        """
        for product in self.reader:
            named = product.document.get("thresholds_version")
            if named is not None and str(named) != thresholds.version:
                return str(named)
        return None

    # ------------------------------------------------------------------ drafting

    def draft(self, candidate: CandidateSet) -> CardDraft:
        return self.builder.draft(candidate)

    def drafts(self, *, candidates: Optional[Iterable[CandidateSet]] = None, limit: int = 100) -> list[CardDraft]:
        """Draft every candidate set of this run, in a stable order.

        Candidates come from the index unless a caller names them: the discovery step is what makes a
        candidate set a record, and a card for a set nobody registered has nothing to be reviewed against.
        The order is by id so two runs of this function hand the same list to the queue.
        """
        found = (
            list(candidates)
            if candidates is not None
            else self.catalog.list_candidates(run_id=self.run_id, limit=limit)
        )
        return [self.builder.draft(candidate) for candidate in sorted(found, key=lambda item: item.candidate_id)]

    def persist(self, draft: CardDraft, *, annotation_version_id: Optional[str] = None) -> CardDraft:
        """File a draft, or leave the card a person is already working on alone.

        Section 9.1 gives the drafting path no permission to change a decision once one has been made, so
        a card that carries a label, a reviewer, a sign-off or a status beyond ``unknown`` is not rewritten
        by a rebuild - the rebuild has to go through the review route that produced the change instead.
        """
        existing = self.catalog.card_final_status(draft.card_id)
        if existing is None:
            self.catalog.record_card(draft.card, annotation_version_id=annotation_version_id)
            return draft
        current = self.catalog.get_card(draft.card_id)
        if _decided_by_a_person(current):
            raise CardUnderReview(
                f"card {draft.card_id} of candidate {draft.candidate_id} is filed as {current.final_status} "
                f"with hypothesis {current.hypothesis_type}"
                + (f" by reviewer {current.reviewer_id}" if current.reviewer_id else "")
                + "; a rebuild cannot overwrite a card somebody has acted on (§9.1)",
                field="final_status",
            )
        if current.model_dump(mode="python") == draft.card.model_dump(mode="python"):
            #: Same bytes, same card: filing it again is a no-op, not a second row to review.
            return draft
        self.catalog.update_card(draft.card, annotation_version_id=annotation_version_id)
        return draft


def _decided_by_a_person(card: EvidenceCard) -> bool:
    """Anything on the card that only a review action can have put there."""
    return bool(
        card.reviewer_id
        or card.reviewed_at
        or card.review_reason
        or card.candidate_label
        or card.ontology.term_id
        or card.sign_off is not SignOff.NOT_SIGNED
        or card.hypothesis_type is not HypothesisType.UNKNOWN
        or card.final_status is not FinalStatus.UNKNOWN
    )

