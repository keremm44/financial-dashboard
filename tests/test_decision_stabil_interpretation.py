from __future__ import annotations

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.decision.market_state import StabilMarketState
from financial_dashboard.decision.stabil_interpretation import (
    StabilHorizonState,
    assess_stabil_horizon,
)
from financial_dashboard.decision.structural import DecisionHorizon


def _ref() -> FactRef:
    return FactRef(
        domain=ContextDomain.STABIL_SUPPORT,
        fact_type="DAILY_STRUCTURAL_SUPPORT",
        symbol="ASELS",
        timeframe="1d",
        native_id="STABIL:SUPPORT:1",
        native_state="ACTIVE",
        origin_time=1,
        confirmed_at=2,
        available_at=2,
        lineage_id="STABIL:ROOT",
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _stabil(
    *,
    validity: str = "ACTIVE",
    progression: str = "SAME",
    motion: str = "FLAT",
    relation: str = "ABOVE_NEAR",
    interaction: str = "HOLDING_ABOVE",
    with_support: bool = True,
) -> StabilMarketState:
    return StabilMarketState(
        data_quality=ContextDataQuality.VALID,
        timeframe="1d",
        as_of=10,
        support_ref=_ref() if with_support else None,
        support_level=100.0 if with_support else None,
        support_floor=98.0 if with_support else None,
        validity=validity,
        dynamics="FLAT",
        progression=progression,
        distance_pct=2.0,
        distance_atr=1.0,
        bars_above_support=4,
        bars_below_support=0,
        reclaim_count=0,
        events=(),
        motion=motion,
        relation=relation,
        interaction=interaction,
        approach_origin="FROM_ABOVE",
        bars_since_rebase=5,
        cross_count=1,
        last_rebase_step_atr=None,
        distance_delta_atr=0.1,
        reclaim_active=False,
        reasons=("FACTUAL_STABIL",),
    )


def test_same_far_flat_support_has_different_lt_and_st_meaning() -> None:
    stabil = _stabil(motion="FLAT", relation="ABOVE_FAR", interaction="HOLDING_ABOVE")

    lt = assess_stabil_horizon(stabil, DecisionHorizon.LONG_TERM)
    st = assess_stabil_horizon(stabil, DecisionHorizon.SHORT_TERM)

    assert lt.state is StabilHorizonState.LAGGING_FOUNDATION
    assert st.state is StabilHorizonState.DISTANT_ABOVE
    assert lt.source_refs == st.source_refs == (_ref(),)


def test_rising_support_is_lt_foundation_evidence_not_st_prerequisite() -> None:
    stabil = _stabil(
        progression="REBASED_HIGHER",
        motion="RISING",
        relation="ABOVE_FAR",
        interaction="SUPPORTED_ADVANCE",
    )

    lt = assess_stabil_horizon(stabil, DecisionHorizon.LONG_TERM)
    st = assess_stabil_horizon(stabil, DecisionHorizon.SHORT_TERM)

    assert lt.state is StabilHorizonState.ADVANCING_FOUNDATION
    assert st.state is StabilHorizonState.DISTANT_ABOVE


def test_support_testing_is_not_relabelled_as_weakness() -> None:
    stabil = _stabil(relation="AT_SUPPORT", interaction="TESTING_SUPPORT")

    lt = assess_stabil_horizon(stabil, DecisionHorizon.LONG_TERM)
    st = assess_stabil_horizon(stabil, DecisionHorizon.SHORT_TERM)

    assert lt.state is StabilHorizonState.SUPPORT_TESTING
    assert st.state is StabilHorizonState.SUPPORT_TESTING


def test_reclaim_above_falling_support_remains_reclaiming_not_recovery() -> None:
    stabil = _stabil(
        progression="REBASED_LOWER",
        motion="FALLING",
        relation="ABOVE_NEAR",
        interaction="RECLAIM_ATTEMPT",
    )

    lt = assess_stabil_horizon(stabil, DecisionHorizon.LONG_TERM)
    st = assess_stabil_horizon(stabil, DecisionHorizon.SHORT_TERM)

    assert lt.state is StabilHorizonState.RECLAIMING
    assert st.state is StabilHorizonState.RECLAIMING
    assert lt.motion == st.motion == "FALLING"


def test_persistent_downside_is_preserved_as_downside_evidence_for_both_horizons() -> None:
    stabil = _stabil(
        validity="BELOW_FLOOR",
        progression="REBASED_LOWER",
        motion="FALLING",
        relation="BELOW_FAR",
        interaction="DOWNSIDE_CONTINUATION",
    )

    lt = assess_stabil_horizon(stabil, DecisionHorizon.LONG_TERM)
    st = assess_stabil_horizon(stabil, DecisionHorizon.SHORT_TERM)

    assert lt.state is StabilHorizonState.DOWNSIDE_CONTINUATION
    assert st.state is StabilHorizonState.DOWNSIDE_CONTINUATION


def test_missing_support_is_explicit_not_established_and_non_actionable() -> None:
    stabil = _stabil(validity="NO_SUPPORT", with_support=False)
    result = assess_stabil_horizon(stabil, DecisionHorizon.LONG_TERM)

    assert result.state is StabilHorizonState.NOT_ESTABLISHED
    assert result.source_refs == ()
    assert not hasattr(result, "action")
    assert not hasattr(result, "blockers")
    assert not hasattr(result, "waiting_for")
