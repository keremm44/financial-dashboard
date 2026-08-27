from __future__ import annotations

import pytest

from _ui_test_data import make_ui_store
from financial_dashboard.decision.history_incremental import IncrementalHistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_replay import (
    HistoricalDecisionInputReplayRunner,
    LegacyHistoricalDecisionInputReplayRunner,
)
from financial_dashboard.decision.history_single_pass import SinglePassHistoricalDecisionInputReplayRunner
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.persistent_history_runner import DecisionTimelineCacheMiss


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


def test_cache_only_loader_fails_closed_before_timeline_is_built(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    runner = HistoricalDecisionInputReplayRunner(store)
    config = HistoricalDecisionInputConfig(max_bars=3)

    with pytest.raises(DecisionTimelineCacheMiss):
        runner.load_cached("THYAO", config=config)

    assert runner.last_persistent_cache_status == "MISS_CACHE_ONLY"
    assert runner.last_native_checkpoint_status == "NOT_TOUCHED"
    assert runner.last_supporting_checkpoint_status == "NOT_TOUCHED"


def test_cache_only_loader_reuses_exact_frozen_timeline_without_domain_replay(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    config = HistoricalDecisionInputConfig(max_bars=3)

    builder = HistoricalDecisionInputReplayRunner(store)
    built = builder.replay("THYAO", config=config)

    reader = HistoricalDecisionInputReplayRunner(store)
    cached = reader.load_cached("THYAO", config=config)

    assert cached.cutoffs == built.cutoffs
    assert cached.snapshots == built.snapshots
    assert reader.last_persistent_cache_status == "HIT_EXACT_CACHE_ONLY"
    assert reader.last_native_checkpoint_status == "NOT_TOUCHED"
    assert reader.last_supporting_checkpoint_status == "NOT_TOUCHED"
    assert cached.timings.native_replay_seconds == 0.0
    assert cached.timings.snapshot_assembly_seconds == 0.0
