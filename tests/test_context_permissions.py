from __future__ import annotations

from financial_dashboard.context.axes import (
    ConflictState,
    ContextAxes,
    ContextDirection,
    ContinuationContext,
    HamReadinessContext,
    MTFContext,
    ObjectiveContext,
    ParticipationContext,
    PatternReadiness,
    ReactionContext,
    ReversalContext,
    StructuralThesis,
    VolatilityContext,
    evaluate_context_axes,
)
from financial_dashboard.context.permissions import (
    GateState,
    PermissionScope,
    PermittedSide,
    resolve_permission,
    resolve_permission_axes,
)
from financial_dashboard.context.snapshot import build_context_snapshot
from financial_dashboard.context.zone_interaction import ZoneInteractionState
from financial_dashboard.context.zones import QualifiedZoneSide

from _context_step4_test_data import event, reaction_zone, structural_projection, zone_snapshot


def _snapshot(*, structural, zones):
    axes = evaluate_context_axes(
        structural=structural,
        zones=zones,
        anchor_timeframe="4h",
        trigger_timeframes=("1h",),
    )
    return build_context_snapshot(
        symbol="ASELS",
        as_of=10,
        anchor_timeframe="4h",
        axes=axes,
        zones=zones,
        all_fact_refs=(),
    )


def test_aligned_canonical_continuation_can_open_scope_without_becoming_action() -> None:
    structural = structural_projection(
        anchor_direction=-1,
        anchor_events=(event("BOS-DOWN", event_type="BOS", direction=-1),),
        ltf_direction=-1,
    )
    envelope = resolve_permission(_snapshot(structural=structural, zones=zone_snapshot()))

    assert envelope.scope is PermissionScope.CONTINUATION_ONLY
    assert envelope.permitted_side is PermittedSide.SHORT
    assert envelope.gate_state is GateState.OPEN
    assert envelope.is_actionable_signal is False


def test_active_counter_reaction_is_only_conditional_reaction_scope() -> None:
    structural = structural_projection(anchor_direction=-1, ltf_direction=1)
    support = reaction_zone(
        side=QualifiedZoneSide.SUPPORT,
        interaction=ZoneInteractionState.DEFENDED,
    )
    envelope = resolve_permission(
        _snapshot(structural=structural, zones=zone_snapshot(support))
    )

    assert envelope.scope is PermissionScope.REACTION_ONLY
    assert envelope.permitted_side is PermittedSide.LONG
    assert envelope.gate_state is GateState.CONDITIONAL
    assert "FUTURE_ACTION_LAYER_TIMING" in envelope.waiting_for


def test_developing_counter_reaction_waits() -> None:
    structural = structural_projection(anchor_direction=-1, ltf_direction=1)
    support = reaction_zone(
        side=QualifiedZoneSide.SUPPORT,
        interaction=ZoneInteractionState.APPROACHING,
        distance_atr=0.2,
    )
    envelope = resolve_permission(
        _snapshot(structural=structural, zones=zone_snapshot(support))
    )

    assert envelope.scope is PermissionScope.REACTION_ONLY
    assert envelope.permitted_side is PermittedSide.LONG
    assert envelope.gate_state is GateState.WAITING


def test_anchor_transition_candidate_waits_for_structural_follow_through() -> None:
    structural = structural_projection(
        anchor_state="TRANSITION_UP",
        anchor_direction=1,
        ltf_direction=1,
    )
    envelope = resolve_permission(_snapshot(structural=structural, zones=zone_snapshot()))

    assert envelope.scope is PermissionScope.STRUCTURAL_TRANSITION
    assert envelope.permitted_side is PermittedSide.LONG
    assert envelope.gate_state is GateState.WAITING
    assert envelope.waiting_for == ("CANONICAL_STRUCTURAL_FOLLOW_THROUGH",)


def test_structurally_confirmed_reversal_remains_conditional_not_buy_sell() -> None:
    structural = structural_projection(
        anchor_direction=1,
        anchor_events=(
            event(
                "CHOCH-UP",
                event_type="CHOCH",
                direction=1,
                relevance="HISTORICAL",
                outcome="FOLLOW_THROUGH_CONFIRMED",
            ),
        ),
        ltf_direction=1,
    )
    envelope = resolve_permission(_snapshot(structural=structural, zones=zone_snapshot()))

    assert envelope.scope is PermissionScope.STRUCTURAL_TRANSITION
    assert envelope.permitted_side is PermittedSide.LONG
    assert envelope.gate_state is GateState.CONDITIONAL
    assert envelope.is_actionable_signal is False


