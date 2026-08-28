from __future__ import annotations

from financial_dashboard.context.axes import (
    ContextDirection,
    ContinuationContext,
    ReactionContext,
    ReversalContext,
    StructuralThesis,
    evaluate_context_axes,
)
from financial_dashboard.context.builder import CrossDomainBuildResult
from financial_dashboard.context.permissions import (
    GateState,
    PermissionScope,
    PermittedSide,
    resolve_permission,
)
from financial_dashboard.context.snapshot import build_context_snapshot
from financial_dashboard.context.zone_interaction import ZoneInteractionState
from financial_dashboard.context.zones import QualifiedZoneSide
from _context_step4_test_data import event, reaction_zone, structural_projection, zone_snapshot


def _resolve(structural, zones):
    axes = evaluate_context_axes(
        structural=structural,
        zones=zones,
        anchor_timeframe="4h",
        trigger_timeframes=("1h",),
    )
    context = build_context_snapshot(
        symbol="ASELS",
        as_of=10,
        anchor_timeframe="4h",
        axes=axes,
        zones=zones,
        all_fact_refs=(),
    )
    return CrossDomainBuildResult(context=context, permission=resolve_permission(context))


def test_scenario_a_htf_aligned_continuation_is_scoped_not_buy_sell() -> None:
    structural = structural_projection(
        anchor_direction=-1,
        anchor_events=(event("BOS-DOWN", event_type="BOS", direction=-1),),
    )
    result = _resolve(structural, zone_snapshot())
    assert result.context.axes.structural_thesis is StructuralThesis.DOWN
    assert result.context.axes.continuation is ContinuationContext.ALIGNED
    assert result.permission.scope is PermissionScope.CONTINUATION_ONLY
    assert result.permission.permitted_side is PermittedSide.SHORT
    assert result.permission.gate_state is GateState.OPEN
    assert result.permission.is_actionable_signal is False


def test_scenario_b_htf_down_with_ltf_counter_reaction_keeps_reversal_absent() -> None:
    structural = structural_projection(anchor_direction=-1, ltf_direction=1)
    support = reaction_zone(
        side=QualifiedZoneSide.SUPPORT,
        interaction=ZoneInteractionState.DEFENDED,
    )
    result = _resolve(structural, zone_snapshot(support))
    axes = result.context.axes
    assert axes.structural_thesis is StructuralThesis.DOWN
    assert axes.reaction is ReactionContext.ACTIVE
    assert axes.reaction_direction is ContextDirection.UP
    assert axes.reversal is ReversalContext.NOT_PRESENT
    assert result.permission.scope is PermissionScope.REACTION_ONLY
    assert result.permission.permitted_side is PermittedSide.SHORT
    assert result.permission.gate_state is GateState.CONDITIONAL
    assert "PULLBACK_DISCOUNT_CONTEXT" in result.permission.allowed_reasons


def test_scenario_c_failed_reaction_does_not_promote_reversal() -> None:
    structural = structural_projection(anchor_direction=-1)
    support = reaction_zone(
        side=QualifiedZoneSide.SUPPORT,
        interaction=ZoneInteractionState.ACCEPTED_THROUGH,
    )
    result = _resolve(structural, zone_snapshot(support))
    assert result.context.axes.reaction is ReactionContext.FAILED
    assert result.context.axes.reversal is ReversalContext.NOT_PRESENT
    assert result.permission.scope is not PermissionScope.REACTION_ONLY


def test_scenario_d_anchor_transition_waits_for_structural_follow_through() -> None:
    structural = structural_projection(
        anchor_state="TRANSITION_UP",
        anchor_direction=1,
        ltf_direction=1,
    )
    result = _resolve(structural, zone_snapshot())
    assert result.context.axes.reversal is ReversalContext.CANDIDATE
    assert result.permission.scope is PermissionScope.STRUCTURAL_TRANSITION
    assert result.permission.permitted_side is PermittedSide.LONG
    assert result.permission.gate_state is GateState.WAITING
    assert "CANONICAL_STRUCTURAL_FOLLOW_THROUGH" in result.permission.waiting_for
