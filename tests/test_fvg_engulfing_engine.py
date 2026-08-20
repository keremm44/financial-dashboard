from __future__ import annotations

import pandas as pd

from financial_dashboard.engines.fvg_engulfing_engine import FvgEngulfingEngine
from financial_dashboard.engines.fvg_engulfing_models import (
    FvgDirection,
    FvgEngulfingConfig,
    FvgEngulfingDataQuality,
    FvgState,
    EngulfingDirection,
    EngulfingState,
)

TZ = "Europe/Istanbul"


def _bar(i: int, o: float, h: float, l: float, c: float, *, closed: bool = True, complete: bool = True) -> dict:
    return {
        "timestamp": pd.Timestamp("2026-01-01 10:00", tz=TZ) + pd.Timedelta(hours=4 * i),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000.0,
        "is_closed": closed,
        "is_complete": complete,
    }


def _warmup(count: int = 100) -> list[dict]:
    return [_bar(i, 100.0, 100.5, 99.5, 100.0) for i in range(count)]


def _bull_fvg_rows(*, candidate: bool = False) -> list[dict]:
    rows = _warmup()
    rows.append(_bar(100, 100.0, 100.5, 99.5, 100.0))
    rows.append(_bar(101, 100.1, 102.0, 100.0, 101.8))
    low = 100.62 if candidate else 100.80
    rows.append(_bar(102, 101.8, 102.6, low, 102.4))
    return rows


def _bear_fvg_rows() -> list[dict]:
    rows = _warmup()
    rows.append(_bar(100, 100.0, 100.5, 99.5, 100.0))
    rows.append(_bar(101, 99.9, 100.0, 98.0, 98.2))
    rows.append(_bar(102, 98.2, 99.2, 97.4, 97.6))
    return rows


def _bull_engulf_rows() -> list[dict]:
    rows = _warmup()
    rows.append(_bar(100, 100.5, 100.7, 99.8, 100.0))
    rows.append(_bar(101, 99.9, 101.0, 99.5, 100.7))
    return rows


def _bear_engulf_rows() -> list[dict]:
    rows = _warmup()
    rows.append(_bar(100, 99.5, 100.2, 99.3, 100.0))
    rows.append(_bar(101, 100.1, 100.5, 99.0, 99.3))
    return rows


def _run(rows: list[dict]) -> FvgEngulfingEngine:
    engine = FvgEngulfingEngine(FvgEngulfingConfig(timeframe="4h"))
    for row in rows:
        engine.update(row)
    return engine


def test_bullish_active_fvg_freezes_source_geometry_at_formation() -> None:
    engine = _run(_bull_fvg_rows())

    assert engine.snapshot.bullish_fvg_active
    assert not engine.snapshot.bullish_fvg_candidate
    formation = engine.fvg_formations[-1]
    assert formation.direction is FvgDirection.BULLISH
    assert formation.state is FvgState.ACTIVE
    assert formation.formation_index == 102
    assert formation.lower_boundary == 100.5
    assert formation.upper_boundary == 100.8
    assert formation.gap_size == 0.3
    assert formation.formation_atr > 0.0
    assert formation.quality >= 55.0
    assert formation.evidence_count >= 7


def test_bearish_active_fvg_uses_mirrored_three_bar_geometry() -> None:
    engine = _run(_bear_fvg_rows())

    assert engine.snapshot.bearish_fvg_active
    formation = engine.fvg_formations[-1]
    assert formation.direction is FvgDirection.BEARISH
    assert formation.state is FvgState.ACTIVE
    assert formation.lower_boundary == 99.2
    assert formation.upper_boundary == 99.5
    assert formation.gap_size == 0.3
    assert formation.quality >= 55.0


def test_weaker_bullish_fvg_can_be_candidate_without_becoming_active() -> None:
    engine = _run(_bull_fvg_rows(candidate=True))

    assert not engine.snapshot.bullish_fvg_active
    assert engine.snapshot.bullish_fvg_candidate
    formation = engine.fvg_formations[-1]
    assert formation.state is FvgState.CANDIDATE
    assert formation.direction is FvgDirection.BULLISH


def test_bullish_engulfing_freezes_original_swallowed_body_zone() -> None:
    engine = _run(_bull_engulf_rows())

    assert engine.snapshot.bullish_engulfing
    formation = engine.engulfing_formations[-1]
    assert formation.direction is EngulfingDirection.BULLISH
    assert formation.state is EngulfingState.ACTIVE
    assert formation.lower_boundary == 100.0
    assert formation.upper_boundary == 100.5
    assert formation.body_size == 0.5
    assert formation.quality > 0.0


def test_bearish_engulfing_freezes_original_swallowed_body_zone() -> None:
    engine = _run(_bear_engulf_rows())

    assert engine.snapshot.bearish_engulfing
    formation = engine.engulfing_formations[-1]
    assert formation.direction is EngulfingDirection.BEARISH
    assert formation.state is EngulfingState.ACTIVE
    assert formation.lower_boundary == 99.5
    assert formation.upper_boundary == 100.0
    assert formation.body_size == 0.5


def test_open_bar_does_not_enter_history_or_mutate_confirmed_formations() -> None:
    engine = _run(_bull_fvg_rows())
    frozen_fvg = engine.fvg_formations
    frozen_engulf = engine.engulfing_formations

    result = engine.update(_bar(103, 102.5, 110.0, 102.0, 109.0, closed=False))

    assert engine.fvg_formations == frozen_fvg
    assert engine.engulfing_formations == frozen_engulf
    assert engine.last_data_quality is FvgEngulfingDataQuality.INCOMPLETE_BAR
    assert result is not None and not result.is_confirmed


def test_source_gap_is_preserved_as_invalid_slot_and_cannot_form_signal() -> None:
    engine = _run(_bull_fvg_rows())
    frozen = engine.fvg_formations

    result = engine.update(_bar(103, 102.4, 103.0, 102.0, 102.8, complete=False))

    assert engine.fvg_formations == frozen
    assert engine.last_data_quality is FvgEngulfingDataQuality.SOURCE_GAP
    assert result is not None and not result.is_confirmed


def test_replay_and_incremental_have_identical_tur1_formations() -> None:
    rows = _bull_fvg_rows() + [
        _bar(103, 102.4, 103.0, 101.9, 102.2),
        _bar(104, 102.2, 103.1, 101.7, 102.8),
    ]
    replay = FvgEngulfingEngine()
    replay.replay(pd.DataFrame(rows))

    incremental = _run(rows)

    assert replay.fvg_formations == incremental.fvg_formations
    assert replay.engulfing_formations == incremental.engulfing_formations
    assert replay.snapshot == incremental.snapshot


def test_future_tail_never_changes_historical_formation_objects() -> None:
    engine = _run(_bull_fvg_rows())
    frozen = engine.fvg_formations

    for i in range(103, 110):
        engine.update(_bar(i, 102.0, 102.7, 101.5, 102.1))

    assert engine.fvg_formations[: len(frozen)] == frozen
