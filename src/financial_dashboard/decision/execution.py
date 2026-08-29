from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from financial_dashboard.context.envelope import (
    ContextDataQuality,
    FactRef,
    normalize_context_data_quality,
)

from .structural import StructuralDirection


class ExecutionTriggerState(StrEnum):
    ABSENT = "ABSENT"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class ExecutionEventKind(StrEnum):
    """Semantic type of a fresh 30m event."""

    PATTERN_CONFIRMATION = "PATTERN_CONFIRMATION"
    REACTION_CONFIRMATION = "REACTION_CONFIRMATION"
    STRUCTURE_BOS = "STRUCTURE_BOS"
    AUDIT_PROXY = "AUDIT_PROXY"
    LEGACY = "LEGACY"


@dataclass(frozen=True, slots=True)
class ExecutionTriggerEvent:
    state: ExecutionTriggerState
    side: StructuralDirection
    timeframe: str
    observed_at: Any
    available_at: Any
    reason: str
    source_refs: tuple[FactRef, ...] = ()
    kind: ExecutionEventKind = ExecutionEventKind.LEGACY

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


def execution_event_kind(event: ExecutionTriggerEvent) -> ExecutionEventKind:
    if isinstance(event.kind, ExecutionEventKind) and event.kind is not ExecutionEventKind.LEGACY:
        return event.kind
    if not isinstance(event.kind, ExecutionEventKind):
        try:
            kind = ExecutionEventKind(str(getattr(event.kind, "value", event.kind)))
        except (TypeError, ValueError):
            kind = ExecutionEventKind.LEGACY
        if kind is not ExecutionEventKind.LEGACY:
            return kind
    reason = str(event.reason).strip().upper()
    if "STRUCTURE_BOS" in reason:
        return ExecutionEventKind.STRUCTURE_BOS
    if "AUDIT_PROXY" in reason:
        return ExecutionEventKind.AUDIT_PROXY
    if "REACTION" in reason:
        return ExecutionEventKind.REACTION_CONFIRMATION
    return ExecutionEventKind.PATTERN_CONFIRMATION


def is_entry_execution_click(event: ExecutionTriggerEvent | None) -> bool:
    if event is None:
        return False
    return execution_event_kind(event) in {
        ExecutionEventKind.PATTERN_CONFIRMATION,
        ExecutionEventKind.REACTION_CONFIRMATION,
        ExecutionEventKind.AUDIT_PROXY,
    }


def is_exit_execution_click(event: ExecutionTriggerEvent | None) -> bool:
    if event is None:
        return False
    return execution_event_kind(event) in {
        ExecutionEventKind.PATTERN_CONFIRMATION,
        ExecutionEventKind.REACTION_CONFIRMATION,
        ExecutionEventKind.AUDIT_PROXY,
    }


@dataclass(frozen=True, slots=True)
class ExecutionTriggerAssessment:
    state: ExecutionTriggerState
    side: StructuralDirection
    timeframe: str
    reasons: tuple[str, ...]
    source_refs: tuple[FactRef, ...]


def assess_execution_trigger(
    side: StructuralDirection,
    *,
    as_of: Any,
    timeframe: str,
    data_quality: ContextDataQuality,
    event: ExecutionTriggerEvent | None = None,
) -> ExecutionTriggerAssessment:
    """Validate one causal event without synthesising a click from sticky state."""

    normalized = timeframe.strip().lower()
    quality = normalize_context_data_quality(data_quality)
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
    if quality is not ContextDataQuality.VALID:
        return ExecutionTriggerAssessment(
            ExecutionTriggerState.UNAVAILABLE,
            side,
            normalized,
            (f"EXECUTION_DATA_{quality.value}:{normalized}",),
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

    if event.side is not side:
        raise ValueError("execution trigger side must match assessed structural side")
    if event.timeframe.strip().lower() != normalized:
        raise ValueError("execution trigger timeframe must match configured trigger timeframe")
    try:
        if event.available_at > as_of:
            raise ValueError("future-unavailable execution trigger cannot be consumed")
        if event.observed_at > as_of:
            raise ValueError("future-observed execution trigger cannot be consumed")
    except TypeError as exc:
        raise TypeError("execution trigger timestamps must be comparable") from exc

    for ref in event.source_refs:
        if ref.timeframe.strip().lower() != normalized:
            raise ValueError("execution trigger refs must belong to trigger timeframe")
        if not ref.is_available_at(as_of):
            raise ValueError("execution trigger cannot contain future-unavailable refs")

    return ExecutionTriggerAssessment(
        event.state,
        side,
        normalized,
        (event.reason,),
        tuple(sorted(event.source_refs, key=lambda ref: ref.deterministic_key)),
    )


__all__ = [
    "ExecutionEventKind",
    "ExecutionTriggerAssessment",
    "ExecutionTriggerEvent",
    "ExecutionTriggerState",
    "assess_execution_trigger",
    "execution_event_kind",
    "is_entry_execution_click",
    "is_exit_execution_click",
]
