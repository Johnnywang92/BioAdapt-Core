"""Benchmark isolation and the two retrieval rates (plan V2.0 sections 6.8, 10.5, 10.6).

Section 6.8 marks an evaluation dataset *and* the knowledge derived from it, so the run cannot reach the answer
through a summary of itself. What it produces is a list of withheld object ids, which means an evaluation run
has to file an attestation - otherwise the score is a number nobody can check. The two rates at the end are
separate because the plan names conflating them as an unacceptable value: a citation that can be found is not
a citation that supports the claim sitting next to it.
"""

from __future__ import annotations

from datetime import date

import pytest

import evaluation_factories as E
from bioadapt_evaluation.leakage import SourceKind, isolate, retrieval_rates
from bioadapt_evaluation.truth import TruthFloors


def run_isolate(candidates, marked, **overrides) -> object:
    fields = {"marked": marked, "today": E.TODAY}
    fields.update(overrides)
    return isolate(E.BENCHMARK_RUN, candidates, **fields)


class TestMasking:
    def test_a_direct_mark_withholds_the_source(self) -> None:
        attestation = run_isolate(
            [E.used("ds_pbmc_01")],
            [E.marked("ds_pbmc_01")],
        )
        assert attestation.masked == ["ds_pbmc_01"]
        assert attestation.kept == []

    def test_a_mark_on_the_parent_withholds_what_was_derived_from_it(self) -> None:
        """A claim summarised from a benchmark dataset travels with it, which is the whole point of §6.8."""
        attestation = run_isolate(
            [E.used("kb_claim_07", derived_from=("ds_pbmc_01",))],
            [E.marked("ds_pbmc_01")],
        )
        assert attestation.masked == ["kb_claim_07"]

    def test_an_unmarked_source_is_kept_and_named(self) -> None:
        attestation = run_isolate([E.used("kb_claim_07")], [E.marked("ds_pbmc_01")])
        assert attestation.kept == ["kb_claim_07"]
        assert attestation.masked == []

    def test_a_mark_belonging_to_another_run_is_orphaned_not_ignored(self) -> None:
        """The registry says this is benchmark material and the run used it anyway: that is the leak."""
        attestation = run_isolate(
            [E.used("ds_other_01")],
            [E.marked("ds_other_01", benchmark_run_id="eval_0002")],
        )
        assert attestation.masked == ["ds_other_01"]
        assert attestation.orphaned == ["ds_other_01"]

    def test_a_mark_dated_after_the_run_is_refused(self) -> None:
        with pytest.raises(ValueError, match="after this run"):
            run_isolate(
                [E.used("ds_pbmc_01")],
                [E.marked("ds_pbmc_01", marked_on=date(2026, 6, 1))],
            )

    def test_one_withheld_citation_does_not_lose_the_whole_use(self) -> None:
        attestation = run_isolate(
            [E.used("ds_pbmc_01", "claim_01"), E.used("kb_clean_02", "claim_01")],
            [E.marked("ds_pbmc_01")],
        )
        assert attestation.unscorable_uses == []
        assert attestation.clean

    def test_a_use_whose_every_source_was_masked_loses_its_score(self) -> None:
        attestation = run_isolate(
            [E.used("ds_pbmc_01", "claim_01"), E.used("ds_pbmc_02", "claim_01")],
            [E.marked("ds_pbmc_01"), E.marked("ds_pbmc_02")],
        )
        assert attestation.unscorable_uses == ["claim_01"]
        assert not attestation.clean

    def test_the_attestation_id_names_content_not_order(self) -> None:
        marks = [E.marked("ds_pbmc_01"), E.marked("ds_pbmc_02")]
        candidates = [E.used("ds_pbmc_01"), E.used("ds_pbmc_02")]
        first = run_isolate(candidates, marks)
        second = run_isolate(list(reversed(candidates)), list(reversed(marks)))
        assert first.attestation_id() == second.attestation_id()


class TestTheTwoRates:
    def test_locatability_and_support_have_different_denominators(self) -> None:
        rates = retrieval_rates(
            [E.retrieval_query()],
            [
                E.judgement(evidence_ref="ev_marker_01", supports=True),
                E.judgement(evidence_ref="ev_de_02", supports=False),
                E.judgement(evidence_ref="ev_unrelated", supports=True),
            ],
            floors=TruthFloors(min_retrieval_pairs=1),
        )
        assert rates.expected_pairs == 2
        assert rates.locatable_pairs == 2
        assert rates.locatable_rate == 1.0
        assert rates.judged_citations == 3
        assert rates.supporting_citations == 2
        assert rates.support_validity_rate == round(2 / 3, 6)

    def test_an_unjudged_citation_is_absent_from_the_denominator_and_reported(self) -> None:
        rates = retrieval_rates(
            [E.retrieval_query()],
            [E.judgement(supports=None, judged_by=None)],
            floors=TruthFloors(min_retrieval_pairs=1),
        )
        assert rates.judged_citations == 0
        assert rates.unjudged_citations == 1
        assert rates.support_validity_rate is None
        assert any("never judged" in reason for reason in rates.reasons)

    def test_a_query_that_expects_an_abstention_costs_no_locatability(self) -> None:
        rates = retrieval_rates(
            [E.retrieval_query(expected_evidence_refs=["none"], expects_no_support=True)],
            [],
            floors=TruthFloors(min_retrieval_pairs=1),
        )
        assert rates.expected_pairs == 0
        assert rates.locatable_rate is None
        assert any("no denominator" in reason for reason in rates.reasons)

    def test_a_missing_expected_answer_lowers_locatability_without_touching_support(self) -> None:
        rates = retrieval_rates(
            [E.retrieval_query()],
            [E.judgement(evidence_ref="ev_marker_01")],
            floors=TruthFloors(min_retrieval_pairs=1),
        )
        assert rates.locatable_rate == 0.5
        assert rates.support_validity_rate == 1.0

    def test_a_retrieved_answer_the_set_never_expected_is_not_locatable(self) -> None:
        rates = retrieval_rates(
            [E.retrieval_query()],
            [E.judgement(evidence_ref="ev_wrong", supports=True)],
            floors=TruthFloors(min_retrieval_pairs=1),
        )
        assert rates.locatable_pairs == 0
        assert rates.support_validity_rate == 1.0

    def test_a_judgement_for_an_unknown_query_is_reported_not_silently_counted(self) -> None:
        rates = retrieval_rates(
            [E.retrieval_query()],
            [E.judgement(query_id="q_99")],
            floors=TruthFloors(min_retrieval_pairs=1),
        )
        assert any("queries this set does not hold" in reason for reason in rates.reasons)

    def test_below_the_retrieval_floor_this_is_a_trend(self) -> None:
        rates = retrieval_rates([E.retrieval_query()], [E.judgement()])
        assert any("this is a trend, not a gate" in reason for reason in rates.reasons)

    def test_the_plan_s_retrieval_floor_is_fifty_pairs(self) -> None:
        assert TruthFloors().min_retrieval_pairs == 50

    def test_the_kind_of_a_source_is_recorded_for_the_reviewer(self) -> None:
        attestation = run_isolate(
            [E.used("kb_claim_07", kind=SourceKind.KNOWLEDGE_CLAIM)],
            [],
        )
        assert attestation.kept == ["kb_claim_07"]
