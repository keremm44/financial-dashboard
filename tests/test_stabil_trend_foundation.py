from __future__ import annotations

import math

import pandas as pd
import pytest

from financial_dashboard.engines import (
    H4Lifecycle,
    H4TrendState,
    StabilTrendConfig,
    StabilTrendEngine,
    WeeklyTrendState,
)
from financial_dashboard.engines.stabil_trend_engine import _confirmed_pivots
from financial_dashboard.engines.stabil_trend_runtime import _daily_snapshot_runtime, _h4_snapshot_runtime


TZ = "Europe/Istanbul"


def _frame(n: int, freq: str, *, start: str, base: float = 100.0, step: float = 0.15, volume: float = 1000.0) -> pd.DataFrame:
    rows = []
    price = base
    times = pd.date_range(start, periods=n, freq=freq, tz=TZ)
    for i, ts in enumerate(times):
        wave = math.sin(i * math.pi / 3.0) * 1.6
        center = base + step * i + wave
        o = price
        c = center
        h = max(o, c) + 0.65
        l = min(o, c) - 0.65
        rows.append({"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": volume, "is_closed": True, "is_complete": True})
        price = c
    return pd.DataFrame(rows)


def _daily_up_structure(n: int = 50) -> pd.DataFrame:
    rows = []
    times = pd.date_range("2026-01-01", periods=n, freq="1D", tz=TZ)
    for i, ts in enumerate(times):
        # Six-bar rising wave: with pivot span=2 it creates unique alternating
        # highs/lows every six bars, while each same-side pivot is >4 bars apart.
        c = 100.0 + 0.22 * i + 1.8 * math.sin(2.0 * math.pi * i / 6.0)
        rows.append({
            "timestamp": ts,
            "open": c - 0.05,
            "high": c + 0.35,
            "low": c - 0.35,
            "close": c,
            "volume": 1000.0,
            "is_closed": True,
            "is_complete": True,
        })
    return pd.DataFrame(rows)


def _quiet_h4(n: int = 55) -> pd.DataFrame:
    rows = []
    times = pd.date_range("2026-05-01", periods=n, freq="4h", tz=TZ)
    price = 100.0
    for i, ts in enumerate(times):
        o = price
        c = o + (0.05 if i % 2 == 0 else -0.04)
        rows.append({
            "timestamp": ts,
            "open": o,
            "high": max(o, c) + 0.30,
            "low": min(o, c) - 0.30,
            "close": c,
            "volume": 1000.0,
            "is_closed": True,
            "is_complete": True,
        })
        price = c
    return pd.DataFrame(rows)


def _small_cfg() -> StabilTrendConfig:
    return StabilTrendConfig(
        weekly_pivot_len=2,
        daily_pivot_len=2,
        weekly_ema_len=5,
        daily_ema_len=5,
        slope_lookback=2,
        acceptance_len=3,
        pullback_lookback=8,
        healthy_depth_atr=3.2,
        deep_depth_atr=5.0,
        max_pullback_bars=8,
        h4_fast_ema_len=5,
        h4_micro_pivot_len=2,
        displacement_factor=1.20,
        h4_evidence_fresh_bars=3,
    )


def test_defaults_match_supplied_pine_inputs() -> None:
    cfg = StabilTrendConfig()
    assert cfg.weekly_pivot_len == 3
    assert cfg.daily_pivot_len == 4
    assert cfg.support_atr_tolerance == pytest.approx(0.35)
    assert cfg.weekly_ema_len == 30
    assert cfg.daily_ema_len == 34
    assert cfg.slope_lookback == 5
    assert cfg.acceptance_len == 8
    assert cfg.pullback_lookback == 24
    assert cfg.healthy_depth_atr == pytest.approx(3.2)
    assert cfg.deep_depth_atr == pytest.approx(5.0)
    assert cfg.max_pullback_bars == 16
    assert cfg.h4_fast_ema_len == 13
    assert cfg.h4_micro_pivot_len == 2
    assert cfg.displacement_factor == pytest.approx(1.35)
    assert cfg.h4_evidence_fresh_bars == 6


def test_pivot_is_unknown_until_right_span_has_closed() -> None:
    frame = pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-01-01", tz=TZ) + pd.Timedelta(days=i), "open": 10.0, "high": h, "low": 8.0, "close": 9.0, "volume": 1000.0, "is_closed": True, "is_complete": True}
        for i, h in enumerate([10.0, 11.0, 15.0, 12.0, 11.0, 10.0])
    ])
    atr = [1.0] * len(frame)
    prefix_highs, _ = _confirmed_pivots(frame.iloc[:4].reset_index(drop=True), 2, atr[:4])
    assert prefix_highs == []
    full_highs, _ = _confirmed_pivots(frame, 2, atr)
    pivot = full_highs[0]
    assert pivot.origin_index == 2
    assert pivot.known_index == 4
    assert pivot.known_time > pivot.origin_time


