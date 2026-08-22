from __future__ import annotations

import pandas as pd

import financial_dashboard.three_domain_replay as replay_module
from financial_dashboard.data.quality import DataQualityStatus
from financial_dashboard.engines.pattern_compression_core import PatternCompressionConfig
from financial_dashboard.engines.three_domain_observer import FOUNDATION_OBSERVER_TIMEFRAMES
from financial_dashboard.three_domain_replay import CachedThreeDomainObserverRunner
from tests.test_structure_location_replay import _store


_TIMEFRAME_STEP = {
    "1d": pd.Timedelta(days=1),
    "4h": pd.Timedelta(hours=4),
    "2h": pd.Timedelta(hours=2),
    "1h": pd.Timedelta(hours=1),
    "30m": pd.Timedelta(minutes=30),
}


def _append_extreme_incomplete_preview(store, *, symbol: str) -> None:
    for timeframe in FOUNDATION_OBSERVER_TIMEFRAMES:
        frame = store.load(symbol, timeframe)
        preview = frame.iloc[[-1]].copy()
        preview["timestamp"] = preview["timestamp"] + _TIMEFRAME_STEP[timeframe]
        preview["open"] = 10_000.0
        preview["high"] = 10_010.0
        preview["low"] = 9_990.0
        preview["close"] = 10_005.0
        preview["volume"] = 1_000_000_000.0
        preview["is_closed"] = False
        preview["is_complete"] = False
        store.merge_and_save(
            preview,
            symbol=symbol,
            timeframe=timeframe,
            source="test-preview",
        )


def test_three_domain_foundation_is_parallel_descriptive_and_incomplete_safe(
    tmp_path,
    monkeypatch,
) -> None:
    store = _store(tmp_path)
    pattern_config = PatternCompressionConfig(profile="Hassas")
    captured_configs = []
    real_pattern_engine = replay_module.PatternCompressionEngine

    def pattern_factory(config=None):
        captured_configs.append(config)
        return real_pattern_engine(config)

    monkeypatch.setattr(replay_module, "PatternCompressionEngine", pattern_factory)
    runner = CachedThreeDomainObserverRunner(
        store,
        pattern_compression_config=pattern_config,
    )

    baseline = runner.run_foundation(symbol="THYAO")
    assert captured_configs == [pattern_config] * len(FOUNDATION_OBSERVER_TIMEFRAMES)

    assert baseline.timeframes == FOUNDATION_OBSERVER_TIMEFRAMES
    assert tuple(snapshot.timeframe for snapshot in baseline.pattern_snapshots) == baseline.timeframes
    assert tuple(state.timeframe for state in baseline.pressure.timeframe_states) == baseline.timeframes
    assert baseline.structure.timeframes == baseline.timeframes
    assert baseline.structure.symbol == "THYAO"
    assert baseline.location.symbol == "THYAO"
    assert baseline.observation.pressure is baseline.pressure
    assert baseline.observation.structure is baseline.structure
    assert baseline.observation.location is baseline.location
    assert not hasattr(baseline.observation, "action")

    for timeframe in baseline.timeframes:
        combined_replay = baseline.structure_location.replay_for(timeframe)
        pattern = baseline.pattern_for(timeframe)
        assert combined_replay.market_structure.bar_count == pattern.bar_count
        assert combined_replay.support_resistance.bar_count == pattern.bar_count
        assert pattern.result is not None
        assert pattern.export is not None

    assert baseline.location.event_outcomes == baseline.structure_location.location_outcomes
    assert (
        baseline.location.linked_event_count + baseline.location.no_match_event_count
        == len(baseline.location.event_outcomes)
    )

    restarted = CachedThreeDomainObserverRunner(
        store,
        pattern_compression_config=pattern_config,
    ).run_foundation(symbol="THYAO")
    assert restarted.pattern_snapshots == baseline.pattern_snapshots
    assert restarted.pressure == baseline.pressure
    assert restarted.structure == baseline.structure
    assert restarted.location == baseline.location
    assert restarted.observation == baseline.observation
    assert restarted.structure_location.confluence == baseline.structure_location.confluence
    assert (
        restarted.structure_location.location_outcomes
        == baseline.structure_location.location_outcomes
    )
    assert (
        restarted.structure_location.event_zone_links
        == baseline.structure_location.event_zone_links
    )

    _append_extreme_incomplete_preview(store, symbol="THYAO")
    with_preview = CachedThreeDomainObserverRunner(
        store,
        pattern_compression_config=pattern_config,
    ).run_foundation(symbol="THYAO")
    assert captured_configs == [pattern_config] * (
        3 * len(FOUNDATION_OBSERVER_TIMEFRAMES)
    )

    # Open/incomplete previews are visible as source-quality limitations but cannot
    # advance any domain's closed-bar state or the combined as-of timestamp.
    assert all(
        state.data_quality is DataQualityStatus.LIMITED
        for state in with_preview.pressure.timeframe_states
    )
    assert with_preview.pattern_snapshots == baseline.pattern_snapshots
    assert with_preview.structure == baseline.structure
    assert with_preview.location == baseline.location
    assert with_preview.observation.as_of == baseline.observation.as_of
    assert with_preview.observation.facts == baseline.observation.facts
    for timeframe in baseline.timeframes:
        before = baseline.structure_location.replay_for(timeframe)
        after = with_preview.structure_location.replay_for(timeframe)
        assert after.market_structure == before.market_structure
        assert after.support_resistance == before.support_resistance
        assert len(after.input_batch.frame) == len(before.input_batch.frame)
