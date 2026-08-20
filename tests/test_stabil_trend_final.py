from __future__ import annotations

import math

import pandas as pd
import pytest

from financial_dashboard.engines import (
    DailyRawState,
    DailyTrendSnapshot,
    DailyTrendState,
    GapState,
    H4EvidenceStatus,
    H4TrendSnapshot,
    H4TrendState,
    StabilMainState,
    StabilReason,
    StabilTrendConfig,
    StabilTrendContext,
    StabilTrendEngine,
    WeeklyTrendSnapshot,
    WeeklyTrendState,
)
from financial_dashboard.engines.stabil_trend_final import EXPORT_STATE_CODE, _main_state, _reason, _stabilize
from financial_dashboard.engines.stabil_trend_public import _advanced


TZ = "Europe/Istanbul"


def _ctx(
    *,
    weekly_state: WeeklyTrendState = WeeklyTrendState.UP_STABLE,
    daily_state: DailyTrendState = DailyTrendState.HEALTHY_ADVANCE,
    daily_raw: DailyRawState = DailyRawState.ADVANCE,
    h4_state: H4TrendState = H4TrendState.NO_RECOVERY,
    evidence: H4EvidenceStatus = H4EvidenceStatus.NONE,
    gap: GapState = GapState.NONE,
    w_ready: bool = True,
    d_ready: bool = True,
    h_ready: bool = True,
    w_quality: bool = True,
    d_quality: bool = True,
) -> StabilTrendContext:
    ts = pd.Timestamp("2026-08-20 16:00", tz=TZ)
    weekly = WeeklyTrendSnapshot(
        timestamp=ts,
        data_ready=w_ready,
        structure_usable=w_ready,
        structure_quality=w_quality,
        support_held=True,
        state=weekly_state,
        slope_atr=0.20,
        acceptance=0.75,
        stretch_atr=1.5,
        higher_high=True,
        higher_low=True,
        support_age=3,
    )
    daily = DailyTrendSnapshot(
        timestamp=ts,
        data_ready=d_ready,
        structure_usable=d_ready,
        structure_quality=d_quality,
        support_fresh=True,
        support_held=True,
        volume_usable=True,
        raw_state=daily_raw,
        state=daily_state,
        gap_state=gap,
        depth_atr=0.4,
        pullback_bars=2,
        slope_atr=0.15,
        acceptance=0.75,
        down_body_atr=0.30,
        red_share=0.20,
        sell_volume_factor=0.80,
        expansion_count=0.0,
        range_compression=0.9,
        higher_high=True,
        higher_low=True,
    )
    h4 = H4TrendSnapshot(timestamp=ts, data_ready=h_ready, state=h4_state)
    return StabilTrendContext(ts, weekly, daily, h4, evidence)


