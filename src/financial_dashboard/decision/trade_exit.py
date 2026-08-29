from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from financial_dashboard.context.envelope import ContextDataQuality, FactRef

from .execution import ExecutionTriggerEvent, ExecutionTriggerState
from .lifecycle import ExitStage
from .structural import HorizonRelation, HorizonStructuralSnapshot, StructuralDirection, ThesisState


class PositionHealth(StrEnum):
    """Derived health of one open long position; never structural authority."""

    HEALTHY = "HEALTHY"
    PROTECTED = "PROTECTED"
    PRESSURED = "PRESSURED"
    UNKNOWN = "UNKNOWN"


class ExitExecutionState(StrEnum):
    """Fresh execution state for a long-position exit path."""

    NOT_ARMED = "NOT_ARMED"
    ABSENT = "ABSENT"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class LongExitAssessment:
    """Structural long-exit readiness, separate from a new SHORT-entry thesis.

    Pass 2 deliberately uses only cross-horizon Structure. Supporting-domain
    deterioration may be added later only after historical audit/calibration. This
    prevents arbitrary exit thresholds from entering the lifecycle while fixing the
    category error that treated every bearish market assessment as a long exit.
    """

    stage: ExitStage
    position_health: PositionHealth
    reasons: tuple[str, ...]
    waiting_for: tuple[str, ...]
    source_refs: tuple[FactRef, ...]


@dataclass(frozen=True, slots=True)
class LongExitExecutionAssessment:
    state: ExitExecutionState
    reasons: tuple[str, ...]
    waiting_for: tuple[str, ...]
    source_refs: tuple[FactRef, ...]


def _refs(snapshot: HorizonStructuralSnapshot) -> tuple[FactRef, ...]:
    values = {
        ref.deterministic_key: ref
        for ref in (*snapshot.long_term.source_refs, *snapshot.short_term.source_refs)
    }
    return tuple(sorted(values.values(), key=lambda ref: ref.deterministic_key))


def st_pressures_open_long(st) -> str | None:
    """1H structure that can arm a long exit without waiting for 1D/4H BOS.

    Pullbacks that are already rotating back toward LT LONG are not pressure.
    """

    if st is None:
        return None
    quality = getattr(st, "data_quality", ContextDataQuality.VALID)
    if quality not in {ContextDataQuality.VALID, ContextDataQuality.DATA_LIMITED}:
        return None
    thesis = getattr(st, "thesis_state", None)
    direction = getattr(st, "direction", None)
    target = getattr(st, "transition_target", None)
    if thesis is ThesisState.INVALIDATED:
        return "ST_LONG_THESIS_INVALIDATED"
    if direction is StructuralDirection.SHORT and thesis is ThesisState.INTACT:
        return "ST_BEARISH_THESIS_ESTABLISHED_AGAINST_OPEN_LONG"
    if (
        direction is StructuralDirection.LONG
        and thesis is ThesisState.TRANSITIONING
        and target is StructuralDirection.SHORT
    ):
        return "ST_LONG_THESIS_TRANSITIONING_TOWARD_SHORT"
    return None


