from __future__ import annotations

from types import SimpleNamespace

from financial_dashboard.context.envelope import (
    CausalFamily,
    ContextDataQuality,
    ContextDomain,
    FactRef,
    SourceFamily,
)
from financial_dashboard.context.fvg_engulfing_projection import (
    FvgEngulfingLifecycleProjection,
    FvgLifecycleProjection,
)
from financial_dashboard.context.permissions import (
    GateState,
    PermissionEnvelope,
    PermissionScope,
    PermittedSide,
)
from financial_dashboard.decision.opportunity import OpportunityCalibration
from financial_dashboard.decision.st_transition import (
    STTransitionState,
    apply_strong_st_long_transition,
    assess_st_long_transition,
    reconcile_st_transition_permission,
)
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    StructuralAssessment,
    StructuralDirection,
    ThesisState,
)


def _ref(native_id: str, *, domain: ContextDomain = ContextDomain.MARKET_STRUCTURE, available_at="2026-01-02 10:00:00") -> FactRef:
    return FactRef(
        domain=domain,
        fact_type="TEST",
        symbol="ASELS",
        timeframe="1h",
        native_id=native_id,
        native_state="VALID",
        origin_time=available_at,
        confirmed_at=available_at,
        available_at=available_at,
        lineage_id=native_id,
        causal_family=CausalFamily.IMPULSE,
        source_family=SourceFamily.PRICE_GEOMETRY,
        data_quality=ContextDataQuality.VALID,
    )


def _event(event_type: str, direction: int, *, at: str):
    return SimpleNamespace(
        ref=_ref(f"{event_type}:{direction}:{at}", available_at=at),
        scope="EXTERNAL",
        confirmation_status="CONFIRMED",
        validity="VALID",
        relevance="CURRENT",
        event_type=event_type,
        direction=direction,
    )


def _fvg() -> FvgLifecycleProjection:
    return FvgLifecycleProjection(
        ref=_ref("FVG:1", domain=ContextDomain.FVG),
        identity="FVG:1h:1:1",
        direction=1,
        state="ACTIVE",
        lower_boundary=99.0,
        upper_boundary=100.0,
        quality=80.0,
        gap_atr=0.5,
        formation_atr=1.0,
        formation_index=5,
        first_test_index=8,
        wick_fill_ratio=0.2,
        close_fill_ratio=0.1,
        maximum_fill_ratio=0.2,
        reaction_evidence_count=3,
        reaction_confirmed=True,
        failed_reaction=False,
        full_fill=False,
        invalid=False,
        invalid_reason="",
        invalid_close_count=0,
    )


def _native(*, transitioning: bool) -> StructuralAssessment:
    return StructuralAssessment(
        horizon=DecisionHorizon.SHORT_TERM,
        authority_timeframe="1h",
        direction=StructuralDirection.SHORT,
        thesis_state=ThesisState.TRANSITIONING if transitioning else ThesisState.INTACT,
        native_state="STATE_TRANSITION_UP" if transitioning else "STATE_BEARISH",
        transition_target=StructuralDirection.LONG if transitioning else None,
        data_quality=ContextDataQuality.VALID,
        authority_as_of="2026-01-02 12:00:00",
        protected_high=110.0,
        protected_low=90.0,
        weak_high=108.0,
        weak_low=92.0,
        source_refs=(),
        reasons=("native",),
    )


def _stabil(
    interaction: str,
    *,
    quality: ContextDataQuality = ContextDataQuality.VALID,
):
    return SimpleNamespace(
        data_quality=quality,
        support_ref=None,
        events=(),
        validity="HELD",
        progression="SAME",
        behavior=SimpleNamespace(
            interaction=interaction,
            motion=("FALLING" if interaction == "DOWNSIDE_CONTINUATION" else "FLAT_AFTER_FALL"),
            relation=("BELOW_FAR" if interaction == "DOWNSIDE_CONTINUATION" else "ABOVE_FAR"),
        ),
    )


def _snapshot(
    *events,
    stabil_interaction: str | None = None,
    stabil_quality: ContextDataQuality = ContextDataQuality.VALID,
):
    structure = SimpleNamespace(
        for_timeframe=lambda timeframe: SimpleNamespace(events=tuple(events))
    )
    targeting = SimpleNamespace(
        nearest_upside_target=SimpleNamespace(
            distance_atr=3.0,
            identity="UP:T1",
            quality="SUPPORTED",
            evidence=(),
        ),
        nearest_downside_target=None,
    )
    lifecycle = FvgEngulfingLifecycleProjection(
        symbol="ASELS",
        timeframes=("1h",),
        fvg=(_fvg(),),
        engulfing=(),
    )
    return SimpleNamespace(
        symbol="ASELS",
        as_of="2026-01-02 12:00:00",
        current_price=101.0,
        structure=structure,
        order_block_behavior=None,
        fvg_engulfing_lifecycle=lifecycle,
        pattern_behavior=None,
        participation_behavior=None,
        volatility_environment=None,
        targeting=targeting,
        stabil_support=(
            None
            if stabil_interaction is None
            else _stabil(stabil_interaction, quality=stabil_quality)
        ),
    )


def _assess(snapshot, native):
    return assess_st_long_transition(
        snapshot,
        native,
        opportunity_calibration=OpportunityCalibration(
            none_max_atr=0.5,
            compressed_max_atr=1.0,
            moderate_max_atr=2.0,
        ),
    )


