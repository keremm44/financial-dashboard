from __future__ import annotations

import pandas as pd
import pytest

from financial_dashboard.engines.order_block_engine import OrderBlockConfig, OrderBlockEngine

TZ = "Europe/Istanbul"


def _bar(i: int, o: float, h: float, l: float, c: float, *, closed: bool = True, complete: bool = True) -> dict:
    return {
        "timestamp": pd.Timestamp("2026-01-02 10:00", tz=TZ) + pd.Timedelta(minutes=5 * i),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1000.0,
        "is_closed": closed,
        "is_complete": complete,
    }


def test_bearish_pair_keeps_a_when_upper_wick_is_protected() -> None:
    engine = OrderBlockEngine()
    engine.update(_bar(0, 100, 103, 99, 102))
    engine.update(_bar(1, 102, 102.5, 98, 99))
    assert len(engine.records) == 1
    record = engine.records[0]
    assert record.bullish is False
    assert record.source_index == 0
    assert record.top == 103
    assert record.bottom == 99
    assert record.base_score == 2


def test_bearish_pair_replaces_a_with_b_when_b_breaks_upper_wick() -> None:
    engine = OrderBlockEngine()
    engine.update(_bar(0, 100, 103, 99, 102))
    engine.update(_bar(1, 102, 104, 98, 99))
    record = engine.records[0]
    assert record.source_index == 1
    assert record.top == 104
    assert record.bottom == 98


def test_bullish_pair_keeps_a_when_lower_wick_is_protected() -> None:
    engine = OrderBlockEngine()
    engine.update(_bar(0, 102, 103, 98, 99))
    engine.update(_bar(1, 99, 104, 98.5, 103))
    record = engine.records[0]
    assert record.bullish is True
    assert record.source_index == 0
    assert record.fill_boundary == 103


def test_bullish_pair_replaces_a_with_b_when_b_breaks_lower_wick() -> None:
    engine = OrderBlockEngine()
    engine.update(_bar(0, 102, 103, 98, 99))
    engine.update(_bar(1, 99, 104, 97, 103))
    record = engine.records[0]
    assert record.bullish is True
    assert record.source_index == 1
    assert record.top == 104
    assert record.bottom == 97


def test_imbalance_is_checked_from_source_plus_two_with_source_anchor() -> None:
    engine = OrderBlockEngine(OrderBlockConfig(minimum_tick=0.01))
    engine.update(_bar(0, 102, 103, 98, 99))
    engine.update(_bar(1, 99, 104, 98.5, 103))  # source=A index 0
    assert engine.records[0].has_imbalance is False
    engine.update(_bar(2, 104, 106, 103.02, 105))
    record = engine.records[0]
    assert record.has_imbalance is True
    assert record.score == 3
    assert record.active is True


def test_preconfirm_fill_accumulates_from_source_plus_two() -> None:
    engine = OrderBlockEngine(OrderBlockConfig(fill_cancel_threshold=0.70))
    engine.update(_bar(0, 102, 110, 100, 99))
    engine.update(_bar(1, 99, 111, 100.5, 106))  # bullish source=A, zone 100..110
    engine.update(_bar(2, 106, 108, 106, 107))  # enters 40% from top
    record = engine.records[0]
    assert record.has_imbalance is False
    assert record.fill_boundary == pytest.approx(106.0)
    assert record.fill_ratio == pytest.approx(0.40)


def test_candidate_is_removed_when_fill_reaches_cancel_threshold_before_imbalance() -> None:
    engine = OrderBlockEngine(OrderBlockConfig(fill_cancel_threshold=0.70))
    engine.update(_bar(0, 102, 110, 100, 99))
    engine.update(_bar(1, 99, 111, 100.5, 106))
    engine.update(_bar(2, 106, 109, 102.5, 108))  # 75% penetration
    assert engine.records == ()


def test_gap_through_marks_full_use_and_removes_record() -> None:
    engine = OrderBlockEngine()
    engine.update(_bar(0, 102, 110, 100, 99))
    engine.update(_bar(1, 99, 111, 100.5, 106))
    engine.update(_bar(2, 95, 99, 94, 96))  # bullish gap-through: high < zone bottom
    assert engine.records == ()


def test_candidate_expires_after_own_imbalance_window() -> None:
    engine = OrderBlockEngine(OrderBlockConfig(imbalance_max_candle=3))
    engine.update(_bar(0, 102, 110, 100, 99))
    engine.update(_bar(1, 99, 111, 100.5, 106))
    engine.update(_bar(2, 106, 109, 105, 107))  # no gap, last eligible index for source=0
    assert any(r.source_index == 0 and r.bullish for r in engine.records)
    engine.update(_bar(3, 107, 109, 105, 106))
    assert not any(r.source_index == 0 and r.bullish for r in engine.records)
    # The same bar can legitimately create a new independent bearish A/B candidate.
    assert any(r.source_index == 2 and not r.bullish for r in engine.records)


def test_new_b_source_is_not_retroactively_updated_on_creation_bar() -> None:
    engine = OrderBlockEngine()
    engine.update(_bar(0, 100, 103, 99, 102))
    engine.update(_bar(1, 102, 104, 98, 99))  # B breaks A high -> B becomes source
    record = engine.records[0]
    assert record.source_index == 1
    assert record.fill_ratio == 0.0
    assert record.has_imbalance is False


def test_open_or_incomplete_bar_freezes_confirmed_state() -> None:
    engine = OrderBlockEngine()
    engine.update(_bar(0, 100, 103, 99, 102))
    engine.update(_bar(1, 102, 102.5, 98, 99))
    before_snapshot = engine.snapshot()
    before_records = engine.records
    assert engine.update(_bar(2, 50, 500, 1, 400, closed=False)) == before_snapshot
    assert engine.records == before_records
    assert engine.update(_bar(3, 50, 500, 1, 400, complete=False)) == before_snapshot
    assert engine.records == before_records


def test_replay_matches_incremental_and_future_tail_does_not_rewrite_prefix() -> None:
    rows = [
        _bar(0, 102, 110, 100, 99),
        _bar(1, 99, 111, 100.5, 106),
        _bar(2, 106, 112, 110.02, 111),
        _bar(3, 111, 113, 109, 110),
        _bar(4, 110, 112, 108, 109),
    ]
    frame = pd.DataFrame(rows)
    replay = OrderBlockEngine()
    replay_results = replay.replay(frame)
    incremental = OrderBlockEngine()
    for row in rows:
        incremental.update(row)
    assert incremental.snapshot() == replay_results[-1]
    assert incremental.records == replay.records

    prefix = pd.DataFrame(rows[:3])
    a = OrderBlockEngine()
    a.replay(prefix)
    b = OrderBlockEngine()
    results = b.replay(frame)
    assert results[2] == a.snapshot()


def test_config_matches_source_input_domain() -> None:
    for value in (3, 4, 5):
        OrderBlockConfig(imbalance_max_candle=value)
    with pytest.raises(ValueError):
        OrderBlockConfig(imbalance_max_candle=2)
    with pytest.raises(ValueError):
        OrderBlockConfig(fill_cancel_threshold=0.05)
