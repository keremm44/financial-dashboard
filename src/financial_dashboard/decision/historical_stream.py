from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

import pandas as pd

from financial_dashboard.decision_audit import DecisionAction as AuditDecisionAction
from financial_dashboard.decision_audit import DecisionEvent, DecisionSide
from financial_dashboard.decision_input import DecisionInputSnapshot

from .composer import ActionPolicy, DecisionAction
from .engine import DecisionEngineConfig, HorizonDecisionAssessment, assess_horizon_decision
from .execution import ExecutionTriggerEvent, ExecutionTriggerState
from .lifecycle import PositionState, TradeLifecycleState, TradeLifecycleTransition, transition_trade_lifecycle
from .opportunity import OpportunityCalibration
from .structural import DecisionHorizon, StructuralDirection
from .trade_exit import (
    ExitExecutionState,
    LongExitAssessment,
    LongExitExecutionAssessment,
    assess_long_exit_execution,
    assess_long_position_exit,
)


@dataclass(frozen=True, slots=True)
class HistoricalDecisionStreamConfig:
    """Configuration for the cheap decision-only historical pass.

    Native/domain engines are deliberately out of scope here. The caller must supply
    a causal, strictly increasing stream of already-built ``DecisionInputSnapshot``
    objects from a single-pass upstream replay.

    The normal lifecycle is long-only: bearish market assessments remain observable
    but cannot execute a short entry or close a long by themselves. ``exit_execution_events``
    supplies a separate fresh 30m exit channel for an EXIT_READY open long.
    """

    horizon: DecisionHorizon = DecisionHorizon.SHORT_TERM
    opportunity_calibration: OpportunityCalibration | None = None
    readiness_position_proxy: bool = False
    enforce_trade_lifecycle: bool = True
    action_policy: ActionPolicy = ActionPolicy()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _decision_side(side: StructuralDirection) -> DecisionSide:
    if side is StructuralDirection.LONG:
        return DecisionSide.LONG
    if side is StructuralDirection.SHORT:
        return DecisionSide.SHORT
    return DecisionSide.NONE


def _audit_action(action: DecisionAction) -> AuditDecisionAction:
    return AuditDecisionAction(action.value)


