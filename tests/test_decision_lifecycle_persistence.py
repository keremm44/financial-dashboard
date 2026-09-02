from dataclasses import dataclass
from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.decision.composer import ActionPolicy, DecisionAction
from financial_dashboard.decision.engine import DecisionEngineConfig
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
from financial_dashboard.decision.lifecycle import ExitStage, PositionState, TradeLifecycleState
from financial_dashboard.decision.lifecycle_persistence import (
    LifecycleCheckpointStatus,
    PersistentTradeLifecycleStore,
    TradeLifecycleCheckpoint,
    causal_prefix_digest,
    decision_config_digest,
    deserialize_trade_lifecycle_state,
    serialize_trade_lifecycle_state,
)
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.persistent_lifecycle_replay import PersistentCanonicalLifecycleReplayRunner
from financial_dashboard.decision.position_metadata import PositionEntryMetadata, STTradeMemory
from financial_dashboard.decision.scenario import ScenarioKind, ScenarioPresence, ScenarioStage
from financial_dashboard.decision.st_economic_history import STEconomicHistory
from financial_dashboard.decision.st_exit_intent import STExitFamily
from financial_dashboard.decision.st_thesis_identity import STEconomicMission, STThesisFamily
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection


CALLS: list[tuple[str, pd.Timestamp]] = []


def _event(as_of, side):
    timestamp = pd.Timestamp(as_of)
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=side,
        timeframe="30m",
        observed_at=timestamp,
        available_at=timestamp,
        reason="CONFIRMED",
        source_refs=(),
    )


def _entry_result(action):
    scenario = SimpleNamespace(
        horizon=DecisionHorizon.SHORT_TERM,
        presence=ScenarioPresence.PRESENT,
        stage=ScenarioStage.QUALIFIED,
        structural_direction=StructuralDirection.LONG,
        kind=ScenarioKind.SHORT_TERM_STANDALONE,
        active_target_identity="target:1",
    )
    return SimpleNamespace(
        action=action,
        selected_horizon=DecisionHorizon.SHORT_TERM if action is DecisionAction.BUY else None,
        scenario_stage=ScenarioStage.QUALIFIED if action is DecisionAction.BUY else None,
        execution_state=ExecutionTriggerState.CONFIRMED if action is DecisionAction.BUY else ExecutionTriggerState.ABSENT,
        execution_event_consumed=action is DecisionAction.BUY,
        arbitration=SimpleNamespace(selected_scenario=scenario if action is DecisionAction.BUY else None),
        reasons=("ENTRY",),
        blockers=(),
        waiting_for=(),
        source_lineage=("entry:lineage",),
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
        if self.entry_action is DecisionAction.BUY and execution_event is None:
            return _entry_result(DecisionAction.WAIT)
        return _entry_result(self.entry_action)

    def position_exit_decision(self, state, *, execution_event=None):
        CALLS.append(("exit", self.as_of))
        terminal = self.exit_action is DecisionAction.SELL
        action = self.exit_action
        if terminal and execution_event is None:
            action = DecisionAction.HOLD
        return SimpleNamespace(
            action=action,
            entry_horizon=state.entry_metadata.entry_horizon,
            as_of=self.as_of,
            stage=ExitStage.EXIT_READY if terminal else ExitStage.MONITOR,
            execution_event_consumed=action is DecisionAction.SELL,
            reasons=("EXIT",),
            blockers=(),
            waiting_for=(),
            source_lineage=("exit:lineage",),
            economic_exit_family=STExitFamily.PROTECTIVE_EXIT if terminal else None,
            economic_reasons=("PERSISTENCE_FIXTURE_TERMINAL_EXIT",) if terminal else (),
            economic_source_lineage=("exit:lineage",) if terminal else (),
        )


def _open_state():
    as_of = pd.Timestamp("2026-01-05 10:00")
    metadata = PositionEntryMetadata(
        symbol="TEST",
        entry_horizon=DecisionHorizon.SHORT_TERM,
        scenario_kind=ScenarioKind.SHORT_TERM_STANDALONE,
        entry_as_of=as_of,
        entry_price=100.0,
        active_target_identity="target:1",
        execution_timeframe="30m",
        execution_observed_at=as_of,
        execution_available_at=as_of,
        execution_reason="CONFIRMED",
        source_lineage=("entry:lineage",),
        st_trade_memory=STTradeMemory(
            thesis_family=STThesisFamily.UNRESOLVED,
            economic_mission=STEconomicMission.UNRESOLVED,
            initial_defended_anchor=None,
        ),
    )
    return TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.MONITOR,
        trade_id="trade:1",
        entry_as_of=as_of,
        entry_metadata=metadata,
        st_economic_history=STEconomicHistory(),
    )


