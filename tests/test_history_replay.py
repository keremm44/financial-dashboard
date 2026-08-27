from __future__ import annotations

from _ui_test_data import make_ui_store
from financial_dashboard.decision.history_incremental import IncrementalHistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_replay import (
    HistoricalDecisionInputReplayRunner,
    LegacyHistoricalDecisionInputReplayRunner,
)
from financial_dashboard.decision.history_single_pass import SinglePassHistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig


def test_canonical_historical_runner_uses_incremental_causal_timeline() -> None:
    assert issubclass(HistoricalDecisionInputReplayRunner, IncrementalHistoricalDecisionInputReplayRunner)


def test_legacy_historical_runner_remains_explicit_debug_fallback() -> None:
    assert LegacyHistoricalDecisionInputReplayRunner is SinglePassHistoricalDecisionInputReplayRunner


def test_canonical_projection_cache_preserves_legacy_decision_snapshots(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    config = HistoricalDecisionInputConfig(max_bars=3)

    legacy = LegacyHistoricalDecisionInputReplayRunner(store).replay("THYAO", config=config)
    canonical = HistoricalDecisionInputReplayRunner(store).replay("THYAO", config=config)

    assert canonical.cutoffs == legacy.cutoffs
    assert canonical.snapshots == legacy.snapshots
