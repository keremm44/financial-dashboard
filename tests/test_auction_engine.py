import pandas as pd
import pytest

from financial_dashboard.engines.auction_engine import AuctionConfig, AuctionProfile, AuctionVolumeProfileEngine, _nodes, build_profile
from financial_dashboard.engines.models import Direction


TZ = "Europe/Istanbul"


def bar(i: int, *, open_: float, high: float, low: float, close: float, volume: float, closed: bool = True):
    return {
        "timestamp": pd.Timestamp("2026-01-02 10:00", tz=TZ) + pd.Timedelta(hours=i),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_closed": closed,
    }


def balanced_frame(n: int = 40) -> pd.DataFrame:
    rows = []
    for i in range(n):
        center = 100.0 + ((i % 5) - 2) * 0.10
        rows.append(bar(i, open_=center - 0.10, high=center + 0.60, low=center - 0.60, close=center + 0.10, volume=1000.0 + (i % 7) * 100.0))
    return pd.DataFrame(rows)


def rising_frame(n: int = 55) -> pd.DataFrame:
    rows = []
    for i in range(n):
        base = 95.0 + i * 0.20
        rows.append(bar(i, open_=base, high=base + 0.80, low=base - 0.30, close=base + 0.55, volume=1200.0 + i * 20.0))
    return pd.DataFrame(rows)


def test_profile_preserves_source_volume_and_builds_value_area():
    frame = balanced_frame(40)
    config = AuctionConfig(timeframe="1h")
    profile = build_profile(frame.to_dict("records"), config)
    assert profile.valid
    assert profile.allocated_volume == pytest.approx(profile.source_volume, rel=1e-9, abs=1e-6)
    assert profile.allocation_error_pct == pytest.approx(0.0, abs=1e-8)
    assert profile.val_price < profile.poc_price < profile.vah_price
    assert profile.value_area_coverage_pct >= config.value_area_percent


def test_hvn_is_exported_as_contiguous_price_band_not_single_bin():
    volumes = (1.0, 2.0, 4.0, 8.0, 10.0, 9.0, 8.0, 3.0, 2.0, 1.0)
    profile = AuctionProfile(
        valid=True,
        bars_used=50,
        low_price=100.0,
        high_price=110.0,
        bin_width=1.0,
        source_volume=sum(volumes),
        allocated_volume=sum(volumes),
        allocation_error_pct=0.0,
        poc_bin=4,
        poc_price=104.5,
        val_bin=2,
        vah_bin=7,
        val_price=102.0,
        vah_price=108.0,
        value_area_coverage_pct=72.0,
        max_bin_volume=max(volumes),
        volumes=volumes,
    )
    hvn, _ = _nodes(profile, AuctionConfig(timeframe="1h"))
    assert hvn
    node = hvn[0]
    assert node.low_bin < node.center_bin or node.high_bin > node.center_bin
    assert node.low_price == pytest.approx(100.0 + node.low_bin)
    assert node.high_price == pytest.approx(100.0 + node.high_bin + 1.0)
    assert node.low_price <= node.center_price <= node.high_price


def test_lvn_can_expand_into_contiguous_valley_band_when_shoulders_allow_it():
    volumes = (10.0, 9.0, 7.0, 3.0, 2.0, 2.0, 3.0, 7.0, 9.0, 10.0)
    profile = AuctionProfile(
        valid=True,
        bars_used=50,
        low_price=100.0,
        high_price=110.0,
        bin_width=1.0,
        source_volume=sum(volumes),
        allocated_volume=sum(volumes),
        allocation_error_pct=0.0,
        poc_bin=1,
        poc_price=101.5,
        val_bin=1,
        vah_bin=8,
        val_price=101.0,
        vah_price=109.0,
        value_area_coverage_pct=72.0,
        max_bin_volume=max(volumes),
        volumes=volumes,
    )
    _, lvn = _nodes(profile, AuctionConfig(timeframe="1h"))
    assert lvn
    node = lvn[0]
    assert node.low_bin < node.center_bin or node.high_bin > node.center_bin
    assert node.local_depth >= 0.0
    assert node.low_price <= node.center_price <= node.high_price


