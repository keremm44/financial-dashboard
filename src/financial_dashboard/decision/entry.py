from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .arbiter import ArbiterState, EntryScenarioArbitration
from .composer import DecisionAction, FinalDecision
from .execution import ExecutionTriggerEvent, ExecutionTriggerState
from .scenario import ScenarioPresence, ScenarioStage
from .structural import DecisionHorizon, StructuralDirection

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot
    from .engine import DecisionEngineConfig, HorizonDecisionAssessment


@dataclass(frozen=True, slots=True)
class EntryDecision:
    """Final long-entry decision after scenario ownership has been resolved.

    Turn 6 does not reinterpret domains or create a second market thesis. It only
    converts the one scenario selected by the Turn 5 arbiter into WAIT/READY/BUY or
    NO_TRADE using the existing fresh 30m execution-event contract.
    """

    action: DecisionAction
    selected_horizon: DecisionHorizon | None
    arbitration: EntryScenarioArbitration
    scenario_stage: ScenarioStage | None
    execution_state: ExecutionTriggerState | None
    execution_event_consumed: bool
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    waiting_for: tuple[str, ...]
    source_lineage: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.action in {DecisionAction.SELL, DecisionAction.HOLD}:
            raise ValueError("entry decision cannot emit SELL or HOLD")
        if self.action is DecisionAction.BUY and self.selected_horizon is None:
            raise ValueError("BUY requires an arbiter-selected horizon")
        if self.execution_event_consumed and self.execution_state is None:
            raise ValueError("consumed execution event requires execution state")

    @property
    def is_actionable_signal(self) -> bool:
        return self.action is DecisionAction.BUY


