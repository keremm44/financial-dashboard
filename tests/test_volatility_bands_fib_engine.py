from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from financial_dashboard.engines.volatility_bands_fib_engine import (
    BandAgreement,
    DataQualityStatus,
    VolatilityBandsConfig,
    VolatilityBandsFibEngine,
    VolatilityState,
)

TZ = "Europe/Istanbul"


def _frame(n: int = 160) -> pd.DataFrame:
    ts = pd.date_range("2026-01-02 10:00", periods=n, freq="2h", tz=TZ)
    x = np.linspace(100.0, 140.0, n) + np.sin(np.arange(n) / 5.0) * 1.5
    return pd.DataFrame({
        "timestamp": ts,
        "open": x - 0.20,
        "high": x + 0.80,
        "low": x - 1.00,
        "close": x,
        "volume": 1000.0,
        "is_closed": True,
        "is_complete": True,
    })


def test_pine_timeframe_contract_allows_only_2h_4h_daily() -> None:
    for tf in ("2h", "4h", "1d", "d"):
        VolatilityBandsConfig(timeframe=tf)
    with pytest.raises(ValueError, match="only 2h, 4h and 1d"):
        VolatilityBandsConfig(timeframe="1h")


def test_warmup_keeps_exports_unavailable_until_minimum_history() -> None:
    engine = VolatilityBandsFibEngine()
    engine.replay(_frame(119))
    assert engine.last_data_quality == DataQualityStatus.WARMUP
    assert engine.export.regime is None
    assert engine.export.quality is None
    assert engine.export.band_state is None
    assert engine.export.band_agreement is None


def test_ready_snapshot_uses_source_export_domain_only() -> None:
    engine = VolatilityBandsFibEngine()
    result = engine.replay(_frame(160))[-1]
    assert engine.last_data_quality == DataQualityStatus.OK
    assert engine.export.regime in {state.value for state in VolatilityState if state != VolatilityState.PENDING}
    assert engine.export.direction in {-2.0, -1.0, 0.0, 1.0, 2.0}
    assert engine.export.quality is not None
    assert engine.export.band_state is not None
    assert engine.export.band_agreement in {x.value for x in BandAgreement}
    assert engine.export.fib_state is None
    assert 0.0 <= float(result.quality) <= 100.0


def test_open_bar_does_not_mutate_confirmed_snapshot() -> None:
    engine = VolatilityBandsFibEngine()
    frame = _frame(160)
    engine.replay(frame)
    before = engine.snapshot()
    before_export = engine.export
    row = frame.iloc[-1].to_dict()
    row["timestamp"] = pd.Timestamp("2026-09-01 12:00", tz=TZ)
    row["close"] = 9999.0
    row["high"] = 10000.0
    row["is_closed"] = False
    assert engine.update(row) == before
    assert engine.snapshot() == before
    assert engine.export == before_export
    assert engine.last_data_quality == DataQualityStatus.INCOMPLETE_BAR


def test_incomplete_source_bar_is_explicit_gap_and_does_not_mutate() -> None:
    engine = VolatilityBandsFibEngine()
    frame = _frame(160)
    engine.replay(frame)
    before = engine.snapshot()
    row = frame.iloc[-1].to_dict()
    row["timestamp"] = pd.Timestamp("2026-09-01 14:00", tz=TZ)
    row["is_complete"] = False
    engine.update(row)
    assert engine.snapshot() == before
    assert engine.last_data_quality == DataQualityStatus.SOURCE_GAP


def test_replay_matches_incremental_updates() -> None:
    frame = _frame(170)
    replay_engine = VolatilityBandsFibEngine()
    replay_result = replay_engine.replay(frame)[-1]
    incremental = VolatilityBandsFibEngine()
    for _, bar in frame.iterrows():
        incremental.update(bar)
    assert incremental.snapshot() == replay_result
    assert incremental.export == replay_engine.export


def test_future_tail_cannot_rewrite_historical_prefix() -> None:
    frame = _frame(180)
    prefix = frame.iloc[:150].copy()
    a = VolatilityBandsFibEngine()
    a.replay(prefix)
    snapshot_a = a.snapshot()
    b = VolatilityBandsFibEngine()
    results = b.replay(frame)
    assert results[149] == snapshot_a


def test_profile_selection_uses_source_thresholds() -> None:
    sensitive = VolatilityBandsFibEngine(VolatilityBandsConfig(profile="Hassas"))
    balanced = VolatilityBandsFibEngine(VolatilityBandsConfig(profile="Dengeli"))
    selective = VolatilityBandsFibEngine(VolatilityBandsConfig(profile="Seçici"))
    assert sensitive._p["expand_atr"] == pytest.approx(1.03)
    assert balanced._p["expand_atr"] == pytest.approx(1.08)
    assert selective._p["expand_atr"] == pytest.approx(1.14)
    assert (sensitive._p["confirm"], balanced._p["confirm"], selective._p["confirm"]) == (2, 2, 3)
