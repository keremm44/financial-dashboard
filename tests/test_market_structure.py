from __future__ import annotations

import pandas as pd

from financial_dashboard.engines.market_structure import (
    CANDIDATE_MERGE,
    CANDIDATE_REPLACE,
    CLASS_HH,
    CLASS_PH,
    SCOPE_EXTERNAL,
    SIDE_HIGH,
    MarketStructureConfig,
    MarketStructureEngine,
    SwingPoint,
)


def _swing(identity: int, price: float, atr: float = 1.0, *, side: str = SIDE_HIGH, source_bar: int = 5) -> SwingPoint:
    return SwingPoint(
        valid=True,
        identity=identity,
        scope=SCOPE_EXTERNAL,
        side=side,
        source_bar=source_bar,
        confirm_bar=source_bar + 2,
        price=price,
        atr_at_source=atr,
        prominence_atr=1.0,
        distance_atr=1.0,
        quality=60.0,
    )


def test_candidate_replace_preserves_identity_and_extreme() -> None:
    engine = MarketStructureEngine(MarketStructureConfig(min_tick=0.01))
    candidate = _swing(7, 100.0)
    incoming = _swing(8, 101.0, source_bar=6)

    updated, action = engine._candidate_update(candidate, incoming, locked_by_break=False)

    assert action == CANDIDATE_REPLACE
    assert updated.identity == 7
    assert updated.price == 101.0
    assert updated.evidence_text == "candidate replace: stronger extreme"


def test_candidate_merge_keeps_existing_extreme_when_incoming_is_weaker() -> None:
    engine = MarketStructureEngine(MarketStructureConfig(equal_tolerance_atr=0.16, min_tick=0.01))
    candidate = _swing(3, 100.0)
    incoming = _swing(4, 99.90, source_bar=6)

    updated, action = engine._candidate_update(candidate, incoming, locked_by_break=False)

    assert action == CANDIDATE_MERGE
    assert updated.identity == 3
    assert updated.price == 100.0
    assert updated.confirm_bar == incoming.confirm_bar


def test_classification_uses_frozen_pair_atr_tolerance() -> None:
    engine = MarketStructureEngine(MarketStructureConfig(equal_tolerance_atr=0.16, min_tick=0.01))
    previous = _swing(1, 100.0, atr=2.0)
    near_equal = _swing(2, 100.20, atr=2.0)
    clearly_higher = _swing(3, 100.50, atr=2.0)

    assert engine._classify(near_equal, previous) == CLASS_PH
    assert engine._classify(clearly_higher, previous) == CLASS_HH


def test_replay_confirms_swings_only_after_opposite_pivot_is_known() -> None:
    engine = MarketStructureEngine(
        MarketStructureConfig(
            external_pivot_len=2,
            internal_pivot_len=1,
            atr_length=3,
            external_min_atr_distance=0.10,
            internal_min_atr_distance=0.10,
            min_tick=0.01,
        )
    )
    highs = [10, 11, 15, 12, 11, 12, 13, 12, 16, 13, 12, 13]
    lows = [9, 10, 11, 10, 7, 9, 10, 9, 11, 8, 7, 9]
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=len(highs), freq="h", tz="UTC"),
            "open": [9.5] * len(highs),
            "high": highs,
            "low": lows,
            "close": [10.0] * len(highs),
            "volume": [100.0] * len(highs),
            "is_closed": [True] * len(highs),
        }
    )

    results = engine.replay(frame)

    external = engine.external_swings
    assert len(external) >= 2
    assert external[0].source_bar == 2
    assert external[0].confirm_bar > external[0].source_bar
    assert external[0].state == "SWING_CONFIRMED"
    assert external[1].side != external[0].side
    assert any(result.events for result in results)


def test_open_bar_does_not_advance_confirmed_state() -> None:
    engine = MarketStructureEngine(MarketStructureConfig(external_pivot_len=2, internal_pivot_len=1, atr_length=2))
    closed = {
        "timestamp": pd.Timestamp("2026-01-01T10:00:00Z"),
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "is_closed": True,
    }
    live = {**closed, "timestamp": pd.Timestamp("2026-01-01T11:00:00Z"), "is_closed": False}

    first = engine.update(closed)
    second = engine.update(live)

    assert second is first
