from __future__ import annotations

from typing import Any, Iterable, Mapping

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.pattern_behavior_projection import _phase as _native_pattern_phase

from .event_stream import ExecutionEventQueue
from .execution import (
    ExecutionEventKind,
    ExecutionTriggerEvent,
    ExecutionTriggerState,
    is_entry_execution_click,
    is_exit_execution_click,
)
from .structural import StructuralDirection


_MICRO_EXECUTION_TIMEFRAME = "30m"
_PRIMARY_EXECUTION_TIMEFRAME = "1h"
_CONFIRMED_PATTERN_PHASES = frozenset({"BREAK_CONFIRMED", "RETEST_HELD"})
_FAILED_PATTERN_PHASES = frozenset({"BREAK_FAILED", "INVALIDATED"})
_BOS_EVENT_TYPES = frozenset({"BOS", "EVENT_BOS"})


def _token(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value) or "").strip().upper()


def _direction(value: Any) -> StructuralDirection | None:
    if isinstance(value, StructuralDirection):
        return None if value is StructuralDirection.UNRESOLVED else value
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        token = _token(value)
        if token in {"1", "UP", "LONG", "BULLISH"}:
            return StructuralDirection.LONG
        if token in {"-1", "DOWN", "SHORT", "BEARISH"}:
            return StructuralDirection.SHORT
        return None
    if numeric > 0:
        return StructuralDirection.LONG
    if numeric < 0:
        return StructuralDirection.SHORT
    return None


def _pattern_row(snapshot: Any, timeframe: str) -> Any | None:
    projection = getattr(snapshot, "pattern_behavior", None)
    if projection is None:
        return None
    lookup = getattr(projection, "for_timeframe", None)
    if lookup is None:
        return None
    try:
        return lookup(timeframe.strip().lower())
    except (KeyError, AttributeError, TypeError):
        return None


def _row_phase(row: Any | None) -> str:
    """Recover native price-pattern phase for causal transition detection."""

    if row is None:
        return ""
    phase = _token(getattr(row, "phase", None))
    quality = getattr(getattr(row, "ref", None), "data_quality", None)
    if phase and phase != "UNAVAILABLE" and quality is not ContextDataQuality.DATA_LIMITED:
        return phase
    native = getattr(row, "native_state", None)
    if native:
        recovered = _token(_native_pattern_phase(str(native), unavailable=False))
        if recovered and recovered != "UNAVAILABLE":
            return recovered
    return phase if phase != "UNAVAILABLE" else ""


def _confirmed_bos_ids(
    snapshot: Any,
    timeframe: str,
) -> dict[str, tuple[StructuralDirection, Any | None]]:
    structure = getattr(snapshot, "structure", None)
    if structure is None:
        return {}
    lookup = getattr(structure, "for_timeframe", None)
    if lookup is None:
        return {}
    try:
        row = lookup(timeframe.strip().lower())
    except (KeyError, AttributeError, TypeError):
        return {}
    found: dict[str, tuple[StructuralDirection, Any | None]] = {}
    for event in getattr(row, "events", ()) or ():
        if _token(getattr(event, "event_type", "")) not in _BOS_EVENT_TYPES:
            continue
        if _token(getattr(event, "confirmation_status", "")) != "CONFIRMED":
            continue
        side = _direction(getattr(event, "direction", 0))
        if side is None:
            continue
        ref = getattr(event, "ref", None)
        native_id = str(getattr(ref, "native_id", "") or "")
        if not native_id:
            continue
        found[native_id] = (side, ref)
    return found


def _native_times(snapshot: Any, source_refs: tuple) -> tuple[Any, Any]:
    as_of = snapshot.as_of
    observed_values = []
    available_values = []
    for ref in source_refs:
        observed = getattr(ref, "confirmed_at", None)
        if observed is None:
            observed = getattr(ref, "origin_time", None)
        available = getattr(ref, "available_at", None)
        if observed is not None:
            observed_values.append(observed)
        if available is not None:
            available_values.append(available)
    observed_at = max(observed_values) if observed_values else as_of
    available_at = max(available_values) if available_values else as_of
    return observed_at, available_at


def _event(
    snapshot: Any,
    *,
    timeframe: str,
    state: ExecutionTriggerState,
    side: StructuralDirection,
    kind: ExecutionEventKind,
    reason: str,
    source_refs: tuple = (),
) -> ExecutionTriggerEvent:
    as_of = snapshot.as_of
    normalized = timeframe.strip().lower()
    usable_refs = tuple(
        ref
        for ref in source_refs
        if ref.timeframe.strip().lower() == normalized
        and getattr(ref, "is_available_at", lambda _as_of: True)(as_of)
    )
    observed_at, available_at = _native_times(snapshot, usable_refs)
    return ExecutionTriggerEvent(
        state=state,
        side=side,
        timeframe=normalized,
        observed_at=observed_at,
        available_at=available_at,
        reason=reason,
        source_refs=usable_refs,
        kind=kind,
    )


