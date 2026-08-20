from __future__ import annotations

import pandas as pd

from financial_dashboard.engines import SupportResistanceConfig, SupportResistanceRangeEngine


def _bar(i: int, *, o: float, h: float, l: float, c: float, closed: bool = True, complete: bool = True) -> dict:
    return {
        "timestamp": pd.Timestamp("2026-01-01", tz="Europe/Istanbul") + pd.Timedelta(hours=i),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000.0 + i,
        "is_closed": closed,
        "is_complete": complete,
    }


def _range_frame(n: int = 80) -> pd.DataFrame:
    pattern = [
        (104.0, 107.0, 100.0, 103.0),
        (103.0, 110.0, 102.0, 106.0),
        (106.0, 108.0, 103.0, 105.0),
        (105.0, 107.0, 102.0, 104.0),
    ]
    rows = []
    for i in range(n):
        o, h, l, c = pattern[i % len(pattern)]
        rows.append(_bar(i, o=o, h=h, l=l, c=c))
    return pd.DataFrame(rows)


def test_pivot_is_known_only_after_right_span_bars_arrive() -> None:
    engine = SupportResistanceRangeEngine(SupportResistanceConfig(pivot_span=2))
    rows = [
        _bar(0, o=1, h=2, l=0, c=1),
        _bar(1, o=1, h=3, l=0.5, c=2),
        _bar(2, o=2, h=6, l=1, c=5),
        _bar(3, o=5, h=4, l=1.5, c=3),
        _bar(4, o=3, h=3.5, l=1, c=2),
    ]
    for row in rows[:4]:
        engine.update(row)
    assert not any(p.origin_index == 2 for p in engine.confirmed_pivots)

    engine.update(rows[4])
    pivot = next(p for p in engine.confirmed_pivots if p.origin_index == 2 and p.side == "HIGH")
    assert pivot.known_index == 4


def test_range_foundation_builds_structured_support_and_resistance_bands() -> None:
    config = SupportResistanceConfig(
        min_range_age=12,
        min_range_height_atr=1.0,
        max_range_height_atr=6.0,
        min_range_quality=35.0,
    )
    engine = SupportResistanceRangeEngine(config)
    results = engine.replay(_range_frame())

    assert results
    snap = engine.snapshot()
    export = engine.export_contract
    assert snap is not None
    assert export.state is not None
    assert export.range_identity is not None
    assert export.upper_center is not None
    assert export.lower_center is not None
    assert export.upper_center > export.lower_center
    assert export.upper_bottom < export.upper_center < export.upper_top
    assert export.lower_bottom < export.lower_center < export.lower_top
    assert export.upper_touches >= 2
    assert export.lower_touches >= 2
    assert export.quality is not None
    assert export.boundary_stability is not None


def test_same_range_keeps_identity_and_damps_boundary_drift() -> None:
    config = SupportResistanceConfig(
        min_range_age=12,
        min_range_height_atr=1.0,
        max_range_height_atr=6.0,
        min_range_quality=35.0,
    )
    engine = SupportResistanceRangeEngine(config)
    engine.replay(_range_frame(64))
    before = engine.export_contract
    assert before.range_identity is not None
    assert before.upper_center is not None

    # New confirmed highs are slightly higher but still overlap the same resistance zone.
    # A defined range should preserve identity and move its boundary gradually, not jump to raw 111.
    shifted = [
        (104.0, 107.0, 100.0, 103.0),
        (103.0, 111.0, 102.0, 106.0),
        (106.0, 108.0, 103.0, 105.0),
        (105.0, 107.0, 102.0, 104.0),
    ]
    for i in range(64, 92):
        o, h, l, c = shifted[i % 4]
        engine.update(_bar(i, o=o, h=h, l=l, c=c))

    after = engine.export_contract
    assert after.range_identity == before.range_identity
    assert after.upper_center is not None
    assert before.upper_center <= after.upper_center < 111.0
    assert after.identity_score is not None
    assert after.identity_score >= config.range_identity_min_score


def test_open_or_incomplete_bar_cannot_mutate_confirmed_snapshot() -> None:
    engine = SupportResistanceRangeEngine(SupportResistanceConfig(min_range_height_atr=1.0))
    engine.replay(_range_frame(48))
    before = engine.snapshot()
    before_export = engine.export_contract

    preview = _bar(49, o=104, h=140, l=70, c=135, closed=False)
    assert engine.update(preview) == before
    assert engine.export_contract == before_export

    incomplete = _bar(50, o=104, h=140, l=70, c=135, complete=False)
    assert engine.update(incomplete) == before
    assert engine.export_contract == before_export


def test_replay_matches_incremental_and_future_tail_cannot_rewrite_prefix() -> None:
    frame = _range_frame(72)
    config = SupportResistanceConfig(min_range_age=12, min_range_height_atr=1.0, min_range_quality=35.0)

    replay_engine = SupportResistanceRangeEngine(config)
    replay_results = replay_engine.replay(frame)

    incremental = SupportResistanceRangeEngine(config)
    incremental_results = [incremental.update(row) for _, row in frame.iterrows()]
    assert replay_results == incremental_results
    assert replay_engine.export_contract == incremental.export_contract

    prefix = frame.iloc[:52].copy()
    prefix_engine = SupportResistanceRangeEngine(config)
    prefix_results = prefix_engine.replay(prefix)

    full_engine = SupportResistanceRangeEngine(config)
    full_results = full_engine.replay(frame)
    assert full_results[: len(prefix_results)] == prefix_results
