from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from .composer import DecisionAction
from .entry import EntryDecision
from .execution import ExecutionTriggerEvent, ExecutionTriggerState
from .exit import PositionExitDecision, transition_position_exit_lifecycle
from .lifecycle import (
    ExitStage,
    PositionState,
    TradeLifecycleState,
    TradeLifecycleTransition,
    transition_entry_lifecycle,
)
from .scenario import ScenarioStage
from .st_economic_history import observe_st_economic_history
from .st_setup_continuity import (
    STSetupContinuityAssessment,
    apply_st_reentry_novelty_policy,
    assess_st_setup_continuity,
)
from .structural import DecisionHorizon, StructuralDirection

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot
    from .engine import DecisionEngineConfig


@dataclass(frozen=True, slots=True)
class ReplayAuditMarkerState:
    """Causal progression markers needed to make audit projection restart-safe.

    These values are downstream audit state, not market evidence and not trading
    policy. They record when an already-computed scenario/readiness/exit stage first
    became active so a resumed replay produces the same event payload as a cold run.
    """

    scenario_qualified_at: Any | None = None
    scenario_qualified_price: float | None = None
    scenario_key: tuple[str, str] | None = None
    ready_for_execution_at: Any | None = None
    ready_for_execution_price: float | None = None
    exit_watch_at: Any | None = None
    exit_watch_price: float | None = None
    exit_ready_at: Any | None = None
    exit_ready_price: float | None = None

    def __post_init__(self) -> None:
        pairs = (
            (self.scenario_qualified_at, self.scenario_qualified_price, "scenario qualified"),
            (self.ready_for_execution_at, self.ready_for_execution_price, "ready for execution"),
            (self.exit_watch_at, self.exit_watch_price, "exit watch"),
            (self.exit_ready_at, self.exit_ready_price, "exit ready"),
        )
        for timestamp, price, label in pairs:
            if (timestamp is None) != (price is None):
                raise ValueError(f"{label} audit marker timestamp/price must appear together")
            if price is not None and float(price) <= 0.0:
                raise ValueError(f"{label} audit marker price must be positive")
        if self.scenario_key is not None:
            if len(self.scenario_key) != 2 or any(not str(item).strip() for item in self.scenario_key):
                raise ValueError("scenario audit marker key must contain horizon and scenario kind")
            if self.scenario_qualified_at is None:
                raise ValueError("scenario audit marker key requires qualified timestamp")
        elif self.scenario_qualified_at is not None:
            raise ValueError("qualified audit marker requires scenario key")
        if self.ready_for_execution_at is not None and self.scenario_qualified_at is None:
            raise ValueError("ready audit marker requires qualified scenario marker")

    @property
    def is_empty(self) -> bool:
        return self == ReplayAuditMarkerState()


@dataclass(frozen=True, slots=True)
class CanonicalLifecycleReplayRow:
    """One causal ownership transition produced from one frozen market snapshot."""

    snapshot: "DecisionInputSnapshot"
    previous_state: TradeLifecycleState
    current_state: TradeLifecycleState
    transition: TradeLifecycleTransition
    entry_decision: EntryDecision | None
    exit_decision: PositionExitDecision | None
    audit_markers: ReplayAuditMarkerState
    execution_proxy_used: bool = False

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
        if self.execution_proxy_used and not self.event_consumed:
            raise ValueError("canonical readiness proxy must be consumed on the same bar")

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
    """Sequential replay result with ownership and restart-safe audit progression."""

    initial_state: TradeLifecycleState
    final_state: TradeLifecycleState
    rows: tuple[CanonicalLifecycleReplayRow, ...]
    initial_audit_markers: ReplayAuditMarkerState = ReplayAuditMarkerState()
    final_audit_markers: ReplayAuditMarkerState = ReplayAuditMarkerState()

    def __post_init__(self) -> None:
        expected = self.initial_state
        for row in self.rows:
            if row.previous_state != expected:
                raise ValueError("canonical lifecycle replay rows must form one contiguous chain")
            expected = row.current_state
        if expected != self.final_state:
            raise ValueError("canonical lifecycle replay final state must match row chain")
        if not self.rows and self.initial_audit_markers != self.final_audit_markers:
            raise ValueError("empty replay cannot mutate audit marker state")


def _validate_initial_state(state: TradeLifecycleState) -> None:
    if state.position is PositionState.OPEN and state.entry_metadata is None:
        raise ValueError(
            "canonical lifecycle replay requires entry metadata for an initial OPEN position"
        )


def _proxy_event(as_of: Any, *, side: StructuralDirection, reason: str) -> ExecutionTriggerEvent:
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=side,
        timeframe="30m",
        observed_at=as_of,
        available_at=as_of,
        reason=reason,
        source_refs=(),
    )


