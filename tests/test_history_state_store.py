from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from financial_dashboard.decision import history_state_store as module
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.state_timeline import CausalStateStore


def test_history_state_store_preserves_snapshot_order(monkeypatch) -> None:
    cutoffs = (
        pd.Timestamp("2026-01-01 10:00", tz="Europe/Istanbul"),
        pd.Timestamp("2026-01-01 11:00", tz="Europe/Istanbul"),
    )
    snapshots = (object(), object())
    timings = SimpleNamespace()
    legacy = SimpleNamespace(
        symbol="TEST",
        decision_timeframe="1h",
        cutoffs=cutoffs,
        snapshots=snapshots,
        timings=timings,
    )

    class _LegacyRunner:
        def __init__(self, store) -> None:
            self.store = store

        def replay(self, symbol, *, config):
            assert symbol == "TEST"
            assert isinstance(config, HistoricalDecisionInputConfig)
            return legacy

    monkeypatch.setattr(module, "SinglePassHistoricalDecisionInputReplayRunner", _LegacyRunner)

    result = module.HistoricalDecisionStateStoreReplayRunner(object()).replay(
        "TEST",
        config=HistoricalDecisionInputConfig(max_bars=2),
    )

    assert isinstance(result.state_store, CausalStateStore)
    assert result.cutoffs == cutoffs
    assert result.snapshots == snapshots
    assert tuple(point.domain_position for point in result.state_store.decisions) == (0, 1)
    assert result.timings is timings
