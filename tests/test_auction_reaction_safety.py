import pandas as pd
import pytest

import financial_dashboard.engines.auction_engine as auction
from financial_dashboard.engines.auction_engine import AuctionConfig, AuctionProfile, AuctionVolumeProfileEngine


TZ = "Europe/Istanbul"


def _bar(i: int, *, close: float = 100.0, high: float | None = None, low: float | None = None, volume: float = 1000.0, closed: bool = True):
    high = close + 0.5 if high is None else high
    low = close - 0.5 if low is None else low
    return {
        "timestamp": pd.Timestamp("2026-01-02 10:00", tz=TZ) + pd.Timedelta(hours=i),
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_closed": closed,
    }


def _dummy_profile() -> AuctionProfile:
    return AuctionProfile(
        valid=True,
        bars_used=20,
        low_price=95.0,
        high_price=105.0,
        bin_width=0.5,
        source_volume=20_000.0,
        allocated_volume=20_000.0,
        allocation_error_pct=0.0,
        poc_bin=10,
        poc_price=100.25,
        val_bin=8,
        vah_bin=12,
        val_price=99.0,
        vah_price=101.5,
        value_area_coverage_pct=70.0,
        max_bin_volume=2000.0,
        volumes=tuple([1000.0] * 20),
    )


def test_reaction_reference_excludes_entire_acceptance_evidence_window(monkeypatch):
    config = AuctionConfig(timeframe="1h")
    acceptance_bars = config.preset.acceptance_bars
    rows = [_bar(i, close=100.0 + i * 0.01) for i in range(12)]
    captured = []

    def fake_build_profile(profile_rows, _config):
        captured.append(tuple(r["timestamp"] for r in profile_rows))
        return _dummy_profile()

    monkeypatch.setattr(auction, "build_profile", fake_build_profile)
    auction._reaction(rows, config, atr=1.0)

    assert len(captured) == 1
    assert captured[0] == tuple(r["timestamp"] for r in rows[:-acceptance_bars])
    assert all(r["timestamp"] not in captured[0] for r in rows[-acceptance_bars:])


def test_acceptance_uses_only_current_and_past_closed_evidence(monkeypatch):
    config = AuctionConfig(timeframe="1h")
    p = config.preset
    ref = _dummy_profile()
    monkeypatch.setattr(auction, "build_profile", lambda *_args, **_kwargs: ref)

    margin = max(1.0 * p.acceptance_margin_atr, ref.bin_width * 0.10, config.min_tick * 2.0)
    accepted_close = ref.vah_price + margin + 0.20
    rows = [_bar(i, close=100.0) for i in range(8)]
    rows.extend(_bar(8 + j, close=accepted_close, high=accepted_close + 0.2, low=accepted_close - 0.2) for j in range(p.acceptance_bars))

    reaction = auction._reaction(rows, config, atr=1.0)
    assert reaction.state == "ACCEPT_UP"
    assert reaction.direction == 1
    assert reaction.evidence_bars == p.acceptance_bars


def test_future_tail_cannot_rewrite_already_emitted_reaction_prefix():
    config = AuctionConfig(timeframe="1h")
    prefix = pd.DataFrame([_bar(i, close=100.0 + (i % 4) * 0.05) for i in range(45)])
    future = pd.DataFrame([_bar(45 + i, close=120.0 + i, high=122.0 + i, low=118.0 + i, volume=5000.0) for i in range(8)])
    full = pd.concat([prefix, future], ignore_index=True)

    prefix_engine = AuctionVolumeProfileEngine(config)
    prefix_history = prefix_engine.replay(prefix)

    full_engine = AuctionVolumeProfileEngine(config)
    full_history = full_engine.replay(full)

    assert full_history[: len(prefix_history)] == prefix_history


def test_open_preview_cannot_create_acceptance_or_rejection_event():
    config = AuctionConfig(timeframe="1h")
    engine = AuctionVolumeProfileEngine(config)
    frame = pd.DataFrame([_bar(i, close=100.0 + (i % 3) * 0.05) for i in range(40)])
    engine.replay(frame)
    before_snapshot = engine.snapshot()
    before_export = engine.export_contract

    preview = _bar(40, close=150.0, high=170.0, low=80.0, volume=999999.0, closed=False)
    returned = engine.update(preview)

    assert returned == before_snapshot
    assert engine.snapshot() == before_snapshot
    assert engine.export_contract == before_export


def test_rejection_requires_same_closed_bar_excursion_and_reentry(monkeypatch):
    config = AuctionConfig(timeframe="1h")
    p = config.preset
    ref = _dummy_profile()
    monkeypatch.setattr(auction, "build_profile", lambda *_args, **_kwargs: ref)

    reject_excursion = max(1.0 * p.rejection_excursion_atr, ref.bin_width * 0.12, config.min_tick * 2.0)
    reject_reentry = max(1.0 * p.rejection_reentry_atr, config.min_tick * 2.0)
    rows = [_bar(i, close=100.0) for i in range(8)]
    current = _bar(
        8,
        close=ref.vah_price - reject_reentry - 0.05,
        high=ref.vah_price + reject_excursion + 0.05,
        low=ref.vah_price - reject_reentry - 0.10,
    )
    rows.append(current)

    reaction = auction._reaction(rows, config, atr=1.0)
    assert reaction.state == "REJECT_UP"
    assert reaction.direction == -1
    assert reaction.excursion_atr > 0.0
    assert reaction.reentry_atr > 0.0
