from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from financial_dashboard.engines.raw_indicator_dashboard import (
    RawDataQuality,
    RawIndicatorConfig,
    RawIndicatorDashboardEngine,
    TrendProfile,
    TrendReason,
    VolumeQuality,
    _calculate_trend,
    _effective_settings,
    _ema,
    _rma,
)

TZ = "Europe/Istanbul"


def _frame(count: int = 140) -> pd.DataFrame:
    rows = []
    for i in range(count):
        base = 100.0 + i * 0.12 + math.sin(i / 4.0) * 1.5
        close = base + math.sin(i / 3.0) * 0.35
        open_ = close - math.sin(i / 5.0) * 0.25
        high = max(open_, close) + 0.8 + (i % 4) * 0.03
        low = min(open_, close) - 0.7 - (i % 3) * 0.02
        volume = 1000.0 + (i % 11) * 73.0 + (i % 3) * 19.0
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-02 10:00", tz=TZ) + pd.Timedelta(hours=i),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return pd.DataFrame(rows)


def test_pine_style_ema_starts_at_first_finite_source() -> None:
    values = np.array([1.0, 2.0, 3.0, 4.0])
    result = _ema(values, 3)
    assert result.tolist() == pytest.approx([1.0, 1.5, 2.25, 3.125])


def test_pine_style_rma_seeds_with_sma_then_wilder_recursion() -> None:
    values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = _rma(values, 3)
    assert np.isnan(result[0]) and np.isnan(result[1])
    assert result[2] == pytest.approx(2.0)
    assert result[3] == pytest.approx(2.0 / 3.0 * 2.0 + 1.0 / 3.0 * 4.0)
    assert result[4] == pytest.approx(2.0 / 3.0 * result[3] + 1.0 / 3.0 * 5.0)


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (TrendProfile.XAG_30M, (6, 2, 66.0, 40.0, 65.0, 72.0, 24, 3.0)),
        (TrendProfile.XAG_1H, (6, 3, 66.0, 40.0, 65.0, 72.0, 24, 3.0)),
        (TrendProfile.XAG_2H, (5, 2, 60.0, 35.0, 65.0, 70.0, 20, 3.0)),
        (TrendProfile.XAG_4H, (5, 2, 60.0, 35.0, 70.0, 72.0, 20, 3.0)),
        (TrendProfile.XAG_1D, (5, 2, 60.0, 30.0, 70.0, 75.0, 20, 3.5)),
    ],
)
def test_xag_profile_table_matches_source(profile: TrendProfile, expected: tuple) -> None:
    settings = _effective_settings(RawIndicatorConfig(profile=profile))
    assert (
        settings.lookback,
        settings.recent_lookback,
        settings.minimum_consistency,
        settings.step_dead_zone_percent,
        settings.hysteresis_hold_percent,
        settings.spike_dominance,
        settings.dynamic_threshold_length,
        settings.dynamic_step_cap_multiplier,
    ) == expected


def test_manual_profile_preserves_manual_trend_settings() -> None:
    cfg = RawIndicatorConfig(
        profile=TrendProfile.MANUAL,
        trend_lookback=7,
        recent_lookback=4,
        minimum_consistency=75.0,
        step_dead_zone_percent=45.0,
        hysteresis_hold_percent=80.0,
        spike_dominance=77.0,
        dynamic_threshold_length=31,
        dynamic_step_cap_multiplier=4.5,
    )
    settings = _effective_settings(cfg)
    assert settings.lookback == 7
    assert settings.recent_lookback == 4
    assert settings.minimum_consistency == 75.0
    assert settings.step_dead_zone_percent == 45.0
    assert settings.hysteresis_hold_percent == 80.0
    assert settings.spike_dominance == 77.0
    assert settings.dynamic_threshold_length == 31
    assert settings.dynamic_step_cap_multiplier == 4.5


def test_stateful_trend_confirms_clean_rise() -> None:
    cfg = RawIndicatorConfig(profile=TrendProfile.MANUAL, trend_lookback=5, minimum_consistency=60.0)
    settings = _effective_settings(cfg)
    source = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    result = _calculate_trend(source, 5, 2.0, settings, previous_direction=0, filter_spikes=True)
    assert result.direction == 1
    assert result.pending == 0
    assert result.reason == TrendReason.CONFIRMED
    assert result.consistency == pytest.approx(100.0)


def test_stateful_trend_marks_dominant_single_step_pending() -> None:
    cfg = RawIndicatorConfig(profile=TrendProfile.MANUAL, trend_lookback=5, minimum_consistency=60.0, spike_dominance=70.0)
    settings = _effective_settings(cfg)
    source = np.array([0.0, 0.0, 0.0, 0.0, 0.1, 5.0])
    result = _calculate_trend(source, 5, 2.0, settings, previous_direction=0, filter_spikes=True)
    assert result.direction == 0
    assert result.pending == 1
    assert result.reason == TrendReason.SPIKE_PENDING


