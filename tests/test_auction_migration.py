from __future__ import annotations

from financial_dashboard.engines import auction_engine as auc
from financial_dashboard.engines.auction_engine import AuctionConfig, AuctionProfile, _migration, _migration_step


def _profile(*, poc: float, val: float, vah: float, bin_width: float = 1.0) -> AuctionProfile:
    low = val - 5.0
    high = vah + 5.0
    return AuctionProfile(
        valid=True,
        bars_used=50,
        low_price=low,
        high_price=high,
        bin_width=bin_width,
        source_volume=1000.0,
        allocated_volume=1000.0,
        allocation_error_pct=0.0,
        poc_bin=5,
        poc_price=poc,
        val_bin=3,
        vah_bin=7,
        val_price=val,
        vah_price=vah,
        value_area_coverage_pct=70.0,
        max_bin_volume=100.0,
        volumes=(10.0,) * 10,
    )


def test_migration_step_requires_value_center_plus_boundary_agreement():
    config = AuctionConfig(timeframe="1h")
    older = _profile(poc=100.0, val=98.0, vah=102.0, bin_width=0.10)

    # Both value center and boundaries move up enough -> value migration step up.
    coherent = _profile(poc=100.5, val=98.5, vah=102.5, bin_width=0.10)
    poc_dir, value_dir, *_ = _migration_step(coherent, older, config, atr=1.0)
    assert poc_dir == 1
    assert value_dir == 1

    # Only VAH expands. Center movement stays below threshold and VAL is unchanged,
    # therefore one boundary alone must not be called migration.
    one_boundary = _profile(poc=100.0, val=98.0, vah=102.2, bin_width=0.10)
    _, value_dir, *_ = _migration_step(one_boundary, older, config, atr=1.0)
    assert value_dir == 0


def test_two_consecutive_up_steps_are_required_for_confirmed_migration(monkeypatch):
    config = AuctionConfig(timeframe="1h")
    lag = config.preset.migration_lag_bars
    rows = [{"i": i} for i in range(lag * 2 + 3)]

    older = _profile(poc=100.0, val=98.0, vah=102.0, bin_width=0.10)
    previous = _profile(poc=100.5, val=98.5, vah=102.5, bin_width=0.10)
    current = _profile(poc=101.0, val=99.0, vah=103.0, bin_width=0.10)

    def fake_build_profile(prefix, _config):
        if len(prefix) == len(rows) - lag:
            return previous
        if len(prefix) == len(rows) - lag * 2:
            return older
        raise AssertionError(f"unexpected prefix length {len(prefix)}")

    monkeypatch.setattr(auc, "build_profile", fake_build_profile)
    migration = _migration(rows, config, current, atr=1.0)

    assert migration.state == "MIG_UP"
    assert migration.direction == 1
    assert migration.confirmed is True


def test_one_up_step_only_is_developing_not_confirmed(monkeypatch):
    config = AuctionConfig(timeframe="1h")
    lag = config.preset.migration_lag_bars
    rows = [{"i": i} for i in range(lag * 2 + 3)]

    older = _profile(poc=100.0, val=98.0, vah=102.0, bin_width=0.10)
    previous = _profile(poc=100.0, val=98.0, vah=102.0, bin_width=0.10)
    current = _profile(poc=100.5, val=98.5, vah=102.5, bin_width=0.10)

    def fake_build_profile(prefix, _config):
        if len(prefix) == len(rows) - lag:
            return previous
        if len(prefix) == len(rows) - lag * 2:
            return older
        raise AssertionError(f"unexpected prefix length {len(prefix)}")

    monkeypatch.setattr(auc, "build_profile", fake_build_profile)
    migration = _migration(rows, config, current, atr=1.0)

    assert migration.state == "MIG_DEVELOPING_UP"
    assert migration.direction == 0
    assert migration.confirmed is False


def test_migration_uses_only_lagged_prefix_snapshots(monkeypatch):
    config = AuctionConfig(timeframe="1h")
    lag = config.preset.migration_lag_bars
    rows = [{"i": i} for i in range(30)]
    current = _profile(poc=101.0, val=99.0, vah=103.0, bin_width=0.10)
    seen_lengths: list[int] = []

    def fake_build_profile(prefix, _config):
        seen_lengths.append(len(prefix))
        if len(prefix) == len(rows) - lag:
            return _profile(poc=100.5, val=98.5, vah=102.5, bin_width=0.10)
        if len(prefix) == len(rows) - lag * 2:
            return _profile(poc=100.0, val=98.0, vah=102.0, bin_width=0.10)
        raise AssertionError(f"unexpected prefix length {len(prefix)}")

    monkeypatch.setattr(auc, "build_profile", fake_build_profile)
    _migration(rows, config, current, atr=1.0)

    assert seen_lengths == [len(rows) - lag, len(rows) - lag * 2]
    assert all(length < len(rows) for length in seen_lengths)
