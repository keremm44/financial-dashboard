from dataclasses import dataclass
from types import SimpleNamespace

import pandas as pd

from financial_dashboard.decision.canonical_events import canonical_decision_events_from_replay
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.execution import ExecutionTriggerState
from financial_dashboard.decision.lifecycle import ExitStage, PositionState
from financial_dashboard.decision.lifecycle_replay import replay_canonical_trade_lifecycle
from financial_dashboard.decision.scenario import ScenarioKind, ScenarioPresence, ScenarioStage
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision.trade_exit import ExitExecutionState, PositionHealth
from financial_dashboard.decision_audit import DecisionAction as AuditDecisionAction


def _scenario():
    return SimpleNamespace(
        horizon=DecisionHorizon.SHORT_TERM,
        presence=ScenarioPresence.PRESENT,
        stage=ScenarioStage.QUALIFIED,
        structural_direction=StructuralDirection.LONG,
        kind=ScenarioKind.SHORT_TERM_STANDALONE,
        active_target_identity="target:st:1",
    )


def _entry(action, *, with_event):
    scenario = _scenario()
    return SimpleNamespace(
        action=action,
        selected_horizon=DecisionHorizon.SHORT_TERM,
        scenario_stage=ScenarioStage.QUALIFIED,
        execution_state=ExecutionTriggerState.CONFIRMED if with_event else ExecutionTriggerState.ABSENT,
        execution_event_consumed=with_event,
        arbitration=SimpleNamespace(selected_scenario=scenario),
        reasons=("ENTRY_READY",),
        blockers=(),
        waiting_for=() if with_event else ("FRESH_EXECUTION_EVENT",),
        source_lineage=("entry:lineage",),
    )


@dataclass(frozen=True)
class _Snapshot:
    symbol: str
    as_of: pd.Timestamp
    current_price: float
    exit_ready: bool = False
    source_refs: tuple = ()

    def entry_decision(self, *, config=None, execution_event=None):
        return _entry(
            DecisionAction.BUY if execution_event is not None else DecisionAction.READY,
            with_event=execution_event is not None,
        )

    def position_exit_decision(self, state, *, execution_event=None):
        assert state.position is PositionState.OPEN
        assert state.entry_metadata is not None
        ready = self.exit_ready
        confirmed = ready and execution_event is not None
        stage = ExitStage.EXIT_READY if ready else ExitStage.MONITOR
        execution = SimpleNamespace(
            state=ExitExecutionState.CONFIRMED if confirmed else ExitExecutionState.ABSENT,
            source_refs=(),
        )
        return SimpleNamespace(
            action=DecisionAction.SELL if confirmed else DecisionAction.HOLD,
            as_of=self.as_of,
            entry_horizon=state.entry_metadata.entry_horizon,
            stage=stage,
            position_health=PositionHealth.PRESSURED if ready else PositionHealth.HEALTHY,
            structural=SimpleNamespace(),
            execution=execution,
            execution_event_consumed=confirmed,
            reasons=("EXIT_READY",) if ready else ("MONITOR",),
            waiting_for=() if confirmed else (("FRESH_LONG_EXIT_EXECUTION_EVENT",) if ready else ()),
            source_refs=(),
            source_lineage=("exit:lineage",),
        )


def test_canonical_readiness_proxy_opens_and_closes_only_at_canonical_boundaries():
    t1 = pd.Timestamp("2026-01-05 10:00")
    t2 = pd.Timestamp("2026-01-05 10:30")
    t3 = pd.Timestamp("2026-01-05 11:00")
    replay = replay_canonical_trade_lifecycle(
        (
            _Snapshot("TEST", t1, 100.0),
            _Snapshot("TEST", t2, 102.0),
            _Snapshot("TEST", t3, 104.0, exit_ready=True),
        ),
        readiness_execution_proxy=True,
    )

    assert [row.action for row in replay.rows] == [
        DecisionAction.BUY,
        DecisionAction.HOLD,
        DecisionAction.SELL,
    ]
    assert [row.execution_proxy_used for row in replay.rows] == [True, False, True]
    assert replay.rows[0].current_state.entry_metadata is not None
    assert replay.rows[0].current_state.entry_metadata.entry_horizon is DecisionHorizon.SHORT_TERM
    assert replay.final_state.position is PositionState.FLAT

    events = canonical_decision_events_from_replay(replay)
    assert [event.action for event in events] == [
        AuditDecisionAction.BUY,
        AuditDecisionAction.HOLD,
        AuditDecisionAction.SELL,
    ]
    assert events[0].snapshot["canonical_lifecycle"] is True
    assert events[0].snapshot["canonical_readiness_proxy"] is True
    assert events[0].snapshot["entry_horizon"] == "SHORT_TERM"
    assert events[0].snapshot["scenario_kind"] == "SHORT_TERM_STANDALONE"
    assert events[0].snapshot["audit_markers"]["scenario_qualified_at"] == t1
    assert events[0].snapshot["audit_markers"]["ready_for_execution_at"] == t1
    assert events[2].snapshot["canonical_readiness_proxy"] is True
    assert events[2].snapshot["position_exit"]["stage"] == "EXIT_READY"


def test_canonical_default_does_not_invent_execution_events():
    t1 = pd.Timestamp("2026-01-05 10:00")
    replay = replay_canonical_trade_lifecycle((_Snapshot("TEST", t1, 100.0),))
    assert replay.rows[0].action is DecisionAction.READY
    assert replay.rows[0].execution_proxy_used is False
    assert replay.final_state.position is PositionState.FLAT


def test_buy_sell_backtest_is_cache_only_and_uses_canonical_lifecycle():
    source = open("scripts/buy_sell_backtest.py", encoding="utf-8").read()
    assert "load_frozen_decision_timeline(" in source
    assert "replay_canonical_trade_lifecycle(" in source
    assert "canonical_decision_events_from_replay(" in source
    assert "FROZEN_DECISION_TIMELINE_CACHE_ONLY" in source
    assert "FROZEN_DECISION_TIMELINE_CACHE_MISS" in source
    assert "DOMAIN_REPLAY_AND_SNAPSHOT_SECONDS\\t0.00" in source
    assert "BUY_SELL_BACKTEST_OK" in source
    assert "HistoricalDecisionInputReplayRunner" not in source
    assert ".replay(args.symbol" not in source


def test_timeline_builder_is_the_explicit_domain_replay_path():
    source = open("scripts/build_decision_timeline_cache.py", encoding="utf-8").read()
    assert "ensure_frozen_decision_timeline(" in source
    assert "HistoricalDecisionInputReplayRunner" in source
    assert "runner.replay(" in source
    assert "DECISION_TIMELINE_CACHE_READY" in source
