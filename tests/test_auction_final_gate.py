from __future__ import annotations

from financial_dashboard.engines import auction_engine as auc
from financial_dashboard.engines.auction_engine import (
    AuctionBalance,
    AuctionConfig,
    AuctionMigration,
    AuctionNode,
    AuctionProfile,
    AuctionReaction,
    _balance,
    _primary_zone,
    _quality,
)


def _profile(*, poc: float = 100.0, val: float = 98.0, vah: float = 102.0, bin_width: float = 0.10) -> AuctionProfile:
    return AuctionProfile(
        valid=True,
        bars_used=130,
        low_price=96.0,
        high_price=104.0,
        bin_width=bin_width,
        source_volume=13000.0,
        allocated_volume=13000.0,
        allocation_error_pct=0.0,
        poc_bin=20,
        poc_price=poc,
        val_bin=10,
        vah_bin=30,
        val_price=val,
        vah_price=vah,
        value_area_coverage_pct=70.0,
        max_bin_volume=500.0,
        volumes=(100.0,) * 42,
    )


def test_confirmed_imbalance_requires_acceptance_migration_and_outside_price(monkeypatch):
    config = AuctionConfig(timeframe="1h")
    current = _profile(poc=101.0, val=99.0, vah=103.0)
    older = _profile()
    reaction_ref = _profile()
    rows = [{"close": 100.0}] * 20 + [{"close": 103.0}]

    def fake_build_profile(prefix, _config):
        # _balance first asks for the older 2x-lag snapshot, then the frozen reaction ref.
        if len(prefix) == len(rows) - config.preset.migration_lag_bars * 2:
            return older
        if len(prefix) == len(rows) - config.preset.acceptance_bars:
            return reaction_ref
        raise AssertionError(f"unexpected prefix length {len(prefix)}")

    monkeypatch.setattr(auc, "build_profile", fake_build_profile)

    accepted = AuctionReaction("ACCEPT_UP", 1, reaction_ref.vah_price, config.preset.acceptance_bars, 0.5, 0.0)
    migrated = AuctionMigration("MIG_UP", 1, True, 0.5, 0.5, 100.0)
    balance = _balance(rows, config, current, accepted, migrated, atr=1.0)
    assert balance.state == "BAL_IMBALANCE_UP"
    assert balance.direction == 1
    assert balance.confirmed is True

    not_accepted = AuctionReaction("TEST_UP", 0, reaction_ref.vah_price)
    balance = _balance(rows, config, current, not_accepted, migrated, atr=1.0)
    assert balance.state != "BAL_IMBALANCE_UP"
    assert balance.confirmed is False


def test_low_overlap_alone_never_becomes_directional_imbalance(monkeypatch):
    config = AuctionConfig(timeframe="1h")
    current = _profile(poc=110.0, val=108.0, vah=112.0)
    older = _profile(poc=100.0, val=98.0, vah=102.0)
    reaction_ref = _profile(poc=105.0, val=103.0, vah=107.0)
    rows = [{"close": 105.0}] * 20

    def fake_build_profile(prefix, _config):
        if len(prefix) == len(rows) - config.preset.migration_lag_bars * 2:
            return older
        if len(prefix) == len(rows) - config.preset.acceptance_bars:
            return reaction_ref
        raise AssertionError(f"unexpected prefix length {len(prefix)}")

    monkeypatch.setattr(auc, "build_profile", fake_build_profile)
    reaction = AuctionReaction("INSIDE_VALUE", 0)
    migration = AuctionMigration("MIG_STABLE", 0, False, 0.0, 0.0, 0.0)
    balance = _balance(rows, config, current, reaction, migration, atr=1.0)
    assert balance.state == "BAL_TRANSITION"
    assert balance.direction == 0
    assert balance.confirmed is False


def test_quality_and_primary_zone_are_bounded_and_explainable():
    config = AuctionConfig(timeframe="1h")
    profile = _profile()
    hvn = (
        AuctionNode("HVN", 100.0, 99.8, 100.2, 90.0, 20, 19, 21, 1.0, 1.5, 0.0, True),
    )
    lvn = (
        AuctionNode("LVN", 102.5, 102.4, 102.6, 80.0, 32, 32, 32, 0.2, 0.5, 0.7, False),
    )
    reaction = AuctionReaction("REJECT_UP", -1, 102.0, 0, 0.4, 0.2)
    migration = AuctionMigration("MIG_STABLE", 0, False, 0.0, 0.0, 0.0)
    balance = AuctionBalance("BAL_BALANCED", 0, False, 85.0, 0.0, False, False)

    quality, strength = _quality(profile, hvn, lvn, reaction, migration, balance, config)
    primary = _primary_zone(profile, hvn, lvn, balance, close=100.1, atr=1.0, config=config)

    assert 0.0 <= quality <= 100.0
    assert 0.0 <= strength <= 100.0
    assert primary is not None
    assert primary.kind in {"POC_HVN", "POC", "HVN", "LVN", "VAH", "VAL"}
    assert primary.low_price <= primary.center_price <= primary.high_price
    assert 0.0 <= primary.score <= 100.0
    assert primary.distance_atr >= 0.0
