from __future__ import annotations

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.projections import (
    StabilSupportBehaviorProjection,
    StabilSupportEventProjection,
    StabilSupportProjection,
    StructuralFactsProjection,
    StructuralScopeProjection,
    StructuralTimeframeProjection,
)
from financial_dashboard.decision.market_state import build_market_state
from financial_dashboard.decision.structural import StructuralDirection


def _scope(state: str, direction: int, *, seed: int) -> StructuralScopeProjection:
    return StructuralScopeProjection(
        scope="EXTERNAL",
        state=state,
        direction=direction,
        protected_high=120.0 + seed,
        protected_low=80.0 + seed,
        weak_high=125.0 + seed,
        weak_low=75.0 + seed,
        strong_high_identity=seed * 10 + 1,
        strong_low_identity=seed * 10 + 2,
        protected_high_identity=seed * 10 + 3,
        protected_low_identity=seed * 10 + 4,
        weak_high_identity=seed * 10 + 5,
        weak_low_identity=seed * 10 + 6,
    )


def _structure() -> StructuralFactsProjection:
    rows = []
    for seed, timeframe in enumerate(("1d", "4h", "2h", "1h", "30m"), start=1):
        rows.append(
            StructuralTimeframeProjection(
                timeframe=timeframe,
                as_of=100 + seed,
                data_quality=ContextDataQuality.VALID,
                external=_scope("STATE_BULLISH", 1, seed=seed),
                internal=_scope("STATE_BULLISH", 1, seed=seed + 20),
                events=(),
            )
        )
    return StructuralFactsProjection(
        symbol="ASELS",
        timeframes=("1d", "4h", "2h", "1h", "30m"),
        timeframe_facts=tuple(rows),
    )


def _stabil_ref(native_id: str, index: int) -> FactRef:
    return FactRef(
        domain=ContextDomain.STABIL_SUPPORT,
        fact_type="DAILY_STRUCTURAL_SUPPORT",
        symbol="ASELS",
        timeframe="1d",
        native_id=native_id,
        native_state="ACTIVE",
        origin_time=index,
        confirmed_at=index,
        available_at=index,
        lineage_id=None,
        causal_family=CausalFamily.STRUCTURAL_LEVEL,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _stabil(*, behavior: bool = True) -> StabilSupportProjection:
    support_ref = _stabil_ref("STABIL:SUPPORT:1", 5)
    event_ref = _stabil_ref("STABIL:EVENT:1", 6)
    event = StabilSupportEventProjection(
        ref=event_ref,
        event_type="RECLAIM_CONFIRMED",
        support_level=98.0,
        support_floor=96.5,
        price=100.0,
        bars_above_support=2,
        bars_below_support=0,
        reclaim_count=1,
    )
    behavior_projection = (
        StabilSupportBehaviorProjection(
            motion="FALLING",
            relation="ABOVE_NEAR",
            interaction="RECLAIM_ATTEMPT",
            approach_origin="POST_RECLAIM",
            bars_since_rebase=2,
            cross_count=1,
            last_rebase_step_atr=-0.8,
            distance_delta_atr=0.25,
            reclaim_active=True,
        )
        if behavior
        else None
    )
    return StabilSupportProjection(
        symbol="ASELS",
        timeframe="1d",
        as_of=10,
        data_quality=ContextDataQuality.VALID,
        support_ref=support_ref,
        support_level=98.0,
        support_floor=96.5,
        validity="ACTIVE",
        dynamics="ABOVE_SUPPORT",
        progression="REBASED_LOWER",
        distance_pct=1.0,
        distance_atr=0.4,
        bars_above_support=2,
        bars_below_support=0,
        reclaim_count=1,
        events=(event,),
        behavior=behavior_projection,
    )


def test_market_state_carries_native_stabil_facts_without_reinterpretation() -> None:
    state = build_market_state(_structure(), stabil=_stabil())
    stabil = state.stabil

    assert stabil.data_quality is ContextDataQuality.VALID
    assert stabil.timeframe == "1d"
    assert stabil.as_of == 10
    assert stabil.support_level == 98.0
    assert stabil.support_floor == 96.5
    assert stabil.validity == "ACTIVE"
    assert stabil.dynamics == "ABOVE_SUPPORT"
    assert stabil.progression == "REBASED_LOWER"
    assert stabil.distance_pct == 1.0
    assert stabil.distance_atr == 0.4
    assert stabil.bars_above_support == 2
    assert stabil.bars_below_support == 0
    assert stabil.reclaim_count == 1
    assert stabil.motion == "FALLING"
    assert stabil.relation == "ABOVE_NEAR"
    assert stabil.interaction == "RECLAIM_ATTEMPT"
    assert stabil.approach_origin == "POST_RECLAIM"
    assert stabil.bars_since_rebase == 2
    assert stabil.cross_count == 1
    assert stabil.last_rebase_step_atr == -0.8
    assert stabil.distance_delta_atr == 0.25
    assert stabil.reclaim_active is True
    assert stabil.events == _stabil().events
    assert "STABIL_INTERACTION:RECLAIM_ATTEMPT" in stabil.reasons


def test_stabil_facts_do_not_change_existing_structural_market_state() -> None:
    structure = _structure()

    baseline = build_market_state(structure)
    with_stabil = build_market_state(structure, stabil=_stabil())

    assert baseline.long_term == with_stabil.long_term
    assert baseline.short_term == with_stabil.short_term
    assert baseline.horizon_relation == with_stabil.horizon_relation
    assert baseline.reasons == with_stabil.reasons
    assert with_stabil.long_term.structural.direction is StructuralDirection.LONG
    assert with_stabil.short_term.structural.direction is StructuralDirection.LONG


def test_missing_stabil_is_explicitly_unavailable_without_invented_facts() -> None:
    stabil = build_market_state(_structure()).stabil

    assert stabil.data_quality is ContextDataQuality.UNAVAILABLE
    assert stabil.timeframe is None
    assert stabil.as_of is None
    assert stabil.support_ref is None
    assert stabil.support_level is None
    assert stabil.motion is None
    assert stabil.interaction is None
    assert stabil.reclaim_active is None
    assert stabil.events == ()
    assert stabil.reasons == ("STABIL:UNAVAILABLE",)


def test_missing_stabil_behavior_preserves_lifecycle_facts_without_guessing_behavior() -> None:
    stabil = build_market_state(_structure(), stabil=_stabil(behavior=False)).stabil

    assert stabil.validity == "ACTIVE"
    assert stabil.progression == "REBASED_LOWER"
    assert stabil.support_level == 98.0
    assert stabil.motion is None
    assert stabil.relation is None
    assert stabil.interaction is None
    assert stabil.approach_origin is None
    assert stabil.reclaim_active is None
    assert "STABIL_BEHAVIOR:UNAVAILABLE" in stabil.reasons


def test_market_state_with_stabil_is_deterministic_for_same_frozen_inputs() -> None:
    structure = _structure()
    stabil = _stabil()

    first = build_market_state(structure, stabil=stabil)
    second = build_market_state(structure, stabil=stabil)

    assert first == second
