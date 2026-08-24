from __future__ import annotations

import math

import pandas as pd
import pytest

from financial_dashboard.engines.volatility_bands_fib_engine import (
    VolatilityBandsConfig,
    _rma,
    _rolling_std,
    _sma,
)
from financial_dashboard.engines.volatility_direction_runtime import (
    RuntimeVolatilityDirectionTransitionEngine,
)
from financial_dashboard.engines.volatility_direction_transition import (
    VolatilityDirectionTransitionEngine,
)


def _frame(count: int = 132) -> pd.DataFrame:
    rows = []
    base = pd.Timestamp("2025-01-01", tz="UTC")
    close = 100.0
    for i in range(count):
        drift = 0.16 if (i // 17) % 2 == 0 else -0.11
        wave = math.sin(i / 4.5) * 0.45
        previous = close
        close = previous + drift + wave * 0.12
        open_ = previous + math.sin(i / 3.0) * 0.07
        span = 0.85 + abs(math.sin(i / 6.0)) * 0.35
        rows.append(
            {
                "timestamp": base + pd.Timedelta(hours=2 * i),
                "open": open_,
                "high": max(open_, close) + span * 0.55,
                "low": min(open_, close) - span * 0.45,
                "close": close,
                "volume": 1_000_000.0 + i * 750.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return pd.DataFrame(rows)


def test_runtime_volatility_matches_canonical_snapshot_stream() -> None:
    frame = _frame()
    config = VolatilityBandsConfig(profile="Dengeli", timeframe="2h")
    canonical = VolatilityDirectionTransitionEngine(config)
    runtime = RuntimeVolatilityDirectionTransitionEngine(config)

    for row in frame.to_dict("records"):
        expected = canonical.update(row)
        actual = runtime.update(row)
        assert actual.confirmed_export == expected.confirmed_export
        assert actual.early == expected.early
        assert actual.core_result is not None
        assert expected.core_result is not None
        assert actual.core_result.state == expected.core_result.state
        assert actual.core_result.direction == expected.core_result.direction
        assert actual.core_result.score == expected.core_result.score
        assert actual.core_result.quality == pytest.approx(expected.core_result.quality, abs=1e-12)
        assert actual.core_result.reasons == expected.core_result.reasons


def test_runtime_volatility_technical_series_match_batch_reference() -> None:
    frame = _frame()
    config = VolatilityBandsConfig(profile="Dengeli", timeframe="2h")
    runtime = RuntimeVolatilityDirectionTransitionEngine(config)
    runtime.replay(frame)
    core = runtime.canonical_engine

    highs = [float(value) for value in frame["high"]]
    lows = [float(value) for value in frame["low"]]
    closes = [float(value) for value in frame["close"]]
    tr = []
    for i in range(len(frame)):
        if i == 0:
            tr.append(highs[i] - lows[i])
        else:
            tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))

    expected_atr = _rma(tr, core.ATR_LENGTH)
    expected_atr_avg = _sma(expected_atr, core.ATR_AVERAGE_LENGTH)
    expected_basis = _sma(closes, core.BOLLINGER_LENGTH)
    expected_std = _rolling_std(closes, core.BOLLINGER_LENGTH)

    assert core.runtime_atr == pytest.approx(expected_atr, abs=1e-12, nan_ok=True)
    for actual, expected in zip(core._runtime_atr_avg, expected_atr_avg):
        assert actual == pytest.approx(expected, abs=1e-12) if expected is not None else actual is None
    for actual, expected in zip(core._runtime_basis, expected_basis):
        assert actual == pytest.approx(expected, abs=1e-12) if expected is not None else actual is None
    for actual, expected in zip(core._runtime_stdev, expected_std):
        assert actual == pytest.approx(expected, abs=1e-12) if expected is not None else actual is None
    assert len(core._runtime_calc) == len(frame)
