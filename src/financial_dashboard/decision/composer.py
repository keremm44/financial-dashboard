from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .eligibility import EligibilityAssessment, EligibilityState
from .execution import ExecutionTriggerAssessment, ExecutionTriggerState
from .position import PositionContext, PositionSide, position_exit_candidate
from .structural import DecisionHorizon, StructuralAssessment, StructuralDirection


class DecisionAction(StrEnum):
    WAIT = "WAIT"
    READY = "READY"
    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"
    HOLD = "HOLD"


class ActionSide(StrEnum):
    NONE = "NONE"
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    """Thin brokerage/action-capability policy, separate from market assessment."""

    permitted_sides: tuple[StructuralDirection, ...] = (StructuralDirection.LONG,)

    def __post_init__(self) -> None:
        if not self.permitted_sides:
            raise ValueError("action policy must permit at least one directional side")
        if any(side is StructuralDirection.UNRESOLVED for side in self.permitted_sides):
            raise ValueError("action policy cannot permit UNRESOLVED")
        if len(set(self.permitted_sides)) != len(self.permitted_sides):
            raise ValueError("action policy permitted_sides must be unique")


@dataclass(frozen=True, slots=True)
class FinalDecision:
    horizon: DecisionHorizon
    market_side: StructuralDirection
    action_side: ActionSide
    action: DecisionAction
    eligibility: EligibilityState
    execution_trigger: ExecutionTriggerState
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    waiting_for: tuple[str, ...]
    source_lineage: tuple[str, ...] = ()
    position_before: PositionSide = PositionSide.FLAT
    position_after: PositionSide = PositionSide.FLAT


def _action_side(side: StructuralDirection) -> ActionSide:
    if side is StructuralDirection.LONG:
        return ActionSide.LONG
    if side is StructuralDirection.SHORT:
        return ActionSide.SHORT
    return ActionSide.NONE


def _position_action_side(side: PositionSide) -> ActionSide:
    if side is PositionSide.LONG:
        return ActionSide.LONG
    if side is PositionSide.SHORT:
        return ActionSide.SHORT
    return ActionSide.NONE


def _lineage(refs) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                ref.lineage_id or f"{ref.domain.value}:{ref.timeframe}:{ref.native_id}"
                for ref in refs
            }
        )
    )


def _decision_lineage(
    structural: StructuralAssessment,
    execution: ExecutionTriggerAssessment,
    additional_lineage: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            set(additional_lineage)
            | set(_lineage(structural.source_refs))
            | set(_lineage(execution.source_refs))
        )
    )


def compose_final_decision(
    structural: StructuralAssessment,
    *,
    eligibility: EligibilityAssessment,
    execution: ExecutionTriggerAssessment,
    policy: ActionPolicy | None = None,
    additional_lineage: tuple[str, ...] = (),
) -> FinalDecision:
    """Compose the frozen flat-state entry path.

    This public function intentionally retains its original narrow contract: it sees
    only Structure, frozen eligibility, the explicit execution assessment and action
    capability policy. Position management is layered by ``compose_position_decision``
    rather than widening this boundary with another decision input.
    """

    cfg = policy or ActionPolicy()
    market_side = structural.direction
    lineage = _decision_lineage(structural, execution, additional_lineage)

    if eligibility.state is EligibilityState.BLOCKED:
        return FinalDecision(
            structural.horizon,
            market_side,
            ActionSide.NONE,
            DecisionAction.NO_TRADE,
            eligibility.state,
            execution.state,
            eligibility.reasons,
            eligibility.blockers,
            (),
            lineage,
        )

    if market_side not in cfg.permitted_sides:
        return FinalDecision(
            structural.horizon,
            market_side,
            ActionSide.NONE,
            DecisionAction.NO_TRADE,
            eligibility.state,
            execution.state,
            ("MARKET_SIDE_VALID_BUT_ACTION_CAPABILITY_DISALLOWS_SIDE",),
            (f"ACTION_SIDE_NOT_PERMITTED:{market_side.value}",),
            (),
            lineage,
        )

    side = _action_side(market_side)
    if eligibility.state is EligibilityState.WAITING:
        return FinalDecision(
            structural.horizon,
            market_side,
            side,
            DecisionAction.WAIT,
            eligibility.state,
            execution.state,
            eligibility.reasons,
            (),
            eligibility.waiting_for,
            lineage,
        )

    if execution.state is ExecutionTriggerState.UNAVAILABLE:
        return FinalDecision(
            structural.horizon,
            market_side,
            side,
            DecisionAction.WAIT,
            eligibility.state,
            execution.state,
            ("MARKET_ELIGIBLE_EXECUTION_DATA_UNAVAILABLE",),
            (),
            (f"{execution.timeframe}:EXECUTION_TRIGGER_DATA",),
            lineage,
        )
    if execution.state is ExecutionTriggerState.FAILED:
        return FinalDecision(
            structural.horizon,
            market_side,
            side,
            DecisionAction.WAIT,
            eligibility.state,
            execution.state,
            ("MARKET_ELIGIBLE_EXECUTION_EVENT_FAILED",),
            (),
            ("NEW_EXECUTION_EVENT",),
            lineage,
        )
    if execution.state is ExecutionTriggerState.ABSENT:
        return FinalDecision(
            structural.horizon,
            market_side,
            side,
            DecisionAction.READY,
            eligibility.state,
            execution.state,
            ("MARKET_ELIGIBLE_AWAITING_FRESH_EXECUTION_EVENT",),
            (),
            ("FRESH_EXECUTION_EVENT",),
            lineage,
        )

    action = DecisionAction.BUY if market_side is StructuralDirection.LONG else DecisionAction.SELL
    position_after = PositionSide.LONG if action is DecisionAction.BUY else PositionSide.SHORT
    return FinalDecision(
        structural.horizon,
        market_side,
        side,
        action,
        eligibility.state,
        execution.state,
        ("MARKET_ELIGIBLE_AND_FRESH_EXECUTION_EVENT_CONFIRMED",),
        (),
        (),
        lineage,
        PositionSide.FLAT,
        position_after,
    )