def _frame(n: int, freq: str, start: str, *, step: float) -> pd.DataFrame:
    rows = []
    price = 100.0
    for i, ts in enumerate(pd.date_range(start, periods=n, freq=freq, tz=TZ)):
        center = 100.0 + i * step + math.sin(i * math.pi / 3.0) * 1.6
        o, c = price, center
        rows.append({
            "timestamp": ts,
            "open": o,
            "high": max(o, c) + 0.65,
            "low": min(o, c) - 0.65,
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
        max_pullback_bars=8,
        h4_fast_ema_len=5,
        h4_micro_pivot_len=2,
        displacement_factor=1.20,
        h4_evidence_fresh_bars=3,
    )


def test_main_resolver_priority_matches_pine_matrix() -> None:
    assert _main_state(_ctx(weekly_state=WeeklyTrendState.NOT_UP)) == StabilMainState.NOT_STABLE_UPTREND
    assert _main_state(_ctx(daily_state=DailyTrendState.STRUCTURE_BROKEN, daily_raw=DailyRawState.STRUCTURE_BROKEN)) == StabilMainState.NOT_STABLE_UPTREND
    assert _main_state(_ctx(daily_state=DailyTrendState.GAP_WATCH, daily_raw=DailyRawState.GAP_WATCH)) == StabilMainState.UPTREND_WEAKENING
    assert _main_state(_ctx(weekly_state=WeeklyTrendState.UP_PARABOLIC)) == StabilMainState.OVEREXTENDED
    assert _main_state(_ctx()) == StabilMainState.STABLE_UPTREND
    assert _main_state(_ctx(weekly_state=WeeklyTrendState.UP_WEAKENING)) == StabilMainState.HEALTHY_UPTREND
    assert _main_state(_ctx(daily_state=DailyTrendState.CONTROLLED_PULLBACK, daily_raw=DailyRawState.PULLBACK)) == StabilMainState.CONTROLLED_CORRECTION
    assert _main_state(_ctx(daily_state=DailyTrendState.CONTROLLED_PULLBACK, daily_raw=DailyRawState.PULLBACK, evidence=H4EvidenceStatus.FRESH)) == StabilMainState.RECOVERY_STARTING
    assert _main_state(_ctx(daily_state=DailyTrendState.PULLBACK_TOO_DEEP, daily_raw=DailyRawState.TOO_DEEP)) == StabilMainState.UPTREND_WEAKENING


def test_reason_priority_matches_pine_matrix() -> None:
    assert _reason(_ctx(w_ready=False)) == StabilReason.WAIT_WEEKLY
    assert _reason(_ctx(d_ready=False)) == StabilReason.WAIT_DAILY
    assert _reason(_ctx(daily_state=DailyTrendState.STRUCTURE_BROKEN, daily_raw=DailyRawState.STRUCTURE_BROKEN, gap=GapState.CONFIRMED)) == StabilReason.GAP_CONFIRMED
    assert _reason(_ctx(daily_state=DailyTrendState.DISTRIBUTION_RISK, daily_raw=DailyRawState.DISTRIBUTION)) == StabilReason.SELLING_EXPANSION
    assert _reason(_ctx(weekly_state=WeeklyTrendState.NOT_UP)) == StabilReason.WEEKLY_NOT_UP
    assert _reason(_ctx(daily_state=DailyTrendState.CONTROLLED_PULLBACK, daily_raw=DailyRawState.PULLBACK, evidence=H4EvidenceStatus.FAILED)) == StabilReason.CONTROLLED_H4_FAILED
    assert _reason(_ctx(daily_state=DailyTrendState.CONTROLLED_PULLBACK, daily_raw=DailyRawState.PULLBACK, evidence=H4EvidenceStatus.FRESH)) == StabilReason.CONTROLLED_H4_FRESH
    assert _reason(_ctx()) == StabilReason.HEALTHY_ADVANCE


def test_official_export_state_codes_are_stable_1_to_7() -> None:
    assert EXPORT_STATE_CODE == {
        StabilMainState.STABLE_UPTREND: 1,
        StabilMainState.HEALTHY_UPTREND: 2,
        StabilMainState.CONTROLLED_CORRECTION: 3,
        StabilMainState.RECOVERY_STARTING: 4,
        StabilMainState.OVEREXTENDED: 5,
        StabilMainState.UPTREND_WEAKENING: 6,
        StabilMainState.NOT_STABLE_UPTREND: 7,
    }


def test_score_stabilization_uses_exact_step_and_fast_move_contract() -> None:
    assert _stabilize(90.0, 40.0, 5.0, False) == pytest.approx(45.0)
    assert _stabilize(10.0, 40.0, 7.0, False) == pytest.approx(33.0)
    assert _stabilize(90.0, 40.0, 10.0, False) == pytest.approx(50.0)
    assert _stabilize(10.0, 80.0, 6.0, False) == pytest.approx(74.0)
    assert _stabilize(90.0, 10.0, 8.0, False) == pytest.approx(18.0)
    assert _stabilize(10.0, 80.0, 5.0, True) == pytest.approx(10.0)


def test_source_time_gate_requires_strict_forward_progress() -> None:
    old = pd.Timestamp("2026-08-20 12:00", tz=TZ)
    new = pd.Timestamp("2026-08-20 16:00", tz=TZ)
    assert _advanced(old, None)
    assert not _advanced(old, old)
    assert _advanced(new, old)
    assert not _advanced(old, new)


def test_same_closed_source_snapshots_do_not_apply_smoothing_twice() -> None:
    cfg = _small_cfg()
    weekly = _frame(48, "7D", "2025-01-01", step=0.35)
    daily = _frame(58, "1D", "2026-01-01", step=0.18)
    h4 = _frame(80, "4h", "2026-02-01", step=0.05)
    engine = StabilTrendEngine(cfg)
    first = engine.analyze(weekly, daily, h4)
    second = engine.analyze(weekly, daily, h4)
    assert second.health == first.health
    assert second.risk == first.risk
    assert second.weekly_score == first.weekly_score
    assert second.daily_health_score == first.daily_health_score
    assert second.h4_recovery_score == first.h4_recovery_score


def test_open_h4_cannot_change_final_export() -> None:
    cfg = _small_cfg()
    weekly = _frame(48, "7D", "2025-01-01", step=0.35)
    daily = _frame(58, "1D", "2026-01-01", step=0.18)
    h4 = _frame(80, "4h", "2026-02-01", step=0.05)
    base = StabilTrendEngine(cfg).analyze(weekly, daily, h4)
    open_row = h4.iloc[-1].copy()
    open_row["timestamp"] = h4.iloc[-1].timestamp + pd.Timedelta(hours=4)
    open_row["high"], open_row["low"], open_row["close"], open_row["volume"] = 100000.0, 0.1, 99999.0, 1_000_000_000.0
    open_row["is_closed"] = False
    open_row["is_complete"] = False
    with_open = StabilTrendEngine(cfg).analyze(weekly, daily, pd.concat([h4, pd.DataFrame([open_row])], ignore_index=True))
    assert with_open == base
