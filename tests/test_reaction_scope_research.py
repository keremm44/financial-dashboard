from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.decision_audit.reaction_scope_research import _diagnostic


def test_raw_confirmation_filtered_out_is_not_aggregation_failure():
    assert (
        _diagnostic(
            raw_fvg_confirmed=3,
            relevant_fvg_confirmed=0,
            raw_ob_confirmed=1,
            relevant_ob_confirmed=0,
            reaction_state="UNKNOWN",
            reaction_confirmed=False,
        )
        == "RAW_CONFIRMATION_FILTERED_OUT_BY_REACTION_RELEVANCE"
    )


def test_relevant_confirmation_missing_from_aggregate_is_real_aggregation_failure():
    assert (
        _diagnostic(
            raw_fvg_confirmed=2,
            relevant_fvg_confirmed=1,
            raw_ob_confirmed=0,
            relevant_ob_confirmed=0,
            reaction_state="UNKNOWN",
            reaction_confirmed=False,
        )
        == "RELEVANT_CONFIRMATION_NOT_AGGREGATED"
    )


def test_relevant_confirmation_aggregated_is_clean():
    assert (
        _diagnostic(
            raw_fvg_confirmed=2,
            relevant_fvg_confirmed=1,
            raw_ob_confirmed=1,
            relevant_ob_confirmed=1,
            reaction_state="CONFIRMED",
            reaction_confirmed=True,
        )
        == "RELEVANT_CONFIRMATION_AGGREGATED_CORRECTLY"
    )