def _qualified_st_entry(entry: Any) -> bool:
    return (
        getattr(entry, "selected_horizon", None) is DecisionHorizon.SHORT_TERM
        and getattr(entry, "scenario_stage", None) is ScenarioStage.QUALIFIED
        and getattr(getattr(entry, "arbitration", None), "selected_scenario", None) is not None
    )


def _compose_flat_entry(
    snapshot: "DecisionInputSnapshot",
    state: TradeLifecycleState,
    *,
    config: "DecisionEngineConfig | None",
    raw_event: ExecutionTriggerEvent | None,
) -> tuple[Any, ExecutionTriggerEvent | None, STSetupContinuityAssessment | None]:
    """Apply Step-10 novelty before a fresh execution event can be consumed."""

    previous = state.last_closed_st_movement
    if previous is None:
        return (
            snapshot.entry_decision(config=config, execution_event=raw_event),
            raw_event,
            None,
        )

    pre_entry = snapshot.entry_decision(config=config, execution_event=None)
    if not _qualified_st_entry(pre_entry):
        if raw_event is None:
            return pre_entry, None, None
        return (
            snapshot.entry_decision(config=config, execution_event=raw_event),
            raw_event,
            None,
        )

    continuity = assess_st_setup_continuity(snapshot, pre_entry, previous)
    gated_pre = apply_st_reentry_novelty_policy(pre_entry, continuity)
    if not continuity.reentry_allowed:
        # A fresh timing event is deliberately ignored rather than consumed because
        # execution freshness is not economic setup novelty.
        return gated_pre, None, continuity
    if raw_event is None:
        return gated_pre, None, continuity

    final_entry = snapshot.entry_decision(config=config, execution_event=raw_event)
    return apply_st_reentry_novelty_policy(final_entry, continuity), raw_event, continuity


def _markers_for_row(
    markers: ReplayAuditMarkerState,
    *,
    snapshot: "DecisionInputSnapshot",
    previous_state: TradeLifecycleState,
    entry: EntryDecision | None,
    exit_decision: PositionExitDecision | None,
) -> ReplayAuditMarkerState:
    if previous_state.position is PositionState.FLAT:
        qualified_at = None
        qualified_price = None
        scenario_key = None
        ready_at = None
        ready_price = None
        if (
            entry is not None
            and entry.selected_horizon is not None
            and entry.scenario_stage is ScenarioStage.QUALIFIED
            and entry.arbitration.selected_scenario is not None
        ):
            scenario = entry.arbitration.selected_scenario
            key = (entry.selected_horizon.value, scenario.kind.value)
            if markers.scenario_key == key:
                qualified_at = markers.scenario_qualified_at
                qualified_price = markers.scenario_qualified_price
                ready_at = markers.ready_for_execution_at
                ready_price = markers.ready_for_execution_price
            else:
                qualified_at = snapshot.as_of
                qualified_price = float(snapshot.current_price)
            scenario_key = key
            if entry.action in {DecisionAction.READY, DecisionAction.BUY} and ready_at is None:
                ready_at = snapshot.as_of
                ready_price = float(snapshot.current_price)
        return ReplayAuditMarkerState(
            scenario_qualified_at=qualified_at,
            scenario_qualified_price=qualified_price,
            scenario_key=scenario_key,
            ready_for_execution_at=ready_at,
            ready_for_execution_price=ready_price,
        )

    exit_watch_at = markers.exit_watch_at
    exit_watch_price = markers.exit_watch_price
    exit_ready_at = markers.exit_ready_at
    exit_ready_price = markers.exit_ready_price
    if exit_decision is not None:
        if exit_decision.stage is ExitStage.EXIT_WATCH and exit_watch_at is None:
            exit_watch_at = snapshot.as_of
            exit_watch_price = float(snapshot.current_price)
        elif exit_decision.stage is ExitStage.MONITOR:
            exit_watch_at = None
            exit_watch_price = None
        if exit_decision.stage is ExitStage.EXIT_READY and exit_ready_at is None:
            exit_ready_at = snapshot.as_of
            exit_ready_price = float(snapshot.current_price)
        elif exit_decision.stage is not ExitStage.EXIT_READY:
            exit_ready_at = None
            exit_ready_price = None

    return replace(
        markers,
        exit_watch_at=exit_watch_at,
        exit_watch_price=exit_watch_price,
        exit_ready_at=exit_ready_at,
        exit_ready_price=exit_ready_price,
    )


def _markers_after_action(
    markers: ReplayAuditMarkerState,
    action: DecisionAction,
) -> ReplayAuditMarkerState:
    if action is DecisionAction.BUY:
        return replace(
            markers,
            scenario_qualified_at=None,
            scenario_qualified_price=None,
            scenario_key=None,
            ready_for_execution_at=None,
            ready_for_execution_price=None,
        )
    if action is DecisionAction.SELL:
        return replace(
            markers,
            exit_watch_at=None,
            exit_watch_price=None,
            exit_ready_at=None,
            exit_ready_price=None,
        )
    return markers


