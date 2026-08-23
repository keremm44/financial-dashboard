from __future__ import annotations

import math

import pandas as pd
import pytest

import financial_dashboard.engines.volume_participation_engine as volume_module
from financial_dashboard.engines.volume_participation_engine import (
    VolumeParticipationConfig,
    VolumeParticipationEngine,
    _ema_series,
    _rma,
    _rolling_relative,
)
from financial_dashboard.engines.volume_participation_lifecycle import (
    VolumeParticipationEngine as LifecycleVolumeParticipationEngine,
)


def _frame(rows: int = 220) -> pd.DataFrame:
    data = []
    price = 100.0
    for index in range(rows):
        drift = math.sin(index / 5.0) * 0.18 + math.cos(index / 13.0) * 0.07
        open_ = price
        close = price + drift
        high = max(open_, close) + 0.35 + (index % 4) * 0.02
        low = min(open_, close) - 0.31 - (index % 3) * 0.015
        volume = 900.0 + (index % 17) * 43.0 + (150.0 if index % 29 == 0 else 0.0)
        data.append(
            {
                "timestamp": pd.Timestamp("2026-01-01", tz="Europe/Istanbul")
                + pd.Timedelta(minutes=30 * index),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "is_closed": True,
                "is_complete": True,
            }
        )
        price = close
    return pd.DataFrame(data)


def _assert_optional_series_close(
    actual: list[float | None], expected: list[float | None]
) -> None:
    assert len(actual) == len(expected)
    for left, right in zip(actual, expected, strict=True):
        if right is None:
            assert left is None
        else:
            assert left == pytest.approx(right, rel=1e-12, abs=1e-12)


def test_incremental_runtime_series_match_batch_reference_math() -> None:
    config = VolumeParticipationConfig()
    engine = VolumeParticipationEngine(config)
    frame = _frame()
    engine.replay(frame)

    expected_atr = _rma(engine._true_range_values, config.atr_length)
    expected_volume_ema = _ema_series(engine._volume_values, config.volume_short_length)
    expected_capital_ema = _ema_series(engine._traded_values, config.volume_short_length)
    expected_rvol = _rolling_relative(engine._volume_values, config.volume_average_length)
    expected_rtv = _rolling_relative(engine._traded_values, config.volume_average_length)

    _assert_optional_series_close(engine._atr_values, expected_atr)
    assert engine._volume_ema_values == pytest.approx(expected_volume_ema, rel=1e-12, abs=1e-12)
    assert engine._capital_ema_values == pytest.approx(expected_capital_ema, rel=1e-12, abs=1e-12)
    _assert_optional_series_close(engine._rvol_values, expected_rvol)
    _assert_optional_series_close(engine._rtv_values, expected_rtv)


def test_runtime_replay_does_not_call_full_history_reference_helpers(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("full-history batch helper entered runtime hot path")

    monkeypatch.setattr(volume_module, "_rma", forbidden)
    monkeypatch.setattr(volume_module, "_ema_series", forbidden)
    monkeypatch.setattr(volume_module, "_rolling_relative", forbidden)

    engine = VolumeParticipationEngine()
    results = engine.replay(_frame())

    assert len(results) == 220
    assert engine.export_contract.state is not None


def test_lifecycle_reuses_core_incremental_atr_series() -> None:
    engine = LifecycleVolumeParticipationEngine()
    engine.replay(_frame())

    assert engine._atr_series() is engine._atr_values
    assert len(engine._atr_values) == len(engine._rows)
