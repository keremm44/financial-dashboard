from __future__ import annotations

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.participation_behavior_projection import (
    AbsorptionBehavior,
    BreakParticipationBehavior,
    EffortResultBehavior,
    ParticipationBehaviorProjection,
    ParticipationBehaviorTimeframeProjection,
    ParticipationTrend,
    ShockBehavior,
)
from financial_dashboard.context.projections import (
    StructuralFactsProjection,
    StructuralScopeProjection,
    StructuralTimeframeProjection,
)
from financial_dashboard.decision.market_state import (
    BridgeState,
    ParticipationPropagationState,
    StructuralRegime,
    TimeframeAuthorityRole,
    build_market_state,
)
from financial_dashboard.decision.structural import (
    HorizonRelation,
    StructuralDirection,
    ThesisState,
)


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


def _internal(state: str, direction: int, *, seed: int) -> StructuralScopeProjection:
    row = _scope(state, direction, seed=seed)
    return StructuralScopeProjection(
        scope="INTERNAL",
        state=row.state,
        direction=row.direction,
        protected_high=row.protected_high,
        protected_low=row.protected_low,
        weak_high=row.weak_high,
        weak_low=row.weak_low,
        strong_high_identity=row.strong_high_identity,
        strong_low_identity=row.strong_low_identity,
        protected_high_identity=row.protected_high_identity,
        protected_low_identity=row.protected_low_identity,
        weak_high_identity=row.weak_high_identity,
        weak_low_identity=row.weak_low_identity,
    )


def _structure(**states: tuple[str, int] | None) -> StructuralFactsProjection:
    order = ("1d", "4h", "2h", "1h", "30m")
    facts: list[StructuralTimeframeProjection] = []
    included: list[str] = []
    for seed, timeframe in enumerate(order, start=1):
        spec = states.get(timeframe)
        if spec is None:
            continue
        state, direction = spec
        included.append(timeframe)
        facts.append(
            StructuralTimeframeProjection(
                timeframe=timeframe,
                as_of=100 + seed,
                data_quality=ContextDataQuality.VALID,
                external=_scope(state, direction, seed=seed),
                internal=_internal(state, direction, seed=seed + 20),
                events=(),
            )
        )
    return StructuralFactsProjection(
        symbol="ASELS",
        timeframes=tuple(included),
        timeframe_facts=tuple(facts),
    )


def _volume_ref(timeframe: str, index: int) -> FactRef:
    return FactRef(
        domain=ContextDomain.VOLUME,
        fact_type="PARTICIPATION_BEHAVIOR",
        symbol="ASELS",
        timeframe=timeframe,
        native_id=f"VOL:{timeframe}:{index}",
        native_state="CONFIRMED",
        origin_time=index,
        confirmed_at=index,
        available_at=index,
        lineage_id=None,
        causal_family=CausalFamily.PARTICIPATION,
        source_family=SourceFamily.VOLUME_SERIES,
        data_quality=ContextDataQuality.VALID,
    )


def _participation(direction: int) -> ParticipationBehaviorProjection:
    rows = []
    for index, timeframe in enumerate(("1h", "2h", "4h"), start=1):
        rows.append(
            ParticipationBehaviorTimeframeProjection(
                timeframe=timeframe,
                ref=_volume_ref(timeframe, index),
                status="DATA_OK",
                final_state="PARTICIPATION_CONFIRMED",
                evidence_direction=direction,
                participation_trend=ParticipationTrend.CONFIRMED,
                effort_result=EffortResultBehavior.EFFICIENT,
                absorption=AbsorptionBehavior.NONE,
                break_participation=BreakParticipationBehavior.NONE,
                shock=ShockBehavior.NONE,
                participation_direction=direction,
                participation_stage="CONFIRMED",
                controlled_pullback=False,
                controlled_reaction=False,
                absorption_side="NONE",
                absorption_stage="NONE",
                break_direction=0,
                break_stage="NONE",
                heavy_conflict=False,
                shock_direction=0,
                rvol=None,
                relative_traded_value=None,
                directional_value_pressure_5=None,
                directional_value_pressure_10=None,
                net_progress_atr=None,
                directional_efficiency=None,
                effort_result_class="EFFICIENT",
            )
        )
    return ParticipationBehaviorProjection(
        symbol="ASELS",
        timeframes=("1h", "2h", "4h"),
        timeframe_facts=tuple(rows),
    )


