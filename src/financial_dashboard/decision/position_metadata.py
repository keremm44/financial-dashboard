from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING, Any

from .composer import DecisionAction
from .execution import ExecutionTriggerEvent, ExecutionTriggerState
from .scenario import ScenarioKind, ScenarioPresence, ScenarioStage
from .st_thesis_identity import (
    STDefendedAnchorKind,
    STEconomicMission,
    STThesisFamily,
    classify_executed_st_thesis,
)
from .structural import DecisionHorizon, StructuralDirection

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot
    from .entry import EntryDecision


@dataclass(frozen=True, slots=True)
class STInitialDefendedAnchor:
    """Compact entry-time economic anchor safe for persistent trade memory.

    The native/domain snapshot and FactRef objects are intentionally not copied into
    lifecycle state. Only the entry-time identity and price bounds needed to preserve
    the original ST thesis are retained.
    """

    kind: STDefendedAnchorKind
    identity: str
    timeframe: str
    low: float
    high: float

    def __post_init__(self) -> None:
        if not isinstance(self.kind, STDefendedAnchorKind):
            raise ValueError("ST trade-memory defended anchor kind is invalid")
        if not isinstance(self.identity, str) or not self.identity.strip() or self.identity != self.identity.strip():
            raise ValueError("ST trade-memory defended anchor identity must be a canonical string")
        if (
            not isinstance(self.timeframe, str)
            or not self.timeframe.strip()
            or self.timeframe != self.timeframe.strip().lower()
        ):
            raise ValueError("ST trade-memory defended anchor timeframe must be canonical")
        if not isfinite(float(self.low)) or not isfinite(float(self.high)):
            raise ValueError("ST trade-memory defended anchor bounds must be finite")
        if float(self.low) > float(self.high):
            raise ValueError("ST trade-memory defended anchor low cannot exceed high")


@dataclass(frozen=True, slots=True)
class STTradeMemory:
    """Minimal immutable ST economic identity persisted for one open trade.

    This stores causal entry facts only. Maturity, healthy-base state, continuation
    failures and CONSUMED are deliberately absent and must remain derived later.
    Initial target identity, entry price and entry as_of already live on the enclosing
    PositionEntryMetadata and are not duplicated here.
    """

    thesis_family: STThesisFamily
    economic_mission: STEconomicMission
    initial_defended_anchor: STInitialDefendedAnchor | None

    def __post_init__(self) -> None:
        if not isinstance(self.thesis_family, STThesisFamily):
            raise ValueError("ST trade-memory thesis family is invalid")
        if not isinstance(self.economic_mission, STEconomicMission):
            raise ValueError("ST trade-memory economic mission is invalid")

        if self.thesis_family is STThesisFamily.UNRESOLVED:
            if self.economic_mission is not STEconomicMission.UNRESOLVED:
                raise ValueError("unresolved ST trade memory cannot carry a resolved mission")
            if self.initial_defended_anchor is not None:
                raise ValueError("unresolved ST trade memory cannot invent a defended anchor")
            return

        expected_mission = {
            STThesisFamily.PULLBACK_CONTINUATION: STEconomicMission.CONTINUE_AFTER_BUYER_REGAIN,
            STThesisFamily.BREAKOUT_ACCEPTANCE: STEconomicMission.EXPAND_FROM_ACCEPTED_HIGHER_AREA,
            STThesisFamily.FAILED_SELL_RECLAIM: STEconomicMission.CAPTURE_FAILED_SELL_RECLAIM,
        }[self.thesis_family]
        expected_anchor_kind = {
            STThesisFamily.PULLBACK_CONTINUATION: STDefendedAnchorKind.REACTION_ZONE,
            STThesisFamily.BREAKOUT_ACCEPTANCE: STDefendedAnchorKind.BREAKOUT_ROLE_SUPPORT,
            STThesisFamily.FAILED_SELL_RECLAIM: STDefendedAnchorKind.FAILED_SELL_RECLAIM_LEVEL,
        }[self.thesis_family]
        if self.economic_mission is not expected_mission:
            raise ValueError("ST trade-memory thesis family and economic mission are inconsistent")
        if self.initial_defended_anchor is None:
            raise ValueError("resolved ST trade memory requires the entry-time defended anchor")
        if self.initial_defended_anchor.kind is not expected_anchor_kind:
            raise ValueError("ST trade-memory thesis family and defended anchor are inconsistent")


