from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.context.envelope import ContextDataQuality
from financial_dashboard.context.permissions import GateState, PermissionEnvelope, PermissionScope, PermittedSide
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.execution import ExecutionTriggerEvent, ExecutionTriggerState
from financial_dashboard.decision.historical_stream import (
    HistoricalDecisionStreamConfig,
    apply_readiness_position_proxy,
    apply_trade_lifecycle,
    assess_snapshot_stream,
)
from financial_dashboard.decision.structural import (
    DecisionHorizon,
    HorizonRelation,
    StructuralDirection,
    ThesisState,
)
from financial_dashboard.decision.timing import TimingState
from financial_dashboard.decision_audit import DecisionAction as AuditDecisionAction


@dataclass(frozen=True)
class _FinalStub:
    action: DecisionAction
    market_side: StructuralDirection
    reasons: tuple[str, ...] = ("TEST",)
    blockers: tuple[str, ...] = ()
    waiting_for: tuple[str, ...] = ()
    source_lineage: tuple[str, ...] = ()


def _assessment(
    *,
    side,
    action,
    as_of,
    lt_direction=StructuralDirection.LONG,
    lt_thesis=ThesisState.INTACT,
    relation=HorizonRelation.ALIGNED,
    transition_target=None,
    execution_state=ExecutionTriggerState.ABSENT,
    timing_state=TimingState.READY,
    waiting_for=(),
    blockers=(),
):
    final = _FinalStub(
        action=action,
        market_side=side,
        waiting_for=tuple(waiting_for),
        blockers=tuple(blockers),
    )
    placeholder = SimpleNamespace()
    lt = SimpleNamespace(
        direction=lt_direction,
        thesis_state=lt_thesis,
        transition_target=transition_target,
        data_quality=ContextDataQuality.VALID,
        source_refs=(),
    )
    st = SimpleNamespace(source_refs=())
    permission = PermissionEnvelope(
        scope=PermissionScope.CONTINUATION_ONLY,
        permitted_side=PermittedSide.LONG,
        gate_state=GateState.OPEN,
    )
    return SimpleNamespace(
        horizon=DecisionHorizon.SHORT_TERM,
        as_of=pd.Timestamp(as_of),
        final=final,
        structural=placeholder,
        structural_snapshot=SimpleNamespace(
            long_term=lt,
            short_term=st,
            relation=relation,
        ),
        permission=permission,
        durability=placeholder,
        reaction=placeholder,
        participation=placeholder,
        environment=placeholder,
        opportunity=placeholder,
        coverage=placeholder,
        conflict=placeholder,
        timing=SimpleNamespace(state=timing_state),
        eligibility=placeholder,
        execution=SimpleNamespace(state=execution_state),
    )


def _exit_event(as_of):
    timestamp = pd.Timestamp(as_of)
    return ExecutionTriggerEvent(
        state=ExecutionTriggerState.CONFIRMED,
        side=StructuralDirection.SHORT,
        timeframe="30m",
        observed_at=timestamp,
        available_at=timestamp,
        reason="TEST_LONG_EXIT_EVENT",
        source_refs=(),
    )


def test_readiness_proxy_opens_long_and_closes_on_opposing_ready():
    rows = (
        (_assessment(side=StructuralDirection.LONG, action=DecisionAction.WAIT, as_of="2026-01-05 10:00"), 100.0),
        (_assessment(side=StructuralDirection.LONG, action=DecisionAction.READY, as_of="2026-01-05 11:00"), 101.0),
        (_assessment(side=StructuralDirection.LONG, action=DecisionAction.READY, as_of="2026-01-05 12:00"), 102.0),
        (_assessment(side=StructuralDirection.SHORT, action=DecisionAction.READY, as_of="2026-01-05 13:00"), 99.0),
    )

    events = apply_readiness_position_proxy(rows)

    assert [event.action for event in events] == [
        AuditDecisionAction.WAIT,
        AuditDecisionAction.BUY,
        AuditDecisionAction.READY,
        AuditDecisionAction.SELL,
    ]
    assert "AUDIT_PROXY_LONG_ENTRY_FROM_READY" in events[1].reasons
    assert "AUDIT_PROXY_LONG_EXIT_FROM_OPPOSING_READY" in events[3].reasons