def test_mtf_roles_are_explicit_and_lower_timeframes_cannot_outvote_1d_lt() -> None:
    state = build_market_state(
        _structure(
            **{
                "1d": ("STATE_BULLISH", 1),
                "4h": ("STATE_BEARISH", -1),
                "2h": ("STATE_BEARISH", -1),
                "1h": ("STATE_BEARISH", -1),
                "30m": ("STATE_BEARISH", -1),
            }
        )
    )

    lt = state.long_term
    assert lt.structural.direction is StructuralDirection.LONG
    assert lt.structural.thesis_state is ThesisState.INTACT
    assert lt.structural_map.structural_regime is StructuralRegime.DIRECTIONAL
    assert lt.structural_map.bridge_state is BridgeState.COUNTER_REACTION
    assert lt.structural_map.for_timeframe("1d").role is TimeframeAuthorityRole.PRIMARY
    assert lt.structural_map.for_timeframe("4h").role is TimeframeAuthorityRole.SECONDARY
    assert lt.structural_map.for_timeframe("2h").role is TimeframeAuthorityRole.BRIDGE
    assert lt.structural_map.for_timeframe("1h").role is TimeframeAuthorityRole.TIMING
    assert lt.structural_map.for_timeframe("30m").role is TimeframeAuthorityRole.EXECUTION
    assert state.horizon_relation is HorizonRelation.COUNTER_REACTION


def test_4h_opposite_transition_marks_lt_transition_but_never_flips_direction() -> None:
    state = build_market_state(
        _structure(
            **{
                "1d": ("STATE_BULLISH", 1),
                "4h": ("STATE_TRANSITION_DOWN", 0),
                "2h": ("STATE_BEARISH", -1),
                "1h": ("STATE_BEARISH", -1),
            }
        )
    )

    assert state.long_term.structural.direction is StructuralDirection.LONG
    assert state.long_term.structural.thesis_state is ThesisState.TRANSITIONING
    assert state.long_term.structural.transition_target is StructuralDirection.SHORT
    assert state.long_term.structural_map.structural_regime is StructuralRegime.TRANSITION


def test_2h_transition_is_bridge_warning_only_for_intact_lt() -> None:
    state = build_market_state(
        _structure(
            **{
                "1d": ("STATE_BULLISH", 1),
                "4h": ("STATE_BULLISH", 1),
                "2h": ("STATE_TRANSITION_DOWN", 0),
                "1h": ("STATE_BULLISH", 1),
            }
        )
    )

    assert state.long_term.structural.direction is StructuralDirection.LONG
    assert state.long_term.structural.thesis_state is ThesisState.INTACT
    assert state.long_term.structural_map.bridge_state is BridgeState.TRANSITION_WARNING


def test_30m_opposition_is_execution_context_not_short_term_direction_authority() -> None:
    state = build_market_state(
        _structure(
            **{
                "1d": ("STATE_BULLISH", 1),
                "4h": ("STATE_BULLISH", 1),
                "2h": ("STATE_BULLISH", 1),
                "1h": ("STATE_BULLISH", 1),
                "30m": ("STATE_BEARISH", -1),
            }
        )
    )

    assert state.short_term.structural.direction is StructuralDirection.LONG
    assert state.short_term.structural.thesis_state is ThesisState.INTACT
    node = state.short_term.structural_map.for_timeframe("30m")
    assert node.role is TimeframeAuthorityRole.EXECUTION
    assert node.external_direction == -1


def test_missing_1d_is_lt_unresolved_not_neutral_or_short() -> None:
    state = build_market_state(
        _structure(
            **{
                "4h": ("STATE_BULLISH", 1),
                "2h": ("STATE_BULLISH", 1),
                "1h": ("STATE_BULLISH", 1),
            }
        )
    )

    assert state.long_term.structural.direction is StructuralDirection.UNRESOLVED
    assert state.long_term.structural.thesis_state is ThesisState.UNRESOLVED
    assert state.long_term.structural_map.structural_regime is StructuralRegime.UNRESOLVED
    assert state.long_term.structural_map.bridge_state is BridgeState.UNRESOLVED


def test_participation_can_be_opposing_without_changing_structural_direction() -> None:
    structure = _structure(
        **{
            "1d": ("STATE_BULLISH", 1),
            "4h": ("STATE_BULLISH", 1),
            "2h": ("STATE_BULLISH", 1),
            "1h": ("STATE_BULLISH", 1),
        }
    )
    state = build_market_state(structure, participation=_participation(-1))

    assert state.long_term.structural.direction is StructuralDirection.LONG
    assert state.short_term.structural.direction is StructuralDirection.LONG
    assert state.long_term.participation_propagation is ParticipationPropagationState.OPPOSING
    assert state.short_term.participation_propagation is ParticipationPropagationState.OPPOSING


def test_market_state_is_deterministic_for_same_frozen_inputs() -> None:
    structure = _structure(
        **{
            "1d": ("STATE_BULLISH", 1),
            "4h": ("STATE_BULLISH", 1),
            "2h": ("STATE_BULLISH", 1),
            "1h": ("STATE_BULLISH", 1),
            "30m": ("STATE_BULLISH", 1),
        }
    )
    participation = _participation(1)

    first = build_market_state(structure, participation=participation)
    second = build_market_state(structure, participation=participation)

    assert first == second
    assert first.long_term.participation_propagation is ParticipationPropagationState.HTF_CONFIRMED
