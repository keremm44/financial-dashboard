from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Iterable

from financial_dashboard.context.envelope import ContextDataQuality, FactRef

from .composer import DecisionAction
from .execution import ExecutionTriggerEvent
from .lifecycle import (
    ExitStage,
    PositionState,
    TradeLifecycleState,
    TradeLifecycleTransition,
    transition_st_exit_intent,
    transition_trade_lifecycle,
)
from .st_exit_intent import STExitFamily
from .st_exit_policy import (
    STCanonicalExitAssessment,
    as_long_exit_assessment,
    assess_st_canonical_exit,
)
from .structural import (
    DecisionHorizon,
    HorizonStructuralSnapshot,
    build_horizon_structural_snapshot,
)
from .trade_exit import (
    ExitExecutionState,
    LongExitAssessment,
    LongExitExecutionAssessment,
    PositionHealth,
    assess_long_exit_execution,
    assess_long_position_exit,
)

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot


@dataclass(frozen=True, slots=True)
class PositionExitDecision:
    """Final action layer for one already-open long position.

    LT ownership continues to use its existing structural exit authority. ST ownership
    uses the Step-8 thesis-aware economic policy. A terminal ST economic outcome arms
    the existing EXIT_READY execution path; Step 9 has not yet changed how urgently
    that path executes, so SELL still requires the current fresh 30m exit event.
    """

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
    economic_exit_family: STExitFamily | None = None
    economic_reasons: tuple[str, ...] = ()
    economic_source_lineage: tuple[str, ...] = ()

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
        if self.economic_exit_family is not None:
            if self.entry_horizon is not DecisionHorizon.SHORT_TERM:
                raise ValueError("terminal economic exit family may belong only to an ST position")
            if self.stage is not ExitStage.EXIT_READY:
                raise ValueError("terminal ST economic exit must arm EXIT_READY")
            if not self.economic_reasons:
                raise ValueError("terminal ST economic exit requires economic reasons")
        if self.entry_horizon is not DecisionHorizon.SHORT_TERM and (
            self.economic_reasons or self.economic_source_lineage
        ):
            raise ValueError("non-ST position cannot carry ST economic exit evidence")


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


def _missing_entry_metadata_exit(snapshot: HorizonStructuralSnapshot) -> LongExitAssessment:
    refs = _canonical_refs(
        (*snapshot.long_term.source_refs, *snapshot.short_term.source_refs)
    )
    return LongExitAssessment(
        ExitStage.EXIT_WATCH,
        PositionHealth.UNKNOWN,
        ("POSITION_ENTRY_HORIZON_UNAVAILABLE",),
        ("POSITION_ENTRY_METADATA_TO_RECOVER",),
        refs,
    )


def compose_position_exit_decision(
    state: TradeLifecycleState,
    structural_snapshot: HorizonStructuralSnapshot,
    *,
    as_of: Any,
    execution_event: ExecutionTriggerEvent | None = None,
    channel_available: bool = True,
    st_economic_exit: STCanonicalExitAssessment | None = None,
) -> PositionExitDecision:
    """Compose HOLD/SELL for one OPEN position from frozen ownership metadata.

    Premature 30m events are deliberately not passed into the execution validator.
    They are therefore not consumed and cannot be cached for a later EXIT_READY bar.
    ST callers must supply the canonical economic assessment; there is no fallback
    path to the pre-Step-8 Structure-only ST exit engine.
    """

    if state.position is not PositionState.OPEN:
        raise ValueError("position exit decision requires OPEN lifecycle ownership")
    if as_of is None:
        raise ValueError("position exit decision as_of must be known")

    metadata = state.entry_metadata
    economic_exit_family: STExitFamily | None = None
    economic_reasons: tuple[str, ...] = ()
    economic_lineage: tuple[str, ...] = ()

    if metadata is None:
        entry_horizon = None
        structural = _missing_entry_metadata_exit(structural_snapshot)
        authority_reason = "POSITION_EXIT_AUTHORITY_UNRESOLVED"
    elif metadata.entry_horizon is DecisionHorizon.LONG_TERM:
        if st_economic_exit is not None:
            raise ValueError("LT exit cannot consume an ST economic assessment")
        entry_horizon = DecisionHorizon.LONG_TERM
        structural = assess_long_position_exit(structural_snapshot)
        authority_reason = "POSITION_EXIT_AUTHORITY_LONG_TERM_ENTRY"
    elif metadata.entry_horizon is DecisionHorizon.SHORT_TERM:
        if st_economic_exit is None:
            raise ValueError("canonical ST exit requires Step-8 economic assessment")
        entry_horizon = DecisionHorizon.SHORT_TERM
        structural = as_long_exit_assessment(st_economic_exit)
        authority_reason = "POSITION_EXIT_AUTHORITY_SHORT_TERM_ECONOMIC_POLICY"
        economic_exit_family = st_economic_exit.exit_family
        economic_reasons = st_economic_exit.reasons
        economic_lineage = st_economic_exit.source_lineage
    else:
        raise ValueError("unsupported position entry horizon")

    armed = structural.stage is ExitStage.EXIT_READY
    event_for_execution = execution_event if armed else None
    execution = assess_long_exit_execution(
        structural,
        as_of=as_of,
        event=event_for_execution,
        channel_available=channel_available,
    )
    consumed = armed and execution_event is not None
    action = (
        DecisionAction.SELL
        if execution.state is ExitExecutionState.CONFIRMED
        else DecisionAction.HOLD
    )

    refs = _canonical_refs((*structural.source_refs, *execution.source_refs))
    lineage = tuple(sorted(set((*_lineage_from_refs(refs), *economic_lineage))))
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
        source_lineage=lineage,
        economic_exit_family=economic_exit_family,
        economic_reasons=economic_reasons,
        economic_source_lineage=economic_lineage,
    )


