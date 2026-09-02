from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Iterable

from .composer import DecisionAction, FinalDecision
from .position_metadata import PositionEntryMetadata, build_position_entry_metadata
from .st_economic_history import STEconomicHistory, initial_st_economic_history
from .st_exit_intent import (
    STClosedExitRecord,
    STExitFamily,
    STExitIntent,
    canonical_exit_lineage,
    canonical_exit_reasons,
)
from .structural import DecisionHorizon

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot
    from .entry import EntryDecision
    from .execution import ExecutionTriggerEvent


class PositionState(StrEnum):
    """Persistent ownership state for the long-only trade lifecycle."""

    FLAT = "FLAT"
    OPEN = "OPEN"


class ExitStage(StrEnum):
    """Persistent maturity of the dedicated long-position exit path."""

    MONITOR = "MONITOR"
    EXIT_WATCH = "EXIT_WATCH"
    EXIT_READY = "EXIT_READY"


@dataclass(frozen=True, slots=True)
class TradeLifecycleState:
    position: PositionState = PositionState.FLAT
    exit_stage: ExitStage | None = None
    trade_id: str | None = None
    entry_as_of: Any | None = None
    entry_metadata: PositionEntryMetadata | None = None
    st_economic_history: STEconomicHistory | None = None
    st_exit_intent: STExitIntent | None = None
    last_closed_st_exit: STClosedExitRecord | None = None

    def __post_init__(self) -> None:
        if self.position is PositionState.FLAT:
            if (
                self.exit_stage is not None
                or self.trade_id is not None
                or self.entry_as_of is not None
                or self.entry_metadata is not None
                or self.st_economic_history is not None
                or self.st_exit_intent is not None
            ):
                raise ValueError("FLAT lifecycle state cannot carry open-trade metadata")
            return

        if self.exit_stage is None or self.trade_id is None or self.entry_as_of is None:
            raise ValueError("OPEN lifecycle state requires exit stage and entry metadata")
        if self.entry_metadata is not None and self.entry_metadata.entry_as_of != self.entry_as_of:
            raise ValueError("position entry metadata must share lifecycle entry_as_of")
        if (
            self.st_economic_history is not None
            and self.entry_metadata is not None
            and self.entry_metadata.entry_horizon is not DecisionHorizon.SHORT_TERM
        ):
            raise ValueError("ST economic history may belong only to a short-term position")
        if self.st_exit_intent is not None:
            if self.entry_metadata is None:
                raise ValueError("terminal ST exit intent requires frozen entry metadata")
            if self.entry_metadata.entry_horizon is not DecisionHorizon.SHORT_TERM:
                raise ValueError("terminal ST exit intent may belong only to a short-term position")


@dataclass(frozen=True, slots=True)
class TradeLifecycleTransition:
    previous: TradeLifecycleState
    current: TradeLifecycleState
    requested_action: DecisionAction
    action: DecisionAction
    reason: str
    as_of: Any

    @property
    def changed_position(self) -> bool:
        return self.previous.position is not self.current.position


def _trade_id(as_of: Any) -> str:
    iso = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)
    return f"trade:{iso}"


def _open_state_with_stage(state: TradeLifecycleState, stage: ExitStage) -> TradeLifecycleState:
    return TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=stage,
        trade_id=state.trade_id,
        entry_as_of=state.entry_as_of,
        entry_metadata=state.entry_metadata,
        st_economic_history=state.st_economic_history,
        st_exit_intent=state.st_exit_intent,
        last_closed_st_exit=state.last_closed_st_exit,
    )


def transition_st_exit_intent(
    state: TradeLifecycleState,
    requested_family: STExitFamily | None,
    *,
    as_of: Any | None = None,
    reasons: Iterable[str] = (),
    source_lineage: Iterable[str] = (),
) -> TradeLifecycleState:
    """Fold one final ST economic-exit request into monotonic lifecycle intent.

    ``None`` means the current policy evaluation did not commit a new terminal exit.
    Once intent exists it survives later HOLD/uncertain evaluations. HARVEST may
    escalate to PROTECTIVE, while PROTECTIVE can never be downgraded to HARVEST.

    This function changes lifecycle state only. It deliberately does not alter the
    canonical exit stage, execution gating, or BUY/SELL action.
    """

    if state.position is not PositionState.OPEN:
        if requested_family is None:
            return state
        raise ValueError("terminal ST exit intent requires OPEN lifecycle ownership")

    metadata = state.entry_metadata
    if metadata is None or metadata.entry_horizon is not DecisionHorizon.SHORT_TERM:
        if requested_family is None:
            return state
        raise ValueError("terminal ST exit intent requires short-term entry ownership")

    existing = state.st_exit_intent
    if requested_family is None:
        return state
    if as_of is None:
        raise ValueError("terminal ST exit intent request requires as_of")

    canonical_reasons = canonical_exit_reasons(reasons)
    canonical_lineage = canonical_exit_lineage(source_lineage)

    if existing is not None:
        if existing.family is STExitFamily.PROTECTIVE_EXIT:
            return state
        if requested_family is STExitFamily.PROFIT_HARVEST:
            return state
        # The only remaining monotonic transition is HARVEST -> PROTECTIVE.

    intent = STExitIntent(
        family=requested_family,
        committed_at=as_of,
        reasons=canonical_reasons,
        source_lineage=canonical_lineage,
    )
    return replace(state, st_exit_intent=intent)


def _closed_st_exit_record(state: TradeLifecycleState, *, exit_as_of: Any) -> STClosedExitRecord | None:
    intent = state.st_exit_intent
    if intent is None:
        return None
    if state.trade_id is None or state.entry_as_of is None:
        raise ValueError("terminal ST exit execution requires trade identity")
    return STClosedExitRecord(
        trade_id=state.trade_id,
        entry_as_of=state.entry_as_of,
        exit_as_of=exit_as_of,
        family=intent.family,
        intent_committed_at=intent.committed_at,
        reasons=intent.reasons,
        source_lineage=intent.source_lineage,
    )


