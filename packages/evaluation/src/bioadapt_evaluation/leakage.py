"""Leakage isolation and the two retrieval rates (plan V2.0 sections 6.8, 10.5, 10.6).

An evaluation set is only worth its number if the platform could not have learned the answer beforehand.
Section 6.8 therefore marks a benchmark dataset *and everything derived from it*, and an evaluation run
masks those sources automatically rather than asking whoever wrote the run to remember them. The mask is a
list of object ids, so the run has to produce an isolation attestation - a reviewer can then see which
sources were withheld instead of taking the score on trust.

The two rates at the end are deliberately not combined. A citation that can be located is not a citation
that supports the claim it is attached to, and section 10.6 names conflating the two as an unacceptable
value, so each gets its own numerator and its own denominator.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Iterable, Optional, Sequence

from pydantic import Field

from bioadapt_schemas.entities import Contract, ImmutableContract, sha256_of_json

from .truth import RetrievalQuery, TruthFloors


class SourceKind(StrEnum):
    DATASET = "dataset"
    REFERENCE_ATLAS = "reference_atlas"
    KNOWLEDGE_CLAIM = "knowledge_claim"
    CASE_LIBRARY_ENTRY = "case_library_entry"
    RETRIEVED_FRAGMENT = "retrieved_fragment"


class BenchmarkSource(ImmutableContract):
    """One object marked ``used_in_benchmark``, and everything it was derived from."""

    object_id: str = Field(min_length=1)
    kind: SourceKind
    #: Section 6.8: the mark propagates to derived knowledge, so a claim summarised from a benchmark dataset
    #: is masked with it rather than surviving as a clean source.
    derived_from: tuple[str, ...] = ()
    benchmark_run_id: str = Field(min_length=1)
    marked_on: date
    note: Optional[str] = None


class CandidateSource(ImmutableContract):
    """A source one claim or one metric actually used, as the run records it."""

    object_id: str = Field(min_length=1)
    kind: SourceKind
    derived_from: tuple[str, ...] = ()
    used_for: str = Field(min_length=1)


class IsolationAttestation(Contract):
    """What an evaluation run withheld, and what it is therefore allowed to claim."""

    benchmark_run_id: str
    masked: list[str] = Field(default_factory=list)
    kept: list[str] = Field(default_factory=list)
    orphaned: list[str] = Field(default_factory=list)
    #: A claim whose every source was masked cannot be scored; the plan's action is to record the isolation,
    #: not to keep the score and hope nobody asks.
    unscorable_uses: list[str] = Field(default_factory=list)
    policy_version: str = "isolation_v1"

    def attestation_id(self) -> str:
        return sha256_of_json(
            {
                "benchmark_run_id": self.benchmark_run_id,
                "masked": sorted(self.masked),
                "kept": sorted(self.kept),
                "orphaned": sorted(self.orphaned),
                "unscorable_uses": sorted(self.unscorable_uses),
                "policy_version": self.policy_version,
            }
        )

    @property
    def clean(self) -> bool:
        return not self.unscorable_uses


def isolate(
    benchmark_run_id: str,
    candidates: Sequence[CandidateSource],
    *,
    marked: Iterable[BenchmarkSource],
    today: date,
) -> IsolationAttestation:
    """Mask every candidate a benchmark mark reaches, including the marks it was derived from.

    A mark that names a run other than this one is recorded as orphaned rather than ignored: it means the
    registry says a source is benchmark material and the run used it anyway, which is the leak the whole
    module exists to catch.
    """
    marks = list(marked)
    by_id = {one.object_id: one for one in marks}
    for one in marks:
        if one.marked_on > today:
            raise ValueError(f"mark on {one.object_id} is dated {one.marked_on}, after this run's date")
    masked: list[str] = []
    kept: list[str] = []
    orphaned: list[str] = []
    by_use: dict[str, list[bool]] = {}
    for candidate in candidates:
        direct = by_id.get(candidate.object_id)
        derived_hit = next(
            (by_id[ancestor] for ancestor in candidate.derived_from if ancestor in by_id), None
        )
        hit = direct or derived_hit
        if hit is None:
            kept.append(candidate.object_id)
            by_use.setdefault(candidate.used_for, []).append(False)
            continue
        if hit.benchmark_run_id != benchmark_run_id:
            orphaned.append(candidate.object_id)
        masked.append(candidate.object_id)
        by_use.setdefault(candidate.used_for, []).append(True)
    #: Only a use whose every source was masked loses its score; one withheld citation beside a clean one
    #: is exactly the isolation this function is for.
    unscorable = sorted(use for use, hits in by_use.items() if all(hits))
    return IsolationAttestation(
        benchmark_run_id=benchmark_run_id,
        masked=sorted(set(masked)),
        kept=sorted(set(kept)),
        orphaned=sorted(set(orphaned)),
        unscorable_uses=sorted(unscorable),
    )


class RetrievalJudgement(ImmutableContract):
    """One (query, evidence) pair as the run retrieved it and as a person or NLI judge rated it."""

    query_id: str
    evidence_ref: str
    retrieved: bool
    #: ``None`` means nobody judged it, which keeps it out of the support denominator and into the shortfall.
    supports: Optional[bool] = None
    judged_by: Optional[str] = None


class RetrievalRates(Contract):
    """Section 10.6's two rates, each with the denominator it was measured over."""

    queries: int
    expected_pairs: int
    locatable_pairs: int
    locatable_rate: Optional[float]
    judged_citations: int
    supporting_citations: int
    support_validity_rate: Optional[float]
    unjudged_citations: int
    reasons: list[str] = Field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "queries": self.queries,
            "expected_pairs": self.expected_pairs,
            "locatable_pairs": self.locatable_pairs,
            "locatable_rate": self.locatable_rate,
            "judged_citations": self.judged_citations,
            "supporting_citations": self.supporting_citations,
            "support_validity_rate": self.support_validity_rate,
            "unjudged_citations": self.unjudged_citations,
            "reasons": list(self.reasons),
        }