def _assign_30m_to_decision_windows(
    snapshots: tuple[Any, ...],
    events: list[ExecutionTriggerEvent],
) -> dict[Any, ExecutionTriggerEvent]:
    queue = ExecutionEventQueue(events)
    assigned: dict[Any, ExecutionTriggerEvent] = {}
    previous_as_of = None
    for snapshot in snapshots:
        event = queue.take_fresh(snapshot.as_of, previous_as_of=previous_as_of)
        if event is not None:
            assigned[snapshot.as_of] = event
        previous_as_of = snapshot.as_of
    return assigned


def _detect_pattern_events(
    snapshots: tuple[Any, ...],
    *,
    timeframe: str,
) -> tuple[list[tuple[Any, ExecutionTriggerEvent]], list[tuple[Any, ExecutionTriggerEvent]]]:
    normalized = timeframe.strip().lower()
    raw_entry: list[tuple[Any, ExecutionTriggerEvent]] = []
    raw_exit: list[tuple[Any, ExecutionTriggerEvent]] = []
    previous_phase = ""
    previous_direction = 0
    previous_bos: frozenset[str] = frozenset()
    seen_previous = False

    for snapshot in snapshots:
        row = _pattern_row(snapshot, normalized)
        phase = _row_phase(row)
        direction = 0 if row is None else int(getattr(row, "classic_direction", 0) or 0)
        bos = _confirmed_bos_ids(snapshot, normalized)
        refs = () if row is None or getattr(row, "ref", None) is None else (row.ref,)
        event: ExecutionTriggerEvent | None = None

        if (
            seen_previous
            and phase in _CONFIRMED_PATTERN_PHASES
            and previous_phase not in _CONFIRMED_PATTERN_PHASES
        ):
            side = _direction(direction)
            if side is not None:
                event = _event(
                    snapshot,
                    timeframe=normalized,
                    state=ExecutionTriggerState.CONFIRMED,
                    side=side,
                    kind=ExecutionEventKind.PATTERN_CONFIRMATION,
                    reason=f"{normalized.upper()}_PATTERN_BREAK_CONFIRMED",
                    source_refs=refs,
                )
        elif (
            seen_previous
            and previous_phase in _CONFIRMED_PATTERN_PHASES
            and phase in _FAILED_PATTERN_PHASES
        ):
            side = _direction(previous_direction)
            if side is not None:
                event = _event(
                    snapshot,
                    timeframe=normalized,
                    state=ExecutionTriggerState.FAILED,
                    side=side,
                    kind=ExecutionEventKind.PATTERN_CONFIRMATION,
                    reason=f"{normalized.upper()}_PATTERN_BREAK_FAILED",
                    source_refs=refs,
                )

        # BOS is retained as diagnostic evidence only. It is never executable.
        if event is None and seen_previous:
            new_ids = tuple(native_id for native_id in bos if native_id not in previous_bos)
            sides = {bos[native_id][0] for native_id in new_ids}
            if len(sides) == 1:
                bos_refs = tuple(
                    ref
                    for native_id in new_ids
                    for ref in (bos[native_id][1],)
                    if ref is not None
                )
                event = _event(
                    snapshot,
                    timeframe=normalized,
                    state=ExecutionTriggerState.CONFIRMED,
                    side=next(iter(sides)),
                    kind=ExecutionEventKind.STRUCTURE_BOS,
                    reason=f"{normalized.upper()}_STRUCTURE_BOS_CONFIRMED",
                    source_refs=bos_refs,
                )

        if event is not None and event.state is ExecutionTriggerState.CONFIRMED:
            if event.side is StructuralDirection.LONG and is_entry_execution_click(event):
                raw_entry.append((snapshot.as_of, event))
            elif event.side is StructuralDirection.SHORT and is_exit_execution_click(event):
                raw_exit.append((snapshot.as_of, event))

        previous_phase = phase
        previous_direction = direction
        previous_bos = frozenset(bos)
        seen_previous = True

    return raw_entry, raw_exit


def detect_30m_execution_events(
    snapshots: Iterable[Any],
) -> tuple[Mapping[Any, ExecutionTriggerEvent], Mapping[Any, ExecutionTriggerEvent]]:
    """Detect optional micro-timing events and assign them once to 1h decision windows."""

    rows = tuple(snapshots)
    raw_entry, raw_exit = _detect_pattern_events(rows, timeframe=_MICRO_EXECUTION_TIMEFRAME)
    return (
        _assign_30m_to_decision_windows(rows, [event for _, event in raw_entry]),
        _assign_30m_to_decision_windows(rows, [event for _, event in raw_exit]),
    )


def detect_1h_execution_events(
    snapshots: Iterable[Any],
) -> tuple[Mapping[Any, ExecutionTriggerEvent], Mapping[Any, ExecutionTriggerEvent]]:
    """Detect primary ST execution events from closed 1h pattern transitions.

    The 1h decision snapshot is the execution owner. Pattern confirmation is enough;
    BOS is deliberately not required. 30m remains a separate optional micro-timing
    channel and cannot create a primary BUY/SELL through this mapping.
    """

    rows = tuple(snapshots)
    raw_entry, raw_exit = _detect_pattern_events(rows, timeframe=_PRIMARY_EXECUTION_TIMEFRAME)
    return dict(raw_entry), dict(raw_exit)


__all__ = ["detect_1h_execution_events", "detect_30m_execution_events"]
