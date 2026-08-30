from __future__ import annotations

from dataclasses import dataclass
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
            if not self.execution_event_consumed:
                raise ValueError("SELL requires a consumed fresh exit execution event")
        elif self.execution.state is ExitExecutionState.CONFIRMED:
            raise ValueError("CONFIRMED exit execution must resolve to SELL")
        if self.execution_event_consumed and self.stage is not ExitStage.EXIT_READY:
            raise ValueError("exit execution event may be consumed only while EXIT_READY")


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
    """Make ST long exits Stabil-aware without turning Stabil into a SELL signal.

    Invalidated Structure remains sufficient to arm an exit.  Otherwise, an intact
    bearish or transition-down 1H Structure needs confirmed Stabil breakdown before
    EXIT_READY.  Stabil deterioration without Structure deterioration can only raise
    EXIT_WATCH, so a single daily support event can never directly sell the position.
    """

    if stabil is None or stabil.data_quality is not ContextDataQuality.VALID:
        return assessment
    if stabil.state is StabilDecisionState.UNKNOWN:
        return assessment

    refs = _canonical_refs((*assessment.source_refs, *stabil.source_refs))
    if st.thesis_state is ThesisState.INVALIDATED:
        return LongExitAssessment(
            assessment.stage,
            assessment.position_health,
            _dedup((*assessment.reasons, "STABIL_OBSERVED_AFTER_STRUCTURE_INVALIDATION")),
            assessment.waiting_for,
            refs,
        )

    structural_deterioration = bool(
        (st.direction is StructuralDirection.SHORT and st.thesis_state is ThesisState.INTACT)
        or (
            st.direction is StructuralDirection.LONG
            and st.thesis_state is ThesisState.TRANSITIONING
            and st.transition_target is StructuralDirection.SHORT
        )
    )

    if structural_deterioration:
        if stabil.breakdown_confirmed:
            return LongExitAssessment(
                ExitStage.EXIT_READY,
                PositionHealth.PRESSURED,
                _dedup((*assessment.reasons, f"STABIL_CONFIRMS_ST_EXIT:{stabil.state.value}")),
                ("FRESH_LONG_EXIT_EXECUTION_EVENT",),
                refs,
            )
        return LongExitAssessment(
            ExitStage.EXIT_WATCH,
            PositionHealth.PRESSURED,
            _dedup((*assessment.reasons, f"STABIL_NOT_YET_CONFIRMING_ST_EXIT:{stabil.state.value}")),
            ("STABIL_BREAKDOWN_CONFIRMATION",),
            refs,
        )

    if stabil.breakdown_confirmed:
        return LongExitAssessment(
            ExitStage.EXIT_WATCH,
            PositionHealth.PRESSURED,
            _dedup((*assessment.reasons, f"STABIL_BREAKDOWN_AHEAD_OF_STRUCTURE:{stabil.state.value}")),
            ("ST_STRUCTURE_DETERIORATION",),
            refs,
        )

    if stabil.breakdown_developing:
        return LongExitAssessment(
            ExitStage.EXIT_WATCH,
            PositionHealth.PRESSURED,
            _dedup((*assessment.reasons, "STABIL_BREAKDOWN_DEVELOPING")),
            ("STABIL_BREAKDOWN_TO_RESOLVE",),
            refs,
        )

    if stabil.state is StabilDecisionState.BULLISH_SOFTENING:
        return LongExitAssessment(
            ExitStage.EXIT_WATCH,
            PositionHealth.PRESSURED,
            _dedup((*assessment.reasons, "STABIL_BULLISH_FOUNDATION_SOFTENING")),
            ("STABIL_FOUNDATION_TO_RESOLVE",),
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
) -> PositionExitDecision:
    if state.position is not PositionState.OPEN:
        raise ValueError("position exit decision requires OPEN lifecycle ownership")
    if as_of is None:
        raise ValueError("position exit decision as_of must be known")

    metadata = state.entry_metadata
    if metadata is None:
        entry_horizon = None
        structural = _missing_entry_metadata_exit(structural_snapshot)
        authority_reason = "POSITION_EXIT_AUTHORITY_UNRESOLVED"
    elif metadata.entry_horizon is DecisionHorizon.LONG_TERM:
        entry_horizon = DecisionHorizon.LONG_TERM
        structural = assess_long_position_exit(structural_snapshot)
        authority_reason = "POSITION_EXIT_AUTHORITY_LONG_TERM_ENTRY"
    elif metadata.entry_horizon is DecisionHorizon.SHORT_TERM:
        entry_horizon = DecisionHorizon.SHORT_TERM
        structural = _short_term_position_exit(structural_snapshot)
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
    return compose_position_exit_decision(
        state,
        structural_snapshot,
        as_of=snapshot.as_of,
        execution_event=execution_event,
        channel_available=channel_available,
        stabil=stabil,
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
    )


__all__ = [
    "PositionExitDecision",
    "assess_position_exit_decision",
    "compose_position_exit_decision",
    "refine_short_term_exit_with_stabil",
    "transition_position_exit_lifecycle",
]
