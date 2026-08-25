"""Bit-exactness guards for the incremental engine kernels.

The raw indicator dashboard computes each new bar through an incremental causal
core while the vectorized frame computation remains the closed-form reference.
These tests pin both paths together (and the volatility series caches) so a
future edit cannot silently change per-bar arithmetic.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from financial_dashboard.engines.raw_indicator_dashboard import (
    RawIndicatorConfig,
    RawIndicatorDashboardEngine,
    TrendProfile,
)
from financial_dashboard.engines.volatility_bands_fib_engine import (
    VolatilityBandsConfig,
    VolatilityBandsFibEngine as CoreEngine,
)
from financial_dashboard.engines.volatility_direction_transition import (
    VolatilityDirectionTransitionEngine,
)

TZ = "Europe/Istanbul"


def _frame(seed: int, periods: int, *, drift: float = 0.10) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    steps = rng.normal(drift, 0.9, periods)
    close = 100.0 + np.cumsum(steps)
    high = close + rng.uniform(0.1, 1.4, periods)
    low = close - rng.uniform(0.1, 1.2, periods)
    open_ = low + rng.uniform(0.0, 1.0, periods) * (high - low)
    volume = rng.uniform(5e4, 4e5, periods)
    frame = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-02 10:00", periods=periods, freq="1h", tz=TZ),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "is_closed": True,
            "is_complete": True,
        }
    )
    for i in range(7, periods, 37):
        frame.loc[i, "is_complete"] = False
    for i in range(11, periods, 53):
        frame.loc[i, "is_closed"] = False
    return frame


def _same(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (math.isnan(a) and math.isnan(b))
    if isinstance(a, np.floating):
        return _same(float(a), b)
    if isinstance(b, np.floating):
        return _same(a, float(b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def test_raw_incremental_update_matches_vectorized_frame() -> None:
    for profile in (TrendProfile.MANUAL, TrendProfile.XAG_1H, TrendProfile.XAG_1D):
        for seed, drift in ((7, 0.10), (23, -0.02)):
            frame = _frame(seed, 150, drift=drift)
            records = frame.to_dict("records")
            confirmed = [r for r in records if r["is_closed"] and r["is_complete"]]
            engine = RawIndicatorDashboardEngine(RawIndicatorConfig(profile=profile))
            incremental = [engine.update(r) for r in records]
            batch = RawIndicatorDashboardEngine(RawIndicatorConfig(profile=profile))._compute_frame(
                pd.DataFrame(confirmed)
            )
            pointer = 0
            for snapshot in incremental:
                if pointer < len(batch) and _same(snapshot, batch[pointer]):
                    pointer += 1
                    continue
                if snapshot.data_quality.value in {"INCOMPLETE_BAR", "SOURCE_GAP"}:
                    continue
                raise AssertionError(
                    f"incremental snapshot diverged from vectorized frame "
                    f"(profile={profile}, seed={seed}, index={pointer})"
                )
            assert pointer == len(batch)


def test_volatility_core_series_state_is_chunk_invariant() -> None:
    rows = _frame(5, 200).to_dict("records")
    for profile in ("Hassas", "Dengeli", "Seçici"):
        cfg = VolatilityBandsConfig(profile=profile, timeframe="2h")
        whole = CoreEngine(cfg)
        full = [whole.update(r) for r in rows]

        chunked = CoreEngine(cfg)
        head = [chunked.update(r) for r in rows[:75]]
        tail = [chunked.update(r) for r in rows[75:]]

        assert _same(head + tail, full)
        assert _same(chunked.export, whole.export)
        assert _same(chunked._atr_values(), whole._atr_values())


def test_direction_transition_cache_rebuild_matches_incremental_feed() -> None:
    rows = _frame(9, 160).to_dict("records")
    engine = VolatilityDirectionTransitionEngine(VolatilityBandsConfig(profile="Dengeli", timeframe="2h"))
    for row in rows:
        engine.update(row)

    bypassed = VolatilityDirectionTransitionEngine(VolatilityBandsConfig(profile="Dengeli", timeframe="2h"))
    bypassed._rows = [dict(r) for r in engine._rows]
    bypassed._ensure_caches()
    assert bypassed._closes_cache == engine._closes_cache
    assert bypassed._tr_cache == engine._tr_cache
    assert bypassed._atr_cache == engine._atr_cache
