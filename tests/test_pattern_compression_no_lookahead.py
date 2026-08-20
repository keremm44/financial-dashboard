from __future__ import annotations

import random

import pandas as pd

from financial_dashboard.engines import PatternCompressionEngine


def _random_bars(count: int, *, seed: int) -> list[dict]:
    random.seed(seed)
    price = 100.0
    bars: list[dict] = []
    for index in range(count):
        move = random.uniform(-1.25, 1.25)
        open_ = price
        close = max(1.0, open_ + move)
        high = max(open_, close) + random.uniform(0.05, 0.75)
        low = min(open_, close) - random.uniform(0.05, 0.75)
        bars.append(
            {
                "timestamp": pd.Timestamp("2026-02-01", tz="UTC") + pd.Timedelta(hours=index),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": random.uniform(600.0, 2200.0),
                "is_closed": True,
            }
        )
        price = close
    return bars


def test_future_bars_cannot_change_any_prefix_result() -> None:
    bars = _random_bars(180, seed=2026081901)
    prefix_length = 110

    prefix_engine = PatternCompressionEngine()
    prefix_results = [prefix_engine.update(bar) for bar in bars[:prefix_length]]

    full_engine = PatternCompressionEngine()
    full_results = [full_engine.update(bar) for bar in bars]

    assert full_results[:prefix_length] == prefix_results


def test_interleaved_open_bar_does_not_change_next_closed_result_or_state() -> None:
    bars = _random_bars(120, seed=2026081902)

    control = PatternCompressionEngine()
    control_results = [control.update(bar) for bar in bars]

    with_preview = PatternCompressionEngine()
    preview_results = []
    for index, bar in enumerate(bars):
        if index == 75:
            before = with_preview.snapshot()
            preview = dict(bar)
            preview["timestamp"] = bar["timestamp"] - pd.Timedelta(minutes=15)
            preview["high"] = bar["high"] + 50.0
            preview["low"] = max(0.01, bar["low"] - 50.0)
            preview["close"] = bar["high"] + 40.0
            preview["is_closed"] = False
            returned = with_preview.update(preview)
            assert returned == before
            assert with_preview.snapshot() == before
        preview_results.append(with_preview.update(bar))

    assert preview_results == control_results
    assert with_preview.active_candidate == control.active_candidate
    assert with_preview.pivot_store == control.pivot_store
    assert with_preview.export_contract == control.export_contract