def test_unresolved_canonical_structure_blocks_permission() -> None:
    structural = structural_projection(anchor_state="NEUTRAL", anchor_direction=0)
    envelope = resolve_permission(_snapshot(structural=structural, zones=zone_snapshot()))

    assert envelope.scope is PermissionScope.NONE
    assert envelope.permitted_side is PermittedSide.NONE
    assert envelope.gate_state is GateState.BLOCKED
    assert envelope.blocking_reasons == ("CANONICAL_STRUCTURE_UNRESOLVED",)


def test_intact_thesis_without_fresh_bos_is_pullback_discount_not_empty_wait() -> None:
    lt_axes = ContextAxes(
        anchor_timeframe="1d",
        structural_thesis=StructuralThesis.UP,
        structural_direction=ContextDirection.UP,
        continuation=ContinuationContext.CONFLICTING,
        reaction=ReactionContext.NONE,
        reaction_direction=ContextDirection.NONE,
        reversal=ReversalContext.NOT_PRESENT,
        reversal_direction=ContextDirection.NONE,
        objective=ObjectiveContext.UPSIDE,
        participation=ParticipationContext.NEUTRAL,
        volatility=VolatilityContext.BALANCED,
        pattern_readiness=PatternReadiness.NO_PATTERN,
        mtf=MTFContext.COUNTER_REACTION,
        ham_readiness=HamReadinessContext.AVAILABLE,
        conflict=ConflictState.MATERIAL,
        reasons=(),
    )
    lt = resolve_permission_axes(lt_axes)
    assert lt.gate_state is GateState.CONDITIONAL
    assert lt.scope is PermissionScope.CONTINUATION_ONLY
    assert lt.permitted_side is PermittedSide.LONG
    assert "PULLBACK_DISCOUNT_CONTEXT" in lt.allowed_reasons
    assert lt.blocking_reasons == ()
    assert lt.is_actionable_signal is False

    st_axes = ContextAxes(
        anchor_timeframe="1h",
        structural_thesis=StructuralThesis.UP,
        structural_direction=ContextDirection.UP,
        continuation=ContinuationContext.WEAK,
        reaction=ReactionContext.NONE,
        reaction_direction=ContextDirection.NONE,
        reversal=ReversalContext.NOT_PRESENT,
        reversal_direction=ContextDirection.NONE,
        objective=ObjectiveContext.UPSIDE,
        participation=ParticipationContext.NEUTRAL,
        volatility=VolatilityContext.BALANCED,
        pattern_readiness=PatternReadiness.NO_PATTERN,
        mtf=MTFContext.ALIGNED,
        ham_readiness=HamReadinessContext.AVAILABLE,
        conflict=ConflictState.NONE,
        reasons=(),
    )
    st = resolve_permission_axes(st_axes)
    assert st.gate_state is GateState.CONDITIONAL
    assert st.scope is PermissionScope.REACTION_ONLY
    assert st.permitted_side is PermittedSide.LONG
    assert "PULLBACK_DISCOUNT_CONTEXT" in st.allowed_reasons


def test_unresolved_context_conflict_is_wait_not_hard_block() -> None:
    axes = ContextAxes(
        anchor_timeframe="1h",
        structural_thesis=StructuralThesis.UP,
        structural_direction=ContextDirection.UP,
        continuation=ContinuationContext.ALIGNED,
        reaction=ReactionContext.NONE,
        reaction_direction=ContextDirection.NONE,
        reversal=ReversalContext.NOT_PRESENT,
        reversal_direction=ContextDirection.NONE,
        objective=ObjectiveContext.NONE,
        participation=ParticipationContext.NEUTRAL,
        volatility=VolatilityContext.BALANCED,
        pattern_readiness=PatternReadiness.NO_PATTERN,
        mtf=MTFContext.UNRESOLVED,
        ham_readiness=HamReadinessContext.DEGRADED,
        conflict=ConflictState.UNRESOLVED,
        reasons=(),
    )
    envelope = resolve_permission_axes(axes)

    assert envelope.gate_state is GateState.WAITING
    assert envelope.permitted_side is PermittedSide.LONG
    assert envelope.blocking_reasons == ()
    assert envelope.waiting_for == ("CONTEXT_CONFLICT_TO_RESOLVE",)