def _position_hold(
    structural: StructuralAssessment,
    *,
    eligibility: EligibilityAssessment,
    execution: ExecutionTriggerAssessment,
    position: PositionContext,
    reasons: tuple[str, ...],
    waiting_for: tuple[str, ...] = (),
    lineage: tuple[str, ...],
) -> FinalDecision:
    return FinalDecision(
        horizon=structural.horizon,
        market_side=structural.direction,
        action_side=_position_action_side(position.side),
        action=DecisionAction.HOLD,
        eligibility=eligibility.state,
        execution_trigger=execution.state,
        reasons=reasons,
        blockers=(),
        waiting_for=waiting_for,
        source_lineage=lineage,
        position_before=position.side,
        position_after=position.side,
    )


def compose_position_decision(
    structural: StructuralAssessment,
    *,
    eligibility: EligibilityAssessment,
    execution: ExecutionTriggerAssessment,
    position: PositionContext,
    policy: ActionPolicy | None = None,
    additional_lineage: tuple[str, ...] = (),
) -> FinalDecision:
    """Layer HOLD/exit semantics over the frozen market decision.

    A FLAT position delegates exactly to ``compose_final_decision``. An open position
    is managed separately: fresh-entry opportunity/policy gates are not converted
    into forced exits, while an opposite Structure-owned exit path still requires a
    fresh execution event before exposure is closed.
    """

    if position.side is PositionSide.FLAT:
        return compose_final_decision(
            structural,
            eligibility=eligibility,
            execution=execution,
            policy=policy,
            additional_lineage=additional_lineage,
        )

    lineage = _decision_lineage(structural, execution, additional_lineage)
    exit_side = position_exit_candidate(structural, position)
    if exit_side is None:
        if structural.direction is StructuralDirection.UNRESOLVED:
            return _position_hold(
                structural,
                eligibility=eligibility,
                execution=execution,
                position=position,
                reasons=("OPEN_POSITION_STRUCTURE_UNRESOLVED",),
                waiting_for=("STRUCTURAL_POSITION_RISK_RESOLUTION",),
                lineage=lineage,
            )
        return _position_hold(
            structural,
            eligibility=eligibility,
            execution=execution,
            position=position,
            reasons=(f"OPEN_POSITION_{position.side.value}_THESIS_NOT_EXIT_ELIGIBLE",),
            lineage=lineage,
        )

    if execution.side is not exit_side:
        raise ValueError("position exit execution side must match Structure-owned exit side")

    if execution.state is ExecutionTriggerState.CONFIRMED:
        action = DecisionAction.SELL if position.side is PositionSide.LONG else DecisionAction.BUY
        return FinalDecision(
            horizon=structural.horizon,
            market_side=structural.direction,
            action_side=ActionSide.NONE,
            action=action,
            eligibility=eligibility.state,
            execution_trigger=execution.state,
            reasons=(
                f"POSITION_{position.side.value}_EXIT_CONFIRMED",
                f"EXIT_STRUCTURE_SIDE:{exit_side.value}",
            ),
            blockers=(),
            waiting_for=(),
            source_lineage=lineage,
            position_before=position.side,
            position_after=PositionSide.FLAT,
        )

    if execution.state is ExecutionTriggerState.UNAVAILABLE:
        waiting = (f"{execution.timeframe}:POSITION_EXIT_TRIGGER_DATA",)
    elif execution.state is ExecutionTriggerState.FAILED:
        waiting = ("NEW_POSITION_EXIT_EVENT",)
    else:
        waiting = ("FRESH_POSITION_EXIT_EVENT",)

    return _position_hold(
        structural,
        eligibility=eligibility,
        execution=execution,
        position=position,
        reasons=(
            f"POSITION_{position.side.value}_EXIT_PATH_ACTIVE",
            f"EXIT_STRUCTURE_SIDE:{exit_side.value}",
            *execution.reasons,
        ),
        waiting_for=waiting,
        lineage=lineage,
    )


__all__ = [
    "ActionPolicy",
    "ActionSide",
    "DecisionAction",
    "FinalDecision",
    "compose_final_decision",
    "compose_position_decision",
]