def test_bearish_intact_with_bullish_reaction_is_watch_only() -> None:
    snapshot = _snapshot(_event("EVENT_CHOCH", 1, at="2026-01-02 11:00:00"))

    result = _assess(snapshot, _native(transitioning=False))

    assert result.state is STTransitionState.WATCH
    assert not result.can_own_trade_thesis


def test_canonical_transition_with_current_choch_and_ready_reaction_is_strong() -> None:
    snapshot = _snapshot(_event("EVENT_CHOCH", 1, at="2026-01-02 11:00:00"))
    native = _native(transitioning=True)

    result = _assess(snapshot, native)
    overlay = apply_strong_st_long_transition(native, result)

    assert result.state is STTransitionState.STRONG
    assert result.current_bullish_choch
    assert result.timing.state.value == "READY"
    assert result.opportunity.state.value == "AMPLE"
    assert overlay.direction is StructuralDirection.LONG
    assert overlay.thesis_state is ThesisState.INTACT
    assert overlay.native_state == "STATE_TRANSITION_UP"
    assert "DECISION_ST_TRANSITION_LONG_OVERLAY" in overlay.reasons


def test_stabil_recovery_can_own_transition_before_native_structure_transitions() -> None:
    snapshot = _snapshot(
        _event("EVENT_CHOCH", 1, at="2026-01-02 11:00:00"),
        stabil_interaction="RECOVERY_CONFIRMED",
    )
    native = _native(transitioning=False)

    result = _assess(snapshot, native)
    overlay = apply_strong_st_long_transition(native, result)

    assert not result.canonical_transition_up
    assert result.stabil.recovery_confirmed
    assert result.state is STTransitionState.STRONG
    assert overlay.direction is StructuralDirection.LONG
    assert overlay.thesis_state is ThesisState.INTACT
    assert overlay.native_state == "STATE_BEARISH"
    assert "NATIVE_1H_STRUCTURE_REMAINS_BEARISH_INTACT" in overlay.reasons
    assert "STABIL_RECOVERY_CONFIRMED_EARLY_LONG_AUTHORITY" in overlay.reasons


def test_data_limited_stabil_recovery_can_own_transition_when_other_evidence_confirms() -> None:
    snapshot = _snapshot(
        _event("EVENT_CHOCH", 1, at="2026-01-02 11:00:00"),
        stabil_interaction="RECOVERY_CONFIRMED",
        stabil_quality=ContextDataQuality.DATA_LIMITED,
    )

    result = _assess(snapshot, _native(transitioning=False))

    assert result.stabil.data_quality is ContextDataQuality.DATA_LIMITED
    assert result.stabil.recovery_confirmed
    assert result.state is STTransitionState.STRONG
    assert result.can_own_trade_thesis


def test_bearish_stabil_blocks_even_canonical_bullish_transition_overlay() -> None:
    snapshot = _snapshot(
        _event("EVENT_CHOCH", 1, at="2026-01-02 11:00:00"),
        stabil_interaction="DOWNSIDE_CONTINUATION",
    )
    native = _native(transitioning=True)

    result = _assess(snapshot, native)

    assert result.canonical_transition_up
    assert result.stabil.breakdown_confirmed
    assert result.state is not STTransitionState.STRONG
    assert not result.can_own_trade_thesis
    assert "STABIL_BEARISH_AUTHORITY_OPPOSES_EARLY_LONG" in result.blockers


def test_newer_bearish_bos_supersedes_older_bullish_choch() -> None:
    snapshot = _snapshot(
        _event("EVENT_CHOCH", 1, at="2026-01-02 10:00:00"),
        _event("EVENT_BOS", -1, at="2026-01-02 11:00:00"),
    )

    result = _assess(snapshot, _native(transitioning=True))

    assert not result.current_bullish_choch
    assert result.state is not STTransitionState.STRONG
    assert not result.can_own_trade_thesis


def test_strong_transition_reconciles_waiting_permission_to_conditional_long() -> None:
    snapshot = _snapshot(_event("EVENT_CHOCH", 1, at="2026-01-02 11:00:00"))
    transition = _assess(snapshot, _native(transitioning=True))
    permission = PermissionEnvelope(
        scope=PermissionScope.STRUCTURAL_TRANSITION,
        permitted_side=PermittedSide.LONG,
        gate_state=GateState.WAITING,
        allowed_reasons=("CANONICAL_STRUCTURAL_TRANSITION_CANDIDATE",),
        waiting_for=("CANONICAL_STRUCTURAL_FOLLOW_THROUGH",),
    )

    result = reconcile_st_transition_permission(permission, transition)

    assert transition.state is STTransitionState.STRONG
    assert result.gate_state is GateState.CONDITIONAL
    assert result.permitted_side is PermittedSide.LONG
    assert "DECISION_ST_STRONG_TRANSITION_LONG" in result.allowed_reasons


def test_hard_permission_block_is_never_bypassed() -> None:
    snapshot = _snapshot(_event("EVENT_CHOCH", 1, at="2026-01-02 11:00:00"))
    transition = _assess(snapshot, _native(transitioning=True))
    permission = PermissionEnvelope(
        scope=PermissionScope.NONE,
        permitted_side=PermittedSide.NONE,
        gate_state=GateState.BLOCKED,
        blocking_reasons=("CONTEXT_CONFLICT_HIGH",),
    )

    result = reconcile_st_transition_permission(permission, transition)

    assert result is permission
    assert result.gate_state is GateState.BLOCKED
