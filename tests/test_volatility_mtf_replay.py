from __future__ import annotations

from types import MappingProxyType

import numpy as np
import pandas as pd

from financial_dashboard.data.analysis_inputs import AnalysisInputSnapshot, TimeframeInputSnapshot
from financial_dashboard.data.engine_input import prepare_engine_input
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.engines.volatility_bands_fib_engine import VolatilityState
from financial_dashboard.engines.volatility_bands_fib_final import VolatilityBandsFibFinalExport
from financial_dashboard.engines.volatility_direction_transition import (
    EarlyDirectionEvidence,
    EarlyDirectionTransition,
    VolatilityDirectionSnapshot,
)
from financial_dashboard.volatility_mtf_replay import (
    VOLATILITY_TIMEFRAMES,
    VolatilityMTFReplay,
    VolatilityMTFReplayRunner,
    VolatilityTimeframeReplay,
    direction_lag_records,
)

TZ = "Europe/Istanbul"


def _frame(timeframe: str, n: int = 170) -> pd.DataFrame:
    hours = {"2h": 2, "4h": 4, "1d": 24}[timeframe]
    ts = pd.date_range("2026-01-02 10:00", periods=n, freq=f"{hours}h", tz=TZ)
    x = np.linspace(100.0, 135.0, n) + np.sin(np.arange(n) / 5.0) * 2.5
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": x - 0.3,
            "high": x + 0.9,
            "low": x - 1.0,
            "close": x,
            "volume": 1000.0,
            "is_closed": True,
            "is_complete": True,
        }
    )


def _inputs() -> AnalysisInputSnapshot:
    items = {}
    fingerprint = []
    for tf in VOLATILITY_TIMEFRAMES:
        frame = _frame(tf)
        items[tf] = TimeframeInputSnapshot(tf, frame, prepare_engine_input(frame))
        fingerprint.append((tf, len(frame), 1))
    return AnalysisInputSnapshot(
        symbol="ASELS",
        timeframes=VOLATILITY_TIMEFRAMES,
        by_timeframe=MappingProxyType(items),
        fingerprint=tuple(fingerprint),
    )


def _snapshot(
    timestamp: pd.Timestamp,
    regime: VolatilityState,
    *,
    early: EarlyDirectionTransition = EarlyDirectionTransition.NONE,
    episode_started: bool = False,
    episode_id: int = 0,
) -> VolatilityDirectionSnapshot:
    return VolatilityDirectionSnapshot(
        timestamp=timestamp,
        core_result=None,
        confirmed_export=VolatilityBandsFibFinalExport(regime=int(regime)),
        early=EarlyDirectionEvidence(
            state=early,
            raw_state=early,
            episode_started=episode_started,
            episode_id=episode_id,
        ),
    )


def test_mtf_replay_reuses_shared_prepared_inputs(tmp_path) -> None:
    inputs = _inputs()
    replay = VolatilityMTFReplayRunner(ParquetOHLCVStore(tmp_path)).replay(
        "ASELS", input_snapshot=inputs
    )
    assert replay.timeframes == VOLATILITY_TIMEFRAMES
    assert replay.profile == "Dengeli"
    for tf in VOLATILITY_TIMEFRAMES:
        assert replay.for_timeframe(tf).latest is not None
        assert len(replay.for_timeframe(tf).snapshots) == len(inputs.for_timeframe(tf).input_batch.frame)


def test_mtf_replay_can_limit_to_one_timeframe_and_latest_bars(tmp_path) -> None:
    inputs = _inputs()
    replay = VolatilityMTFReplayRunner(ParquetOHLCVStore(tmp_path)).replay(
        "ASELS",
        input_snapshot=inputs,
        timeframes=("2h",),
        max_bars=130,
    )
    assert replay.timeframes == ("2h",)
    assert len(replay.for_timeframe("2h").snapshots) == 130
    assert replay.for_timeframe("2h").latest is not None


def test_mtf_replay_rejects_invalid_max_bars(tmp_path) -> None:
    runner = VolatilityMTFReplayRunner(ParquetOHLCVStore(tmp_path))
    try:
        runner.replay("ASELS", input_snapshot=_inputs(), timeframes=("2h",), max_bars=0)
    except ValueError as error:
        assert "max_bars" in str(error)
    else:
        raise AssertionError("max_bars=0 must fail closed")


def test_mtf_replay_rejects_unsupported_timeframes(tmp_path) -> None:
    runner = VolatilityMTFReplayRunner(ParquetOHLCVStore(tmp_path))
    try:
        runner.replay("ASELS", input_snapshot=_inputs(), timeframes=("1h",))
    except ValueError as error:
        assert "unsupported volatility timeframe" in str(error)
    else:
        raise AssertionError("1h must remain outside the volatility contract")


