from dataclasses import dataclass, replace
from hashlib import sha256
from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.decision.canonical_events import canonical_decision_events_from_replay
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.engine import DECISION_CONTRACT_VERSION, DecisionEngineConfig
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
from financial_dashboard.decision.lifecycle import ExitStage
from financial_dashboard.decision.lifecycle_persistence import (
    LifecycleCheckpointStatus,
    PersistentTradeLifecycleStore,
)
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.persistent_lifecycle_replay import PersistentCanonicalLifecycleReplayRunner
from financial_dashboard.decision.position_metadata import PositionEntryMetadata
from financial_dashboard.decision.scenario import ScenarioKind, ScenarioPresence, ScenarioStage
from financial_dashboard.decision.st_exit_intent import STExitFamily
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision.trade_exit import (
    ExitExecutionState,
    LongExitExecutionAssessment,
    PositionHealth,
)


def _event(as_of, side, *, available_at=None):
    observed = pd.Timestamp(as_of)
    available = observed if available_at is None else pd.Timestamp(available_at)
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=side,
        timeframe="30m",
        observed_at=observed,
        available_at=available,
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
        active_target_identity="target:restart",
    )
    return SimpleNamespace(
        action=action,
        selected_horizon=DecisionHorizon.SHORT_TERM if action is DecisionAction.BUY else None,
        scenario_stage=ScenarioStage.QUALIFIED if action is DecisionAction.BUY else None,
        execution_state=(
            ExecutionTriggerState.CONFIRMED
            if action is DecisionAction.BUY
            else ExecutionTriggerState.ABSENT
        ),
        execution_event_consumed=action is DecisionAction.BUY,
        arbitration=SimpleNamespace(
            selected_scenario=scenario if action is DecisionAction.BUY else None
        ),
        reasons=("ENTRY",),
        blockers=(),
        waiting_for=(),
        source_lineage=("entry:restart",),
    )


@dataclass(frozen=True)
class _Snapshot:
    symbol: str
    as_of: pd.Timestamp
    current_price: float
    entry_action: DecisionAction = DecisionAction.WAIT
    exit_stage: ExitStage = ExitStage.MONITOR
    exit_action: DecisionAction = DecisionAction.HOLD
    source_refs: tuple = ()

    def entry_decision(self, *, config=None, execution_event=None):
        if self.entry_action is DecisionAction.BUY and execution_event is None:
            return _entry_result(DecisionAction.WAIT)
        return _entry_result(self.entry_action)

    def position_exit_decision(self, state, *, execution_event=None):
        terminal = self.exit_stage is ExitStage.EXIT_READY
        action = self.exit_action
        if action is DecisionAction.SELL and execution_event is None:
            action = DecisionAction.HOLD
        if not terminal:
            execution_state = ExitExecutionState.NOT_ARMED
        elif action is DecisionAction.SELL:
            execution_state = ExitExecutionState.CONFIRMED
        else:
            execution_state = ExitExecutionState.ABSENT
        execution = LongExitExecutionAssessment(
            state=execution_state,
            reasons=("EXECUTION",),
            waiting_for=(),
            source_refs=(),
        )
        return SimpleNamespace(
            action=action,
            entry_horizon=state.entry_metadata.entry_horizon,
            as_of=self.as_of,
            stage=self.exit_stage,
            position_health=(
                PositionHealth.HEALTHY
                if self.exit_stage is ExitStage.MONITOR
                else PositionHealth.PRESSURED
            ),
            execution=execution,
            execution_event_consumed=action is DecisionAction.SELL,
            reasons=("EXIT",),
            blockers=(),
            waiting_for=(),
            source_lineage=("exit:restart",),
            economic_exit_family=STExitFamily.PROTECTIVE_EXIT if terminal else None,
            economic_reasons=("RESTART_FIXTURE_TERMINAL_EXIT",) if terminal else (),
            economic_source_lineage=("exit:restart",) if terminal else (),
        )