def test_trade_lifecycle_uses_dedicated_exit_path_not_legacy_sell_candidate():
    exit_time = pd.Timestamp("2026-01-05 14:00")
    rows = (
        (_assessment(side=StructuralDirection.LONG, action=DecisionAction.BUY, as_of="2026-01-05 10:00"), 100.0),
        (_assessment(side=StructuralDirection.LONG, action=DecisionAction.BUY, as_of="2026-01-05 11:00"), 101.0),
        (
            _assessment(
                side=StructuralDirection.SHORT,
                action=DecisionAction.SELL,
                as_of="2026-01-05 12:00",
                relation=HorizonRelation.COUNTER_REACTION,
            ),
            99.0,
        ),
        (
            _assessment(
                side=StructuralDirection.SHORT,
                action=DecisionAction.NO_TRADE,
                as_of="2026-01-05 13:00",
                lt_direction=StructuralDirection.SHORT,
                lt_thesis=ThesisState.INTACT,
                relation=HorizonRelation.COUNTER_REACTION,
            ),
            98.0,
        ),
        (
            _assessment(
                side=StructuralDirection.SHORT,
                action=DecisionAction.NO_TRADE,
                as_of=exit_time,
                lt_direction=StructuralDirection.SHORT,
                lt_thesis=ThesisState.INTACT,
                relation=HorizonRelation.COUNTER_REACTION,
            ),
            97.0,
        ),
        (_assessment(side=StructuralDirection.SHORT, action=DecisionAction.SELL, as_of="2026-01-05 15:00"), 96.0),
    )

    events = apply_trade_lifecycle(
        rows,
        exit_execution_events={exit_time: _exit_event(exit_time)},
    )

    assert [event.action for event in events] == [
        AuditDecisionAction.BUY,
        AuditDecisionAction.HOLD,
        AuditDecisionAction.HOLD,
        AuditDecisionAction.HOLD,
        AuditDecisionAction.SELL,
        AuditDecisionAction.WAIT,
    ]
    assert events[0].snapshot["lifecycle_phase"] == "ENTRY_EXECUTED"
    assert events[0].snapshot["permission"]["gate_state"] == "OPEN"
    assert events[1].snapshot["trade_lifecycle"]["requested_action"] == "BUY"
    assert events[2].snapshot["trade_lifecycle"]["requested_action"] == "SELL"
    assert events[2].snapshot["trade_lifecycle"]["action"] == "HOLD"
    assert events[2].snapshot["lifecycle_phase"] == "EXIT_READY"
    assert events[2].snapshot["long_exit"]["position_health"] == "PRESSURED"
    assert events[2].snapshot["long_exit"]["stage"] == "EXIT_READY"
    assert events[3].snapshot["long_exit"]["stage"] == "EXIT_READY"
    assert events[3].snapshot["lifecycle_phase"] == "EXIT_READY"
    assert events[3].snapshot["long_exit"]["execution"]["state"] == "ABSENT"
    assert events[4].snapshot["long_exit"]["execution"]["state"] == "CONFIRMED"
    assert events[4].snapshot["trade_lifecycle"]["position_state"] == "FLAT"
    assert events[4].snapshot["lifecycle_phase"] == "EXIT_EXECUTED"
    assert events[4].side.value == "LONG"
    assert events[5].snapshot["trade_lifecycle"]["transition_reason"] == "LIFECYCLE_FLAT_SELL_SUPPRESSED"
    assert events[5].snapshot["decision"]["waiting_for"] == ["LIFECYCLE_LONG_ENTRY_PATH"]


