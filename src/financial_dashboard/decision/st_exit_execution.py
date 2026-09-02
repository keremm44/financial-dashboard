from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from .execution import ExecutionTriggerEvent
from .lifecycle import PositionState, TradeLifecycleState
from .st_exit_intent import STExitFamily
from .st_exit_policy import STCanonicalExitAssessment, as_long_exit_assessment
from .structural import DecisionHorizon
from .trade_exit import (
    ExitExecutionState,
    LongExitExecutionAssessment,
    assess_long_exit_execution,
)


class STExitExecutionUrgency(StrEnum):
    """Step-9 execution meaning after the economic ST exit family is already terminal."""

    NOT_ARMED = "NOT_ARMED"
    HARVEST_QUALITY_WINDOW = "HARVEST_QUALITY_WINDOW"
    HARVEST_RELEASE_DUE = "HARVEST_RELEASE_DUE"
    PROTECTIVE_IMMEDIATE = "PROTECTIVE_IMMEDIATE"


@dataclass(frozen=True, slots=True)
class STExitExecutionAssessment:
    urgency: STExitExecutionUrgency
    execution: LongExitExecutionAssessment
    execution_event_consumed: bool
    policy_mandated: bool = False

    def __post_init__(self) -> None:
        if self.policy_mandated:
            if self.execution_event_consumed:
                raise ValueError("policy-mandated ST exit cannot consume a timing event")
            if self.execution.waiting_for:
                raise ValueError("policy-mandated ST exit cannot wait for timing confirmation")


def _policy_mandated(reason: str, urgency: STExitExecutionUrgency) -> STExitExecutionAssessment:
    return STExitExecutionAssessment(
        urgency=urgency,
        execution=LongExitExecutionAssessment(
            state=ExitExecutionState.ABSENT,
            reasons=(reason,),
            waiting_for=(),
            source_refs=(),
        ),
        execution_event_consumed=False,
        policy_mandated=True,
    )


def assess_st_exit_execution(
    state: TradeLifecycleState,
    economic: STCanonicalExitAssessment,
    *,
    as_of: Any,
    event: ExecutionTriggerEvent | None,
    channel_available: bool,
) -> STExitExecutionAssessment:
    """Apply Step-9 urgency without reclassifying the economic exit family.

    PROTECTIVE exits are policy-mandated immediately and never validate or consume a
    new Timing/execution event. PROFIT_HARVEST receives one causal exit-quality
    opportunity on the decision cycle where terminal intent is first committed. If
    that opportunity does not execute, any later causal decision releases the trade
    without requiring a new confirmation. The durable ``intent.committed_at`` value
    makes that boundary restart-deterministic without a fixed day/bar timeout.
    """

    if state.position is not PositionState.OPEN:
        raise ValueError("ST exit execution urgency requires OPEN lifecycle ownership")
    metadata = state.entry_metadata
    if metadata is None or metadata.entry_horizon is not DecisionHorizon.SHORT_TERM:
        raise ValueError("ST exit execution urgency requires short-term entry ownership")
    if as_of is None:
        raise ValueError("ST exit execution urgency as_of must be known")

    if economic.exit_family is None:
        execution = assess_long_exit_execution(
            as_long_exit_assessment(economic),
            as_of=as_of,
            event=None,
            channel_available=channel_available,
        )
        return STExitExecutionAssessment(
            urgency=STExitExecutionUrgency.NOT_ARMED,
            execution=execution,
            execution_event_consumed=False,
        )

    if economic.exit_family is STExitFamily.PROTECTIVE_EXIT:
        return _policy_mandated(
            "ST_PROTECTIVE_EXIT_EXECUTION_IMMEDIATE",
            STExitExecutionUrgency.PROTECTIVE_IMMEDIATE,
        )

    if economic.exit_family is not STExitFamily.PROFIT_HARVEST:
        raise ValueError("unsupported terminal ST exit family")

    intent = state.st_exit_intent
    if intent is not None:
        if intent.family is STExitFamily.PROTECTIVE_EXIT:
            raise ValueError("harvest execution cannot downgrade persisted protective intent")
        try:
            if as_of < intent.committed_at:
                raise ValueError("harvest execution cannot predate persisted terminal intent")
        except TypeError as exc:
            raise TypeError("harvest execution timestamps must be comparable") from exc
        if as_of > intent.committed_at:
            return _policy_mandated(
                "ST_PROFIT_HARVEST_BOUNDED_PATIENCE_EXHAUSTED",
                STExitExecutionUrgency.HARVEST_RELEASE_DUE,
            )

    execution = assess_long_exit_execution(
        as_long_exit_assessment(economic),
        as_of=as_of,
        event=event,
        channel_available=channel_available,
    )
    consumed = (
        event is not None
        and execution.state in {ExitExecutionState.CONFIRMED, ExitExecutionState.FAILED}
    )
    if execution.state is not ExitExecutionState.CONFIRMED:
        execution = replace(
            execution,
            reasons=("ST_PROFIT_HARVEST_EXIT_QUALITY_WINDOW", *execution.reasons),
            waiting_for=("HARVEST_EXIT_QUALITY_WINDOW",),
        )
    return STExitExecutionAssessment(
        urgency=STExitExecutionUrgency.HARVEST_QUALITY_WINDOW,
        execution=execution,
        execution_event_consumed=consumed,
    )


__all__ = [
    "STExitExecutionAssessment",
    "STExitExecutionUrgency",
    "assess_st_exit_execution",
]
