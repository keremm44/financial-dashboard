from __future__ import annotations

import pandas as pd
import pytest

from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.auction_profile_replay import (
    AuctionProfileHistoricalReplayRunner,
    AuctionProfileMTFReplayRunner,
    AuctionProfileReplayRunner,
)
from financial_dashboard.data.analysis_inputs import load_analysis_inputs
from financial_dashboard.engines.auction_engine import AuctionConfig
from financial_dashboard.engines.auction_estimated_profile import (
    AuctionProfileQuality,
    AuctionProfileSource,
    EstimatedAuctionProfileEngine,
)
from _ui_test_data import make_ui_store


def _frame(n: int = 180) -> pd.DataFrame:
    ts = pd.date_range("2026-01-02 10:00", periods=n, freq="1h", tz="Europe/Istanbul")
    rows = []
    price = 100.0
    for i, t in enumerate(ts):
        drift = (i % 17 - 8) * 0.03
        o = price
        c = price + drift + (0.35 if i % 9 < 5 else -0.22)
        h = max(o, c) + 0.8 + (i % 4) * 0.05
        l = min(o, c) - 0.7 - (i % 3) * 0.04
        rows.append({
            "timestamp": t,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 1000.0 + (i % 11) * 130.0,
            "is_closed": True,
            "is_complete": True,
        })
        price = c
    return pd.DataFrame(rows)


def _signature(snapshot) -> tuple[object, ...]:
    return (
        snapshot.as_of,
        snapshot.data_quality,
        snapshot.poc,
        snapshot.vah,
        snapshot.val,
        snapshot.export.reaction_state,
        snapshot.export.migration_state,
        snapshot.export.balance_state,
        snapshot.export.primary_zone_kind,
        tuple((x.kind, x.low_price, x.high_price, x.score) for x in snapshot.export.hvn_nodes),
        tuple((x.kind, x.low_price, x.high_price, x.score) for x in snapshot.export.lvn_nodes),
        snapshot.provenance,
    )


def test_estimated_profile_provenance_never_claims_true_price_at_volume() -> None:
    snapshot = EstimatedAuctionProfileEngine(AuctionConfig(timeframe="1h")).analyze(_frame())
    p = snapshot.provenance
    assert p.source is AuctionProfileSource.OHLCV_ESTIMATED
    assert p.method == "BAR_VOLUME_DISTRIBUTED_BY_HIGH_LOW_BIN_OVERLAP"
    assert p.is_true_price_at_volume is False
    assert p.is_tick_profile is False
    assert p.is_footprint is False
    assert snapshot.data_quality is AuctionProfileQuality.OK


def test_estimated_profile_conserves_source_volume_and_reports_value_area() -> None:
    snapshot = EstimatedAuctionProfileEngine(AuctionConfig(timeframe="1h")).analyze(_frame())
    assert snapshot.profile.valid
    assert snapshot.provenance.allocation_error_pct is not None
    assert snapshot.provenance.allocation_error_pct < 1e-6
    assert snapshot.provenance.value_area_coverage_pct is not None
    assert snapshot.provenance.value_area_coverage_pct >= 70.0
    assert snapshot.poc is not None
    assert snapshot.val is not None
    assert snapshot.vah is not None
    assert snapshot.val <= snapshot.poc <= snapshot.vah


def test_open_and_incomplete_bars_cannot_mutate_estimated_snapshot() -> None:
    base = _frame()
    engine = EstimatedAuctionProfileEngine(AuctionConfig(timeframe="1h"))
    before = engine.analyze(base)
    future = base.iloc[-1].copy()
    future["timestamp"] = pd.Timestamp("2026-12-01 10:00", tz="Europe/Istanbul")
    future["high"] = 10000.0
    future["close"] = 9999.0
    future["is_closed"] = False
    future2 = future.copy()
    future2["timestamp"] = pd.Timestamp("2026-12-01 11:00", tz="Europe/Istanbul")
    future2["is_closed"] = True
    future2["is_complete"] = False
    extended = pd.concat([base, pd.DataFrame([future, future2])], ignore_index=True)
    after = engine.analyze(extended)
    assert _signature(after) == _signature(before)


def test_limited_history_is_factual_not_failure() -> None:
    snapshot = EstimatedAuctionProfileEngine(AuctionConfig(timeframe="1h")).analyze(_frame(40))
    assert snapshot.profile.valid
    assert snapshot.data_quality is AuctionProfileQuality.LIMITED_HISTORY
    assert 0.0 < snapshot.provenance.history_fraction < 1.0


def test_shared_snapshot_mtf_replay_reuses_prepared_batches(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    inputs = load_analysis_inputs(store, symbol="THYAO", timeframes=ANALYSIS_TIMEFRAMES)
    replay = AuctionProfileMTFReplayRunner(store).replay(
        " thyao ",
        timeframes=ANALYSIS_TIMEFRAMES,
        input_snapshot=inputs,
    )
    assert replay.symbol == "THYAO"
    assert replay.timeframes == ANALYSIS_TIMEFRAMES
    for tf in ANALYSIS_TIMEFRAMES:
        item = replay.for_timeframe(tf)
        assert item.input_batch is inputs.for_timeframe(tf).input_batch
        assert item.snapshot.provenance.source is AuctionProfileSource.OHLCV_ESTIMATED


def test_historical_replay_latest_matches_direct_and_is_window_stable(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    direct = AuctionProfileReplayRunner(store).replay("THYAO", timeframe="1h")
    long = AuctionProfileHistoricalReplayRunner(store).replay(
        "THYAO", timeframe="1h", minimum_bars=20, step=1, max_points=30
    )
    short = AuctionProfileHistoricalReplayRunner(store).replay(
        "THYAO", timeframe="1h", minimum_bars=20, step=1, max_points=10
    )
    assert long.points
    assert _signature(long.latest) == _signature(direct.snapshot)
    assert tuple(_signature(p.snapshot) for p in short.points) == tuple(
        _signature(p.snapshot) for p in long.points[-10:]
    )


def test_historical_replay_rejects_invalid_controls(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    runner = AuctionProfileHistoricalReplayRunner(store)
    with pytest.raises(ValueError, match="minimum_bars"):
        runner.replay("THYAO", timeframe="1h", minimum_bars=0)
    with pytest.raises(ValueError, match="step"):
        runner.replay("THYAO", timeframe="1h", step=0)
    with pytest.raises(ValueError, match="max_points"):
        runner.replay("THYAO", timeframe="1h", max_points=0)