def transition_trade_lifecycle(
    state: TradeLifecycleState,
    final: FinalDecision,
    *,
    as_of: Any,
    exit_stage: ExitStage | None = None,
    exit_execution_confirmed: bool = False,
    entry_metadata: PositionEntryMetadata | None = None,
) -> TradeLifecycleTransition:
    """Fold one market decision through persistent long-only ownership.

    The optional ``entry_metadata`` is a compatibility bridge for the dedicated
    entry path. When supplied on the opening BUY it is frozen into the position and
    is never replaced by later repeated BUY decisions.

    Step 7 terminal ST exit intent is orthogonal to execution. Existing exit-stage
    and fresh-event requirements remain unchanged in this transition.
    """

    requested = final.action

    if state.position is PositionState.FLAT:
        if exit_execution_confirmed:
            raise ValueError("exit execution cannot be confirmed while lifecycle is FLAT")
        if entry_metadata is not None and requested is not DecisionAction.BUY:
            raise ValueError("entry metadata may be attached only to an opening BUY")
        if requested is DecisionAction.BUY:
            if entry_metadata is not None and entry_metadata.entry_as_of != as_of:
                raise ValueError("entry metadata must be fresh at lifecycle as_of")
            current = TradeLifecycleState(
                position=PositionState.OPEN,
                exit_stage=ExitStage.MONITOR,
                trade_id=_trade_id(as_of),
                entry_as_of=as_of,
                entry_metadata=entry_metadata,
                st_economic_history=initial_st_economic_history(entry_metadata),
                last_closed_st_exit=state.last_closed_st_exit,
            )
            return TradeLifecycleTransition(
                state,
                current,
                requested,
                DecisionAction.BUY,
                "LIFECYCLE_FLAT_ENTRY_EXECUTED",
                as_of,
            )
        if requested is DecisionAction.SELL:
            return TradeLifecycleTransition(
                state,
                state,
                requested,
                DecisionAction.WAIT,
                "LIFECYCLE_FLAT_SELL_SUPPRESSED",
                as_of,
            )
        if requested is DecisionAction.HOLD:
            raise ValueError("HOLD cannot be requested while lifecycle is FLAT")
        return TradeLifecycleTransition(
            state,
            state,
            requested,
            requested,
            "LIFECYCLE_FLAT_NO_POSITION_CHANGE",
            as_of,
        )

    # OPEN ownership is immutable. Any metadata supplied by a later/repeated entry
    # decision is deliberately ignored rather than backfilling or promoting the
    # original entry horizon from later market information.
    target_stage = exit_stage or state.exit_stage or ExitStage.MONITOR
    if exit_execution_confirmed:
        if target_stage is not ExitStage.EXIT_READY:
            raise ValueError("long exit execution requires EXIT_READY stage")
        return TradeLifecycleTransition(
            state,
            TradeLifecycleState(
                last_closed_st_exit=_closed_st_exit_record(state, exit_as_of=as_of)
            ),
            requested,
            DecisionAction.SELL,
            "LIFECYCLE_OPEN_EXIT_EXECUTED_CONFIRMED_EVENT",
            as_of,
        )

    current = _open_state_with_stage(state, target_stage)
    if requested is DecisionAction.BUY:
        reason = "LIFECYCLE_REPEATED_BUY_SUPPRESSED"
    elif requested is DecisionAction.SELL:
        reason = "LIFECYCLE_LEGACY_SELL_IGNORED_BY_LONG_EXIT_CONTRACT"
    elif state.exit_stage is not target_stage:
        reason = f"LIFECYCLE_EXIT_STAGE_{state.exit_stage.value}_TO_{target_stage.value}"
    elif target_stage is ExitStage.EXIT_READY:
        reason = "LIFECYCLE_EXIT_READY_AWAITING_FRESH_EVENT"
    elif target_stage is ExitStage.EXIT_WATCH:
        reason = "LIFECYCLE_EXIT_WATCH_POSITION_HELD"
    else:
        reason = "LIFECYCLE_OPEN_POSITION_HELD"

    return TradeLifecycleTransition(
        state,
        current,
        requested,
        DecisionAction.HOLD,
        reason,
        as_of,
    )


def transition_entry_lifecycle(
    state: TradeLifecycleState,
    entry: "EntryDecision",
    snapshot: "DecisionInputSnapshot",
    *,
    execution_event: "ExecutionTriggerEvent | None" = None,
) -> TradeLifecycleTransition:
    """Apply one entry result without reinterpreting its market semantics.

    Only a FLAT->BUY transition creates metadata. Repeated BUY while OPEN is handled
    by the existing suppression rule and cannot overwrite the original entry record.
    """

    if snapshot.as_of is None:
        raise ValueError("entry lifecycle snapshot as_of must be known")

    metadata: PositionEntryMetadata | None = None
    if state.position is PositionState.FLAT and entry.action is DecisionAction.BUY:
        if execution_event is None:
            raise ValueError("opening BUY requires raw execution event for position metadata")
        metadata = build_position_entry_metadata(
            snapshot,
            entry,
            execution_event=execution_event,
        )

    return transition_trade_lifecycle(
        state,
        entry,  # EntryDecision and FinalDecision share the action contract.
        as_of=snapshot.as_of,
        entry_metadata=metadata,
    )


__all__ = [
    "ExitStage",
    "PositionState",
    "TradeLifecycleState",
    "TradeLifecycleTransition",
    "transition_entry_lifecycle",
    "transition_st_exit_intent",
    "transition_trade_lifecycle",
]