def test_lifecycle_state_json_roundtrip_preserves_frozen_metadata():
    state = _open_state()
    restored = deserialize_trade_lifecycle_state(serialize_trade_lifecycle_state(state))
    assert restored == state
    assert restored.entry_metadata.entry_horizon is DecisionHorizon.SHORT_TERM


def test_canonical_persistence_rejects_metadata_less_open_state():
    legacy = TradeLifecycleState(
        position=PositionState.OPEN,
        exit_stage=ExitStage.MONITOR,
        trade_id="legacy",
        entry_as_of=pd.Timestamp("2026-01-05 09:30"),
    )
    with pytest.raises(ValueError, match="requires entry metadata"):
        serialize_trade_lifecycle_state(legacy)


def test_store_distinguishes_corrupt_checkpoint_from_known_flat(tmp_path):
    store = PersistentTradeLifecycleStore(tmp_path)
    assert store.load("TEST").status is LifecycleCheckpointStatus.ABSENT

    path = store.path_for("TEST")
    path.write_text("{not-json", encoding="utf-8")
    loaded = store.load("TEST")
    assert loaded.status is LifecycleCheckpointStatus.INVALID
    assert loaded.checkpoint is None


def test_persistent_runner_processes_only_new_tail_and_preserves_ownership(tmp_path):
    CALLS.clear()
    runner = PersistentCanonicalLifecycleReplayRunner(tmp_path)
    t1 = pd.Timestamp("2026-01-05 10:00")
    t2 = pd.Timestamp("2026-01-05 10:30")
    t3 = pd.Timestamp("2026-01-05 11:00")
    entry_events = {t1: _event(t1, StructuralDirection.LONG)}

    first_snapshots = (
        _Snapshot("TEST", t1, 100.0, entry_action=DecisionAction.BUY),
        _Snapshot("TEST", t2, 101.0),
    )
    first = runner.run("TEST", first_snapshots, entry_execution_events=entry_events)
    assert first.resumed is False
    assert first.processed_count == 2
    assert first.replay.final_state.position is PositionState.OPEN
    frozen = first.replay.final_state.entry_metadata
    assert CALLS == [("entry", t1), ("exit", t2)]

    CALLS.clear()
    full_snapshots = (*first_snapshots, _Snapshot("TEST", t3, 99.0, exit_action=DecisionAction.SELL))
    exit_events = {t3: _event(t3, StructuralDirection.SHORT)}
    second = runner.run(
        "TEST",
        full_snapshots,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
    )
    assert second.resumed is True
    assert second.processed_count == 1
    assert [row.action for row in second.replay.rows] == [DecisionAction.SELL]
    assert second.replay.initial_state.entry_metadata == frozen
    assert second.replay.final_state.position is PositionState.FLAT
    assert CALLS == [("exit", t3)]

    CALLS.clear()
    same = runner.run(
        "TEST",
        full_snapshots,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
    )
    assert same.resumed is True
    assert same.processed_count == 0
    assert same.replay.rows == ()
    assert same.replay.final_state.position is PositionState.FLAT
    assert CALLS == []