def assess_long_position_exit(snapshot: HorizonStructuralSnapshot) -> LongExitAssessment:
    """Classify one open-long exit stage without numeric voting or magic thresholds.

    LT invalidation / established 1D short still arms immediately. An intact LT
    long no longer waits for 1D/4H BOS: established 1H short or 1H transition-down
    arms EXIT_READY. A same-side 1H pullback recovering toward LT stays MONITOR.
    EXIT_READY is not SELL; a fresh 30m SHORT execution event is still required.
    """

    lt = snapshot.long_term
    st = snapshot.short_term
    relation = snapshot.relation
    refs = _refs(snapshot)

    if lt.data_quality is not ContextDataQuality.VALID:
        return LongExitAssessment(
            ExitStage.EXIT_WATCH,
            PositionHealth.UNKNOWN,
            (f"LT_STRUCTURE_DATA_{lt.data_quality.value}",),
            ("LT_STRUCTURE_AUTHORITY_TO_RECOVER",),
            refs,
        )

    if lt.thesis_state is ThesisState.INVALIDATED or relation is HorizonRelation.POST_INVALIDATION:
        return LongExitAssessment(
            ExitStage.EXIT_READY,
            PositionHealth.PRESSURED,
            ("LT_LONG_THESIS_INVALIDATED",),
            ("FRESH_LONG_EXIT_EXECUTION_EVENT",),
            refs,
        )

    if lt.direction is StructuralDirection.UNRESOLVED or lt.thesis_state is ThesisState.UNRESOLVED:
        return LongExitAssessment(
            ExitStage.EXIT_WATCH,
            PositionHealth.UNKNOWN,
            ("LT_STRUCTURE_UNRESOLVED_FOR_OPEN_LONG",),
            ("LT_STRUCTURE_AUTHORITY_TO_RESOLVE",),
            refs,
        )

    if lt.direction is StructuralDirection.SHORT and lt.thesis_state is ThesisState.INTACT:
        return LongExitAssessment(
            ExitStage.EXIT_READY,
            PositionHealth.PRESSURED,
            ("LT_BEARISH_THESIS_ESTABLISHED_AGAINST_OPEN_LONG",),
            ("FRESH_LONG_EXIT_EXECUTION_EVENT",),
            refs,
        )

    st_pressure = st_pressures_open_long(st)
    if st_pressure is not None:
        return LongExitAssessment(
            ExitStage.EXIT_READY,
            PositionHealth.PRESSURED,
            (st_pressure,),
            ("FRESH_LONG_EXIT_EXECUTION_EVENT",),
            refs,
        )

    if (
        lt.direction is StructuralDirection.LONG
        and lt.thesis_state is ThesisState.TRANSITIONING
        and lt.transition_target is StructuralDirection.SHORT
    ):
        return LongExitAssessment(
            ExitStage.EXIT_WATCH,
            PositionHealth.PRESSURED,
            ("LT_LONG_THESIS_TRANSITIONING_TOWARD_SHORT",),
            ("LT_TRANSITION_TO_RESOLVE",),
            refs,
        )

    if lt.direction is StructuralDirection.SHORT and lt.thesis_state is ThesisState.TRANSITIONING:
        return LongExitAssessment(
            ExitStage.EXIT_WATCH,
            PositionHealth.PRESSURED,
            ("LT_ESTABLISHED_SIDE_SHORT_BUT_TRANSITIONING",),
            ("LT_TRANSITION_TO_RESOLVE",),
            refs,
        )

    if lt.direction is StructuralDirection.LONG and lt.thesis_state is ThesisState.INTACT:
        if relation is HorizonRelation.ALIGNED:
            return LongExitAssessment(
                ExitStage.MONITOR,
                PositionHealth.HEALTHY,
                ("LT_LONG_INTACT_ST_ALIGNED",),
                (),
                refs,
            )
        if relation is HorizonRelation.COUNTER_REACTION:
            return LongExitAssessment(
                ExitStage.EXIT_READY,
                PositionHealth.PRESSURED,
                ("LT_LONG_INTACT_ST_COUNTER_REACTION",),
                ("FRESH_LONG_EXIT_EXECUTION_EVENT",),
                refs,
            )
        if relation is HorizonRelation.PULLBACK:
            return LongExitAssessment(
                ExitStage.MONITOR,
                PositionHealth.PROTECTED,
                ("LT_LONG_INTACT_ST_PULLBACK",),
                (),
                refs,
            )
        if relation is HorizonRelation.ST_UNRESOLVED:
            return LongExitAssessment(
                ExitStage.MONITOR,
                PositionHealth.UNKNOWN,
                ("LT_LONG_INTACT_ST_UNRESOLVED",),
                ("ST_STRUCTURE_TO_RESOLVE",),
                refs,
            )
        if relation is HorizonRelation.STRUCTURAL_CONFLICT:
            return LongExitAssessment(
                ExitStage.EXIT_WATCH,
                PositionHealth.PRESSURED,
                ("LT_LONG_INTACT_CROSS_HORIZON_STRUCTURAL_CONFLICT",),
                ("CROSS_HORIZON_STRUCTURE_TO_RECONCILE",),
                refs,
            )

        return LongExitAssessment(
            ExitStage.MONITOR,
            PositionHealth.UNKNOWN,
            (f"LT_LONG_INTACT_RELATION_{relation.value}",),
            (),
            refs,
        )

    return LongExitAssessment(
        ExitStage.EXIT_WATCH,
        PositionHealth.UNKNOWN,
        ("OPEN_LONG_EXIT_STATE_NOT_CANONICALLY_CLASSIFIED",),
        ("CANONICAL_LT_STRUCTURE_STATE",),
        refs,
    )


