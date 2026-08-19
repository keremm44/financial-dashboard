from __future__ import annotations

import hashlib
import json
import random

import pandas as pd
import pytest

from financial_dashboard.engines.market_structure import MarketStructureConfig
from financial_dashboard.engines.market_structure_engine import MarketStructureEngine


VALID_STATES = {
    "STATE_NEUTRAL",
    "STATE_BULLISH",
    "STATE_BEARISH",
    "STATE_TRANSITION_UP",
    "STATE_TRANSITION_DOWN",
}


def _config(profile: str = "Dengeli") -> MarketStructureConfig:
    return MarketStructureConfig(
        profile=profile,
        external_pivot_len=3,
        internal_pivot_len=1,
        atr_length=5,
        external_min_atr_distance=0.20,
        internal_min_atr_distance=0.10,
        min_tick=0.01,
    )


def _frame_from_closes(closes: list[float]) -> pd.DataFrame:
    opens = [closes[0], *closes[:-1]]
    highs = [max(o, c) + 0.45 + (i % 3) * 0.03 for i, (o, c) in enumerate(zip(opens, closes))]
    lows = [min(o, c) - 0.45 - (i % 2) * 0.04 for i, (o, c) in enumerate(zip(opens, closes))]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=len(closes), freq="h", tz="UTC"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1000.0 + i * 7.0 for i in range(len(closes))],
            "is_closed": [True] * len(closes),
        }
    )


def _scenario_frame(name: str) -> pd.DataFrame:
    if name == "trend_up":
        closes = [100 + i * 0.45 + (1.2 if i % 8 in (3, 4) else -0.55 if i % 8 == 6 else 0.0) for i in range(90)]
    elif name == "trend_down":
        closes = [140 - i * 0.42 + (0.60 if i % 9 == 5 else -1.10 if i % 9 in (2, 3) else 0.0) for i in range(90)]
    elif name == "range_break":
        closes = []
        for i in range(55):
            closes.append(100 + ((i % 10) - 5) * 0.30)
        closes.extend([101.0, 101.4, 102.2, 103.1, 104.0, 104.8, 104.3, 105.1, 105.8, 106.2])
    elif name == "shock_recovery":
        closes = [100 + ((i % 7) - 3) * 0.22 for i in range(35)]
        closes.extend([98.5, 96.0, 93.0, 90.0, 91.5, 93.8, 95.5, 97.0, 98.4, 99.6, 100.4, 101.2])
    else:
        raise AssertionError(name)
    return _frame_from_closes(closes)


