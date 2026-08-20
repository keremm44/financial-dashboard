from __future__ import annotations

import pandas as pd
import pytest

from financial_dashboard.engines import FvgEngulfingEngine, FvgState
from financial_dashboard.engines.fvg_engulfing_engine import FvgFormation
from financial_dashboard.engines.fvg_engulfing_final import FvgEngulfingExport, FvgSideExport, _LifecycleMetrics
from financial_dashboard.engines.fvg_engulfing_models import FvgDirection

TZ = "Europe/Istanbul"


def _bar(i: int, o: float, h: float, l: float, c: float, *, closed: bool = True, complete: bool = True) -> dict:
    return {"timestamp": pd.Timestamp("2026-01-01 10:00", tz=TZ) + pd.Timedelta(hours=4 * i), "open": o, "high": h, "low": l, "close": c, "volume": 1000.0, "is_closed": closed, "is_complete": complete}


def _bull_fvg_rows() -> list[dict]:
    rows = [_bar(i, 100.0, 100.5, 99.5, 100.0) for i in range(100)]
    return rows + [_bar(100, 100.0, 100.5, 99.5, 100.0), _bar(101, 100.1, 102.0, 100.0, 101.8), _bar(102, 101.8, 102.6, 100.8, 102.4)]


def _metrics(**overrides) -> _LifecycleMetrics:
    values = dict(close=105.0, low=104.0, high=106.0, candle_bullish=False, candle_bearish=False, close_location=0.60, body_to_prior_atr=0.5, net_progress_atr=0.2, directional_efficiency=0.6, higher_close_share=0.5, lower_close_share=0.0, buy_continuation_candidate=True, sell_continuation_candidate=False, buy_continuation_confirmed=False, sell_continuation_confirmed=False, lower_rejection=False, upper_rejection=False, bullish_engulfing=False, bearish_engulfing=False)
    values.update(overrides)
    return _LifecycleMetrics(**values)


def _formation(quality: float, embedded: float) -> FvgFormation:
    return FvgFormation(FvgDirection.BULLISH, FvgState.CANDIDATE, 10, pd.Timestamp("2026-01-01", tz=TZ), 100.0, 101.0, 1.0, 0.5, 2.0, quality, embedded, 7)


def test_candidate_alignment_adds_only_missing_embedded_contribution() -> None:
    engine = FvgEngulfingEngine(); engine._fvg_formations = [_formation(55.0, 5.0)]
    engine._repair_candidate_alignment(10, _metrics())
    repaired = engine.fvg_formations[0]
    assert repaired.embedded_candle_contribution == 10.0
    assert repaired.quality == pytest.approx(60.0)


def test_candidate_alignment_does_not_double_count_when_alignment_already_present() -> None:
    engine = FvgEngulfingEngine(); original = _formation(60.0, 10.0); engine._fvg_formations = [original]
    engine._repair_candidate_alignment(10, _metrics(candle_bullish=True, close_location=0.8))
    assert engine.fvg_formations[0] == original


def test_candidate_alignment_removes_stale_counter_absence_contribution() -> None:
    engine = FvgEngulfingEngine(); engine._fvg_formations = [_formation(60.0, 10.0)]
    engine._repair_candidate_alignment(10, _metrics(sell_continuation_confirmed=True, upper_rejection=True))
    repaired = engine.fvg_formations[0]
    assert repaired.embedded_candle_contribution == 5.0
    assert repaired.quality == pytest.approx(55.0)


def test_final_replay_and_incremental_have_identical_lifecycle_and_export() -> None:
    rows = _bull_fvg_rows() + [_bar(103, 102.4, 103.0, 101.9, 102.2), _bar(104, 102.2, 103.1, 101.7, 102.8)]
    replay = FvgEngulfingEngine(); replay.replay(pd.DataFrame(rows))
    incremental = FvgEngulfingEngine()
    for row in rows: incremental.update(row)
    assert replay.export == incremental.export
    assert replay.active_bullish_fvg == incremental.active_bullish_fvg
    assert replay.active_bearish_fvg == incremental.active_bearish_fvg
    assert replay.completed_fvg == incremental.completed_fvg
    assert replay.completed_engulfing == incremental.completed_engulfing


def test_open_and_source_gap_bars_cannot_overwrite_last_confirmed_export() -> None:
    engine = FvgEngulfingEngine()
    frozen = FvgEngulfingExport(bull_fvg=FvgSideExport(state=3, top=110.0, bottom=100.0, quality=70.0, fill=0.0))
    engine._export = frozen
    engine.update(_bar(0, 100.0, 101.0, 99.0, 100.5, closed=False)); assert engine.export == frozen
    engine.update(_bar(0, 100.0, 101.0, 99.0, 100.5, complete=False)); assert engine.export == frozen
