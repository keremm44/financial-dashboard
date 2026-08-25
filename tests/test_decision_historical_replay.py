from types import SimpleNamespace

import pandas as pd

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.composer import DecisionAction
from financial_dashboard.decision.historical_replay import (
    CausalCutoffStore,
    HistoricalReplayConfig,
    apply_readiness_position_proxy,
)
from financial_dashboard.decision.structural import DecisionHorizon, StructuralDirection
from financial_dashboard.decision_audit import DecisionAction as AuditDecisionAction


def test_cutoff_store_never_exposes_future_bars(tmp_path):
    base = ParquetOHLCVStore(tmp_path)
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-05 10:00", "2026-01-05 11:00", "2026-01-05 12:00"]),
            "open": [10.0, 11.0, 12.0],
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.5, 11.5, 12.5],
            "volume": [100.0, 100.0, 100.0],
        }
    )
    base.merge_and_save(frame, symbol="TEST", timeframe="1h", source="TEST")

    # 11:00 bar is only consumable after its causal availability time. Using the
    # clock itself keeps this test independent of exchange/session assumptions.
    provisional = CausalCutoffStore(tmp_path, cutoff="2100-01-01")
    cutoff = provisional.clock.available_at(pd.Timestamp("2026-01-05 11:00"), "1h")
    historical = CausalCutoffStore(tmp_path, cutoff=cutoff)
    clipped = historical.load("TEST", "1h")

    assert list(clipped["timestamp"]) == list(pd.to_datetime(["2026-01-05 10:00", "2026-01-05 11:00"]))


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


def test_historical_replay_has_no_magic_opportunity_calibration():
    config = HistoricalReplayConfig()
    assert config.opportunity_calibration is None
    assert config.readiness_position_proxy is False