def test_persistent_resume_reconstructs_same_action_and_state_chain_as_cold_replay(tmp_path):
    t1 = pd.Timestamp("2026-01-05 10:00")
    t2 = pd.Timestamp("2026-01-05 10:30")
    t3 = pd.Timestamp("2026-01-05 11:00")
    snapshots = (
        _Snapshot("TEST", t1, 100.0, entry_action=DecisionAction.BUY),
        _Snapshot("TEST", t2, 101.0),
        _Snapshot("TEST", t3, 99.0, exit_action=DecisionAction.SELL),
    )
    entry_events = {t1: _event(t1, StructuralDirection.LONG)}
    exit_events = {t3: _event(t3, StructuralDirection.SHORT)}

    cold = replay_canonical_trade_lifecycle(
        snapshots,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
    )

    runner = PersistentCanonicalLifecycleReplayRunner(tmp_path)
    prefix = runner.run(
        "TEST",
        snapshots[:2],
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
    )
    resumed = runner.run(
        "TEST",
        snapshots,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
    )

    reconstructed_rows = (*prefix.replay.rows, *resumed.replay.rows)
    assert [row.action for row in reconstructed_rows] == [row.action for row in cold.rows]
    assert [row.current_state for row in reconstructed_rows] == [row.current_state for row in cold.rows]
    assert resumed.replay.final_state == cold.final_state


def test_persistent_runner_fails_closed_when_consumed_prefix_changes(tmp_path):
    runner = PersistentCanonicalLifecycleReplayRunner(tmp_path)
    t1 = pd.Timestamp("2026-01-05 10:00")
    entry_events = {t1: _event(t1, StructuralDirection.LONG)}
    original = (_Snapshot("TEST", t1, 100.0, entry_action=DecisionAction.BUY),)
    runner.run("TEST", original, entry_execution_events=entry_events)

    changed = (_Snapshot("TEST", t1, 100.5, entry_action=DecisionAction.BUY),)
    with pytest.raises(ValueError, match="causal prefix changed"):
        runner.run("TEST", changed, entry_execution_events=entry_events)


def test_persistent_runner_fails_closed_when_config_or_old_event_changes(tmp_path):
    runner = PersistentCanonicalLifecycleReplayRunner(tmp_path)
    t1 = pd.Timestamp("2026-01-05 10:00")
    entry_events = {t1: _event(t1, StructuralDirection.LONG)}
    snapshots = (_Snapshot("TEST", t1, 100.0, entry_action=DecisionAction.BUY),)
    runner.run("TEST", snapshots, entry_execution_events=entry_events)

    changed_config = DecisionEngineConfig(action_policy=ActionPolicy())
    # Same semantic default config remains resumable.
    assert runner.run("TEST", snapshots, config=changed_config, entry_execution_events=entry_events).processed_count == 0

    altered_event = {t1: ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.LONG,
        timeframe="30m",
        observed_at=t1,
        available_at=t1,
        reason="DIFFERENT_REASON",
        source_refs=(),
    )}
    with pytest.raises(ValueError, match="causal prefix changed"):
        runner.run("TEST", snapshots, entry_execution_events=altered_event)


def test_checkpoint_digest_helpers_are_deterministic():
    t1 = pd.Timestamp("2026-01-05 10:00")
    snapshots = (_Snapshot("TEST", t1, 100.0),)
    cfg = DecisionEngineConfig()
    assert causal_prefix_digest(snapshots) == causal_prefix_digest(snapshots)
    assert decision_config_digest(cfg) == decision_config_digest(DecisionEngineConfig())


def test_store_roundtrip_checkpoint(tmp_path):
    state = _open_state()
    cfg = DecisionEngineConfig()
    checkpoint = TradeLifecycleCheckpoint(
        symbol="TEST",
        state=state,
        prefix_count=1,
        last_as_of=state.entry_as_of,
        causal_prefix_digest="a" * 64,
        decision_config_digest=decision_config_digest(cfg),
    )
    store = PersistentTradeLifecycleStore(tmp_path)
    store.save(checkpoint)
    loaded = store.load("TEST")
    assert loaded.status is LifecycleCheckpointStatus.LOADED
    assert loaded.checkpoint == checkpoint