def _dedup(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _no_selection_decision(arbitration: EntryScenarioArbitration) -> EntryDecision:
    if arbitration.state is ArbiterState.NO_SCENARIO:
        return EntryDecision(
            action=DecisionAction.NO_TRADE,
            selected_horizon=None,
            arbitration=arbitration,
            scenario_stage=None,
            execution_state=None,
            execution_event_consumed=False,
            reasons=_dedup(list(arbitration.reasons) + ["ENTRY_NO_SELECTED_SCENARIO"]),
            blockers=(),
            waiting_for=(),
            source_lineage=(),
        )

    return EntryDecision(
        action=DecisionAction.WAIT,
        selected_horizon=None,
        arbitration=arbitration,
        scenario_stage=None,
        execution_state=None,
        execution_event_consumed=False,
        reasons=_dedup(list(arbitration.reasons) + ["ENTRY_OWNERSHIP_UNRESOLVED"]),
        blockers=(),
        waiting_for=_dedup(list(arbitration.waiting_for)),
        source_lineage=(),
    )


def compose_entry_decision(
    arbitration: EntryScenarioArbitration,
    *,
    selected_assessment: "HorizonDecisionAssessment | None" = None,
    execution_event_consumed: bool = False,
) -> EntryDecision:
    """Compose entry action from one already-arbitrated scenario.

    A selected scenario must be PRESENT and long-entry compatible. BLOCKED and
    DEVELOPING scenarios retain ownership but cannot execute. Only QUALIFIED may
    delegate to the existing horizon composer, whose BUY still requires a fresh
    confirmed execution event.
    """

    scenario = arbitration.selected_scenario
    if arbitration.selected_horizon is None or scenario is None:
        if selected_assessment is not None:
            raise ValueError("assessment cannot be supplied without selected scenario")
        return _no_selection_decision(arbitration)

    if scenario.horizon is not arbitration.selected_horizon:
        raise ValueError("selected scenario horizon must match arbiter selection")
    if scenario.presence is not ScenarioPresence.PRESENT:
        raise ValueError("arbiter-selected scenario must be PRESENT")
    if scenario.structural_direction is not StructuralDirection.LONG:
        raise ValueError("current entry product accepts long-entry scenarios only")

    base_lineage = tuple(sorted(set(scenario.source_lineage)))

    if scenario.stage is ScenarioStage.BLOCKED:
        if selected_assessment is not None:
            raise ValueError("blocked scenario must not consume execution assessment")
        return EntryDecision(
            action=DecisionAction.NO_TRADE,
            selected_horizon=arbitration.selected_horizon,
            arbitration=arbitration,
            scenario_stage=scenario.stage,
            execution_state=None,
            execution_event_consumed=False,
            reasons=_dedup(list(arbitration.reasons) + list(scenario.reasons) + ["ENTRY_SCENARIO_BLOCKED"]),
            blockers=_dedup(list(scenario.blockers)),
            waiting_for=(),
            source_lineage=base_lineage,
        )

    if scenario.stage in {ScenarioStage.DEVELOPING, ScenarioStage.UNAVAILABLE}:
        if selected_assessment is not None:
            raise ValueError("non-qualified scenario must not consume execution assessment")
        return EntryDecision(
            action=DecisionAction.WAIT,
            selected_horizon=arbitration.selected_horizon,
            arbitration=arbitration,
            scenario_stage=scenario.stage,
            execution_state=None,
            execution_event_consumed=False,
            reasons=_dedup(list(arbitration.reasons) + list(scenario.reasons) + ["ENTRY_SCENARIO_NOT_QUALIFIED"]),
            blockers=(),
            waiting_for=_dedup(list(scenario.waiting_for) or ["SCENARIO_TO_QUALIFY"]),
            source_lineage=base_lineage,
        )

    if scenario.stage is not ScenarioStage.QUALIFIED:
        raise ValueError("selected PRESENT scenario has invalid entry stage")
    if selected_assessment is None:
        raise ValueError("qualified scenario requires selected horizon assessment")
    if selected_assessment.horizon is not arbitration.selected_horizon:
        raise ValueError("selected assessment horizon must match arbiter selection")

    final: FinalDecision = selected_assessment.final
    if final.market_side is not StructuralDirection.LONG:
        raise ValueError("qualified entry assessment must remain LONG")
    if final.action in {DecisionAction.SELL, DecisionAction.HOLD}:
        raise ValueError("selected horizon composer emitted illegal entry action")

    lineage = tuple(sorted(set(base_lineage) | set(final.source_lineage)))
    reasons = _dedup(list(arbitration.reasons) + list(scenario.reasons) + list(final.reasons))

    # The selected scenario is the ownership gate. The legacy horizon composer may
    # only contribute execution readiness/action after that gate has qualified.
    return EntryDecision(
        action=final.action,
        selected_horizon=arbitration.selected_horizon,
        arbitration=arbitration,
        scenario_stage=scenario.stage,
        execution_state=selected_assessment.execution.state,
        execution_event_consumed=execution_event_consumed,
        reasons=reasons,
        blockers=_dedup(list(final.blockers)),
        waiting_for=_dedup(list(final.waiting_for)),
        source_lineage=lineage,
    )


def assess_entry_decision(
    snapshot: "DecisionInputSnapshot",
    *,
    config: "DecisionEngineConfig | None" = None,
    execution_event: ExecutionTriggerEvent | None = None,
) -> EntryDecision:
    """Evaluate the complete Turn 4 -> Turn 5 -> Turn 6 entry chain causally."""

    arbitration = snapshot.entry_arbitration(config=config)
    scenario = arbitration.selected_scenario

    if scenario is None or scenario.stage is not ScenarioStage.QUALIFIED:
        # A fresh event is intentionally not consumed when ownership/setup is not
        # qualified. It cannot be cached and reused later because execution events
        # remain fresh-at-as_of by contract.
        return compose_entry_decision(arbitration)

    from .engine import assess_horizon_decision

    assessment = assess_horizon_decision(
        snapshot,
        arbitration.selected_horizon,
        config=config,
        execution_event=execution_event,
    )
    return compose_entry_decision(
        arbitration,
        selected_assessment=assessment,
        execution_event_consumed=execution_event is not None,
    )


__all__ = [
    "EntryDecision",
    "assess_entry_decision",
    "compose_entry_decision",
]