def test_persistent_resume_matches_full_canonical_event_payload_and_markers(tmp_path):
    t1 = pd.Timestamp("2026-01-05 10:00")
    t2 = pd.Timestamp("2026-01-05 10:30")
    t3 = pd.Timestamp("2026-01-05 11:00")
    t4 = pd.Timestamp("2026-01-05 11:30")
    snapshots = (
        _Snapshot("TEST", t1, 100.0, entry_action=DecisionAction.BUY),
        _Snapshot("TEST", t2, 101.0),
        _Snapshot("TEST", t3, 100.5),
        _Snapshot(
            "TEST",
            t4,
            99.0,
            exit_stage=ExitStage.EXIT_READY,
            exit_action=DecisionAction.SELL,
        ),
    )
    available_at = t1 - pd.Timedelta(minutes=5)
    entry_events = {
        t1: _event(t1, StructuralDirection.LONG, available_at=available_at)
    }
    exit_events = {t4: _event(t4, StructuralDirection.SHORT)}

    cold = replay_canonical_trade_lifecycle(
        snapshots,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
    )
    cold_events = canonical_decision_events_from_replay(cold)

    runner = PersistentCanonicalLifecycleReplayRunner(tmp_path)
    prefix = runner.run(
        "TEST",
        snapshots[:2],
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
    )
    assert prefix.checkpoint.state.entry_metadata.execution_available_at == available_at
    assert prefix.checkpoint.audit_markers.exit_watch_at is None
    assert prefix.checkpoint.audit_markers.exit_watch_price is None

    resumed = runner.run(
        "TEST",
        snapshots,
        entry_execution_events=entry_events,
        exit_execution_events=exit_events,
    )
    assert resumed.replay.initial_audit_markers == prefix.checkpoint.audit_markers
    assert resumed.replay.rows[0].audit_markers.exit_watch_at is None
    assert resumed.replay.final_audit_markers.exit_ready_at == t4
    assert resumed.replay.final_audit_markers.exit_ready_price == 99.0

    reconstructed_events = (
        *canonical_decision_events_from_replay(prefix.replay),
        *canonical_decision_events_from_replay(resumed.replay),
    )
    assert reconstructed_events == cold_events
    assert resumed.replay.final_state == cold.final_state
    assert resumed.replay.final_audit_markers == cold.final_audit_markers


def test_previous_decision_contract_version_requires_explicit_cold_replay(tmp_path):
    t1 = pd.Timestamp("2026-01-05 10:00")
    snapshot = _Snapshot("TEST", t1, 100.0)
    runner = PersistentCanonicalLifecycleReplayRunner(tmp_path)
    prefix = runner.run("TEST", (snapshot,))

    config = DecisionEngineConfig()
    assert DECISION_CONTRACT_VERSION == 3
    assert config.decision_contract_version == DECISION_CONTRACT_VERSION
    previous_version = DECISION_CONTRACT_VERSION - 1
    previous_repr = repr(config).replace(
        f"decision_contract_version={DECISION_CONTRACT_VERSION}",
        f"decision_contract_version={previous_version}",
    )
    previous_digest = sha256(previous_repr.encode("utf-8")).hexdigest()
    assert previous_digest != prefix.checkpoint.decision_config_digest

    runner.store.save(
        replace(prefix.checkpoint, decision_config_digest=previous_digest)
    )

    with pytest.raises(ValueError, match="cold replay is required"):
        runner.run("TEST", (snapshot,), config=config)


def test_old_checkpoint_schema_fails_closed(tmp_path):
    store = PersistentTradeLifecycleStore(tmp_path)
    store.path_for("TEST").write_text(
        '{"schema_version":1,"contract_version":1}',
        encoding="utf-8",
    )
    loaded = store.load("TEST")
    assert loaded.status is LifecycleCheckpointStatus.INVALID
    assert loaded.checkpoint is None


def test_position_metadata_rejects_future_execution_availability():
    as_of = pd.Timestamp("2026-01-05 10:00")
    with pytest.raises(ValueError, match="availability cannot be after"):
        PositionEntryMetadata(
            symbol="TEST",
            entry_horizon=DecisionHorizon.SHORT_TERM,
            scenario_kind=ScenarioKind.CONTINUATION,
            entry_as_of=as_of,
            entry_price=100.0,
            active_target_identity=None,
            execution_timeframe="30m",
            execution_observed_at=as_of,
            execution_available_at=as_of + pd.Timedelta(seconds=1),
            execution_reason="CONFIRMED",
            source_lineage=(),
        )
