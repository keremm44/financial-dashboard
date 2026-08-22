from __future__ import annotations

import pandas as pd

from financial_dashboard.engines.models import Direction, EngineResult
from financial_dashboard.engines.volatility_bands_fib import VolatilityBandsFibEngine
from financial_dashboard.engines.volatility_bands_fib_engine import VolatilityBandsConfig
from financial_dashboard.engines.volatility_direction_transition import (
    EarlyDirectionTransition,
    VolatilityDirectionTransitionEngine,
)


TZ = "Europe/Istanbul"


def _base_frame(n: int = 130) -> pd.DataFrame:
    rows = []
    for i in range(n):
        close = 100.0 - i * 0.015 + ((i % 4) - 1.5) * 0.015
        rows.append(
            {
                "timestamp": pd.Timestamp("2026-01-02 10:00", tz=TZ) + pd.Timedelta(hours=2 * i),
                "open": close + 0.03,
                "high": close + 0.55,
                "low": close - 0.55,
                "close": close,
                "volume": 1000.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return pd.DataFrame(rows)


def _with_up_transition() -> pd.DataFrame:
    frame = _base_frame()
    previous = float(frame.iloc[-1]["close"])
    row = {
        "timestamp": frame.iloc[-1]["timestamp"] + pd.Timedelta(hours=2),
        "open": previous + 0.05,
        "high": previous + 1.15,
        "low": previous - 0.10,
        "close": previous + 0.85,
        "volume": 1200.0,
        "is_closed": True,
        "is_complete": True,
    }
    return pd.concat([frame, pd.DataFrame([row])], ignore_index=True)


def _with_down_transition() -> pd.DataFrame:
    frame = _base_frame()
    # Establish a mild upward local context before the reversal bar.
    for offset in range(8):
        idx = len(frame) - 8 + offset
        close = 98.4 + offset * 0.08
        frame.loc[idx, ["open", "high", "low", "close"]] = [
            close - 0.03,
            close + 0.55,
            close - 0.55,
            close,
        ]
    previous = float(frame.iloc[-1]["close"])
    row = {
        "timestamp": frame.iloc[-1]["timestamp"] + pd.Timedelta(hours=2),
        "open": previous - 0.05,
        "high": previous + 0.10,
        "low": previous - 1.15,
        "close": previous - 0.85,
        "volume": 1200.0,
        "is_closed": True,
        "is_complete": True,
    }
    return pd.concat([frame, pd.DataFrame([row])], ignore_index=True)


def test_early_up_can_appear_without_rewriting_confirmed_export() -> None:
    frame = _with_up_transition()
    config = VolatilityBandsConfig(profile="Dengeli", timeframe="2h")
    wrapper = VolatilityDirectionTransitionEngine(config)
    snapshots = wrapper.replay(frame)

    direct = VolatilityBandsFibEngine(config)
    direct.replay(frame)

    assert snapshots[-1].early.state is EarlyDirectionTransition.EARLY_UP
    assert snapshots[-1].early.evidence_count >= 5
    assert snapshots[-1].confirmed_export == direct.final_export
    assert wrapper.canonical_engine.final_export == direct.final_export


def test_early_down_can_appear_without_rewriting_confirmed_export() -> None:
    frame = _with_down_transition()
    config = VolatilityBandsConfig(profile="Dengeli", timeframe="2h")
    wrapper = VolatilityDirectionTransitionEngine(config)
    snapshots = wrapper.replay(frame)

    direct = VolatilityBandsFibEngine(config)
    direct.replay(frame)

    assert snapshots[-1].early.state is EarlyDirectionTransition.EARLY_DOWN
    assert snapshots[-1].early.evidence_count >= 5
    assert snapshots[-1].confirmed_export == direct.final_export


def test_weak_opposite_candle_is_not_promoted_to_early_transition() -> None:
    frame = _base_frame()
    previous = float(frame.iloc[-1]["close"])
    weak = {
        "timestamp": frame.iloc[-1]["timestamp"] + pd.Timedelta(hours=2),
        "open": previous,
        "high": previous + 0.20,
        "low": previous - 0.20,
        "close": previous + 0.05,
        "volume": 1000.0,
        "is_closed": True,
        "is_complete": True,
    }
    frame = pd.concat([frame, pd.DataFrame([weak])], ignore_index=True)
    latest = VolatilityDirectionTransitionEngine().replay(frame)[-1]
    assert latest.early.state is EarlyDirectionTransition.NONE


def test_canonical_one_bar_shock_suppresses_early_direction_relabel() -> None:
    engine = VolatilityDirectionTransitionEngine()
    frame = _with_up_transition()
    engine._rows = frame.to_dict("records")
    shock = EngineResult(
        engine="VolatilityBandsFib",
        state="COHERENCE_CONFLICT",
        timestamp=frame.iloc[-1]["timestamp"],
        direction=Direction.UP,
        reasons=("vol=VOL_ONE_BAR_SHOCK",),
    )
    early = engine._early_evidence(shock)
    assert early.state is EarlyDirectionTransition.NONE
    assert "canonical_one_bar_shock" in early.reasons


def test_open_and_incomplete_bars_freeze_both_clocks() -> None:
    engine = VolatilityDirectionTransitionEngine()
    frame = _with_up_transition()
    engine.replay(frame)
    before = engine.snapshot()
    before_count = len(engine._rows)

    open_bar = frame.iloc[-1].to_dict()
    open_bar["timestamp"] = pd.Timestamp("2026-09-30", tz=TZ)
    open_bar["close"] = 9999.0
    open_bar["high"] = 10000.0
    open_bar["is_closed"] = False
    assert engine.update(open_bar) == before
    assert len(engine._rows) == before_count

    incomplete = frame.iloc[-1].to_dict()
    incomplete["timestamp"] = pd.Timestamp("2026-10-01", tz=TZ)
    incomplete["close"] = 1.0
    incomplete["is_complete"] = False
    assert engine.update(incomplete) == before
    assert len(engine._rows) == before_count


def test_future_tail_cannot_rewrite_prefix_transition_history() -> None:
    frame = _with_up_transition()
    tail_rows = []
    last = frame.iloc[-1]
    for i in range(12):
        close = float(last["close"]) + (i + 1) * 0.20
        tail_rows.append(
            {
                "timestamp": last["timestamp"] + pd.Timedelta(hours=2 * (i + 1)),
                "open": close - 0.08,
                "high": close + 0.50,
                "low": close - 0.45,
                "close": close,
                "volume": 1200.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    full = pd.concat([frame, pd.DataFrame(tail_rows)], ignore_index=True)

    prefix_replay = VolatilityDirectionTransitionEngine().replay(frame)
    full_replay = VolatilityDirectionTransitionEngine().replay(full)
    assert full_replay[: len(prefix_replay)] == prefix_replay


def test_existing_timeframe_contract_is_preserved() -> None:
    for timeframe in ("2h", "4h", "1d"):
        VolatilityDirectionTransitionEngine(VolatilityBandsConfig(timeframe=timeframe))