def test_daily_pullback_origin_and_reference_atr_freeze_after_start() -> None:
    cfg = _small_cfg()
    daily = _daily_up_structure()

    # At 44 the latest confirmed sequence is H32-L34-H38-L40 and therefore
    # already satisfies order, alternation, spacing, excursion and freshness.
    pre = _daily_snapshot_runtime(daily.iloc[:45].reset_index(drop=True), WeeklyTrendState.UP_STABLE, cfg)
    assert pre.data_ready
    assert pre.structure_usable
    assert pre.structure_quality

    # Start a moderate pullback after the last local high. Keep it safely above
    # the frozen structural support floor so only pullback depth evolves.
    anchor = float(daily.loc[44, "close"])
    for i, drop in zip(range(45, 49), [1.00, 1.25, 1.45, 1.60]):
        c = anchor - drop
        daily.loc[i, ["open", "high", "low", "close", "volume"]] = [c + 0.08, c + 0.30, c - 0.30, c, 900.0]

    snap_1 = _daily_snapshot_runtime(daily.iloc[:46].reset_index(drop=True), WeeklyTrendState.UP_STABLE, cfg)
    snap_2 = _daily_snapshot_runtime(daily.iloc[:49].reset_index(drop=True), WeeklyTrendState.UP_STABLE, cfg)
    assert snap_1.pullback_start_index is not None
    assert snap_1.support_held
    assert snap_2.support_held
    assert snap_2.pullback_start_index == snap_1.pullback_start_index
    assert snap_2.pullback_origin_high == pytest.approx(snap_1.pullback_origin_high)
    assert snap_2.pullback_reference_atr == pytest.approx(snap_1.pullback_reference_atr)
    assert snap_2.pullback_bars > snap_1.pullback_bars


def test_h4_displacement_freezes_event_reference_and_can_fail_only_later() -> None:
    cfg = _small_cfg()
    h4 = _quiet_h4()
    i = 50
    prev = float(h4.loc[i - 1, "close"])
    h4.loc[i, ["open", "high", "low", "close", "volume"]] = [prev, prev + 4.4, prev - 0.2, prev + 4.1, 2500.0]
    candidate = _h4_snapshot_runtime(h4.iloc[: i + 1].reset_index(drop=True), cfg)
    assert candidate.lifecycle == H4Lifecycle.DISPLACEMENT_ACTIVE
    assert candidate.event_index == i
    frozen_low = candidate.event_low
    frozen_mid = candidate.event_mid
    assert frozen_low is not None and frozen_mid is not None

    j = i + 1
    h4.loc[j, ["open", "high", "low", "close", "volume"]] = [frozen_low + 0.3, frozen_low + 0.5, frozen_low - 1.0, frozen_low - 0.6, 1200.0]
    failed = _h4_snapshot_runtime(h4.iloc[: j + 1].reset_index(drop=True), cfg)
    assert failed.lifecycle == H4Lifecycle.RECOVERY_FAILED
    assert failed.state == H4TrendState.RECOVERY_FAILED
    assert failed.event_low == pytest.approx(frozen_low)
    assert failed.event_mid == pytest.approx(frozen_mid)
    assert failed.invalidation_index == j


def test_as_of_future_tail_and_open_bar_cannot_change_mtf_snapshot() -> None:
    cfg = _small_cfg()
    weekly = _frame(45, "7D", start="2025-01-01", step=0.35)
    daily = _frame(55, "1D", start="2026-01-01", step=0.18)
    h4 = _frame(75, "4h", start="2026-02-01", step=0.05)
    as_of = min(weekly.iloc[-6].timestamp, daily.iloc[-6].timestamp, h4.iloc[-6].timestamp)

    base_engine = StabilTrendEngine(cfg)
    base = base_engine.analyze(weekly, daily, h4, as_of=as_of)

    weekly_future = weekly.copy()
    daily_future = daily.copy()
    h4_future = h4.copy()
    for frame in (weekly_future, daily_future, h4_future):
        mask = frame["timestamp"] > as_of
        frame.loc[mask, "high"] = 10000.0
        frame.loc[mask, "low"] = 1.0
        frame.loc[mask, "close"] = 9999.0
        frame.loc[mask, "volume"] = 1_000_000_000.0

    future_engine = StabilTrendEngine(cfg)
    future = future_engine.analyze(weekly_future, daily_future, h4_future, as_of=as_of)
    assert future == base

    open_h4 = pd.concat([h4, pd.DataFrame([{
        "timestamp": as_of,
        "open": 100.0,
        "high": 100000.0,
        "low": 0.1,
        "close": 99999.0,
        "volume": 9_999_999_999.0,
        "is_closed": False,
        "is_complete": False,
    }])], ignore_index=True)
    open_engine = StabilTrendEngine(cfg)
    with_open = open_engine.analyze(weekly, daily, open_h4, as_of=as_of)
    assert with_open == base