def _random_frame(seed: int, bars: int = 240) -> pd.DataFrame:
    rng = random.Random(seed)
    price = 100.0
    closes: list[float] = []
    for i in range(bars):
        regime = (i // 40) % 6
        drift = {0: 0.18, 1: -0.15, 2: 0.0, 3: 0.28, 4: -0.25, 5: 0.02}[regime]
        shock = rng.choice([0.0] * 18 + [rng.uniform(-2.2, 2.2)])
        price = max(1.0, price + drift + rng.uniform(-0.70, 0.70) + shock)
        closes.append(round(price, 4))
    return _frame_from_closes(closes)


def _canonical_result(result) -> dict:
    return {
        "timestamp": str(result.timestamp),
        "state": result.state,
        "direction": str(result.direction),
        "score": result.score,
        "quality": result.quality,
        "levels": {k: result.levels[k] for k in sorted(result.levels)},
        "events": list(result.events),
        "reasons": list(result.reasons),
        "is_confirmed": result.is_confirmed,
    }


def _history_digest(results) -> str:
    payload = json.dumps([_canonical_result(r) for r in results], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("scenario", ["trend_up", "trend_down", "range_break", "shock_recovery"])
@pytest.mark.parametrize("profile", ["Hassas", "Dengeli", "Seçici"])
def test_scenario_matrix_produces_safe_structural_outputs(scenario: str, profile: str) -> None:
    frame = _scenario_frame(scenario)
    engine = MarketStructureEngine(_config(profile))

    results = engine.replay(frame)

    assert len(results) == len(frame)
    assert results[-1].state in VALID_STATES
    assert all(result.state in VALID_STATES for result in results)
    assert all(result.score is not None and 0 <= result.score <= 100 for result in results)
    assert all(result.is_confirmed for result in results)
    assert engine.export_contract is not None
    assert engine.export_contract.handshake == 314159.0


@pytest.mark.parametrize("scenario", ["trend_up", "range_break", "shock_recovery"])
def test_full_replay_equals_incremental_bar_by_bar_history(scenario: str) -> None:
    frame = _scenario_frame(scenario)
    replay_engine = MarketStructureEngine(_config())
    incremental_engine = MarketStructureEngine(_config())

    replay_results = replay_engine.replay(frame)
    incremental_results = [incremental_engine.update(row) for _, row in frame.iterrows()]

    assert all(result is not None for result in incremental_results)
    assert [_canonical_result(r) for r in replay_results] == [_canonical_result(r) for r in incremental_results]
    assert _history_digest(replay_results) == _history_digest(incremental_results)


def test_future_bars_cannot_rewrite_existing_history() -> None:
    frame = _scenario_frame("shock_recovery")
    split = 34
    prefix = frame.iloc[:split].copy()

    prefix_results = MarketStructureEngine(_config()).replay(prefix)
    full_results = MarketStructureEngine(_config()).replay(frame)

    assert [_canonical_result(r) for r in prefix_results] == [_canonical_result(r) for r in full_results[:split]]
    assert _history_digest(prefix_results) == _history_digest(full_results[:split])


def test_replaying_identical_data_twice_is_bitwise_deterministic_at_contract_level() -> None:
    frame = _random_frame(seed=20260819, bars=300)

    first = MarketStructureEngine(_config()).replay(frame)
    second = MarketStructureEngine(_config()).replay(frame.copy(deep=True))

    assert _history_digest(first) == _history_digest(second)
    assert [_canonical_result(r) for r in first] == [_canonical_result(r) for r in second]


def test_unclosed_future_bar_cannot_mutate_confirmed_snapshot_or_history() -> None:
    frame = _scenario_frame("range_break")
    engine = MarketStructureEngine(_config())
    closed_results = engine.replay(frame)
    before_digest = _history_digest(closed_results)
    before_snapshot = _canonical_result(engine.snapshot())
    before_external = repr(engine.external_context)
    before_internal = repr(engine.internal_context)

    last = frame.iloc[-1].to_dict()
    live = {
        **last,
        "timestamp": frame.iloc[-1]["timestamp"] + pd.Timedelta(hours=1),
        "open": last["close"],
        "high": last["close"] + 50.0,
        "low": max(0.01, last["close"] - 50.0),
        "close": last["close"] + 25.0,
        "is_closed": False,
    }
    returned = engine.update(live)

    assert _canonical_result(returned) == before_snapshot
    assert _canonical_result(engine.snapshot()) == before_snapshot
    assert repr(engine.external_context) == before_external
    assert repr(engine.internal_context) == before_internal
    assert _history_digest(closed_results) == before_digest


@pytest.mark.parametrize("seed", [3, 7, 11, 23, 41, 97, 211, 997])
def test_randomized_stress_preserves_invariants(seed: int) -> None:
    frame = _random_frame(seed=seed, bars=260)
    engine = MarketStructureEngine(_config())

    results = engine.replay(frame)

    assert len(results) == len(frame)
    assert all(result.state in VALID_STATES for result in results)
    assert all(result.score is not None and 0 <= result.score <= 100 for result in results)
    assert all(result.is_confirmed for result in results)
    assert all(result.timestamp == frame.iloc[i]["timestamp"] for i, result in enumerate(results))
    assert engine.export_contract is not None
    assert engine.export_contract.handshake == 314159.0

    # The same randomized market must produce the exact same state/event history.
    repeat = MarketStructureEngine(_config()).replay(frame)
    assert _history_digest(results) == _history_digest(repeat)
