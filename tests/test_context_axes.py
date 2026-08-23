from __future__ import annotations

from financial_dashboard.context.axes import (
    ConflictState,
    ContextDirection,
    ContinuationContext,
    MTFContext,
    ReactionContext,
    ReversalContext,
    StructuralThesis,
    evaluate_context_axes,
    evaluate_continuation,
    evaluate_reversal,
    evaluate_structural_thesis,
)
from financial_dashboard.context.zone_interaction import ZoneInteractionState
from financial_dashboard.context.zones import QualifiedZoneSide

from _context_step4_test_data import event, reaction_zone, structural_projection, zone_snapshot


def test_anchor_structure_is_authority_even_when_ltf_opposes() -> None:
    structural = structural_projection(anchor_direction=-1, ltf_direction=1)
    thesis, direction, _ = evaluate_structural_thesis(structural, anchor_timeframe="4h")

    assert thesis is StructuralThesis.DOWN
    assert direction is ContextDirection.DOWN

    axes = evaluate_context_axes(
        structural=structural,
        zones=zone_snapshot(),
        anchor_timeframe="4h",
        trigger_timeframes=("1h",),
    )
    assert axes.structural_thesis is StructuralThesis.DOWN
    assert axes.mtf is MTFContext.COUNTER_REACTION


def test_continuation_requires_canonical_aligned_bos() -> None:
    structural = structural_projection(
        anchor_direction=-1,
        anchor_events=(event("BOS-1", event_type="BOS", direction=-1),),
    )
    state, _ = evaluate_continuation(
        structural,
        anchor_timeframe="4h",
        structural_thesis=StructuralThesis.DOWN,
        structural_direction=ContextDirection.DOWN,
    )
    assert state is ContinuationContext.ALIGNED

    without_bos = structural_projection(anchor_direction=-1)
    state, _ = evaluate_continuation(
        without_bos,
        anchor_timeframe="4h",
        structural_thesis=StructuralThesis.DOWN,
        structural_direction=ContextDirection.DOWN,
    )
    assert state is ContinuationContext.WEAK


def test_active_counter_reaction_does_not_rewrite_reversal_or_thesis() -> None:
    structural = structural_projection(anchor_direction=-1, ltf_direction=1)
    support = reaction_zone(
        side=QualifiedZoneSide.SUPPORT,
        interaction=ZoneInteractionState.DEFENDED,
    )
    axes = evaluate_context_axes(
        structural=structural,
        zones=zone_snapshot(support),
        anchor_timeframe="4h",
        trigger_timeframes=("1h",),
    )

    assert axes.structural_thesis is StructuralThesis.DOWN
    assert axes.reaction is ReactionContext.ACTIVE
    assert axes.reaction_direction is ContextDirection.UP
    assert axes.reversal is ReversalContext.NOT_PRESENT
    assert axes.conflict in {ConflictState.LOW, ConflictState.MATERIAL}


def test_anchor_transition_creates_reversal_candidate_without_ltf_upgrade() -> None:
    structural = structural_projection(
        anchor_state="TRANSITION_UP",
        anchor_direction=1,
        ltf_direction=1,
    )
    thesis, direction, _ = evaluate_structural_thesis(structural, anchor_timeframe="4h")
    reversal, reversal_direction, _ = evaluate_reversal(
        structural,
        anchor_timeframe="4h",
        structural_thesis=thesis,
    )

    assert thesis is StructuralThesis.TRANSITION_UP
    assert direction is ContextDirection.UP
    assert reversal is ReversalContext.CANDIDATE
    assert reversal_direction is ContextDirection.UP


def test_external_choch_follow_through_can_be_structurally_confirmed() -> None:
    structural = structural_projection(
        anchor_direction=1,
        anchor_events=(
            event(
                "CHOCH-1",
                event_type="CHOCH",
                direction=1,
                relevance="HISTORICAL",
                outcome="FOLLOW_THROUGH_CONFIRMED",
            ),
        ),
    )
    reversal, direction, _ = evaluate_reversal(
        structural,
        anchor_timeframe="4h",
        structural_thesis=StructuralThesis.UP,
    )

    assert reversal is ReversalContext.STRUCTURALLY_CONFIRMED
    assert direction is ContextDirection.UP


def test_failed_external_choch_is_not_confirmed_reversal() -> None:
    structural = structural_projection(
        anchor_direction=-1,
        anchor_events=(
            event(
                "CHOCH-FAIL",
                event_type="CHOCH",
                direction=1,
                validity="FAILED",
                relevance="HISTORICAL",
                outcome="FAILED",
            ),
        ),
    )
    reversal, _, _ = evaluate_reversal(
        structural,
        anchor_timeframe="4h",
        structural_thesis=StructuralThesis.DOWN,
    )
    assert reversal is ReversalContext.FAILED


def test_reaction_developing_is_distinct_from_active() -> None:
    structural = structural_projection(anchor_direction=-1)
    support = reaction_zone(
        side=QualifiedZoneSide.SUPPORT,
        interaction=ZoneInteractionState.APPROACHING,
        distance_atr=0.2,
    )
    axes = evaluate_context_axes(
        structural=structural,
        zones=zone_snapshot(support),
        anchor_timeframe="4h",
    )
    assert axes.reaction is ReactionContext.DEVELOPING
    assert axes.reaction_direction is ContextDirection.UP
    assert axes.reversal is ReversalContext.NOT_PRESENT
