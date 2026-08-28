"""T6 tolerance — counter-CHOCH is pullback context, counter-BOS stays a hard conflict.

Market rationale (docs/karar_esnekligi_analiz_ve_plan.md §12): during a trend
pullback the LT anchor's latest current external event is naturally a counter
CHOCH. Treating that as HIGH context conflict locked 766/871 WAITs behind
CONTEXT_CONFLICT_HIGH while the decision layer's own nuanced table said HIGH
only 11 times. T6 keeps the hard veto for an opposing BOS (continuity actually
broke) and downgrades the counter-CHOCH case to MATERIAL, whose severity is
owned by the independent-family conflict gate.
"""

from __future__ import annotations

from financial_dashboard.context.axes import (
    ContextAxes,
    ContextDirection,
    ConflictState,
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
    evaluate_conflict,
    evaluate_continuation,
)
from financial_dashboard.context.envelope import ContextDataQuality

from tests._context_step4_test_data import ref as _context_ref
from financial_dashboard.context.permissions import (
    GateState,
    PermissionScope,
    resolve_permission_axes,
)
from financial_dashboard.context.projections import (
    StructuralEventProjection,
    StructuralFactsProjection,
    StructuralTimeframeProjection,
)


def _event(native_id: str, event_type: str, direction: int) -> StructuralEventProjection:
    return StructuralEventProjection(
        ref=_context_ref(native_id, timeframe="1d", fact_type=event_type),
        scope="EXTERNAL",
        event_type=event_type,
        direction=direction,
        broken_level=100.0,
        origin_price=101.0,
        confirmation_status="CONFIRMED",
        validity="VALID",
        relevance="CURRENT",
        outcome="UNRESOLVED",
        bos_maturity="MATURE",
    )


def _structural(event: StructuralEventProjection) -> StructuralFactsProjection:
    timeframe = StructuralTimeframeProjection(
        timeframe="1d",
        as_of=None,
        data_quality=ContextDataQuality.VALID,
        external=None,
        internal=None,
        events=(event,),
    )
    return StructuralFactsProjection(
        symbol="ASELS",
        timeframes=("1d",),
        timeframe_facts=(timeframe,),
    )


def _conflict(continuation: ContinuationContext) -> ConflictState:
    state, _ = evaluate_conflict(
        structural_direction=ContextDirection.UP,
        continuation=continuation,
        reaction=ReactionContext.NONE,
        reaction_direction=ContextDirection.NONE,
        reversal=ReversalContext.NOT_PRESENT,
        reversal_direction=ContextDirection.NONE,
        mtf=MTFContext.ALIGNED,
        participation=ParticipationContext.NEUTRAL,
    )
    return state


def test_counter_choch_against_thesis_is_pullback_context_not_high():
    continuation, _ = evaluate_continuation(
        _structural(_event("1", "CHOCH", -1)),
        anchor_timeframe="1d",
        structural_thesis=StructuralThesis.UP,
        structural_direction=ContextDirection.UP,
    )
    assert continuation is ContinuationContext.CONFLICTING
    assert _conflict(continuation) is ConflictState.MATERIAL


def test_counter_bos_against_thesis_keeps_hard_high_veto():
    continuation, _ = evaluate_continuation(
        _structural(_event("2", "BOS", -1)),
        anchor_timeframe="1d",
        structural_thesis=StructuralThesis.UP,
        structural_direction=ContextDirection.UP,
    )
    assert continuation is ContinuationContext.CONFLICTING_BREAK
    assert _conflict(continuation) is ConflictState.HIGH


def test_aligned_bos_stays_aligned():
    continuation, _ = evaluate_continuation(
        _structural(_event("3", "BOS", 1)),
        anchor_timeframe="1d",
        structural_thesis=StructuralThesis.UP,
        structural_direction=ContextDirection.UP,
    )
    assert continuation is ContinuationContext.ALIGNED
    assert _conflict(continuation) is ConflictState.NONE


def _axes(continuation: ContinuationContext, conflict: ConflictState) -> ContextAxes:
    return ContextAxes(
        anchor_timeframe="1d",
        structural_thesis=StructuralThesis.UP,
        structural_direction=ContextDirection.UP,
        continuation=continuation,
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
        conflict=conflict,
        reasons=(),
    )


def test_permission_no_longer_blocks_counter_choch_as_context_conflict_high():
    continuation = ContinuationContext.CONFLICTING
    envelope = resolve_permission_axes(_axes(continuation, ConflictState.MATERIAL))
    assert envelope.gate_state is GateState.CONDITIONAL
    assert envelope.scope is PermissionScope.CONTINUATION_ONLY
    assert "PULLBACK_DISCOUNT_CONTEXT" in envelope.allowed_reasons
    assert "CONTEXT_CONFLICT_HIGH" not in envelope.blocking_reasons


def test_permission_still_blocks_counter_bos_as_context_conflict_high():
    continuation = ContinuationContext.CONFLICTING_BREAK
    envelope = resolve_permission_axes(_axes(continuation, ConflictState.HIGH))
    assert envelope.gate_state is GateState.BLOCKED
    assert envelope.blocking_reasons == ("CONTEXT_CONFLICT_HIGH",)
