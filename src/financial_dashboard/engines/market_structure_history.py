from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from .market_structure import SCOPE_EXTERNAL
from .market_structure_events import (
    MarketStructureEventRecord,
    StructureEventConfirmation,
)
from .market_structure_state import (
    BosMaturity,
    EVENT_BOS,
    EVENT_CHOCH,
)


class StructureHistoryBoundaryState(StrEnum):
    """How much directional structure is observable after the replay's left edge."""

    NO_EXTERNAL_STRUCTURE = "NO_EXTERNAL_STRUCTURE"
    LEFT_BOUNDARY_ACTIVE = "LEFT_BOUNDARY_ACTIVE"
    INITIAL_STRUCTURE_NOT_CURRENT = "INITIAL_STRUCTURE_NOT_CURRENT"
    POST_INITIAL_PROGRESSION = "POST_INITIAL_PROGRESSION"
    NO_INITIALIZATION_RECORD = "NO_INITIALIZATION_RECORD"


@dataclass(frozen=True, slots=True)
class StructureHistoryDiagnostic:
    """Deterministic history-boundary facts for one Market Structure timeframe.

    This does not claim that a finite history window is sufficient.  It records
    whether the currently surfaced progression still depends on the first direction
    established after an engine started from neutral.
    """

    symbol: str
    timeframe: str
    input_bar_count: int
    input_start: Any
    input_end: Any
    state: StructureHistoryBoundaryState
    external_structure_event_count: int
    first_external_event_uid: str | None
    first_external_event_type: str | None
    first_external_event_at: Any
    first_external_event_maturity: BosMaturity
    initial_structure_event_uid: str | None
    initial_structure_event_at: Any
    bars_before_first_external_event: int | None
    bars_before_initial_structure: int | None
    choch_count: int
    transition_confirmation_bos_count: int
    continuation_bos_count: int
    current_progression_uses_initial_structure: bool
    reasons: tuple[str, ...] = ()


def assess_structure_history(
    *,
    symbol: str,
    timeframe: str,
    input_bar_count: int,
    input_start: Any,
    input_end: Any,
    events: Iterable[MarketStructureEventRecord],
    current_progression_event_uids: Iterable[str] = (),
) -> StructureHistoryDiagnostic:
    """Assess observable external structure without inventing pre-cache context."""

    normalized_timeframe = timeframe.strip().lower()
    progression_uids = frozenset(current_progression_event_uids)
    structural = tuple(
        sorted(
            (
                event
                for event in events
                if event.scope == SCOPE_EXTERNAL
                and event.event_type in {EVENT_BOS, EVENT_CHOCH}
                and event.confirmation_status is StructureEventConfirmation.CONFIRMED
            ),
            key=lambda event: (event.event_bar, event.event_uid),
        )
    )
    initial_events = tuple(event for event in structural if event.is_initial_structure)
    initial = initial_events[0] if initial_events else None
    first = structural[0] if structural else None
    current_uses_initial = any(
        event.event_uid in progression_uids for event in initial_events
    )

    if not structural:
        state = StructureHistoryBoundaryState.NO_EXTERNAL_STRUCTURE
        reasons = ("NO_CONFIRMED_EXTERNAL_BOS_OR_CHOCH_IN_CACHE",)
    elif current_uses_initial:
        state = StructureHistoryBoundaryState.LEFT_BOUNDARY_ACTIVE
        reasons = (
            "CURRENT_PROGRESSION_DEPENDS_ON_NEUTRAL_START_INITIALIZATION",
            "PRE_CACHE_DIRECTIONAL_CONTEXT_NOT_OBSERVED",
        )
    elif initial is not None and any(
        event.event_bar > initial.event_bar for event in structural
    ):
        state = StructureHistoryBoundaryState.POST_INITIAL_PROGRESSION
        reasons = ("LATER_EXTERNAL_STRUCTURE_OBSERVED_AFTER_INITIALIZATION",)
    elif initial is not None:
        state = StructureHistoryBoundaryState.INITIAL_STRUCTURE_NOT_CURRENT
        reasons = ("LATEST_EXTERNAL_EVENT_NO_LONGER_SURFACES_INITIAL_STRUCTURE",)
    else:
        state = StructureHistoryBoundaryState.NO_INITIALIZATION_RECORD
        reasons = ("EXTERNAL_STRUCTURE_EXISTS_WITHOUT_IN_WINDOW_INITIALIZATION",)

    return StructureHistoryDiagnostic(
        symbol=symbol,
        timeframe=normalized_timeframe,
        input_bar_count=input_bar_count,
        input_start=input_start,
        input_end=input_end,
        state=state,
        external_structure_event_count=len(structural),
        first_external_event_uid=None if first is None else first.event_uid,
        first_external_event_type=None if first is None else first.event_type,
        first_external_event_at=None if first is None else first.confirmed_at,
        first_external_event_maturity=(
            BosMaturity.NOT_APPLICABLE if first is None else first.bos_maturity
        ),
        initial_structure_event_uid=None if initial is None else initial.event_uid,
        initial_structure_event_at=None if initial is None else initial.confirmed_at,
        bars_before_first_external_event=None if first is None else first.event_bar,
        bars_before_initial_structure=None if initial is None else initial.event_bar,
        choch_count=sum(event.event_type == EVENT_CHOCH for event in structural),
        transition_confirmation_bos_count=sum(
            event.event_type == EVENT_BOS
            and event.bos_maturity is BosMaturity.TRANSITION_CONFIRMATION
            for event in structural
        ),
        continuation_bos_count=sum(
            event.event_type == EVENT_BOS
            and event.bos_maturity is BosMaturity.CONTINUATION
            for event in structural
        ),
        current_progression_uses_initial_structure=current_uses_initial,
        reasons=reasons,
    )
