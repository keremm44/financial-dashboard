from __future__ import annotations

import math

import pandas as pd
import pytest

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.quality import DataQualityStatus
from financial_dashboard.engines.raw_indicator_dashboard import (
    RawDataQuality,
    RawIndicatorConfig,
    TrendProfile,
    VolumeQuality,
)
from financial_dashboard.ham_mtf_replay import (
    HAM_EVIDENCE_TIMEFRAMES,
    HamMTFEvidenceReplayRunner,
    ham_profile_for_timeframe,
    replay_ham_evidence_from_cache,
)
from _ui_test_data import make_ui_store


EXPECTED_PROFILES = {
    "1d": TrendProfile.XAG_1D,
    "4h": TrendProfile.XAG_4H,
    "2h": TrendProfile.XAG_2H,
    "1h": TrendProfile.XAG_1H,
    "30m": TrendProfile.XAG_30M,
}


def test_profile_mapping_is_exact_and_rejects_unsupported_timeframes() -> None:
    assert {
        timeframe: ham_profile_for_timeframe(timeframe)
        for timeframe in HAM_EVIDENCE_TIMEFRAMES
    } == EXPECTED_PROFILES
    assert ham_profile_for_timeframe(" 4H ") == TrendProfile.XAG_4H
    with pytest.raises(ValueError, match="unsupported Ham evidence timeframe"):
        ham_profile_for_timeframe("15m")


def test_five_timeframe_runner_replays_every_full_cache_independently(tmp_path) -> None:
    make_ui_store(tmp_path)
    result = HamMTFEvidenceReplayRunner(ParquetOHLCVStore(tmp_path)).replay("thyao")

    assert result.symbol == "THYAO"
    assert result.timeframes == HAM_EVIDENCE_TIMEFRAMES
    assert result.total_bar_count == 5 * 160
    assert tuple(replay.timeframe for replay in result.timeframe_replays) == HAM_EVIDENCE_TIMEFRAMES

    for replay in result.timeframe_replays:
        assert replay.profile == EXPECTED_PROFILES[replay.timeframe]
        assert replay.bar_count == 160
        assert replay.bar_count == len(replay.input_batch.frame)
        assert replay.earliest_timestamp == replay.input_batch.frame.iloc[0]["timestamp"]
        assert replay.latest_timestamp == replay.input_batch.frame.iloc[-1]["timestamp"]
        assert replay.source_quality.status == DataQualityStatus.OK
        assert replay.history[0].data_quality == RawDataQuality.WARMUP
        assert replay.latest.raw.valid_evidence_count >= 6
        assert replay.latest.indicator_count == 10
        assert not hasattr(replay.latest, "system_state")


def test_restart_is_deterministic_and_one_timeframe_append_cannot_change_others(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    runner = HamMTFEvidenceReplayRunner(store)
    before = runner.replay("THYAO")
    restarted = HamMTFEvidenceReplayRunner(store).replay("THYAO")

    for timeframe in HAM_EVIDENCE_TIMEFRAMES:
        assert restarted.replay_for(timeframe).history == before.replay_for(timeframe).history

    old_1h = store.load("THYAO", "1h")
    previous = old_1h.iloc[-1]
    close = float(previous["close"]) + 0.4
    appended = pd.DataFrame(
        [
            {
                "timestamp": previous["timestamp"] + pd.Timedelta(hours=1),
                "open": float(previous["close"]),
                "high": close + 0.5,
                "low": float(previous["close"]) - 0.5,
                "close": close,
                "volume": float(previous["volume"]) + 50.0,
                "is_closed": True,
                "is_complete": True,
            }
        ]
    )
    store.merge_and_save(appended, symbol="THYAO", timeframe="1h", source="test")
    after = runner.replay("THYAO")

    assert after.replay_for("1h").bar_count == before.replay_for("1h").bar_count + 1
    for timeframe in ("1d", "4h", "2h", "30m"):
        assert after.replay_for(timeframe).history == before.replay_for(timeframe).history


def test_incomplete_cache_tail_is_reported_but_never_enters_evidence_history(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    before = HamMTFEvidenceReplayRunner(store).replay(
        "THYAO", timeframes=("30m",)
    ).replay_for("30m")
    previous = store.load("THYAO", "30m").iloc[-1]
    preview = pd.DataFrame(
        [
            {
                "timestamp": previous["timestamp"] + pd.Timedelta(minutes=30),
                "open": float(previous["close"]),
                "high": float(previous["close"]) * 5.0,
                "low": float(previous["close"]) * 0.2,
                "close": float(previous["close"]) * 4.0,
                "volume": float(previous["volume"]) * 10.0,
                "is_closed": False,
                "is_complete": True,
            }
        ]
    )
    store.merge_and_save(preview, symbol="THYAO", timeframe="30m", source="preview")

    after = HamMTFEvidenceReplayRunner(store).replay(
        "THYAO", timeframes=("30m",)
    ).replay_for("30m")
    assert after.source_quality.status == DataQualityStatus.LIMITED
    assert "One or more candles are open" in after.source_quality.warnings
    assert after.bar_count == before.bar_count
    assert after.history == before.history
    assert after.latest == before.latest


def test_short_daily_cache_keeps_all_bars_and_exposes_volume_warmup(tmp_path) -> None:
    store = ParquetOHLCVStore(tmp_path)
    count = 43
    closes = [100.0 + index * 0.15 + math.sin(index / 4.0) for index in range(count)]
    opens = [closes[0], *closes[:-1]]
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=count, freq="1D", tz="UTC"),
            "open": opens,
            "high": [max(open_, close) + 0.5 for open_, close in zip(opens, closes)],
            "low": [min(open_, close) - 0.5 for open_, close in zip(opens, closes)],
            "close": closes,
            "volume": [1000.0 + index for index in range(count)],
            "is_closed": True,
            "is_complete": True,
        }
    )
    store.merge_and_save(frame, symbol="ASELS", timeframe="1d", source="test")

    replay = replay_ham_evidence_from_cache(
        tmp_path,
        symbol="ASELS",
        timeframes=("1d",),
    ).replay_for("1d")
    assert replay.bar_count == count
    assert replay.profile == TrendProfile.XAG_1D
    assert replay.latest.raw.volume_quality == VolumeQuality.WAITING
    assert replay.latest.raw.volume_calculable is False
    assert replay.latest.families.flow.ready is False
    assert replay.warmup_bar_count > 0
    assert replay.ready_bar_count > 0


def test_runner_rejects_duplicate_timeframes_and_profile_mismatch(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    runner = HamMTFEvidenceReplayRunner(store)
    with pytest.raises(ValueError, match="must be unique"):
        runner.replay("THYAO", timeframes=("1h", "1H"))

    with pytest.raises(ValueError, match="must use profile"):
        HamMTFEvidenceReplayRunner(
            store,
            raw_configs={"1h": RawIndicatorConfig(profile=TrendProfile.XAG_4H)},
        )