def replay_canonical_trade_lifecycle(
    snapshots: Iterable["DecisionInputSnapshot"],
    *,
    config: "DecisionEngineConfig | None" = None,
    entry_execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
    exit_execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
    initial_state: TradeLifecycleState | None = None,
    initial_audit_markers: ReplayAuditMarkerState | None = None,
    readiness_execution_proxy: bool = False,
) -> CanonicalLifecycleReplayResult:
    """Replay the canonical long-only ownership path over frozen snapshots.

    FLAT bars evaluate only the entry path and OPEN bars evaluate only the exit path.
    Execution events are looked up only on their current bar and never cached.
    Step-10 re-entry novelty is evaluated before a fresh entry event can be consumed;
    first entries and LT entries retain their existing entry contracts. Audit marker
    state remains independent from trading ownership so restart does not alter output.

    ``readiness_execution_proxy`` is hindsight-audit infrastructure only. When no raw
    execution event exists, it substitutes a same-bar confirmed 30m event exactly at
    an already-computed READY or EXIT_READY boundary. It cannot bypass the Step-10
    economic novelty gate and is explicitly marked on the replay row.
    """

    state = initial_state or TradeLifecycleState()
    _validate_initial_state(state)
    starting_state = state
    markers = initial_audit_markers or ReplayAuditMarkerState()
    starting_markers = markers
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
        proxy_used = False

        if state.position is PositionState.FLAT:
            raw_entry_event = entry_events.get(snapshot.as_of)
            entry, transition_entry_event, continuity = _compose_flat_entry(
                snapshot,
                state,
                config=config,
                raw_event=raw_entry_event,
            )
            if (
                readiness_execution_proxy
                and transition_entry_event is None
                and raw_entry_event is None
                and entry.action is DecisionAction.READY
            ):
                proxy_event = _proxy_event(
                    snapshot.as_of,
                    side=StructuralDirection.LONG,
                    reason="AUDIT_PROXY_CANONICAL_ENTRY_READY",
                )
                proxied = snapshot.entry_decision(
                    config=config,
                    execution_event=proxy_event,
                )
                entry = (
                    proxied
                    if continuity is None
                    else apply_st_reentry_novelty_policy(proxied, continuity)
                )
                if entry.action is DecisionAction.BUY:
                    transition_entry_event = proxy_event
                    proxy_used = True
            transition = transition_entry_lifecycle(
                state,
                entry,
                snapshot,
                execution_event=transition_entry_event,
            )
        else:
            raw_exit_event = exit_events.get(snapshot.as_of)
            exit_decision = snapshot.position_exit_decision(
                state,
                execution_event=raw_exit_event,
            )
            if (
                readiness_execution_proxy
                and raw_exit_event is None
                and exit_decision.stage is ExitStage.EXIT_READY
                and exit_decision.action is DecisionAction.HOLD
            ):
                raw_exit_event = _proxy_event(
                    snapshot.as_of,
                    side=StructuralDirection.SHORT,
                    reason="AUDIT_PROXY_CANONICAL_EXIT_READY",
                )
                exit_decision = snapshot.position_exit_decision(
                    state,
                    execution_event=raw_exit_event,
                )
                proxy_used = exit_decision.action is DecisionAction.SELL

            observed_history = observe_st_economic_history(snapshot, state)
            observed_state = (
                state
                if observed_history == state.st_economic_history
                else replace(state, st_economic_history=observed_history)
            )
            observed_transition = transition_position_exit_lifecycle(observed_state, exit_decision)
            # One replay row remains one contiguous atomic transition from the prior
            # bar's state. Only current state receives the causal history fold.
            transition = replace(observed_transition, previous=state)

        row_markers = _markers_for_row(
            markers,
            snapshot=snapshot,
            previous_state=previous,
            entry=entry,
            exit_decision=exit_decision,
        )
        state = transition.current
        rows.append(
            CanonicalLifecycleReplayRow(
                snapshot=snapshot,
                previous_state=previous,
                current_state=state,
                transition=transition,
                entry_decision=entry,
                exit_decision=exit_decision,
                audit_markers=row_markers,
                execution_proxy_used=proxy_used,
            )
        )
        markers = _markers_after_action(row_markers, transition.action)
        previous_as_of = snapshot.as_of

    return CanonicalLifecycleReplayResult(
        initial_state=starting_state,
        final_state=state,
        rows=tuple(rows),
        initial_audit_markers=starting_markers,
        final_audit_markers=markers,
    )


__all__ = [
    "CanonicalLifecycleReplayResult",
    "CanonicalLifecycleReplayRow",
    "ReplayAuditMarkerState",
    "replay_canonical_trade_lifecycle",
]
