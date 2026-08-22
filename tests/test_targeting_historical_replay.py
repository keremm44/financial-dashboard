from __future__ import annotations

import math

import pandas as pd

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.market_workspace import MarketAnalysisWorkspaceRunner
from financial_dashboard.targeting_historical_replay import (
    TargetingHistoricalReplayRunner,
    snapshot_signature,
)

from _ui_test_data import make_ui_store


TZ = "Europe/Istanbul"


def _frame(count: int) -> pd.DataFrame:
    timestamps = pd.date_range("2026-06-01 10:00", periods=count, freq="1h", tz=TZ)
    closes = [100.0 + 0.025 * index + 2.1 * math.sin(index / 3.0) for index in range(count)]
    opens = [closes[0], *closes[:-1]]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": [max(open_, close) + 0.55 for open_, close in zip(opens, closes)],
            "low": [min(open_, close) - 0.55 for open_, close in zip(opens, closes)],
            "close": closes,
            "volume": [1000.0 + index * 5.0 for index in range(count)],
            "is_closed": True,
            "is_complete": True,
        }
    )


def _save(store: ParquetOHLCVStore, frame: pd.DataFrame) -> None:
    store.merge_and_save(frame, symbol="TEST", timeframe="1h", source="replay-test")


def test_historical_targeting_points_are_causal_and_monotonic(tmp_path) -> None:
    store = ParquetOHLCVStore(tmp_path)
    _save(store, _frame(80))

    replay = TargetingHistoricalReplayRunner(store).replay(
        "TEST",
        timeframes=("1h",),
        reference_timeframe="1h",
        minimum_bars_per_timeframe=20,
        step=10,
    )

    assert len(replay.points) >= 5
    assert [pd.Timestamp(point.available_at) for point in replay.points] == sorted(
        pd.Timestamp(point.available_at) for point in replay.points
    )
    for point in replay.points:
        assert pd.Timestamp(point.snapshot.as_of) == pd.Timestamp(point.available_at)
        for cluster in point.snapshot.clusters:
            for evidence in cluster.evidence:
                assert pd.Timestamp(evidence.available_at) <= pd.Timestamp(point.available_at)


def test_historical_targeting_is_future_tail_invariant(tmp_path) -> None:
    prefix_store = ParquetOHLCVStore(tmp_path / "prefix")
    full_store = ParquetOHLCVStore(tmp_path / "full")
    prefix = _frame(60)
    _save(prefix_store, prefix)
    _save(full_store, _frame(80))

    prefix_replay = TargetingHistoricalReplayRunner(prefix_store).replay(
        "TEST",
        timeframes=("1h",),
        reference_timeframe="1h",
        minimum_bars_per_timeframe=20,
        step=59,
    )
    full_replay = TargetingHistoricalReplayRunner(full_store).replay(
        "TEST",
        timeframes=("1h",),
        reference_timeframe="1h",
        minimum_bars_per_timeframe=20,
        step=59,
    )

    assert prefix_replay.latest is not None
    assert full_replay.latest is not None
    assert pd.Timestamp(prefix_replay.points[-1].available_at) == pd.Timestamp(
        full_replay.points[-1].available_at
    )
    assert snapshot_signature(prefix_replay.latest) == snapshot_signature(full_replay.latest)


def test_workspace_target_domains_are_clipped_to_reference_cutoff(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    workspace = MarketAnalysisWorkspaceRunner(store).run(symbol="THYAO")

    targeting = workspace.targeting_result
    assert targeting is not None
    cutoff = pd.Timestamp(targeting.as_of)

    assert workspace.liquidity_result is not None
    for timeframe in workspace.liquidity_result.timeframes:
        assert pd.Timestamp(workspace.liquidity_result.for_timeframe(timeframe).available_at) <= cutoff

    assert workspace.order_block_result is not None
    for timeframe in workspace.order_block_result.timeframes:
        assert pd.Timestamp(workspace.order_block_result.for_timeframe(timeframe).available_at) <= cutoff

    assert workspace.fvg_engulfing_result is not None
    for timeframe in workspace.fvg_engulfing_result.timeframes:
        assert pd.Timestamp(workspace.fvg_engulfing_result.for_timeframe(timeframe).available_at) <= cutoff
