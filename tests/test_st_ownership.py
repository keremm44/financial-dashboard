from types import SimpleNamespace

import pandas as pd

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.decision.scenario import ScenarioKind, ScenarioPresence
from financial_dashboard.decision.st_ownership import (
    STEconomicOwnership,
    classify_st_economic_ownership,
)
from financial_dashboard.decision.structural import DecisionHorizon


def _ref(as_of, *, domain=ContextDomain.SUPPORT_RESISTANCE, native_id="fact:1"):
    return FactRef(
        domain=domain,
        fact_type="TEST_FACT",
        symbol="TEST",
        timeframe="1h",
        native_id=native_id,
        native_state="VALID",
        origin_time=as_of,
        confirmed_at=as_of,
        available_at=as_of,
        lineage_id=native_id,
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _scenario(
    horizon,
    *,
    presence=ScenarioPresence.PRESENT,
    kind=ScenarioKind.CONTINUATION,
    target="target:1",
):
    return SimpleNamespace(
        horizon=horizon,
        presence=presence,
        kind=kind,
        active_target_identity=target,
    )


def _base_snapshot(as_of, *, price=104.0):
    return SimpleNamespace(
        symbol="TEST",
        as_of=as_of,
        current_price=price,
        support_resistance=None,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=None,
    )


def _with_sr(
    snapshot,
    *,
    state,
    direction,
    boundary,
    role_support=(None, None),
    candidate_index=10,
):
    ref = _ref(snapshot.as_of)
    row = SimpleNamespace(
        timeframe="1h",
        ref=ref,
        state=state,
        range_identity=7,
        break_direction=direction,
        break_candidate_index=candidate_index,
        break_boundary=boundary,
        role_reversal_support_low=role_support[0],
        role_reversal_support_high=role_support[1],
    )
    return SimpleNamespace(
        **{
            **snapshot.__dict__,
            "support_resistance": SimpleNamespace(timeframe_facts=(row,)),
        }
    )


def _with_confirmed_ob(snapshot):
    ref = _ref(
        snapshot.as_of,
        domain=ContextDomain.ORDER_BLOCK,
        native_id="ob:pullback:1",
    )
    ob = SimpleNamespace(
        timeframe="1h",
        ref=ref,
        identity="pullback:1",
        bullish=True,
        bottom=98.0,
        top=100.0,
        state="ACTIVE",
        interaction="REACTION_CONFIRMED",
    )
    return SimpleNamespace(
        **{
            **snapshot.__dict__,
            "order_block_behavior": SimpleNamespace(observations=(ob,)),
        }
    )


def _ownership(snapshot, *, st_kind=ScenarioKind.CONTINUATION, target="target:st"):
    return classify_st_economic_ownership(
        snapshot,
        _scenario(DecisionHorizon.LONG_TERM),
        _scenario(
            DecisionHorizon.SHORT_TERM,
            kind=st_kind,
            target=target,
        ),
    )


def test_breakout_acceptance_is_independent_st_product():
    as_of = pd.Timestamp("2026-01-05 10:00")
    snapshot = _with_sr(
        _base_snapshot(as_of),
        state="RANGE_BREAK_CONFIRMED",
        direction=1,
        boundary=100.0,
        role_support=(99.0, 100.0),
    )

    assert _ownership(snapshot, st_kind=ScenarioKind.SHORT_TERM_STANDALONE) is (
        STEconomicOwnership.INDEPENDENT_ST
    )


def test_failed_sell_reclaim_is_independent_st_product():
    as_of = pd.Timestamp("2026-01-05 10:00")
    snapshot = _with_sr(
        _base_snapshot(as_of, price=97.0),
        state="RANGE_BREAK_FAILED",
        direction=-1,
        boundary=96.0,
    )

    assert _ownership(snapshot, st_kind=ScenarioKind.SHORT_TERM_STANDALONE) is (
        STEconomicOwnership.INDEPENDENT_ST
    )


def test_pullback_with_confirmed_buyer_regain_is_independent_st_product():
    as_of = pd.Timestamp("2026-01-05 10:00")
    snapshot = _with_confirmed_ob(_base_snapshot(as_of, price=102.0))

    assert _ownership(snapshot) is STEconomicOwnership.INDEPENDENT_ST


def test_generic_short_term_continuation_without_st_thesis_is_lt_timing_only():
    as_of = pd.Timestamp("2026-01-05 10:00")

    assert _ownership(_base_snapshot(as_of)) is STEconomicOwnership.LT_TIMING_ONLY


def test_ambiguous_st_families_do_not_claim_independent_ownership():
    as_of = pd.Timestamp("2026-01-05 10:00")
    breakout = _with_sr(
        _base_snapshot(as_of),
        state="RANGE_BREAK_CONFIRMED",
        direction=1,
        boundary=100.0,
        role_support=(99.0, 100.0),
    )
    snapshot = _with_confirmed_ob(breakout)

    assert _ownership(snapshot) is STEconomicOwnership.UNRESOLVED


def test_missing_target_context_keeps_ownership_unresolved():
    as_of = pd.Timestamp("2026-01-05 10:00")

    assert _ownership(_base_snapshot(as_of), target=None) is STEconomicOwnership.UNRESOLVED


def test_lt_absence_does_not_turn_generic_st_timing_into_false_independence():
    as_of = pd.Timestamp("2026-01-05 10:00")
    snapshot = _base_snapshot(as_of)
    ownership = classify_st_economic_ownership(
        snapshot,
        _scenario(DecisionHorizon.LONG_TERM, presence=ScenarioPresence.ABSENT),
        _scenario(DecisionHorizon.SHORT_TERM),
    )

    assert ownership is STEconomicOwnership.UNRESOLVED
