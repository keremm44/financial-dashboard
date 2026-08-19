import pandas as pd

from financial_dashboard.engines.liquidity_engine import LiquidityEngine
from financial_dashboard.engines.liquidity_models import LiquidityConfig


CFG = LiquidityConfig(
    atr_tolerance=0.10,
    min_tick=0.01,
    min_touches_active=2,
    test_tolerance_factor=1.0,
    pivot_span=1,
    atr_length=3,
)


def bar(i, high, low, close, *, closed=True):
    return {
        "timestamp": pd.Timestamp("2026-08-19 10:00", tz="Europe/Istanbul") + pd.Timedelta(minutes=5 * i),
        "open": float(close),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "is_closed": closed,
    }


def fixture_rows():
    return [
        bar(0, 99.0, 98.0, 98.5),
        bar(1, 100.0, 98.5, 99.0),
        bar(2, 99.0, 98.2, 98.7),
        bar(3, 100.0, 98.6, 99.1),
        bar(4, 99.0, 98.3, 98.8),
        bar(5, 100.4, 98.7, 99.7),
        bar(6, 99.8, 98.4, 99.1),
    ]


def test_replay_matches_incremental_exactly():
    rows = fixture_rows()
    replay_engine = LiquidityEngine(CFG)
    replay_history = replay_engine.replay(pd.DataFrame(rows))

    incremental_engine = LiquidityEngine(CFG)
    incremental_history = [incremental_engine.update(row) for row in rows]

    assert incremental_history == replay_history
    assert incremental_engine.snapshot() == replay_engine.snapshot()
    assert incremental_engine.pools == replay_engine.pools
    assert incremental_engine.export_contract == replay_engine.export_contract


def test_future_bars_do_not_rewrite_prefix_history():
    rows = fixture_rows()
    prefix_len = 5

    prefix_engine = LiquidityEngine(CFG)
    prefix_history = prefix_engine.replay(pd.DataFrame(rows[:prefix_len]))

    full_engine = LiquidityEngine(CFG)
    full_history = full_engine.replay(pd.DataFrame(rows))

    assert full_history[:prefix_len] == prefix_history


def test_open_preview_inserted_between_closed_bars_has_no_effect_on_future_state():
    rows = fixture_rows()

    baseline = LiquidityEngine(CFG)
    baseline_history = [baseline.update(row) for row in rows]

    with_preview = LiquidityEngine(CFG)
    preview_history = []
    for index, row in enumerate(rows):
        if index == 5:
            preview = dict(row)
            preview["high"] = 101.5
            preview["low"] = 97.5
            preview["close"] = 100.5
            preview["is_closed"] = False
            before = with_preview.snapshot()
            out = with_preview.update(preview)
            assert out == before
        preview_history.append(with_preview.update(row))

    assert preview_history == baseline_history
    assert with_preview.pools == baseline.pools
    assert with_preview.export_contract == baseline.export_contract


def test_same_duplicate_sequence_is_deterministic_across_fresh_engines():
    rows = fixture_rows()
    duplicate_sequence = rows[:4] + [dict(rows[3])] + rows[4:]

    first = LiquidityEngine(CFG)
    first_history = [first.update(row) for row in duplicate_sequence]

    second = LiquidityEngine(CFG)
    second_history = [second.update(row) for row in duplicate_sequence]

    assert first_history == second_history
    assert first.pools == second.pools
    assert first.export_contract == second.export_contract


def test_replay_is_repeatable_on_same_engine_instance():
    frame = pd.DataFrame(fixture_rows())
    engine = LiquidityEngine(CFG)

    first = engine.replay(frame)
    first_pools = engine.pools
    first_export = engine.export_contract

    second = engine.replay(frame)

    assert second == first
    assert engine.pools == first_pools
    assert engine.export_contract == first_export