def test_lifecycle_readiness_proxy_exercises_real_ownership_and_exit_stages():
    rows = (
        (
            _assessment(
                side=StructuralDirection.LONG,
                action=DecisionAction.READY,
                as_of="2026-01-05 10:00",
                waiting_for=("FRESH_EXECUTION_EVENT",),
            ),
            100.0,
        ),
        (
            _assessment(
                side=StructuralDirection.LONG,
                action=DecisionAction.WAIT,
                as_of="2026-01-05 11:00",
            ),
            101.0,
        ),
        (
            _assessment(
                side=StructuralDirection.SHORT,
                action=DecisionAction.NO_TRADE,
                as_of="2026-01-05 12:00",
                lt_direction=StructuralDirection.SHORT,
                lt_thesis=ThesisState.INTACT,
                relation=HorizonRelation.COUNTER_REACTION,
                blockers=("ACTION_SIDE_NOT_PERMITTED:SHORT",),
            ),
            98.0,
        ),
    )

    events = apply_trade_lifecycle(rows, readiness_proxy=True)

    assert [event.action for event in events] == [
        AuditDecisionAction.BUY,
        AuditDecisionAction.HOLD,
        AuditDecisionAction.SELL,
    ]
    assert events[0].snapshot["lifecycle_readiness_proxy"] is True
    assert events[0].snapshot["trade_lifecycle"]["position_state"] == "OPEN"
    assert events[0].waiting_for == ()
    assert "AUDIT_PROXY_LONG_ENTRY_FROM_READY" in events[0].reasons
    assert events[1].snapshot["long_exit"]["position_health"] == "HEALTHY"
    assert events[2].snapshot["long_exit"]["stage"] == "EXIT_READY"
    assert events[2].snapshot["trade_lifecycle"]["position_state"] == "FLAT"
    assert events[2].waiting_for == ()
    assert events[2].blockers == ()
    assert "AUDIT_PROXY_LONG_EXIT_FROM_EXIT_READY" in events[2].reasons


def test_historical_stream_defaults_to_long_only_action_policy():
    config = HistoricalDecisionStreamConfig()
    assert config.opportunity_calibration is None
    assert config.readiness_position_proxy is False
    assert config.lifecycle_readiness_proxy is False
    assert config.enforce_trade_lifecycle is True
    assert config.action_policy.permitted_sides == (StructuralDirection.LONG,)


def test_historical_stream_rejects_overlapping_proxy_modes():
    with pytest.raises(ValueError, match="mutually exclusive"):
        HistoricalDecisionStreamConfig(
            readiness_position_proxy=True,
            lifecycle_readiness_proxy=True,
        )
    with pytest.raises(ValueError, match="requires enforce_trade_lifecycle"):
        HistoricalDecisionStreamConfig(
            lifecycle_readiness_proxy=True,
            enforce_trade_lifecycle=False,
        )


def test_historical_stream_module_cannot_rebuild_workspace_or_load_cache():
    source = Path("src/financial_dashboard/decision/historical_stream.py").read_text(encoding="utf-8")
    forbidden = (
        "market_workspace",
        "MarketAnalysisWorkspaceRunner",
        "ParquetOHLCVStore",
        "CausalCutoffStore",
        "clip_analysis_inputs_at_cutoff",
    )
    for token in forbidden:
        assert token not in source


def test_snapshot_stream_must_be_strictly_increasing(monkeypatch):
    import financial_dashboard.decision.historical_stream as module

    monkeypatch.setattr(
        module,
        "assess_horizon_decision",
        lambda snapshot, horizon, config, execution_event: _assessment(
            side=StructuralDirection.LONG,
            action=DecisionAction.WAIT,
            as_of=snapshot.as_of,
        ),
    )
    first = SimpleNamespace(as_of=pd.Timestamp("2026-01-05 11:00"), current_price=100.0, source_refs=())
    second = SimpleNamespace(as_of=pd.Timestamp("2026-01-05 11:00"), current_price=101.0, source_refs=())

    with pytest.raises(ValueError, match="strictly increasing"):
        assess_snapshot_stream((first, second))