def _dedup(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _event_from_assessment(
    assessment: HorizonDecisionAssessment,
    *,
    price: float,
    action_override: AuditDecisionAction | None = None,
    proxy_reason: str | None = None,
    lifecycle: TradeLifecycleTransition | None = None,
    long_exit: LongExitAssessment | None = None,
    exit_execution: LongExitExecutionAssessment | None = None,
) -> DecisionEvent:
    final = assessment.final
    action = action_override or _audit_action(final.action)

    extra_reasons: list[str] = []
    if proxy_reason is not None:
        extra_reasons.append(proxy_reason)
    if long_exit is not None:
        extra_reasons.extend(long_exit.reasons)
    if exit_execution is not None:
        extra_reasons.extend(exit_execution.reasons)
    if lifecycle is not None:
        extra_reasons.append(lifecycle.reason)
    reasons = _dedup((*final.reasons, *extra_reasons))

    if long_exit is not None:
        waiting_for = _dedup(
            (
                *long_exit.waiting_for,
                *(() if exit_execution is None else exit_execution.waiting_for),
            )
        )
    else:
        waiting_for = tuple(final.waiting_for)
    if lifecycle is not None and lifecycle.action is DecisionAction.WAIT and not waiting_for:
        waiting_for = ("LIFECYCLE_LONG_ENTRY_PATH",)

    lifecycle_snapshot = None
    if lifecycle is not None:
        lifecycle_snapshot = {
            "previous_position": lifecycle.previous.position.value,
            "position_state": lifecycle.current.position.value,
            "previous_exit_stage": None
            if lifecycle.previous.exit_stage is None
            else lifecycle.previous.exit_stage.value,
            "exit_stage": None
            if lifecycle.current.exit_stage is None
            else lifecycle.current.exit_stage.value,
            "trade_id": lifecycle.current.trade_id,
            "entry_as_of": lifecycle.current.entry_as_of,
            "requested_action": lifecycle.requested_action.value,
            "action": lifecycle.action.value,
            "transition_reason": lifecycle.reason,
            "changed_position": lifecycle.changed_position,
        }

    snapshot = {
        "historical_stream": True,
        "readiness_position_proxy": proxy_reason is not None,
        "trade_lifecycle": lifecycle_snapshot,
        "long_exit": None
        if long_exit is None
        else {
            "stage": long_exit.stage.value,
            "position_health": long_exit.position_health.value,
            "reasons": list(long_exit.reasons),
            "waiting_for": list(long_exit.waiting_for),
            "source_refs": _jsonable(long_exit.source_refs),
            "execution": None
            if exit_execution is None
            else {
                "state": exit_execution.state.value,
                "reasons": list(exit_execution.reasons),
                "waiting_for": list(exit_execution.waiting_for),
                "source_refs": _jsonable(exit_execution.source_refs),
            },
        },
        "horizon": assessment.horizon.value,
        "structural": _jsonable(assessment.structural),
        "relation": assessment.structural_snapshot.relation.value,
        "durability": _jsonable(assessment.durability),
        "reaction": _jsonable(assessment.reaction),
        "participation": _jsonable(assessment.participation),
        "environment": _jsonable(assessment.environment),
        "opportunity": _jsonable(assessment.opportunity),
        "coverage": _jsonable(assessment.coverage),
        "conflict": _jsonable(assessment.conflict),
        "timing": _jsonable(assessment.timing),
        "eligibility": _jsonable(assessment.eligibility),
        "execution": _jsonable(assessment.execution),
    }

    lifecycle_owns_long = lifecycle is not None and (
        lifecycle.previous.position is PositionState.OPEN
        or lifecycle.current.position is PositionState.OPEN
        or lifecycle.action in {DecisionAction.BUY, DecisionAction.SELL, DecisionAction.HOLD}
    )
    side = DecisionSide.LONG if lifecycle_owns_long else _decision_side(final.market_side)

    return DecisionEvent(
        timestamp=assessment.as_of,
        action=action,
        side=side,
        price=float(price),
        reasons=reasons,
        blockers=tuple(final.blockers),
        waiting_for=waiting_for,
        source_lineage=tuple(final.source_lineage),
        snapshot=snapshot,
    )


def assess_snapshot_stream(
    snapshots: Iterable[DecisionInputSnapshot],
    *,
    config: HistoricalDecisionStreamConfig | None = None,
    execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
) -> tuple[tuple[HorizonDecisionAssessment, float], ...]:
    """Evaluate the decision layer over one strictly causal snapshot stream."""

    cfg = config or HistoricalDecisionStreamConfig()
    action_policy = cfg.action_policy
    if cfg.readiness_position_proxy:
        # Preserve the explicitly legacy proxy's ability to observe opposing READY.
        action_policy = ActionPolicy(
            permitted_sides=(StructuralDirection.LONG, StructuralDirection.SHORT)
        )
    engine_config = DecisionEngineConfig(
        opportunity_calibration=cfg.opportunity_calibration,
        action_policy=action_policy,
    )
    event_map = execution_events or {}
    previous_as_of: Any | None = None
    rows: list[tuple[HorizonDecisionAssessment, float]] = []

    for snapshot in snapshots:
        if previous_as_of is not None and snapshot.as_of <= previous_as_of:
            raise ValueError("historical decision snapshots must be strictly increasing")
        for ref in snapshot.source_refs:
            if not ref.is_available_at(snapshot.as_of):
                raise ValueError("historical decision snapshot contains future-unavailable evidence")
        assessment = assess_horizon_decision(
            snapshot,
            cfg.horizon,
            config=engine_config,
            execution_event=event_map.get(snapshot.as_of),
        )
        rows.append((assessment, float(snapshot.current_price)))
        previous_as_of = snapshot.as_of
    return tuple(rows)


def apply_trade_lifecycle(
    assessments: Iterable[tuple[HorizonDecisionAssessment, float]],
    *,
    exit_execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
) -> tuple[DecisionEvent, ...]:
    """Fold causal decisions through ownership plus the dedicated long-exit path."""

    state = TradeLifecycleState()
    exit_event_map = exit_execution_events or {}
    events: list[DecisionEvent] = []

    for assessment, price in assessments:
        long_exit: LongExitAssessment | None = None
        exit_execution: LongExitExecutionAssessment | None = None

        if state.position is PositionState.OPEN:
            long_exit = assess_long_position_exit(assessment.structural_snapshot)
            exit_event = exit_event_map.get(assessment.as_of)
            channel_available = (
                exit_event is not None
                or assessment.execution.state is not ExecutionTriggerState.UNAVAILABLE
            )
            exit_execution = assess_long_exit_execution(
                long_exit,
                as_of=assessment.as_of,
                event=exit_event,
                channel_available=channel_available,
            )
            lifecycle = transition_trade_lifecycle(
                state,
                assessment.final,
                as_of=assessment.as_of,
                exit_stage=long_exit.stage,
                exit_execution_confirmed=(
                    exit_execution.state is ExitExecutionState.CONFIRMED
                ),
            )
        else:
            lifecycle = transition_trade_lifecycle(
                state,
                assessment.final,
                as_of=assessment.as_of,
            )

        state = lifecycle.current
        events.append(
            _event_from_assessment(
                assessment,
                price=price,
                action_override=_audit_action(lifecycle.action),
                lifecycle=lifecycle,
                long_exit=long_exit,
                exit_execution=exit_execution,
            )
        )
    return tuple(events)


def apply_readiness_position_proxy(
    assessments: Iterable[tuple[HorizonDecisionAssessment, float]],
) -> tuple[DecisionEvent, ...]:
    """Legacy READY-side audit proxy; not the production lifecycle contract."""

    holding_long = False
    events: list[DecisionEvent] = []
    for assessment, price in assessments:
        final = assessment.final
        override: AuditDecisionAction | None = None
        proxy_reason: str | None = None
        if final.action is DecisionAction.READY:
            if not holding_long and final.market_side is StructuralDirection.LONG:
                holding_long = True
                override = AuditDecisionAction.BUY
                proxy_reason = "AUDIT_PROXY_LONG_ENTRY_FROM_READY"
            elif holding_long and final.market_side is StructuralDirection.SHORT:
                holding_long = False
                override = AuditDecisionAction.SELL
                proxy_reason = "AUDIT_PROXY_LONG_EXIT_FROM_OPPOSING_READY"
        events.append(
            _event_from_assessment(
                assessment,
                price=price,
                action_override=override,
                proxy_reason=proxy_reason,
            )
        )
    return tuple(events)


def decision_events_from_snapshot_stream(
    snapshots: Iterable[DecisionInputSnapshot],
    *,
    config: HistoricalDecisionStreamConfig | None = None,
    execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
    exit_execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
) -> tuple[DecisionEvent, ...]:
    cfg = config or HistoricalDecisionStreamConfig()
    assessments = assess_snapshot_stream(
        snapshots,
        config=cfg,
        execution_events=execution_events,
    )
    if cfg.readiness_position_proxy:
        return apply_readiness_position_proxy(assessments)
    if cfg.enforce_trade_lifecycle:
        return apply_trade_lifecycle(
            assessments,
            exit_execution_events=exit_execution_events,
        )
    return tuple(
        _event_from_assessment(assessment, price=price)
        for assessment, price in assessments
    )


__all__ = [
    "HistoricalDecisionStreamConfig",
    "apply_readiness_position_proxy",
    "apply_trade_lifecycle",
    "assess_snapshot_stream",
    "decision_events_from_snapshot_stream",
]
