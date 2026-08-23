from __future__ import annotations

from financial_dashboard.context.axes import evaluate_context_axes
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
