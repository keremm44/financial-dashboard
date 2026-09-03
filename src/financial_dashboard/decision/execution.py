from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from financial_dashboard.context.envelope import ContextDataQuality, FactRef

from .structural import StructuralDirection


class ExecutionTriggerState(StrEnum):
    ABSENT = "ABSENT"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ExecutionTriggerEvent:
    """One fresh, closed-bar execution event supplied by an event detector.

    The decision composer intentionally does not infer a fresh event from a sticky
    snapshot state. That would allow the same historical confirmation to emit BUY or
    SELL repeatedly on later bars. A concrete detector/replay may create this object
    only when a new event is observed at the current decision timestamp.
    """

    state: ExecutionTriggerState
    side: StructuralDirection
    timeframe: str
    observed_at: Any
    available_at: Any
    reason: str
    source_refs: tuple[FactRef, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in {ExecutionTriggerState.CONFIRMED, ExecutionTriggerState.FAILED}:
            raise ValueError("execution trigger event must be CONFIRMED or FAILED")
        if self.side is StructuralDirection.UNRESOLVED:
            raise ValueError("execution trigger event side must be directional")
        if not self.timeframe.strip():
            raise ValueError("execution trigger timeframe must be non-empty")
        if self.observed_at is None or self.available_at is None:
            raise ValueError("execution trigger timestamps must be known")
        if not self.reason.strip():
            raise ValueError("execution trigger reason must be non-empty")


@dataclass(frozen=True, slots=True)
class ExecutionTriggerAssessment:
    state: ExecutionTriggerState
    side: StructuralDirection
    timeframe: str
    reasons: tuple[str, ...]
    source_refs: tuple[FactRef, ...]


def _event_proves_valid_price_channel(event: ExecutionTriggerEvent) -> bool:
    """Require explicit normalized causal refs before recovering generic DATA_LIMITED."""

    return bool(event.source_refs) and all(
        ref.data_quality is ContextDataQuality.VALID for ref in event.source_refs
    )


def assess_execution_trigger(
    side: StructuralDirection,
    *,
    as_of: Any,
    timeframe: str,
    data_quality: ContextDataQuality,
    event: ExecutionTriggerEvent | None = None,
) -> ExecutionTriggerAssessment:
    """Validate one fresh execution event without inventing a trigger from state.

    A valid trigger channel with no fresh event is ``ABSENT``. Missing/degraded
    trigger data is ``UNAVAILABLE``. A fresh event backed by explicit VALID causal
    price refs may recover a generic DATA_LIMITED channel; missing/unavailable
    evidence is never promoted. Supplied stale/future/mismatched events are
    programming errors and fail closed rather than being silently reused.
    """

    normalized = timeframe.strip().lower()
    if as_of is None:
        raise ValueError("execution assessment as_of must be known")
    if side is StructuralDirection.UNRESOLVED:
        return ExecutionTriggerAssessment(
            ExecutionTriggerState.UNAVAILABLE,
            side,
            normalized,
            ("EXECUTION_SIDE_UNRESOLVED",),
            (),
        )

    if event is not None:
        if event.side is not side:
            raise ValueError("execution trigger side must match assessed structural side")
        if event.timeframe.strip().lower() != normalized:
            raise ValueError("execution trigger timeframe must match configured trigger timeframe")
        try:
            if event.available_at > as_of:
                raise ValueError("future-unavailable execution trigger cannot be consumed")
            if event.observed_at != as_of:
                raise ValueError("execution trigger event must be fresh at decision as_of")
        except TypeError as exc:
            raise TypeError("execution trigger timestamps must be comparable") from exc

        for ref in event.source_refs:
            if ref.timeframe.strip().lower() != normalized:
                raise ValueError("execution trigger refs must belong to trigger timeframe")
            if not ref.is_available_at(as_of):
                raise ValueError("execution trigger cannot contain future-unavailable refs")

    if data_quality is not ContextDataQuality.VALID and not (
        event is not None and _event_proves_valid_price_channel(event)
    ):
        return ExecutionTriggerAssessment(
            ExecutionTriggerState.UNAVAILABLE,
            side,
            normalized,
            (f"EXECUTION_DATA_{data_quality.value}:{normalized}",),
            (),
        )
    if event is None:
        return ExecutionTriggerAssessment(
            ExecutionTriggerState.ABSENT,
            side,
            normalized,
            ("NO_FRESH_EXECUTION_EVENT",),
            (),
        )

    return ExecutionTriggerAssessment(
        event.state,
        side,
        normalized,
        (event.reason,),
        tuple(sorted(event.source_refs, key=lambda ref: ref.deterministic_key)),
    )


__all__ = [
    "ExecutionTriggerAssessment",
    "ExecutionTriggerEvent",
    "ExecutionTriggerState",
    "assess_execution_trigger",
]
