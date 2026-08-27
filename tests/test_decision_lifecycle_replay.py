from dataclasses import dataclass
from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
from financial_dashboard.decision.lifecycle import ExitStage, PositionState, TradeLifecycleState
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.scenario import ScenarioKind, ScenarioPresence, ScenarioStage
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection


CALLS: list[tuple[str, pd.Timestamp]] = []


def _entry_event(as_of):
    timestamp = pd.Timestamp(as_of)
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.LONG,
        timeframe="30m",
        observed_at=timestamp,
        available_at=timestamp,
        reason="ENTRY_CONFIRMED",
        source_refs=(),
    )


def _exit_event(as_of):
    timestamp = pd.Timestamp(as_of)
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.SHORT,
        timeframe="30m",
        observed_at=timestamp,
        available_at=timestamp,
        reason="EXIT_CONFIRMED",
        source_refs=(),
    )


def _entry_decision(action: DecisionAction):
    scenario = SimpleNamespace(
        horizon=DecisionHorizon.SHORT_TERM,
        presence=ScenarioPresence.PRESENT,
        stage=ScenarioStage.QUALIFIED,
        structural_direction=StructuralDirection.LONG,
        kind=ScenarioKind.SHORT_TERM_STANDALONE,
        active_target_identity="target:st:1",
    )
    return SimpleNamespace(
        action=action,
        selected_horizon=DecisionHorizon.SHORT_TERM if action is DecisionAction.BUY else None,
        scenario_stage=ScenarioStage.QUALIFIED if action is DecisionAction.BUY else None,
        execution_state=ExecutionTriggerState.CONFIRMED if action is DecisionAction.BUY else ExecutionTriggerState.ABSENT,
        execution_event_consumed=action is DecisionAction.BUY,
        arbitration=SimpleNamespace(selected_scenario=scenario if action is DecisionAction.BUY else None),
        reasons=("ENTRY_TEST",),
        blockers=(),
        waiting_for=() if action is DecisionAction.BUY else ("ENTRY_SETUP",),
        source_lineage=("entry:test",),
    )


@dataclass(frozen=True)
class _Snapshot:
    symbol: str
    as_of: pd.Timestamp
    current_price: float
    entry_action: DecisionAction = DecisionAction.WAIT
    exit_action: DecisionAction = DecisionAction.HOLD
    source_refs: tuple = ()

    def entry_decision(self, *, config=None, execution_event=None):
        CALLS.append(("entry", self.as_of))
        if self.entry_action is DecisionAction.BUY:
            assert execution_event is not None
        return _entry_decision(self.entry_action)

    def position_exit_decision(self, state, *, execution_event=None):
        CALLS.append(("exit", self.as_of))
        assert state.position is PositionState.OPEN
        assert state.entry_metadata is not None
        action = self.exit_action
        if action is DecisionAction.SELL and execution_event is None:
            action = DecisionAction.HOLD
        stage = ExitStage.EXIT_READY if self.exit_action is DecisionAction.SELL else ExitStage.MONITOR
        return SimpleNamespace(
            action=action,
            entry_horizon=state.entry_metadata.entry_horizon,
            as_of=self.as_of,
            stage=stage,
            execution_event_consumed=action is DecisionAction.SELL,
            reasons=("EXIT_TEST",),
            blockers=(),
            waiting_for=() if action is DecisionAction.SELL else ("EXIT_CONDITION",),
            source_lineage=("exit:test",),
        )


def test_canonical_replay_routes_flat_only_to_entry_and_open_only_to_exit():
    CALLS.clear()
    t1 = pd.Timestamp("2026-01-05 10:00")
    t2 = pd.Timestamp("2026-01-05 10:30")
    t3 = pd.Timestamp("2026-01-05 11:00")
    t4 = pd.Timestamp("2026-01-05 11:30")
    snapshots = (
        _Snapshot("TEST", t1, 100.0, entry_action=DecisionAction.BUY),
        _Snapshot("TEST", t2, 101.0, entry_action=DecisionAction.BUY),
        _Snapshot("TEST", t3, 99.0, exit_action=DecisionAction.SELL),
        _Snapshot("TEST", t4, 98.0, entry_action=DecisionAction.WAIT),
    )

    result = replay_canonical_trade_lifecycle(
        snapshots,
        entry_execution_events={t1: _entry_event(t1), t2: _entry_event(t2)},
        exit_execution_events={t3: _exit_event(t3)},
    )

    assert [row.action for row in result.rows] == [
        DecisionAction.BUY,
        DecisionAction.HOLD,
        DecisionAction.SELL,
        DecisionAction.WAIT,
    ]
    assert CALLS == [
        ("entry", t1),
        ("exit", t2),
        ("exit", t3),
        ("entry", t4),
    ]
    assert result.rows[0].current_state.entry_metadata is not None
    assert result.rows[0].current_state.entry_metadata.entry_horizon is DecisionHorizon.SHORT_TERM
    assert result.rows[1].current_state.entry_metadata == result.rows[0].current_state.entry_metadata
    assert result.rows[2].current_state == TradeLifecycleState()
    assert result.final_state.position is PositionState.FLAT


def test_canonical_replay_does_not_reuse_event_from_another_bar():
    t1 = pd.Timestamp("2026-01-05 10:00")
    t2 = pd.Timestamp("2026-01-05 10:30")
    result = replay_canonical_trade_lifecycle(
        (
            _Snapshot("TEST", t1, 100.0, entry_action=DecisionAction.WAIT),
            _Snapshot("TEST", t2, 101.0, entry_action=DecisionAction.BUY),
        ),
        entry_execution_events={t1: _entry_event(t1)},
    )

    assert result.rows[0].action is DecisionAction.WAIT
    assert result.rows[1].action is DecisionAction.BUY
    # The fake BUY contract detects that no t2 event exists before metadata can open.
    # A fresh event from t1 is never carried to t2.


def test_canonical_replay_rejects_legacy_open_without_entry_metadata():
    legacy = TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.MONITOR,
        trade_id="legacy",
        entry_as_of=pd.Timestamp("2026-01-05 09:30"),
    )
    with pytest.raises(ValueError, match="requires entry metadata"):
        replay_canonical_trade_lifecycle((), initial_state=legacy)


def test_canonical_replay_requires_strict_order_and_one_symbol():
    t1 = pd.Timestamp("2026-01-05 10:00")
    with pytest.raises(ValueError, match="strictly increasing"):
        replay_canonical_trade_lifecycle(
            (
                _Snapshot("TEST", t1, 100.0),
                _Snapshot("TEST", t1, 101.0),
            )
        )
    with pytest.raises(ValueError, match="one symbol"):
        replay_canonical_trade_lifecycle(
            (
                _Snapshot("AAA", t1, 100.0),
                _Snapshot("BBB", t1 + pd.Timedelta(minutes=30), 101.0),
            )
        )