def test_direction_lag_diagnostics_are_non_negative(tmp_path) -> None:
    replay = VolatilityMTFReplayRunner(ParquetOHLCVStore(tmp_path)).replay(
        "ASELS", input_snapshot=_inputs()
    )
    for record in direction_lag_records(replay):
        if record.candidate_lag_bars is not None:
            assert 0 <= record.candidate_lag_bars <= record.candidate_horizon_bars
        if record.confirmed_lag_bars is not None:
            assert 0 <= record.confirmed_lag_bars <= record.confirmation_horizon_bars


def test_direction_lag_reads_canonical_regime_not_coherence_state() -> None:
    timestamps = pd.date_range("2026-08-01 10:00", periods=4, freq="2h", tz=TZ)
    snapshots = (
        _snapshot(
            timestamps[0],
            VolatilityState.BALANCED,
            early=EarlyDirectionTransition.EARLY_UP,
            episode_started=True,
            episode_id=1,
        ),
        _snapshot(timestamps[1], VolatilityState.UP_CANDIDATE),
        _snapshot(timestamps[2], VolatilityState.UP_CONFIRMED),
        _snapshot(timestamps[3], VolatilityState.UP_CONFIRMED),
    )
    replay = VolatilityMTFReplay(
        symbol="ASELS",
        timeframes=("2h",),
        by_timeframe=MappingProxyType(
            {
                "2h": VolatilityTimeframeReplay(
                    symbol="ASELS",
                    timeframe="2h",
                    snapshots=snapshots,
                )
            }
        ),
    )

    records = direction_lag_records(replay)
    assert len(records) == 1
    record = records[0]
    assert record.early_index == 0
    assert record.candidate_index == 1
    assert record.confirmed_index == 2
    assert record.candidate_lag_bars == 1
    assert record.confirmed_lag_bars == 2
    assert record.outcome == "CONFIRMED"


def test_stale_candidate_is_not_credited_to_old_early_episode() -> None:
    timestamps = pd.date_range("2026-08-01 10:00", periods=70, freq="2h", tz=TZ)
    snapshots = []
    for i, timestamp in enumerate(timestamps):
        regime = VolatilityState.DOWN_CANDIDATE if i == 59 else VolatilityState.BALANCED
        snapshots.append(
            _snapshot(
                timestamp,
                regime,
                early=EarlyDirectionTransition.EARLY_DOWN if i == 0 else EarlyDirectionTransition.NONE,
                episode_started=i == 0,
                episode_id=1 if i == 0 else 0,
            )
        )
    replay = VolatilityMTFReplay(
        symbol="ASELS",
        timeframes=("2h",),
        by_timeframe=MappingProxyType(
            {
                "2h": VolatilityTimeframeReplay(
                    symbol="ASELS",
                    timeframe="2h",
                    snapshots=tuple(snapshots),
                )
            }
        ),
        profile="Dengeli",
    )

    record = direction_lag_records(replay)[0]
    assert record.candidate_index is None
    assert record.confirmed_index is None
    assert record.outcome == "EXPIRED"
    assert record.window_end_index == 12
    assert record.candidate_horizon_bars == 8
    assert record.confirmation_horizon_bars == 12


def test_next_episode_bounds_previous_lag_matching() -> None:
    timestamps = pd.date_range("2026-08-01 10:00", periods=8, freq="2h", tz=TZ)
    snapshots = (
        _snapshot(
            timestamps[0],
            VolatilityState.BALANCED,
            early=EarlyDirectionTransition.EARLY_UP,
            episode_started=True,
            episode_id=1,
        ),
        _snapshot(timestamps[1], VolatilityState.BALANCED),
        _snapshot(
            timestamps[2],
            VolatilityState.BALANCED,
            early=EarlyDirectionTransition.EARLY_DOWN,
            episode_started=True,
            episode_id=2,
        ),
        _snapshot(timestamps[3], VolatilityState.UP_CANDIDATE),
        _snapshot(timestamps[4], VolatilityState.UP_CONFIRMED),
        _snapshot(timestamps[5], VolatilityState.DOWN_CANDIDATE),
        _snapshot(timestamps[6], VolatilityState.DOWN_CONFIRMED),
        _snapshot(timestamps[7], VolatilityState.DOWN_CONFIRMED),
    )
    replay = VolatilityMTFReplay(
        symbol="ASELS",
        timeframes=("2h",),
        by_timeframe=MappingProxyType(
            {"2h": VolatilityTimeframeReplay("ASELS", "2h", snapshots)}
        ),
    )

    records = direction_lag_records(replay)
    assert len(records) == 2
    assert records[0].candidate_index is None
    assert records[0].confirmed_index is None
    assert records[0].outcome == "SUPERSEDED"
    assert records[0].window_end_index == 1
    assert records[1].candidate_index == 5
    assert records[1].confirmed_index == 6
    assert records[1].outcome == "CONFIRMED"