def test_volume_quality_becomes_adequate_with_usable_varying_volume() -> None:
    result = RawIndicatorDashboardEngine().replay(_frame())[-1]
    assert result.volume_quality == VolumeQuality.ADEQUATE
    assert result.volume_calculable is True
    assert result.volume_reliable is True
    assert result.volume_trust == pytest.approx(1.0)


def test_missing_volume_never_fabricates_flow_evidence() -> None:
    frame = _frame()
    frame["volume"] = np.nan
    result = RawIndicatorDashboardEngine().replay(frame)[-1]
    assert result.volume_quality == VolumeQuality.MISSING
    assert result.volume_calculable is False
    assert not result.indicators["CMF"].valid
    assert result.indicators["CMF"].evidence is None
    assert not result.indicators["OBV"].valid
    assert result.indicators["OBV"].evidence is None


def test_raw_indicator_snapshot_contains_all_tur1_domains() -> None:
    result = RawIndicatorDashboardEngine().replay(_frame())[-1]
    assert result.data_quality == RawDataQuality.OK
    assert result.atr is not None and result.atr > 0.0
    assert result.atr_ratio is not None and result.atr_ratio > 0.0
    assert result.price_context_valid is True
    assert set(result.indicators) == {
        "CMF",
        "OBV",
        "CCI",
        "RSI",
        "MACD",
        "MOMENTUM",
        "STOCHASTIC",
        "STOCH_RSI",
        "SMI",
        "PRICE_CONTEXT",
    }
    assert result.indicators["MOMENTUM"].value is not None
    assert result.indicators["RSI"].value is not None
    assert 0.0 <= result.indicators["RSI"].value <= 100.0


def test_timing_evidence_is_relative_to_065_capacity() -> None:
    result = RawIndicatorDashboardEngine().replay(_frame())[-1]
    for name in ("STOCHASTIC", "STOCH_RSI"):
        evidence = result.indicators[name]
        if evidence.evidence is not None:
            assert abs(evidence.evidence) <= 0.65 + 1e-12
            assert abs(evidence.relative_evidence or 0.0) <= 1.0 + 1e-12


def test_momentum_raw_formula_is_close_minus_close_n() -> None:
    frame = _frame()
    result = RawIndicatorDashboardEngine(RawIndicatorConfig(momentum_length=10)).replay(frame)[-1]
    expected = frame.iloc[-1]["close"] - frame.iloc[-11]["close"]
    assert result.indicators["MOMENTUM"].value == pytest.approx(expected)


def test_open_bar_does_not_mutate_confirmed_snapshot() -> None:
    frame = _frame()
    engine = RawIndicatorDashboardEngine()
    confirmed = engine.replay(frame)[-1]
    open_row = frame.iloc[-1].to_dict()
    open_row["timestamp"] = open_row["timestamp"] + pd.Timedelta(hours=1)
    open_row["close"] = float(open_row["close"]) * 1.25
    open_row["high"] = max(float(open_row["high"]), float(open_row["close"]))
    open_row["is_closed"] = False

    observed = engine.update(open_row)

    assert observed.data_quality == RawDataQuality.INCOMPLETE_BAR
    assert observed.timestamp == confirmed.timestamp
    assert observed.indicators == confirmed.indicators
    assert engine.snapshot == confirmed


def test_source_gap_does_not_mutate_confirmed_snapshot() -> None:
    frame = _frame()
    engine = RawIndicatorDashboardEngine()
    confirmed = engine.replay(frame)[-1]
    gap_row = frame.iloc[-1].to_dict()
    gap_row["timestamp"] = gap_row["timestamp"] + pd.Timedelta(hours=1)
    gap_row["is_complete"] = False

    observed = engine.update(gap_row)

    assert observed.data_quality == RawDataQuality.SOURCE_GAP
    assert observed.timestamp == confirmed.timestamp
    assert observed.indicators == confirmed.indicators
    assert engine.snapshot == confirmed


def test_replay_equals_incremental_final_snapshot() -> None:
    frame = _frame(120)
    replay_final = RawIndicatorDashboardEngine().replay(frame)[-1]

    incremental = RawIndicatorDashboardEngine()
    for row in frame.to_dict("records"):
        incremental.update(row)

    assert incremental.snapshot == replay_final


def test_future_tail_cannot_change_historical_snapshot() -> None:
    frame = _frame(140)
    cutoff = 110
    prefix_result = RawIndicatorDashboardEngine().replay(frame.iloc[:cutoff])[-1]
    full_results = RawIndicatorDashboardEngine().replay(frame)

    assert full_results[cutoff - 1] == prefix_result


def test_warmup_is_explicit_not_fake_zero_evidence() -> None:
    result = RawIndicatorDashboardEngine().replay(_frame(8))[-1]
    assert result.data_quality == RawDataQuality.WARMUP
    assert not result.indicators["RSI"].valid
    assert result.indicators["RSI"].evidence is None
