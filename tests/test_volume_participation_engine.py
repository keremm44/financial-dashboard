from __future__ import annotations

import math

import pandas as pd
import pytest

from financial_dashboard.engines import ParticipationState, VolumeParticipationConfig, VolumeParticipationEngine
from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.volume_participation_engine import VolumeParticipationEngine as CoreVolumeParticipationEngine


def _bar(i: int, *, o: float, h: float, l: float, c: float, v: float, closed: bool = True, complete: bool = True) -> dict:
    return {
        "timestamp": pd.Timestamp("2026-01-01", tz="Europe/Istanbul") + pd.Timedelta(hours=i),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "is_closed": closed,
        "is_complete": complete,
    }


def _steady_frame(n: int = 180, *, volume: float = 1000.0) -> pd.DataFrame:
    rows = []
    price = 100.0
    for i in range(n):
        drift = 0.08 * math.sin(i / 4.0)
        o = price
        c = price + drift
        rows.append(_bar(i, o=o, h=max(o, c) + 0.45, l=min(o, c) - 0.45, c=c, v=volume))
        price = c
    return pd.DataFrame(rows)


def _flat_frame(n: int = 35, *, volume: float = 1000.0) -> pd.DataFrame:
    return pd.DataFrame([_bar(i, o=100.0, h=100.5, l=99.5, c=100.0, v=volume) for i in range(n)])


def _candidate_config() -> VolumeParticipationConfig:
    return VolumeParticipationConfig(
        minimum_history=30,
        percentile_length=20,
        volume_long_length=20,
        volume_average_length=10,
        minimum_nonzero_volume_share=0.70,
        rising_rvol=0.80,
        high_rvol=1.10,
        abnormal_rvol=1.50,
        rising_rtv=0.80,
        high_rtv=1.10,
        abnormal_rtv=1.50,
        minimum_volume_slope=-1.0,
        minimum_capital_slope=-1.0,
        minimum_capital_pressure=0.0,
        minimum_directional_share=0.10,
        minimum_progress_atr=0.01,
        minimum_efficiency=0.01,
        minimum_body_atr=0.0,
        up_close_location=0.50,
        down_close_location=0.25,
        maximum_directional_wick_ratio=1.0,
        minimum_directional_close_share=0.20,
        participation_minimum_evidence=5,
        participation_confirmation_bars=2,
        confirmation_minimum_rvol=0.0,
        confirmation_minimum_rtv=0.0,
    )


def test_balanced_defaults_match_pine_thresholds() -> None:
    cfg = VolumeParticipationConfig()
    assert cfg.minimum_history == 150
    assert cfg.minimum_nonzero_volume_share == pytest.approx(0.70)
    assert cfg.rising_rvol == pytest.approx(1.20)
    assert cfg.high_rvol == pytest.approx(1.50)
    assert cfg.abnormal_rvol == pytest.approx(2.25)
    assert cfg.rising_rtv == pytest.approx(1.16)
    assert cfg.high_rtv == pytest.approx(1.48)
    assert cfg.abnormal_rtv == pytest.approx(2.20)
    assert cfg.minimum_directional_share == pytest.approx(0.60)
    assert cfg.minimum_capital_pressure == pytest.approx(0.13)
    assert cfg.minimum_progress_atr == pytest.approx(0.45)
    assert cfg.minimum_efficiency == pytest.approx(0.52)
    assert cfg.participation_minimum_evidence == 6
    assert cfg.participation_confirmation_bars == 2


def test_default_engine_waits_for_pine_minimum_history() -> None:
    engine = VolumeParticipationEngine()
    result = engine.replay(_steady_frame(149))[-1]
    assert result is not None
    assert result.state in {ParticipationState.PENDING.value, ParticipationState.VOLUME_UNAVAILABLE.value}
    assert result.direction == Direction.NEUTRAL
    assert engine.export_contract.rvol is None


def test_rvol_and_rtv_use_current_value_over_rolling_average() -> None:
    cfg = VolumeParticipationConfig(minimum_history=30, percentile_length=20, volume_long_length=20, volume_average_length=10)
    frame = _steady_frame(40, volume=1000.0)
    frame.loc[39, "volume"] = 2000.0
    frame.loc[39, "high"] = 102.0
    frame.loc[39, "low"] = 100.0
    frame.loc[39, "close"] = 101.0
    engine = VolumeParticipationEngine(cfg)
    engine.replay(frame)
    export = engine.export_contract

    expected_rvol = 2000.0 / ((9 * 1000.0 + 2000.0) / 10.0)
    assert export.rvol == pytest.approx(expected_rvol, rel=1e-12)

    traded = frame["volume"] * ((frame["high"] + frame["low"] + frame["close"]) / 3.0)
    expected_rtv = float(traded.iloc[-1] / traded.iloc[-10:].mean())
    assert export.relative_traded_value == pytest.approx(expected_rtv, rel=1e-12)


