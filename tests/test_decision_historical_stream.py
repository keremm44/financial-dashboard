from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.historical_stream import (
    HistoricalDecisionStreamConfig,
    apply_readiness_position_proxy,
    apply_trade_lifecycle,
    assess_snapshot_stream,
)
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision_audit import DecisionAction as AuditDecisionAction


def _assessment(*, side, action, as_of):
    final = SimpleNamespace(
        action=action,
        market_side=side,
        reasons=("TEST",),
        blockers=(),
        waiting_for=(),
        source_lineage=(),
    )
    placeholder = SimpleNamespace()
    return SimpleNamespace(
        horizon=DecisionHorizon.SHORT_TERM,
        as_of=pd.Timestamp(as_of),
        final=final,
        structural=placeholder,
        structural_snapshot=SimpleNamespace(relation=SimpleNamespace(value="ALIGNED")),
        durability=placeholder,
        reaction=placeholder,
        participation=placeholder,
        environment=placeholder,
        opportunity=placeholder,
        coverage=placeholder,
        conflict=placeholder,
        timing=placeholder,
        eligibility=placeholder,
        execution=placeholder,
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


def test_trade_lifecycle_suppresses_repeated_execution_actions():
    rows = (
        (_assessment(side=StructuralDirection.LONG, action=DecisionAction.BUY, as_of="2026-01-05 10:00"), 100.0),
        (_assessment(side=StructuralDirection.LONG, action=DecisionAction.BUY, as_of="2026-01-05 11:00"), 101.0),
        (_assessment(side=StructuralDirection.LONG, action=DecisionAction.READY, as_of="2026-01-05 12:00"), 102.0),
        (_assessment(side=StructuralDirection.SHORT, action=DecisionAction.SELL, as_of="2026-01-05 13:00"), 99.0),
        (_assessment(side=StructuralDirection.SHORT, action=DecisionAction.SELL, as_of="2026-01-05 14:00"), 98.0),
    )

    events = apply_trade_lifecycle(rows)

    assert [event.action for event in events] == [
        AuditDecisionAction.BUY,
        AuditDecisionAction.HOLD,
        AuditDecisionAction.HOLD,
        AuditDecisionAction.SELL,
        AuditDecisionAction.WAIT,
    ]
    assert events[0].snapshot["trade_lifecycle"]["position_state"] == "OPEN"
    assert events[1].snapshot["trade_lifecycle"]["requested_action"] == "BUY"
    assert events[1].snapshot["trade_lifecycle"]["action"] == "HOLD"
    assert events[3].snapshot["trade_lifecycle"]["position_state"] == "FLAT"
    assert events[4].snapshot["trade_lifecycle"]["transition_reason"] == "LIFECYCLE_FLAT_SELL_SUPPRESSED"


def test_historical_stream_has_no_magic_opportunity_calibration():
    config = HistoricalDecisionStreamConfig()
    assert config.opportunity_calibration is None
    assert config.readiness_position_proxy is False
    assert config.enforce_trade_lifecycle is True


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
