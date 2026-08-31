from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING, Any, Iterable

from financial_dashboard.context.envelope import ContextDataQuality, FactRef

from .composer import DecisionAction
from .execution import ExecutionTriggerEvent
from .lifecycle import (
    ExitStage,
    PositionState,
    TradeLifecycleState,
    TradeLifecycleTransition,
    transition_trade_lifecycle,
)
from .st_bearish_reversal import (
    STBearishReversalAssessment,
    assess_st_bearish_reversal,
    refine_short_term_exit_with_bearish_reversal,
)
from .stabil_authority import (
    StabilDecisionAssessment,
    StabilDecisionState,
    assess_stabil_authority,
)
from .structural import (
    DecisionHorizon,
    HorizonStructuralSnapshot,
    StructuralDirection,
    ThesisState,
    build_horizon_structural_snapshot,
)
from .trade_exit import (
    ExitExecutionState,
    LongExitAssessment,
    LongExitExecutionAssessment,
    PositionHealth,
    arm_open_long_on_30m_short,
    assess_long_exit_execution,
    assess_long_position_exit,
    exit_click_event,
)

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot


_LT_PEAK_GIVEBACK_ARM_PCT = 4.0
_LT_PEAK_GIVEBACK_HARD_EXIT_PCT = 5.0


@dataclass(frozen=True, slots=True)
class PositionExitDecision:
    action: DecisionAction
    as_of: Any
    entry_horizon: DecisionHorizon | None
    stage: ExitStage
    position_health: PositionHealth
    structural: LongExitAssessment
    execution: LongExitExecutionAssessment
    execution_event_consumed: bool
    reasons: tuple[str, ...]
    waiting_for: tuple[str, ...]
    source_refs: tuple[FactRef, ...]
    source_lineage: tuple[str, ...]
    position_peak_price: float | None = None
    peak_giveback_pct: float | None = None
    risk_cap_exit: bool = False

    def __post_init__(self) -> None:
        if self.action not in {DecisionAction.HOLD, DecisionAction.SELL}:
            raise ValueError("position exit decision may emit only HOLD or SELL")
        if self.as_of is None:
            raise ValueError("position exit decision as_of must be known")
        if self.action is DecisionAction.SELL:
            if self.stage is not ExitStage.EXIT_READY:
                raise ValueError("SELL requires EXIT_READY")
            if self.execution.state is not ExitExecutionState.CONFIRMED:
                raise ValueError("SELL requires CONFIRMED exit execution")
            if not self.risk_cap_exit and not self.execution_event_consumed:
                raise ValueError("SELL requires a consumed fresh exit execution event")
        elif self.execution.state is ExitExecutionState.CONFIRMED:
            raise ValueError("CONFIRMED exit execution must resolve to SELL")
        if self.execution_event_consumed and self.stage is not ExitStage.EXIT_READY:
            raise ValueError("exit execution event may be consumed only while EXIT_READY")
        if self.risk_cap_exit:
            if self.entry_horizon is not DecisionHorizon.LONG_TERM:
                raise ValueError("peak giveback hard exit belongs only to long-term positions")
            if self.action is not DecisionAction.SELL:
                raise ValueError("peak giveback hard exit must resolve to SELL")
            if self.peak_giveback_pct is None or self.peak_giveback_pct < _LT_PEAK_GIVEBACK_HARD_EXIT_PCT:
                raise ValueError("peak giveback hard exit requires the hard threshold")