def test_effort_result_uses_same_three_bar_window() -> None:
    cfg = VolumeParticipationConfig(minimum_history=30, percentile_length=20, volume_long_length=20, volume_average_length=10)
    frame = _steady_frame(40, volume=1000.0)
    frame.loc[37, ["open", "high", "low", "close", "volume"]] = [100.0, 103.0, 99.0, 102.0, 2500.0]
    frame.loc[38, ["open", "high", "low", "close", "volume"]] = [102.0, 103.0, 99.5, 100.5, 2500.0]
    frame.loc[39, ["open", "high", "low", "close", "volume"]] = [100.5, 102.0, 99.5, 100.1, 2500.0]
    engine = VolumeParticipationEngine(cfg)
    engine.replay(frame)
    export = engine.export_contract

    assert export.net_progress_atr is not None
    assert export.directional_efficiency is not None
    assert export.volume_result_efficiency is not None
    assert abs(export.net_progress_atr) < 1.0
    assert export.directional_efficiency < 0.5
    assert export.volume_result_efficiency < 1.0


def test_candidate_is_neutral_and_only_next_candidate_bar_can_confirm() -> None:
    cfg = _candidate_config()
    engine = CoreVolumeParticipationEngine(cfg)
    engine.replay(_flat_frame())

    first = engine.update(_bar(35, o=100.0, h=102.5, l=99.9, c=102.3, v=2400.0))
    assert first is not None
    assert first.state == ParticipationState.UP_CANDIDATE.value
    assert first.direction == Direction.NEUTRAL

    second = engine.update(_bar(36, o=102.3, h=104.8, l=102.2, c=104.6, v=2500.0))
    assert second is not None
    assert second.state == ParticipationState.UP_CONFIRMED.value
    assert second.direction == Direction.UP


def test_zero_volume_never_invents_directional_participation() -> None:
    engine = VolumeParticipationEngine()
    result = engine.replay(_steady_frame(180, volume=0.0))[-1]
    assert result is not None
    assert result.state == ParticipationState.VOLUME_UNAVAILABLE.value
    assert result.direction == Direction.NEUTRAL
    assert engine.export_contract.rvol is None


def test_open_and_incomplete_bars_cannot_mutate_confirmed_state() -> None:
    engine = VolumeParticipationEngine()
    engine.replay(_steady_frame(180))
    before = engine.snapshot()
    before_export = engine.export_contract

    assert engine.update(_bar(180, o=100, h=150, l=99, c=149, v=50000, closed=False)) == before
    assert engine.export_contract == before_export
    assert engine.update(_bar(181, o=100, h=150, l=99, c=149, v=50000, complete=False)) == before
    assert engine.export_contract == before_export


def test_replay_matches_incremental_and_future_tail_cannot_rewrite_prefix() -> None:
    cfg = VolumeParticipationConfig(minimum_history=30, percentile_length=20, volume_long_length=20, volume_average_length=10)
    frame = _steady_frame(70)

    replay_engine = VolumeParticipationEngine(cfg)
    replay_results = replay_engine.replay(frame)
    incremental = VolumeParticipationEngine(cfg)
    incremental_results = [incremental.update(row) for _, row in frame.iterrows()]
    assert replay_results == incremental_results
    assert replay_engine.export_contract == incremental.export_contract

    prefix = frame.iloc[:52].copy()
    prefix_engine = VolumeParticipationEngine(cfg)
    prefix_results = prefix_engine.replay(prefix)
    full_engine = VolumeParticipationEngine(cfg)
    full_results = full_engine.replay(frame)
    assert full_results[: len(prefix_results)] == prefix_results


def test_missing_volume_is_rejected() -> None:
    engine = VolumeParticipationEngine(VolumeParticipationConfig(minimum_history=1, percentile_length=1, volume_long_length=1, volume_average_length=1))
    with pytest.raises(ValueError, match="complete OHLCV"):
        engine.update({"open": 1, "high": 2, "low": 0, "close": 1})