def assess_position_exit_decision(
    snapshot: "DecisionInputSnapshot",
    state: TradeLifecycleState,
    *,
    execution_event: ExecutionTriggerEvent | None = None,
) -> PositionExitDecision:
    """Build the causal canonical exit decision from one frozen market snapshot."""

    if snapshot.as_of is None:
        raise ValueError("position exit snapshot as_of must be known")
    if state.entry_metadata is not None and state.entry_metadata.symbol != snapshot.symbol:
        raise ValueError("position entry metadata symbol must match exit snapshot symbol")

    structural_snapshot = build_horizon_structural_snapshot(snapshot.structure)
    st_economic_exit = None
    if (
        state.entry_metadata is not None
        and state.entry_metadata.entry_horizon is DecisionHorizon.SHORT_TERM
    ):
        st_economic_exit = assess_st_canonical_exit(snapshot, state)

    channel_available = snapshot.quality_for_timeframe("30m") is ContextDataQuality.VALID
    return compose_position_exit_decision(
        state,
        structural_snapshot,
        as_of=snapshot.as_of,
        execution_event=execution_event,
        channel_available=channel_available,
        st_economic_exit=st_economic_exit,
    )


def transition_position_exit_lifecycle(
    state: TradeLifecycleState,
    decision: PositionExitDecision,
) -> TradeLifecycleTransition:
    """Atomically fold Step-8 economic intent and the existing execution result."""

    if state.position is not PositionState.OPEN:
        raise ValueError("position exit lifecycle transition requires OPEN state")
    if state.entry_metadata is not None and decision.entry_horizon is not state.entry_metadata.entry_horizon:
        raise ValueError("exit decision entry horizon must match frozen position metadata")
    if state.entry_metadata is None and decision.entry_horizon is not None:
        raise ValueError("metadata-less position cannot acquire an exit horizon later")

    intent_state = state
    if decision.entry_horizon is DecisionHorizon.SHORT_TERM:
        if decision.economic_exit_family is None:
            intent_state = transition_st_exit_intent(state, None)
        else:
            intent_state = transition_st_exit_intent(
                state,
                decision.economic_exit_family,
                as_of=decision.as_of,
                reasons=decision.economic_reasons,
                source_lineage=decision.economic_source_lineage,
            )

    transition = transition_trade_lifecycle(
        intent_state,
        decision,
        as_of=decision.as_of,
        exit_stage=decision.stage,
        exit_execution_confirmed=decision.action is DecisionAction.SELL,
    )
    if intent_state is state:
        return transition
    return replace(transition, previous=state)


__all__ = [
    "PositionExitDecision",
    "assess_position_exit_decision",
    "compose_position_exit_decision",
    "transition_position_exit_lifecycle",
]
