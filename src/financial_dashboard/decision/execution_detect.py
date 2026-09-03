from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.pattern_behavior_projection import (
    PatternBehaviorPhase,
    _phase as _native_pattern_phase,
)

from .execution import ExecutionTriggerEvent, ExecutionTriggerState
from .structural import StructuralDirection

_EXECUTION_TIMEFRAME = "30m"
_CONFIRMED_PHASES = frozenset(
    {
        PatternBehaviorPhase.BREAK_CONFIRMED,
        PatternBehaviorPhase.RETEST_HELD,
    }
)


def _pattern_row(snapshot: Any, timeframe: str = _EXECUTION_TIMEFRAME) -> Any | None:
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


def _effective_phase(row: Any | None) -> PatternBehaviorPhase | None:
    """Recover price-only native phase when generic source quality is DATA_LIMITED.

    Pattern geometry is OHLC-price-only. Historical source quality can be marked
    DATA_LIMITED for non-price warnings (for example volume/open-tail diagnostics)
    even though the already-frozen native pattern state is causally available. This
    mirrors Decision's existing price-only Structure normalization without changing
    the persisted native fact or inventing a phase when native state is absent.
    """

    if row is None:
        return None
    phase = getattr(row, "phase", None)
    if phase is not None and not isinstance(phase, PatternBehaviorPhase):
        try:
            phase = PatternBehaviorPhase(str(getattr(phase, "value", phase)))
        except ValueError:
            phase = None
    ref = getattr(row, "ref", None)
    quality = getattr(ref, "data_quality", ContextDataQuality.UNAVAILABLE)
    if phase is not None and phase is not PatternBehaviorPhase.UNAVAILABLE and quality is ContextDataQuality.VALID:
        return phase

    native_state = str(getattr(row, "native_state", "") or "").strip()
    if not native_state:
        return phase
    return _native_pattern_phase(native_state, unavailable=False)


def _direction(row: Any | None) -> StructuralDirection | None:
    if row is None:
        return None
    try:
        value = int(getattr(row, "classic_direction", 0) or 0)
    except (TypeError, ValueError):
        return None
    if value > 0:
        return StructuralDirection.LONG
    if value < 0:
        return StructuralDirection.SHORT
    return None


def _execution_ref(row: Any | None, *, as_of: Any):
    """Return one causal price-only ref suitable for the execution channel.

    DATA_LIMITED is normalized only when an explicit native pattern state exists and
    its ref is already available at ``as_of``. UNAVAILABLE/missing evidence remains
    unavailable and cannot produce an event.
    """

    if row is None:
        return None
    ref = getattr(row, "ref", None)
    if ref is None or not ref.is_available_at(as_of):
        return None
    phase = _effective_phase(row)
    if phase is None or phase is PatternBehaviorPhase.UNAVAILABLE:
        return None
    if ref.data_quality is ContextDataQuality.VALID:
        return ref
    if ref.data_quality is ContextDataQuality.DATA_LIMITED and str(
        getattr(row, "native_state", "") or ""
    ).strip():
        return replace(ref, data_quality=ContextDataQuality.VALID)
    return None


def detect_30m_execution_events(
    snapshots: Iterable[Any],
) -> tuple[Mapping[Any, ExecutionTriggerEvent], Mapping[Any, ExecutionTriggerEvent]]:
    """Detect fresh 30m pattern confirmations from the frozen snapshot stream.

    A sticky confirmed pattern is never re-emitted. Only the causal transition into
    BREAK_CONFIRMED/RETEST_HELD creates one event at the current decision ``as_of``.
    LONG confirmations feed entry; SHORT confirmations feed long-exit timing. The
    detector reads only the already-frozen DecisionInput stream and never replays a
    market domain.
    """

    entry: dict[Any, ExecutionTriggerEvent] = {}
    exit_: dict[Any, ExecutionTriggerEvent] = {}
    previous_phase: PatternBehaviorPhase | None = None
    seen_previous = False

    for snapshot in snapshots:
        as_of = getattr(snapshot, "as_of", None)
        if as_of is None:
            raise ValueError("execution detector snapshot as_of must be known")
        row = _pattern_row(snapshot)
        phase = _effective_phase(row)
        side = _direction(row)

        transitioned = (
            seen_previous
            and phase in _CONFIRMED_PHASES
            and previous_phase not in _CONFIRMED_PHASES
        )
        if transitioned and side is not None:
            ref = _execution_ref(row, as_of=as_of)
            if ref is not None:
                event = ExecutionTriggerEvent(
                    state=ExecutionTriggerState.CONFIRMED,
                    side=side,
                    timeframe=_EXECUTION_TIMEFRAME,
                    observed_at=as_of,
                    available_at=ref.available_at,
                    reason="30M_PATTERN_CONFIRMATION_TRANSITION",
                    source_refs=(ref,),
                )
                if side is StructuralDirection.LONG:
                    entry[as_of] = event
                else:
                    exit_[as_of] = event

        previous_phase = phase
        seen_previous = True

    return entry, exit_


__all__ = ["detect_30m_execution_events"]
