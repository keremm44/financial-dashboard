from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from financial_dashboard.decision_audit.early_move_research import (
    EarlyMoveAuditConfig,
    _pre_move_atr,
    audit_early_move_states,
)
from financial_dashboard.decision_audit.models import DecisionAction, DecisionEvent
from financial_dashboard.decision_audit.research import LargeMarketMove


def _bars() -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2026-01-01 10:00", tz="Europe/Istanbul")
    close = 100.0
    for index in range(20):
        timestamp = start + pd.Timedelta(hours=4 * index)
        rows.append(
            {
                "timestamp": timestamp,
                "high": close + 2.0,
                "low": close - 2.0,
                "close": close,
            }
        )
        close += 1.0
    return pd.DataFrame(rows)


def _event(timestamp: pd.Timestamp, price: float) -> DecisionEvent:
    return DecisionEvent(
        timestamp=timestamp,
        action=DecisionAction.WAIT,
        price=price,
        reasons=("WAIT_REASON",),
        blockers=(),
        waiting_for=("SETUP_TRIGGER",),
        snapshot={"entry_decision": {"selected_horizon": None, "trade_horizon": None}},
    )


def test_pre_move_atr_uses_only_completed_bars_strictly_before_start():
    bars = _bars()
    start = bars.iloc[16]["timestamp"]
    atr, count = _pre_move_atr(bars, start, 14)

    assert count == 14
    assert atr == 4.0


def test_early_move_checkpoint_uses_first_causal_decision_reaching_atr_threshold(monkeypatch):
    bars = _bars()
    start = bars.iloc[16]["timestamp"]
    end = start + pd.Timedelta(hours=12)
    move = LargeMarketMove(
        direction="UP",
        classification="LARGE",
        start_time=start,
        end_time=end,
        start_price=100.0,
        end_price=120.0,
        move_pct=20.0,
        duration_hours=12.0,
        four_hour_bars=4,
        trading_days=2,
        move_pct_per_4h_bar=6.67,
        move_pct_per_trading_day=10.0,
    )
    events = (
        _event(start + pd.Timedelta(hours=1), 102.0),
        _event(start + pd.Timedelta(hours=2), 103.5),
        _event(start + pd.Timedelta(hours=3), 106.0),
    )

    fake_assessment = SimpleNamespace(
        timing=SimpleNamespace(state=SimpleNamespace(value="READY")),
        opportunity=SimpleNamespace(state=SimpleNamespace(value="MODERATE")),
        eligibility=SimpleNamespace(state=SimpleNamespace(value="ELIGIBLE")),
    )
    fake_scenario = SimpleNamespace(
        presence=SimpleNamespace(value="PRESENT"),
        stage=SimpleNamespace(value="QUALIFIED"),
        kind=SimpleNamespace(value="CONTINUATION"),
    )
    monkeypatch.setattr(
        "financial_dashboard.decision_audit.early_move_research.assess_horizon_decision",
        lambda *args, **kwargs: fake_assessment,
    )
    monkeypatch.setattr(
        "financial_dashboard.decision_audit.early_move_research.assess_entry_scenario",
        lambda *args, **kwargs: fake_scenario,
    )

    snapshots = tuple(SimpleNamespace(as_of=event.timestamp) for event in events)
    report = audit_early_move_states(
        symbol="TEST",
        moves=(move,),
        market_bars_4h=bars,
        decisions=events,
        snapshots=snapshots,
        config=EarlyMoveAuditConfig(atr_period=14, atr_multiples=(0.75, 1.25)),
    )

    episode = report.episodes[0]
    assert episode.start_atr_4h == 4.0
    assert episode.checkpoints[0].reached_at == events[1].timestamp
    assert episode.checkpoints[0].reached_price == 103.5
    assert episode.checkpoints[1].reached_at == events[2].timestamp
    assert episode.checkpoints[1].reached_price == 106.0
    assert episode.checkpoints[0].st_stage == "QUALIFIED"
    assert episode.checkpoints[0].waiting_for == ("SETUP_TRIGGER",)