@dataclass(frozen=True, slots=True)
class PositionEntryMetadata:
    """Immutable facts captured at the instant an entry BUY is executed.

    This is ownership/audit metadata, not fresh market evidence. It must never be
    recomputed from later bars or used to invent a different entry horizon while a
    position remains open.

    For SHORT_TERM entries, ``st_trade_memory`` is built from the Step-1 causal thesis
    shadow on the entry snapshot. The legacy ``active_target_identity`` field is the
    frozen initial target reference for the trade; it is intentionally not duplicated
    inside STTradeMemory.
    """

    symbol: str
    entry_horizon: DecisionHorizon
    scenario_kind: ScenarioKind
    entry_as_of: Any
    entry_price: float
    active_target_identity: str | None
    execution_timeframe: str
    execution_observed_at: Any
    execution_available_at: Any
    execution_reason: str
    source_lineage: tuple[str, ...]
    st_trade_memory: STTradeMemory | None = None

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("position entry metadata symbol must be non-empty")
        if self.entry_as_of is None or self.execution_observed_at is None or self.execution_available_at is None:
            raise ValueError("position entry metadata timestamps must be known")
        if self.entry_as_of != self.execution_observed_at:
            raise ValueError("position execution must be fresh at entry_as_of")
        try:
            if self.execution_available_at > self.entry_as_of:
                raise ValueError("position execution availability cannot be after entry_as_of")
        except TypeError as exc:
            raise TypeError("position entry metadata timestamps must be comparable") from exc
        if not isfinite(float(self.entry_price)) or float(self.entry_price) <= 0.0:
            raise ValueError("position entry price must be finite and positive")
        if self.scenario_kind is ScenarioKind.NONE:
            raise ValueError("open position metadata requires a concrete entry scenario")
        if self.active_target_identity is not None and (
            not isinstance(self.active_target_identity, str)
            or not self.active_target_identity.strip()
            or self.active_target_identity != self.active_target_identity.strip()
        ):
            raise ValueError("position entry target identity must be a canonical string when present")
        if self.execution_timeframe.strip().lower() != "30m":
            raise ValueError("v1 position entry execution timeframe is fixed to 30m")
        if not self.execution_reason.strip():
            raise ValueError("position entry execution reason must be non-empty")
        canonical = tuple(sorted(set(self.source_lineage)))
        if self.source_lineage != canonical:
            raise ValueError("position entry source lineage must be sorted and unique")
        if self.entry_horizon is DecisionHorizon.LONG_TERM and self.st_trade_memory is not None:
            raise ValueError("LT position metadata cannot carry ST trade memory")
        if (
            self.st_trade_memory is not None
            and self.st_trade_memory.thesis_family is not STThesisFamily.UNRESOLVED
            and self.active_target_identity is None
        ):
            raise ValueError("resolved ST trade memory requires the frozen initial target reference")

    @property
    def initial_target_identity(self) -> str | None:
        """Semantic alias for the entry-frozen target reference."""

        return self.active_target_identity


def _build_st_trade_memory(
    snapshot: "DecisionInputSnapshot",
    entry: "EntryDecision",
) -> STTradeMemory:
    shadow = classify_executed_st_thesis(snapshot, entry)
    if shadow is None:
        raise ValueError("executed ST BUY must produce an entry-time thesis shadow")

    anchor = shadow.initial_defended_anchor
    persistent_anchor = (
        None
        if anchor is None
        else STInitialDefendedAnchor(
            kind=anchor.kind,
            identity=anchor.identity,
            timeframe=anchor.timeframe.strip().lower(),
            low=float(anchor.low),
            high=float(anchor.high),
        )
    )
    return STTradeMemory(
        thesis_family=shadow.family,
        economic_mission=shadow.economic_mission,
        initial_defended_anchor=persistent_anchor,
    )


def build_position_entry_metadata(
    snapshot: "DecisionInputSnapshot",
    entry: "EntryDecision",
    *,
    execution_event: ExecutionTriggerEvent,
) -> PositionEntryMetadata:
    """Freeze entry-origin facts from one actually executed Turn 6 BUY.

    The raw execution event is required even though Turn 6 already assessed it. This
    prevents persistence from fabricating execution provenance from a boolean flag.
    SHORT_TERM entries additionally freeze only the compact Step-1 economic identity;
    no current domain snapshot is copied into trade state.
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

    st_trade_memory = (
        _build_st_trade_memory(snapshot, entry)
        if entry.selected_horizon is DecisionHorizon.SHORT_TERM
        else None
    )

    return PositionEntryMetadata(
        symbol=str(snapshot.symbol),
        entry_horizon=entry.selected_horizon,
        scenario_kind=scenario.kind,
        entry_as_of=snapshot.as_of,
        entry_price=float(snapshot.current_price),
        active_target_identity=scenario.active_target_identity,
        execution_timeframe=execution_event.timeframe.strip().lower(),
        execution_observed_at=execution_event.observed_at,
        execution_available_at=execution_event.available_at,
        execution_reason=execution_event.reason.strip(),
        source_lineage=tuple(sorted(set(entry.source_lineage))),
        st_trade_memory=st_trade_memory,
    )


__all__ = [
    "PositionEntryMetadata",
    "STInitialDefendedAnchor",
    "STTradeMemory",
    "build_position_entry_metadata",
]