def test_node_bands_respect_minimum_separation_and_do_not_overlap():
    volumes = (1.0, 8.0, 10.0, 8.0, 1.0, 8.0, 10.0, 8.0, 1.0, 1.0, 1.0, 1.0)
    profile = AuctionProfile(
        valid=True,
        bars_used=50,
        low_price=100.0,
        high_price=112.0,
        bin_width=1.0,
        source_volume=sum(volumes),
        allocated_volume=sum(volumes),
        allocation_error_pct=0.0,
        poc_bin=2,
        poc_price=102.5,
        val_bin=1,
        vah_bin=8,
        val_price=101.0,
        vah_price=109.0,
        value_area_coverage_pct=72.0,
        max_bin_volume=max(volumes),
        volumes=volumes,
    )
    config = AuctionConfig(timeframe="1h", max_hvn_nodes=3)
    hvn, _ = _nodes(profile, config)
    for left, right in zip(hvn, hvn[1:]):
        assert left.high_bin + config.preset.node_min_separation_bins < right.low_bin or right.high_bin + config.preset.node_min_separation_bins < left.low_bin


def test_engine_exports_core_profile_and_primary_zone():
    engine = AuctionVolumeProfileEngine(AuctionConfig(timeframe="1h"))
    history = engine.replay(balanced_frame(40))
    assert len(history) == 40
    result = history[-1]
    export = engine.export_contract
    assert export is not None
    assert export.poc is not None
    assert export.vah is not None
    assert export.val is not None
    assert export.primary_zone_kind in {"POC", "POC_HVN", "HVN", "LVN", "VAH", "VAL"}
    assert 0.0 <= result.quality <= 100.0
    assert result.levels["poc"] == pytest.approx(export.poc)


def test_replay_matches_incremental_exactly():
    frame = rising_frame(55)
    replay_engine = AuctionVolumeProfileEngine(AuctionConfig(timeframe="1h"))
    replay_history = replay_engine.replay(frame)

    incremental_engine = AuctionVolumeProfileEngine(AuctionConfig(timeframe="1h"))
    incremental_history = [incremental_engine.update(row) for _, row in frame.iterrows()]

    assert replay_history == incremental_history
    assert replay_engine.snapshot() == incremental_engine.snapshot()
    assert replay_engine.export_contract == incremental_engine.export_contract


def test_open_bar_is_preview_only_and_does_not_mutate_state():
    engine = AuctionVolumeProfileEngine(AuctionConfig(timeframe="1h"))
    engine.replay(balanced_frame(35))
    before_snapshot = engine.snapshot()
    before_export = engine.export_contract
    preview = bar(35, open_=100.0, high=150.0, low=50.0, close=140.0, volume=999999.0, closed=False)
    returned = engine.update(preview)
    assert returned == before_snapshot
    assert engine.snapshot() == before_snapshot
    assert engine.export_contract == before_export


def test_future_bars_do_not_rewrite_prefix_history():
    frame = rising_frame(55)
    prefix = frame.iloc[:35].copy()
    prefix_engine = AuctionVolumeProfileEngine(AuctionConfig(timeframe="1h"))
    prefix_history = prefix_engine.replay(prefix)

    full_engine = AuctionVolumeProfileEngine(AuctionConfig(timeframe="1h"))
    full_history = full_engine.replay(frame)

    assert full_history[: len(prefix_history)] == prefix_history


def test_missing_volume_fails_closed():
    engine = AuctionVolumeProfileEngine(AuctionConfig(timeframe="1h"))
    with pytest.raises(ValueError):
        engine.update({"timestamp": pd.Timestamp("2026-01-02", tz=TZ), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "is_closed": True})


def test_zero_volume_returns_unavailable_not_false_direction():
    engine = AuctionVolumeProfileEngine(AuctionConfig(timeframe="1h"))
    result = engine.update(bar(0, open_=100.0, high=101.0, low=99.0, close=100.5, volume=0.0))
    assert result.state == "AUCTION_UNAVAILABLE"
    assert result.direction is Direction.NEUTRAL
    assert result.quality == 0.0


def test_timeframe_presets_are_distinct():
    assert AuctionConfig(timeframe="1h").preset.lookback != AuctionConfig(timeframe="4h").preset.lookback
    assert AuctionConfig(timeframe="2h").preset.bins != AuctionConfig(timeframe="1d").preset.bins