def _dedup(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _canonical_refs(refs: Iterable[FactRef]) -> tuple[FactRef, ...]:
    values = {ref.deterministic_key: ref for ref in refs}
    return tuple(sorted(values.values(), key=lambda ref: ref.deterministic_key))


def _lineage_from_refs(refs: Iterable[FactRef]) -> tuple[str, ...]:
    values: list[str] = []
    for ref in refs:
        lineage_id = getattr(ref, "lineage_id", None)
        if lineage_id:
            values.append(str(lineage_id))
            continue
        domain = getattr(getattr(ref, "domain", None), "value", None)
        timeframe = getattr(ref, "timeframe", None)
        native_id = getattr(ref, "native_id", None)
        if domain and timeframe and native_id:
            values.append(f"{domain}:{timeframe}:{native_id}")
    return tuple(sorted(set(values)))


def _position_peak_and_giveback(
    state: TradeLifecycleState,
    current_price: float | None,
) -> tuple[float | None, float | None]:
    values: list[float] = []
    if state.peak_price is not None:
        values.append(float(state.peak_price))
    if state.entry_metadata is not None:
        values.append(float(state.entry_metadata.entry_price))

    price: float | None = None
    if current_price is not None:
        price = float(current_price)
        if not isfinite(price) or price <= 0.0:
            raise ValueError("position exit current price must be finite and positive")
        values.append(price)

    peak = max(values) if values else None
    if peak is None or price is None:
        return peak, None
    giveback = max(0.0, (peak - price) / peak * 100.0)
    return peak, giveback


def _apply_long_term_peak_giveback_protection(
    assessment: LongExitAssessment,
    giveback_pct: float | None,
) -> LongExitAssessment:
    if giveback_pct is None or giveback_pct < _LT_PEAK_GIVEBACK_ARM_PCT:
        return assessment

    refs = assessment.source_refs
    if giveback_pct >= _LT_PEAK_GIVEBACK_HARD_EXIT_PCT:
        return LongExitAssessment(
            ExitStage.EXIT_READY,
            PositionHealth.PRESSURED,
            _dedup((*assessment.reasons, "LT_PEAK_GIVEBACK_HARD_CAP_REACHED")),
            (),
            refs,
        )

    if assessment.stage is ExitStage.EXIT_READY:
        return LongExitAssessment(
            ExitStage.EXIT_READY,
            assessment.position_health,
            _dedup((*assessment.reasons, "LT_PEAK_GIVEBACK_CONFIRMATION_ZONE")),
            assessment.waiting_for,
            refs,
        )

    return LongExitAssessment(
        ExitStage.EXIT_READY,
        PositionHealth.PRESSURED,
        _dedup((*assessment.reasons, "LT_PEAK_GIVEBACK_CONFIRMATION_ZONE")),
        ("FRESH_LONG_EXIT_EXECUTION_EVENT",),
        refs,
    )


def _short_term_position_exit(snapshot: HorizonStructuralSnapshot) -> LongExitAssessment:
    st = snapshot.short_term
    refs = _canonical_refs(st.source_refs)
    if st.data_quality is not ContextDataQuality.VALID:
        return LongExitAssessment(
            ExitStage.EXIT_WATCH, PositionHealth.UNKNOWN,
            (f"ST_STRUCTURE_DATA_{st.data_quality.value}",),
            ("ST_STRUCTURE_AUTHORITY_TO_RECOVER",), refs,
        )
    if st.thesis_state is ThesisState.INVALIDATED:
        return LongExitAssessment(
            ExitStage.EXIT_READY, PositionHealth.PRESSURED,
            ("ST_LONG_THESIS_INVALIDATED",), ("FRESH_LONG_EXIT_EXECUTION_EVENT",), refs,
        )
    if st.direction is StructuralDirection.UNRESOLVED or st.thesis_state is ThesisState.UNRESOLVED:
        return LongExitAssessment(
            ExitStage.EXIT_WATCH, PositionHealth.UNKNOWN,
            ("ST_STRUCTURE_UNRESOLVED_FOR_OPEN_LONG",),
            ("ST_STRUCTURE_AUTHORITY_TO_RESOLVE",), refs,
        )
    if st.direction is StructuralDirection.SHORT and st.thesis_state is ThesisState.INTACT:
        return LongExitAssessment(
            ExitStage.EXIT_READY, PositionHealth.PRESSURED,
            ("ST_BEARISH_THESIS_ESTABLISHED_AGAINST_ST_POSITION",),
            ("FRESH_LONG_EXIT_EXECUTION_EVENT",), refs,
        )
    if (
        st.direction is StructuralDirection.LONG
        and st.thesis_state is ThesisState.TRANSITIONING
        and st.transition_target is StructuralDirection.SHORT
    ):
        return LongExitAssessment(
            ExitStage.EXIT_READY, PositionHealth.PRESSURED,
            ("ST_LONG_THESIS_TRANSITIONING_TOWARD_SHORT",),
            ("FRESH_LONG_EXIT_EXECUTION_EVENT",), refs,
        )
    if st.direction is StructuralDirection.SHORT and st.thesis_state is ThesisState.TRANSITIONING:
        return LongExitAssessment(
            ExitStage.EXIT_WATCH, PositionHealth.PRESSURED,
            ("ST_ESTABLISHED_SIDE_SHORT_BUT_TRANSITIONING",),
            ("ST_TRANSITION_TO_RESOLVE",), refs,
        )
    if st.direction is StructuralDirection.LONG and st.thesis_state is ThesisState.INTACT:
        return LongExitAssessment(
            ExitStage.MONITOR, PositionHealth.HEALTHY,
            ("ST_LONG_THESIS_INTACT",), (), refs,
        )
    return LongExitAssessment(
        ExitStage.EXIT_WATCH, PositionHealth.UNKNOWN,
        ("OPEN_ST_LONG_EXIT_STATE_NOT_CANONICALLY_CLASSIFIED",),
        ("CANONICAL_ST_STRUCTURE_STATE",), refs,
    )


def refine_short_term_exit_with_stabil(
    assessment: LongExitAssessment,
    st,
    stabil: StabilDecisionAssessment | None,
) -> LongExitAssessment:
    """Use Stabil as exit context/confirmation, never as an exit permission gate."""

    if stabil is None or not stabil.usable:
        return assessment

    refs = _canonical_refs((*assessment.source_refs, *stabil.source_refs))

    if assessment.stage is ExitStage.EXIT_READY:
        if stabil.breakdown_confirmed:
            reason = f"STABIL_CONFIRMS_ST_EXIT:{stabil.state.value}"
        elif stabil.breakdown_developing:
            reason = f"STABIL_SUPPORTS_ST_EXIT_DEVELOPING:{stabil.state.value}"
        elif stabil.state is StabilDecisionState.BULLISH_SOFTENING:
            reason = "STABIL_FOUNDATION_NOT_ADVANCING_WITH_ST_EXIT"
        elif stabil.state in {
            StabilDecisionState.BULLISH_PROGRESS,
            StabilDecisionState.BULLISH_SUPPORTED,
            StabilDecisionState.RECOVERY_CONFIRMED,
        }:
            reason = f"STABIL_STILL_SUPPORTIVE_BUT_NO_EXIT_VETO:{stabil.state.value}"
        else:
            reason = f"STABIL_NEUTRAL_CONTEXT_NO_EXIT_VETO:{stabil.state.value}"
        return LongExitAssessment(
            ExitStage.EXIT_READY,
            assessment.position_health,
            _dedup((*assessment.reasons, reason)),
            assessment.waiting_for,
            refs,
        )

    if stabil.breakdown_confirmed:
        return LongExitAssessment(
            ExitStage.EXIT_WATCH,
            PositionHealth.PRESSURED,
            _dedup((*assessment.reasons, f"STABIL_BREAKDOWN_AHEAD_OF_STRUCTURE:{stabil.state.value}")),
            _dedup((*assessment.waiting_for, "ST_STRUCTURE_DETERIORATION")),
            refs,
        )

    if stabil.breakdown_developing:
        return LongExitAssessment(
            ExitStage.EXIT_WATCH,
            PositionHealth.PRESSURED,
            _dedup((*assessment.reasons, "STABIL_BREAKDOWN_DEVELOPING")),
            _dedup((*assessment.waiting_for, "ST_STRUCTURE_DETERIORATION")),
            refs,
        )

    if stabil.state is StabilDecisionState.BULLISH_SOFTENING:
        return LongExitAssessment(
            ExitStage.EXIT_WATCH,
            PositionHealth.PRESSURED,
            _dedup((*assessment.reasons, "STABIL_BULLISH_FOUNDATION_SOFTENING")),
            _dedup((*assessment.waiting_for, "ST_STRUCTURE_DETERIORATION")),
            refs,
        )

    return LongExitAssessment(
        assessment.stage,
        assessment.position_health,
        _dedup((*assessment.reasons, f"STABIL_POSITION_CONTEXT:{stabil.state.value}")),
        assessment.waiting_for,
        refs,
    )


def _missing_entry_metadata_exit(snapshot: HorizonStructuralSnapshot) -> LongExitAssessment:
    refs = _canonical_refs((*snapshot.long_term.source_refs, *snapshot.short_term.source_refs))
    return LongExitAssessment(
        ExitStage.EXIT_WATCH, PositionHealth.UNKNOWN,
        ("POSITION_ENTRY_HORIZON_UNAVAILABLE",),
        ("POSITION_ENTRY_METADATA_TO_RECOVER",), refs,
    )


def compose_position_exit_decision(
    state: TradeLifecycleState,
    structural_snapshot: HorizonStructuralSnapshot,
    *,
    as_of: Any,
    execution_event: ExecutionTriggerEvent | None = None,
    channel_available: bool = True,
    stabil: StabilDecisionAssessment | None = None,
    short_term_reversal: STBearishReversalAssessment | None = None,
    current_price: float | None = None,
) -> PositionExitDecision:
    if state.position is not PositionState.OPEN:
        raise ValueError("position exit decision requires OPEN lifecycle ownership")
    if as_of is None:
        raise ValueError("position exit decision as_of must be known")

    position_peak_price, peak_giveback_pct = _position_peak_and_giveback(state, current_price)
    metadata = state.entry_metadata
    risk_cap_exit = False

    if metadata is None:
        entry_horizon = None
        structural = _missing_entry_metadata_exit(structural_snapshot)
        authority_reason = "POSITION_EXIT_AUTHORITY_UNRESOLVED"
    elif metadata.entry_horizon is DecisionHorizon.LONG_TERM:
        entry_horizon = DecisionHorizon.LONG_TERM
        structural = assess_long_position_exit(structural_snapshot)
        structural = _apply_long_term_peak_giveback_protection(structural, peak_giveback_pct)
        risk_cap_exit = bool(
            peak_giveback_pct is not None
            and peak_giveback_pct >= _LT_PEAK_GIVEBACK_HARD_EXIT_PCT
        )
        authority_reason = "POSITION_EXIT_AUTHORITY_LONG_TERM_ENTRY"
    elif metadata.entry_horizon is DecisionHorizon.SHORT_TERM:
        entry_horizon = DecisionHorizon.SHORT_TERM
        structural = _short_term_position_exit(structural_snapshot)
        structural = refine_short_term_exit_with_bearish_reversal(
            structural,
            short_term_reversal,
        )
        structural = refine_short_term_exit_with_stabil(
            structural,
            structural_snapshot.short_term,
            stabil,
        )
        authority_reason = "POSITION_EXIT_AUTHORITY_SHORT_TERM_ENTRY"
    else:
        raise ValueError("unsupported position entry horizon")

    click = exit_click_event(execution_event)
    structural = arm_open_long_on_30m_short(
        structural, as_of=as_of, event=click, allow=metadata is not None,
    )
    armed = structural.stage is ExitStage.EXIT_READY

    if risk_cap_exit:
        execution = LongExitExecutionAssessment(
            ExitExecutionState.CONFIRMED,
            ("LT_PEAK_GIVEBACK_HARD_CAP_EXECUTION",),
            (),
            (),
        )
        consumed = False
        action = DecisionAction.SELL
    else:
        event_for_execution = click if armed else None
        execution = assess_long_exit_execution(
            structural,
            as_of=as_of,
            event=event_for_execution,
            execution_timeframe="1h",
            channel_available=channel_available,
        )
        consumed = armed and click is not None
        action = DecisionAction.SELL if execution.state is ExitExecutionState.CONFIRMED else DecisionAction.HOLD

    refs = _canonical_refs((*structural.source_refs, *execution.source_refs))
    return PositionExitDecision(
        action=action,
        as_of=as_of,
        entry_horizon=entry_horizon,
        stage=structural.stage,
        position_health=structural.position_health,
        structural=structural,
        execution=execution,
        execution_event_consumed=consumed,
        reasons=_dedup((authority_reason, *structural.reasons, *execution.reasons)),
        waiting_for=_dedup((*structural.waiting_for, *execution.waiting_for)),
        source_refs=refs,
        source_lineage=_lineage_from_refs(refs),
        position_peak_price=position_peak_price,
        peak_giveback_pct=peak_giveback_pct,
        risk_cap_exit=risk_cap_exit,
    )


def assess_position_exit_decision(
    snapshot: "DecisionInputSnapshot",
    state: TradeLifecycleState,
    *,
    execution_event: ExecutionTriggerEvent | None = None,
) -> PositionExitDecision:
    if snapshot.as_of is None:
        raise ValueError("position exit snapshot as_of must be known")
    if state.entry_metadata is not None and state.entry_metadata.symbol != snapshot.symbol:
        raise ValueError("position entry metadata symbol must match exit snapshot symbol")

    from .engine import _decision_structure_projection, _execution_channel_quality

    structural_snapshot = build_horizon_structural_snapshot(_decision_structure_projection(snapshot.structure))
    channel_available = _execution_channel_quality(snapshot, "1h") is ContextDataQuality.VALID
    stabil = assess_stabil_authority(getattr(snapshot, "stabil_support", None))
    short_term_reversal = None
    if (
        state.entry_metadata is not None
        and state.entry_metadata.entry_horizon is DecisionHorizon.SHORT_TERM
    ):
        short_term_reversal = assess_st_bearish_reversal(
            snapshot,
            structural_snapshot.short_term,
        )
    return compose_position_exit_decision(
        state,
        structural_snapshot,
        as_of=snapshot.as_of,
        execution_event=execution_event,
        channel_available=channel_available,
        stabil=stabil,
        short_term_reversal=short_term_reversal,
        current_price=float(snapshot.current_price),
    )


def transition_position_exit_lifecycle(
    state: TradeLifecycleState,
    decision: PositionExitDecision,
) -> TradeLifecycleTransition:
    if state.position is not PositionState.OPEN:
        raise ValueError("position exit lifecycle transition requires OPEN state")
    if state.entry_metadata is not None and decision.entry_horizon is not state.entry_metadata.entry_horizon:
        raise ValueError("exit decision entry horizon must match frozen position metadata")
    if state.entry_metadata is None and decision.entry_horizon is not None:
        raise ValueError("metadata-less position cannot acquire an exit horizon later")

    return transition_trade_lifecycle(
        state,
        decision,
        as_of=decision.as_of,
        exit_stage=decision.stage,
        exit_execution_confirmed=decision.action is DecisionAction.SELL,
        position_peak_price=decision.position_peak_price,
    )


__all__ = [
    "PositionExitDecision",
    "assess_position_exit_decision",
    "compose_position_exit_decision",
    "refine_short_term_exit_with_stabil",
    "transition_position_exit_lifecycle",
]