def retrieval_rates(
    queries: Sequence[RetrievalQuery],
    judgements: Sequence[RetrievalJudgement],
    *,
    floors: Optional[TruthFloors] = None,
) -> RetrievalRates:
    """Report locatability and support separately, and refuse to invent either denominator."""
    floors = floors or TruthFloors()
    reasons: list[str] = []
    by_query: dict[str, list[RetrievalJudgement]] = {}
    for one in judgements:
        by_query.setdefault(one.query_id, []).append(one)
    known = {one.query_id for one in queries}
    extra = sorted(set(by_query) - known)
    if extra:
        reasons.append(f"judgements name queries this set does not hold: {extra}")
    expected = 0
    located = 0
    for query in queries:
        if not query.expects_no_support:
            expected += len(query.expected_evidence_refs)
            found = {
                one.evidence_ref
                for one in by_query.get(query.query_id, [])
                if one.retrieved and one.evidence_ref in query.expected_evidence_refs
            }
            located += len(found)
    retrieved = [one for one in judgements if one.retrieved]
    judged = [one for one in retrieved if one.supports is not None]
    unjudged = len(retrieved) - len(judged)
    if unjudged:
        reasons.append(
            f"{unjudged} retrieved citation(s) were never judged, so they are absent from the support "
            "denominator rather than counted against it (§10.6)"
        )
    if len(queries) < floors.min_retrieval_pairs:
        reasons.append(
            f"{len(queries)} queries against the floor of {floors.min_retrieval_pairs}: this is a trend, "
            "not a gate (§10.5)"
        )
    if expected == 0:
        reasons.append("no query in this set names expected evidence, so locatability has no denominator")
    if not judged:
        reasons.append("no citation was judged, so support validity has no denominator")
    return RetrievalRates(
        queries=len(queries),
        expected_pairs=expected,
        locatable_pairs=located,
        locatable_rate=None if expected == 0 else round(located / expected, 6),
        judged_citations=len(judged),
        supporting_citations=sum(1 for one in judged if one.supports),
        support_validity_rate=None if not judged else round(sum(1 for one in judged if one.supports) / len(judged), 6),
        unjudged_citations=unjudged,
        reasons=reasons,
    )
