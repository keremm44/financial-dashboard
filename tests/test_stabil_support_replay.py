from __future__ import annotations

import pandas as pd

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.data.analysis_inputs import load_analysis_inputs
from financial_dashboard.engines.stabil_support_lifecycle import SupportLifecycleEventType
from financial_dashboard.stabil_support_replay import (
    StabilSupportHistoricalReplayRunner,
    StabilSupportReplayRunner,
)
from financial_dashboard.ui.stabil_support_view_models import (
    stabil_support_event_counts_frame,
    stabil_support_events_frame,
    stabil_support_rebase_frame,
    stabil_support_reclaim_frame,
    stabil_support_replay_frame,
    stabil_support_retest_frame,
    stabil_support_summary_values,
)
from _ui_test_data import make_ui_store


def _snapshot_signature(snapshot) -> tuple[object, ...]:
    return (
        snapshot.as_of,
        snapshot.support_level,
        snapshot.support_floor,
        snapshot.support_origin_at,
        snapshot.support_confirmed_at,
        snapshot.support_available_at,
        snapshot.validity,
        snapshot.dynamics,
        snapshot.progression,
        snapshot.distance_pct,
        snapshot.distance_atr,
        snapshot.distance_delta_atr,
        snapshot.bars_since_support,
        snapshot.bars_above_support,
        snapshot.bars_below_support,
        snapshot.reclaim_count,
        tuple(
            (
                event.sequence,
                event.event_type,
                event.event_time,
                event.support_level,
                event.previous_support,
                event.new_support,
                event.bars_below_support,
            )
            for event in snapshot.events
        ),
    )


def test_latest_replay_reuses_shared_daily_batch_and_preserves_causality(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    inputs = load_analysis_inputs(
        store,
        symbol="THYAO",
        timeframes=ANALYSIS_TIMEFRAMES,
    )

    result = StabilSupportReplayRunner(store).replay(
        " thyao ",
        input_snapshot=inputs,
    )

    assert result.symbol == "THYAO"
    assert result.timeframe == "1d"
    assert result.input_batch is inputs.for_timeframe("1d").input_batch
    assert result.snapshot.as_of == result.input_batch.frame.iloc[-1]["timestamp"]
    for event in result.snapshot.events:
        assert pd.Timestamp(event.available_at) <= pd.Timestamp(result.snapshot.as_of)
        if event.confirmed_at is not None:
            assert pd.Timestamp(event.confirmed_at) <= pd.Timestamp(event.available_at)


def test_historical_replay_latest_matches_direct_replay(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    direct = StabilSupportReplayRunner(store).replay("THYAO")
    historical = StabilSupportHistoricalReplayRunner(store).replay(
        "THYAO",
        minimum_bars=20,
        step=3,
        max_points=30,
    )

    assert historical.points
    assert historical.points[-1].as_of == direct.snapshot.as_of
    assert _snapshot_signature(historical.latest) == _snapshot_signature(direct.snapshot)
    assert all(
        point.snapshot.as_of == point.as_of
        for point in historical.points
    )


def test_historical_replay_is_stable_when_only_display_window_changes(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    short = StabilSupportHistoricalReplayRunner(store).replay(
        "THYAO",
        minimum_bars=20,
        step=1,
        max_points=10,
    )
    long = StabilSupportHistoricalReplayRunner(store).replay(
        "THYAO",
        minimum_bars=20,
        step=1,
        max_points=30,
    )

    assert len(short.points) == 10
    assert tuple(_snapshot_signature(point.snapshot) for point in short.points) == tuple(
        _snapshot_signature(point.snapshot) for point in long.points[-10:]
    )


def test_view_models_keep_support_lifecycle_descriptive(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    replay = StabilSupportHistoricalReplayRunner(store).replay(
        "THYAO",
        minimum_bars=20,
        step=2,
        max_points=25,
    )
    snapshot = replay.latest
    assert snapshot is not None

    summary = stabil_support_summary_values(snapshot)
    assert "State" in summary
    assert "Distance %" in summary
    assert "Distance ATR" in summary
    forbidden = " ".join(summary.values()).upper()
    assert "BUY" not in forbidden
    assert "SELL" not in forbidden
    assert "STOP" not in forbidden
    assert "TAKE PROFIT" not in forbidden

    timeline = stabil_support_replay_frame(replay)
    assert not timeline.empty
    assert {
        "As of",
        "State",
        "Validity",
        "Dynamics",
        "Support",
        "Bars below",
        "Progression",
    }.issubset(timeline.columns)

    events = stabil_support_events_frame(snapshot)
    counts = stabil_support_event_counts_frame(snapshot)
    assert set(events.columns).issuperset({"Event", "Available", "Bars below"})
    assert set(counts.columns) == {"Event", "Count"}

    reclaim = stabil_support_reclaim_frame(snapshot)
    if not reclaim.empty:
        allowed = {
            SupportLifecycleEventType.SUPPORT_BREACHED.value,
            SupportLifecycleEventType.SUPPORT_FLOOR_BROKEN.value,
            SupportLifecycleEventType.SUPPORT_RECLAIMED.value,
            SupportLifecycleEventType.SUPPORT_LOST.value,
        }
        assert set(reclaim["Event"]).issubset(allowed)

    retest = stabil_support_retest_frame(snapshot)
    if not retest.empty:
        assert set(retest["Event"]).issubset(
            {
                SupportLifecycleEventType.SUPPORT_TESTED.value,
                SupportLifecycleEventType.SUPPORT_HELD.value,
            }
        )

    rebase = stabil_support_rebase_frame(snapshot)
    if not rebase.empty:
        assert set(rebase["Event"]).issubset(
            {
                SupportLifecycleEventType.SUPPORT_REBASED_HIGHER.value,
                SupportLifecycleEventType.SUPPORT_REBASED_LOWER.value,
                SupportLifecycleEventType.SUPPORT_LOST.value,
            }
        )
