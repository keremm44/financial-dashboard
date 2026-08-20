from __future__ import annotations

import math

import numpy as np
import pandas as pd

from financial_dashboard.engines.raw_indicator_dashboard import (
    RawIndicatorConfig,
    RawIndicatorDashboardEngine,
    TrendProfile,
    TrendReason,
    _calculate_trend,
    _dynamic_threshold,
    _effective_settings,
)

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
                "volume": 1000.0 + (i % 9) * 71.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return pd.DataFrame(rows)


def test_invalid_macd_configuration_never_emits_macd_evidence() -> None:
    cfg = RawIndicatorConfig(macd_fast_length=30, macd_slow_length=10)
    result = RawIndicatorDashboardEngine(cfg).replay(_frame())[-1]

    macd = result.indicators["MACD"]
    assert macd.valid is False
    assert macd.direction == 0
    assert macd.pending_direction == 0
    assert macd.reason == TrendReason.DATA_WAIT
    assert macd.evidence is None
    assert macd.relative_evidence is None


def test_flat_oscillator_ranges_do_not_fabricate_timing_or_smi_evidence() -> None:
    frame = _frame()
    frame["open"] = 100.0
    frame["high"] = 100.0
    frame["low"] = 100.0
    frame["close"] = 100.0

    result = RawIndicatorDashboardEngine().replay(frame)[-1]

    for name in ("STOCHASTIC", "STOCH_RSI", "SMI"):
        evidence = result.indicators[name]
        assert evidence.value is None
        assert evidence.valid is False
        assert evidence.evidence is None
        assert evidence.relative_evidence is None


def test_dynamic_threshold_caps_one_bar_outlier_before_robust_ema() -> None:
    source = np.array([float(i) for i in range(30)], dtype=float)
    source[-1] = 200.0

    capped = _dynamic_threshold(
        source,
        average_length=10,
        lookback=5,
        multiplier=0.35,
        cap_multiplier=3.0,
        cumulative=False,
    )
    effectively_uncapped = _dynamic_threshold(
        source,
        average_length=10,
        lookback=5,
        multiplier=0.35,
        cap_multiplier=1000.0,
        cumulative=False,
    )

    assert np.isfinite(capped[-1])
    assert np.isfinite(effectively_uncapped[-1])
    assert capped[-1] < effectively_uncapped[-1]


def test_hysteresis_holds_existing_direction_below_full_confirmation_threshold() -> None:
    cfg = RawIndicatorConfig(
        profile=TrendProfile.MANUAL,
        trend_lookback=5,
        recent_lookback=2,
        minimum_consistency=60.0,
        hysteresis_hold_percent=65.0,
        use_spike_filter=False,
    )
    settings = _effective_settings(cfg)
    source = np.array([0.0, 0.8, 1.6, 2.4, 3.2, 3.4], dtype=float)

    result = _calculate_trend(
        source,
        index=5,
        movement_threshold=4.0,
        settings=settings,
        previous_direction=1,
        filter_spikes=False,
    )

    assert result.direction == 1
    assert result.pending == 0
    assert result.reason == TrendReason.DIRECTION_HELD


def test_confirmed_opposite_structure_replaces_previous_direction() -> None:
    cfg = RawIndicatorConfig(
        profile=TrendProfile.MANUAL,
        trend_lookback=5,
        recent_lookback=2,
        minimum_consistency=60.0,
        use_spike_filter=False,
    )
    settings = _effective_settings(cfg)
    source = np.array([5.0, 4.0, 3.0, 2.0, 1.0, 0.0], dtype=float)

    result = _calculate_trend(
        source,
        index=5,
        movement_threshold=2.0,
        settings=settings,
        previous_direction=1,
        filter_spikes=False,
    )

    assert result.direction == -1
    assert result.pending == 0
    assert result.reason == TrendReason.CONFIRMED
