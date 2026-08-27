from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from .entry import EntryDecision
from .execution import ExecutionTriggerEvent
from .exit import PositionExitDecision, transition_position_exit_lifecycle
from .lifecycle import (
    PositionState,
    TradeLifecycleState,
    TradeLifecycleTransition,
    transition_entry_lifecycle,
)

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot
    from .engine import DecisionEngineConfig


@dataclass(frozen=True, slots=True)
class CanonicalLifecycleReplayRow:
    """One causal ownership transition produced from one frozen market snapshot."""

    snapshot: "DecisionInputSnapshot"
    previous_state: TradeLifecycleState
    current_state: TradeLifecycleState
    transition: TradeLifecycleTransition
    entry_decision: EntryDecision | None
    exit_decision: PositionExitDecision | None

    def __post_init__(self) -> None:
        if self.previous_state != self.transition.previous:
            raise ValueError("replay row previous state must match lifecycle transition")
        if self.current_state != self.transition.current:
            raise ValueError("replay row current state must match lifecycle transition")
        if (self.entry_decision is None) == (self.exit_decision is None):
            raise ValueError("replay row must contain exactly one entry or exit decision")
        if self.previous_state.position is PositionState.FLAT and self.entry_decision is None:
            raise ValueError("FLAT replay row must be owned by entry decision")
        if self.previous_state.position is PositionState.OPEN and self.exit_decision is None:
            raise ValueError("OPEN replay row must be owned by position exit decision")

    @property
    def action(self):
        return self.transition.action

    @property
    def event_consumed(self) -> bool:
        if self.entry_decision is not None:
            return bool(self.entry_decision.execution_event_consumed)
        assert self.exit_decision is not None
        return bool(self.exit_decision.execution_event_consumed)


@dataclass(frozen=True, slots=True)
class CanonicalLifecycleReplayResult:
    """Sequential Turn 6 -> 7 -> 8 replay result with explicit ownership state."""

    initial_state: TradeLifecycleState
    final_state: TradeLifecycleState
    rows: tuple[CanonicalLifecycleReplayRow, ...]

    def __post_init__(self) -> None:
        expected = self.initial_state
        for row in self.rows:
            if row.previous_state != expected:
                raise ValueError("canonical lifecycle replay rows must form one contiguous chain")
            expected = row.current_state
        if expected != self.final_state:
            raise ValueError("canonical lifecycle replay final state must match row chain")


def _validate_initial_state(state: TradeLifecycleState) -> None:
    # Tur 7 intentionally allowed metadata-less OPEN states as a temporary compatibility
    # bridge. The canonical Tur 9 replay must not guess their horizon from later bars.
    if state.position is PositionState.OPEN and state.entry_metadata is None:
        raise ValueError(
            "canonical lifecycle replay requires entry metadata for an initial OPEN position"
        )


def replay_canonical_trade_lifecycle(
    snapshots: Iterable["DecisionInputSnapshot"],
    *,
    config: "DecisionEngineConfig | None" = None,
    entry_execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
    exit_execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
    initial_state: TradeLifecycleState | None = None,
) -> CanonicalLifecycleReplayResult:
    """Replay the canonical long-only ownership path over frozen snapshots.

    FLAT bars evaluate only the Turn 6 entry path and can open only through the Turn 7
    metadata transition. OPEN bars evaluate only the Turn 8 exit path. Entry and exit
    execution maps are intentionally separate and looked up only at the current bar;
    no event is cached or carried into a later snapshot.
    """

    state = initial_state or TradeLifecycleState()
    _validate_initial_state(state)
    starting_state = state
    entry_events = entry_execution_events or {}
    exit_events = exit_execution_events or {}
    rows: list[CanonicalLifecycleReplayRow] = []
    previous_as_of: Any | None = None
    stream_symbol: str | None = None

    for snapshot in snapshots:
        if snapshot.as_of is None:
            raise ValueError("canonical lifecycle replay snapshot as_of must be known")
        if previous_as_of is not None and snapshot.as_of <= previous_as_of:
            raise ValueError("canonical lifecycle replay snapshots must be strictly increasing")
        if not str(snapshot.symbol).strip():
            raise ValueError("canonical lifecycle replay snapshot symbol must be non-empty")
        if stream_symbol is None:
            stream_symbol = str(snapshot.symbol)
        elif str(snapshot.symbol) != stream_symbol:
            raise ValueError("canonical lifecycle replay stream must contain one symbol")
        if state.entry_metadata is not None and state.entry_metadata.symbol != str(snapshot.symbol):
            raise ValueError("open position symbol must match replay snapshot symbol")
        for ref in snapshot.source_refs:
            if not ref.is_available_at(snapshot.as_of):
                raise ValueError("canonical lifecycle replay contains future-unavailable evidence")

        previous = state
        entry: EntryDecision | None = None
        exit_decision: PositionExitDecision | None = None

        if state.position is PositionState.FLAT:
            raw_entry_event = entry_events.get(snapshot.as_of)
            entry = snapshot.entry_decision(
                config=config,
                execution_event=raw_entry_event,
            )
            transition = transition_entry_lifecycle(
                state,
                entry,
                snapshot,
                execution_event=raw_entry_event,
            )
        else:
            raw_exit_event = exit_events.get(snapshot.as_of)
            exit_decision = snapshot.position_exit_decision(
                state,
                execution_event=raw_exit_event,
            )
            transition = transition_position_exit_lifecycle(state, exit_decision)

        state = transition.current
        rows.append(
            CanonicalLifecycleReplayRow(
                snapshot=snapshot,
                previous_state=previous,
                current_state=state,
                transition=transition,
                entry_decision=entry,
                exit_decision=exit_decision,
            )
        )
        previous_as_of = snapshot.as_of

    return CanonicalLifecycleReplayResult(
        initial_state=starting_state,
        final_state=state,
        rows=tuple(rows),
    )


__all__ = [
    "CanonicalLifecycleReplayResult",
    "CanonicalLifecycleReplayRow",
    "replay_canonical_trade_lifecycle",
]
