from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

import pandas as pd

from financial_dashboard.decision_audit import DecisionAction as AuditDecisionAction
from financial_dashboard.decision_audit import DecisionEvent, DecisionSide

from .composer import DecisionAction
from .lifecycle import PositionState
from .lifecycle_replay import CanonicalLifecycleReplayResult
from .structural import StructuralDirection


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


def _dedup(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _lifecycle_payload(row) -> dict[str, Any]:
    transition = row.transition
    return {
        "previous_position": transition.previous.position.value,
        "position_state": transition.current.position.value,
        "previous_exit_stage": None if transition.previous.exit_stage is None else transition.previous.exit_stage.value,
        "exit_stage": None if transition.current.exit_stage is None else transition.current.exit_stage.value,
        "trade_id": transition.current.trade_id,
        "entry_as_of": transition.current.entry_as_of,
        "requested_action": transition.requested_action.value,
        "action": transition.action.value,
        "transition_reason": transition.reason,
        "changed_position": transition.changed_position,
    }


def _phase(row) -> str:
    if row.action is DecisionAction.BUY:
        return "ENTRY_EXECUTED"
    if row.action is DecisionAction.SELL:
        return "EXIT_EXECUTED"
    if row.previous_state.position is PositionState.OPEN:
        assert row.exit_decision is not None
        return row.exit_decision.stage.value
    if row.entry_decision is None:
        return row.action.value
    if row.entry_decision.action is DecisionAction.READY:
        return "READY_FOR_EXECUTION"
    if row.entry_decision.action is DecisionAction.WAIT:
        return "ENTRY_WAIT"
    if row.entry_decision.action is DecisionAction.NO_TRADE:
        return "NO_SETUP"
    return row.entry_decision.action.value


def _audit_marker_payload(row) -> dict[str, Any]:
    markers = row.audit_markers
    return {
        "scenario_qualified_at": markers.scenario_qualified_at,
        "scenario_qualified_price": markers.scenario_qualified_price,
        "ready_for_execution_at": markers.ready_for_execution_at,
        "ready_for_execution_price": markers.ready_for_execution_price,
        "exit_watch_at": markers.exit_watch_at,
        "exit_watch_price": markers.exit_watch_price,
        "exit_ready_at": markers.exit_ready_at,
        "exit_ready_price": markers.exit_ready_price,
    }


def canonical_decision_events_from_replay(
    replay: CanonicalLifecycleReplayResult,
) -> tuple[DecisionEvent, ...]:
    """Project canonical lifecycle rows into restart-stable hindsight-audit events.

    Audit progression is already carried causally by the replay row. This adapter
    never reconstructs marker history from its local call boundary, so projecting a
    resumed tail is identical to projecting the same rows inside a cold replay.
    """

    events: list[DecisionEvent] = []

    for row in replay.rows:
        snapshot = row.snapshot
        entry = row.entry_decision
        exit_decision = row.exit_decision

        metadata = row.current_state.entry_metadata or row.previous_state.entry_metadata
        selected_scenario = None if entry is None else entry.arbitration.selected_scenario
        entry_horizon = (
            metadata.entry_horizon.value
            if metadata is not None
            else None if entry is None or entry.selected_horizon is None else entry.selected_horizon.value
        )
        scenario_kind = (
            metadata.scenario_kind.value
            if metadata is not None
            else None if selected_scenario is None else selected_scenario.kind.value
        )

        if entry is not None:
            reasons = entry.reasons
            blockers = entry.blockers
            waiting_for = entry.waiting_for
            lineage = entry.source_lineage
            entry_payload = {
                "selected_horizon": entry_horizon,
                "scenario_stage": None if entry.scenario_stage is None else entry.scenario_stage.value,
                "scenario_kind": scenario_kind,
                "execution_state": None if entry.execution_state is None else entry.execution_state.value,
                "execution_event_consumed": entry.execution_event_consumed,
                "reasons": list(entry.reasons),
                "blockers": list(entry.blockers),
                "waiting_for": list(entry.waiting_for),
            }
            exit_payload = None
        else:
            assert exit_decision is not None
            reasons = exit_decision.reasons
            blockers = ()
            waiting_for = exit_decision.waiting_for
            lineage = exit_decision.source_lineage
            entry_payload = None
            exit_payload = {
                "entry_horizon": entry_horizon,
                "stage": exit_decision.stage.value,
                "position_health": exit_decision.position_health.value,
                "execution": _jsonable(exit_decision.execution),
                "execution_event_consumed": exit_decision.execution_event_consumed,
                "reasons": list(exit_decision.reasons),
                "waiting_for": list(exit_decision.waiting_for),
            }

        target_path = snapshot.target_path(StructuralDirection.LONG) if hasattr(snapshot, "target_path") else None
        target_path_payload = None if target_path is None else {
            "status": target_path.status.value,
            "active_identity": None if target_path.active_node is None else target_path.active_node.identity,
            "nodes": [
                {
                    "identity": node.identity,
                    "state": node.state.value,
                    "roles": [role.value for role in node.roles],
                    "sources": [source.value for source in node.sources],
                }
                for node in target_path.nodes
            ],
        }
        snapshot_payload = {
            "canonical_lifecycle": True,
            "canonical_readiness_proxy": bool(row.execution_proxy_used),
            "lifecycle_phase": _phase(row),
            "trade_lifecycle": _lifecycle_payload(row),
            "entry_horizon": entry_horizon,
            "scenario_kind": scenario_kind,
            "entry_metadata": None if metadata is None else _jsonable(metadata),
            "entry_decision": entry_payload,
            "position_exit": exit_payload,
            "long_exit": None if exit_payload is None else {
                "stage": exit_payload["stage"],
                "position_health": exit_payload["position_health"],
                "execution": exit_payload["execution"],
            },
            "execution": None if entry is None else {
                "state": None if entry.execution_state is None else entry.execution_state.value,
                "event_consumed": entry.execution_event_consumed,
            },
            "audit_markers": _audit_marker_payload(row),
            "target_path": target_path_payload,
            "decision": {
                "action": row.action.value,
                "reasons": list(reasons),
                "blockers": list(blockers),
                "waiting_for": list(waiting_for),
            },
        }

        side = DecisionSide.LONG if (
            row.previous_state.position is PositionState.OPEN
            or row.current_state.position is PositionState.OPEN
            or row.action in {DecisionAction.BUY, DecisionAction.SELL, DecisionAction.HOLD}
            or (selected_scenario is not None and selected_scenario.structural_direction is StructuralDirection.LONG)
        ) else DecisionSide.NONE

        events.append(
            DecisionEvent(
                timestamp=snapshot.as_of,
                action=AuditDecisionAction(row.action.value),
                side=side,
                price=float(snapshot.current_price),
                reasons=_dedup(reasons),
                blockers=_dedup(blockers),
                waiting_for=_dedup(waiting_for),
                source_lineage=_dedup(lineage),
                snapshot=snapshot_payload,
            )
        )

    return tuple(events)


__all__ = ["canonical_decision_events_from_replay"]
