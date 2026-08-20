from __future__ import annotations

import math

import pandas as pd

from financial_dashboard.engines import RawIndicatorDashboardEngine
from financial_dashboard.engines.raw_indicator_dashboard import RawDataQuality, RawIndicatorConfig, VolumeQuality

TZ = "Europe/Istanbul"


def _frame(count: int = 140) -> pd.DataFrame:
    rows = []
    for i in range(count):
        base = 100.0 + i * 0.12 + math.sin(i / 4.0) * 1.5
        close = base + math.sin(i / 3.0) * 0.35
        open_ = close - math.sin(i / 5.0) * 0.25
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-02 10:00", tz=TZ) + pd.Timedelta(hours=i),
                "open": open_,
                "high": max(open_, close) + 0.8,
                "low": min(open_, close) - 0.7,
                "close": close,
                "volume": 1000.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return pd.DataFrame(rows)


def test_limited_volume_does_not_scale_raw_flow_evidence() -> None:
    frame = _frame()
    # Constant volume keeps coverage calculable while variation is insufficient,
    # therefore volume trust is reduced but raw CMF/OBV evidence remains source-faithful.
    result = RawIndicatorDashboardEngine().replay(frame)[-1]

    assert result.volume_quality == VolumeQuality.LIMITED
    assert result.volume_calculable is True
    assert result.volume_reliable is False
    assert result.volume_trust == 0.0
    assert result.indicators["CMF"].valid is True
    assert result.indicators["OBV"].valid is True
    assert result.indicators["CMF"].evidence is not None
    assert result.indicators["OBV"].evidence is not None


def test_batch_replay_freezes_open_and_source_gap_rows_without_advancing_state() -> None:
    confirmed = _frame(120)
    rows = confirmed.to_dict("records")

    open_row = dict(rows[70])
    open_row["timestamp"] = open_row["timestamp"] + pd.Timedelta(minutes=10)
    open_row["close"] = float(open_row["close"]) * 1.50
    open_row["high"] = max(float(open_row["high"]), float(open_row["close"]))
    open_row["is_closed"] = False

    gap_row = dict(rows[90])
    gap_row["timestamp"] = gap_row["timestamp"] + pd.Timedelta(minutes=10)
    gap_row["close"] = float(gap_row["close"]) * 0.50
    gap_row["low"] = min(float(gap_row["low"]), float(gap_row["close"]))
    gap_row["is_complete"] = False

    mixed = pd.DataFrame(rows[:71] + [open_row] + rows[71:91] + [gap_row] + rows[91:])
    replay = RawIndicatorDashboardEngine()
    replay_results = replay.replay(mixed)

    incremental = RawIndicatorDashboardEngine()
    incremental_results = [incremental.update(row) for row in mixed.to_dict("records")]

    assert replay.snapshot == incremental.snapshot
    assert replay_results == incremental_results
    assert replay_results[71].data_quality == RawDataQuality.INCOMPLETE_BAR
    assert replay_results[92].data_quality == RawDataQuality.SOURCE_GAP


def test_public_engine_surface_points_to_tur1_engine() -> None:
    assert RawIndicatorDashboardEngine.__module__ == "financial_dashboard.engines.raw_indicator_dashboard"
    assert RawIndicatorConfig().limited_volume_evidence_weight == 0.50
