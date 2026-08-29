from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING, Any

from .composer import DecisionAction
from .execution import ExecutionTriggerEvent, ExecutionTriggerState
from .scenario import ScenarioKind, ScenarioPresence, ScenarioStage
from .structural import DecisionHorizon, StructuralDirection

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot
    from .entry import EntryDecision


@dataclass(frozen=True, slots=True)
class PositionEntryMetadata:
    """Immutable facts captured at the instant an entry BUY is executed.

    This is ownership/audit metadata, not fresh market evidence. It must never be
    recomputed from later bars or used to invent a different entry horizon while a
    position remains open.
    """

    symbol: str
    entry_horizon: DecisionHorizon
    scenario_kind: ScenarioKind
    entry_as_of: Any
    entry_price: float
    active_target_identity: str | None
    execution_timeframe: str
    execution_observed_at: Any
    execution_reason: str
    source_lineage: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("position entry metadata symbol must be non-empty")
        if self.entry_as_of is None or self.execution_observed_at is None:
            raise ValueError("position entry metadata timestamps must be known")
        if self.entry_as_of != self.execution_observed_at:
            raise ValueError("position execution must be fresh at entry_as_of")
        if not isfinite(float(self.entry_price)) or float(self.entry_price) <= 0.0:
            raise ValueError("position entry price must be finite and positive")
        if self.scenario_kind is ScenarioKind.NONE:
            raise ValueError("open position metadata requires a concrete entry scenario")
        if self.execution_timeframe.strip().lower() != "30m":
            raise ValueError("v1 position entry execution timeframe is fixed to 30m")
        if not self.execution_reason.strip():
            raise ValueError("position entry execution reason must be non-empty")
        canonical = tuple(sorted(set(self.source_lineage)))
        if self.source_lineage != canonical:
            raise ValueError("position entry source lineage must be sorted and unique")


def build_position_entry_metadata(
    snapshot: "DecisionInputSnapshot",
    entry: "EntryDecision",
    *,
    execution_event: ExecutionTriggerEvent,
) -> PositionEntryMetadata:
    """Freeze entry-origin facts from one actually executed Turn 6 BUY.

    The raw execution event is required even though Turn 6 already assessed it. This
    prevents persistence from fabricating execution provenance from a boolean flag.
    """

    if entry.action is not DecisionAction.BUY:
        raise ValueError("position metadata can be created only from an executed BUY")
    if entry.selected_horizon is None:
        raise ValueError("executed BUY requires selected entry horizon")
    if entry.scenario_stage is not ScenarioStage.QUALIFIED:
        raise ValueError("executed BUY requires QUALIFIED selected scenario")
    if entry.execution_state is not ExecutionTriggerState.CONFIRMED:
        raise ValueError("executed BUY requires CONFIRMED execution state")
    if not entry.execution_event_consumed:
        raise ValueError("executed BUY metadata requires consumed execution event")

    scenario = entry.arbitration.selected_scenario
    if scenario is None:
        raise ValueError("executed BUY requires selected scenario")
    if scenario.horizon is not entry.selected_horizon:
        raise ValueError("entry scenario horizon must match selected horizon")
    if scenario.presence is not ScenarioPresence.PRESENT:
        raise ValueError("executed BUY scenario must be PRESENT")
    if scenario.stage is not ScenarioStage.QUALIFIED:
        raise ValueError("executed BUY scenario must remain QUALIFIED")
    if scenario.structural_direction is not StructuralDirection.LONG:
        raise ValueError("current position product accepts long-entry scenario only")
    if scenario.kind is ScenarioKind.NONE:
        raise ValueError("executed BUY requires concrete scenario kind")

    if snapshot.as_of is None:
        raise ValueError("position entry snapshot as_of must be known")
    if not str(snapshot.symbol).strip():
        raise ValueError("position entry snapshot symbol must be non-empty")

    if execution_event.state is not ExecutionTriggerState.CONFIRMED:
        raise ValueError("position entry event must be CONFIRMED")
    if execution_event.side is not StructuralDirection.LONG:
        raise ValueError("position entry event must match long-entry product")
    if execution_event.timeframe.strip().lower() != "30m":
        raise ValueError("position entry event timeframe must be 30m")
    if execution_event.observed_at != snapshot.as_of:
        raise ValueError("position entry event must be fresh at snapshot as_of")
    try:
        if execution_event.available_at > snapshot.as_of:
            raise ValueError("future-unavailable position entry event cannot be stored")
    except TypeError as exc:
        raise TypeError("position entry event timestamps must be comparable") from exc

    # Pullback continuation is an ST-duration swing even when LT thesis permitted
    # the BUY. Freeze the exit clock to SHORT_TERM so 1H owns the trade, not a
    # month-long LT hold that sells the washout.
    entry_horizon = (
        DecisionHorizon.SHORT_TERM
        if scenario.kind is ScenarioKind.PULLBACK_CONTINUATION
        else entry.selected_horizon
    )

    return PositionEntryMetadata(
        symbol=str(snapshot.symbol),
        entry_horizon=entry_horizon,
        scenario_kind=scenario.kind,
        entry_as_of=snapshot.as_of,
        entry_price=float(snapshot.current_price),
        active_target_identity=scenario.active_target_identity,
        execution_timeframe=execution_event.timeframe.strip().lower(),
        execution_observed_at=execution_event.observed_at,
        execution_reason=execution_event.reason.strip(),
        source_lineage=tuple(sorted(set(entry.source_lineage))),
    )


__all__ = ["PositionEntryMetadata", "build_position_entry_metadata"]
