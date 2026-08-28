from __future__ import annotations

from typing import Any, Iterable, Mapping

from .execution import ExecutionTriggerEvent, ExecutionTriggerState
from .structural import StructuralDirection


_EXECUTION_TIMEFRAME = "30m"
_CONFIRMED_PATTERN_PHASES = frozenset({"BREAK_CONFIRMED", "RETEST_HELD"})
_FAILED_PATTERN_PHASES = frozenset({"BREAK_FAILED", "INVALIDATED"})


def _phase_name(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value) or "").strip().upper()


def _direction(value: int) -> StructuralDirection | None:
    if value > 0:
        return StructuralDirection.LONG
    if value < 0:
        return StructuralDirection.SHORT
    return None


def _pattern_row(snapshot: Any) -> Any | None:
    projection = getattr(snapshot, "pattern_behavior", None)
    if projection is None:
        return None
    lookup = getattr(projection, "for_timeframe", None)
    if lookup is None:
        return None
    try:
        return lookup(_EXECUTION_TIMEFRAME)
    except (KeyError, AttributeError, TypeError):
        return None


def _confirmed_bos_ids(snapshot: Any) -> dict[str, int]:
    structure = getattr(snapshot, "structure", None)
    if structure is None:
        return {}
    lookup = getattr(structure, "for_timeframe", None)
    if lookup is None:
        return {}
    try:
        row = lookup(_EXECUTION_TIMEFRAME)
    except (KeyError, AttributeError, TypeError):
        return {}
    found: dict[str, int] = {}
    for event in getattr(row, "events", ()) or ():
        if str(getattr(event, "event_type", "")).strip().upper() != "BOS":
            continue
        if str(getattr(event, "confirmation_status", "")).strip().upper() != "CONFIRMED":
            continue
        native_id = str(getattr(getattr(event, "ref", None), "native_id", "") or "")
        if not native_id:
            continue
        found[native_id] = int(getattr(event, "direction", 0) or 0)
    return found


def _event(
    snapshot: Any,
    *,
    state: ExecutionTriggerState,
    side: StructuralDirection,
    reason: str,
    source_refs: tuple = (),
) -> ExecutionTriggerEvent:
    as_of = snapshot.as_of
    usable_refs = tuple(
        ref for ref in source_refs if getattr(ref, "is_available_at", lambda _as_of: True)(as_of)
    )
    return ExecutionTriggerEvent(
        state=state,
        side=side,
        timeframe=_EXECUTION_TIMEFRAME,
        observed_at=as_of,
        available_at=as_of,
        reason=reason,
        source_refs=usable_refs,
    )


def detect_30m_execution_events(
    snapshots: Iterable[Any],
) -> tuple[Mapping[Any, ExecutionTriggerEvent], Mapping[Any, ExecutionTriggerEvent]]:
    """Emit fresh 30m CONFIRMED events from frozen snapshot *transitions*.

    Sticky BREAK_CONFIRMED / BOS that already existed on the previous decision
    bar is not an event. Only the bar where 30m pattern first confirms, or a
    new 30m BOS identity appears, produces an event. LONG confirms feed entry;
    SHORT confirms feed exit. No BUY/SELL is invented here.
    """

    entry: dict[Any, ExecutionTriggerEvent] = {}
    exit_events: dict[Any, ExecutionTriggerEvent] = {}
    previous_phase = ""
    previous_direction = 0
    previous_bos: frozenset[str] = frozenset()
    seen_previous = False

    for snapshot in snapshots:
        row = _pattern_row(snapshot)
        phase = "" if row is None else _phase_name(getattr(row, "phase", None))
        direction = 0 if row is None else int(getattr(row, "classic_direction", 0) or 0)
        bos = _confirmed_bos_ids(snapshot)
        refs = () if row is None or getattr(row, "ref", None) is None else (row.ref,)
        event: ExecutionTriggerEvent | None = None

        if seen_previous and phase in _CONFIRMED_PATTERN_PHASES and previous_phase not in _CONFIRMED_PATTERN_PHASES:
            side = _direction(direction)
            if side is not None:
                event = _event(
                    snapshot,
                    state=ExecutionTriggerState.CONFIRMED,
                    side=side,
                    reason="30M_PATTERN_BREAK_CONFIRMED",
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
                    state=ExecutionTriggerState.FAILED,
                    side=side,
                    reason="30M_PATTERN_BREAK_FAILED",
                    source_refs=refs,
                )

        if event is None and seen_previous:
            new_ids = tuple(native_id for native_id in bos if native_id not in previous_bos)
            if len(new_ids) == 1:
                side = _direction(bos[new_ids[0]])
                if side is not None:
                    event = _event(
                        snapshot,
                        state=ExecutionTriggerState.CONFIRMED,
                        side=side,
                        reason="30M_STRUCTURE_BOS_CONFIRMED",
                    )

        if event is not None and event.state is ExecutionTriggerState.CONFIRMED:
            if event.side is StructuralDirection.LONG:
                entry[snapshot.as_of] = event
            elif event.side is StructuralDirection.SHORT:
                exit_events[snapshot.as_of] = event

        previous_phase = phase
        previous_direction = direction
        previous_bos = frozenset(bos)
        seen_previous = True

    return entry, exit_events


__all__ = ["detect_30m_execution_events"]