def _validate_exit_event(event: ExecutionTriggerEvent, *, as_of: Any, timeframe: str) -> None:
    normalized = timeframe.strip().lower()
    if event.side is not StructuralDirection.SHORT:
        raise ValueError("long exit execution event must be SHORT-side")
    if event.timeframe.strip().lower() != normalized:
        raise ValueError("long exit execution event timeframe must match exit timeframe")
    try:
        if event.available_at > as_of:
            raise ValueError("future-unavailable long exit event cannot be consumed")
        if event.observed_at != as_of:
            raise ValueError("long exit execution event must be fresh at decision as_of")
    except TypeError as exc:
        raise TypeError("long exit execution timestamps must be comparable") from exc
    for ref in event.source_refs:
        if ref.timeframe.strip().lower() != normalized:
            raise ValueError("long exit execution refs must belong to exit timeframe")
        if not ref.is_available_at(as_of):
            raise ValueError("long exit execution cannot contain future-unavailable refs")


def arm_open_long_on_30m_short(
    assessment: LongExitAssessment,
    *,
    as_of: Any,
    event: ExecutionTriggerEvent | None,
    allow: bool,
) -> LongExitAssessment:
    """30m never arms EXIT_READY. Pattern SHORT and structure BOS are clicks.

    Intact LT ALIGNED/PULLBACK stays MONITOR. SELL requires 1H/LT to already
    have armed EXIT_READY; the validator then consumes a fresh 30m SHORT event.
    Signature kept for compose/stream call sites.
    """

    del as_of, event, allow
    return assessment


def assess_long_exit_execution(
    assessment: LongExitAssessment,
    *,
    as_of: Any,
    event: ExecutionTriggerEvent | None,
    execution_timeframe: str = "30m",
    channel_available: bool = True,
) -> LongExitExecutionAssessment:
    """Validate a fresh exit event only after the long exit path is structurally armed."""

    if as_of is None:
        raise ValueError("long exit execution as_of must be known")

    if event is not None:
        _validate_exit_event(event, as_of=as_of, timeframe=execution_timeframe)

    if assessment.stage is not ExitStage.EXIT_READY:
        return LongExitExecutionAssessment(
            ExitExecutionState.NOT_ARMED,
            ("LONG_EXIT_PATH_NOT_ARMED",),
            tuple(assessment.waiting_for),
            () if event is None else tuple(event.source_refs),
        )

    if not channel_available:
        return LongExitExecutionAssessment(
            ExitExecutionState.UNAVAILABLE,
            ("LONG_EXIT_EXECUTION_DATA_UNAVAILABLE",),
            (f"{execution_timeframe}:LONG_EXIT_EXECUTION_DATA",),
            (),
        )

    if event is None:
        return LongExitExecutionAssessment(
            ExitExecutionState.ABSENT,
            ("NO_FRESH_LONG_EXIT_EXECUTION_EVENT",),
            ("FRESH_LONG_EXIT_EXECUTION_EVENT",),
            (),
        )

    state = (
        ExitExecutionState.CONFIRMED
        if event.state is ExecutionTriggerState.CONFIRMED
        else ExitExecutionState.FAILED
    )
    waiting = () if state is ExitExecutionState.CONFIRMED else ("NEW_LONG_EXIT_EXECUTION_EVENT",)
    return LongExitExecutionAssessment(
        state,
        (event.reason,),
        waiting,
        tuple(sorted(event.source_refs, key=lambda ref: ref.deterministic_key)),
    )


__all__ = [
    "ExitExecutionState",
    "LongExitAssessment",
    "LongExitExecutionAssessment",
    "PositionHealth",
    "arm_open_long_on_30m_short",
    "assess_long_exit_execution",
    "assess_long_position_exit",
    "st_pressures_open_long",
]
