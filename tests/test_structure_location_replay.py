from __future__ import annotations

from datetime import datetime

import pandas as pd

import financial_dashboard.structure_location_replay as replay_module
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.data.pipeline import MarketDataPipeline
from financial_dashboard.engines.market_structure import MarketStructureConfig
from financial_dashboard.engines.market_structure_state import BreakConfig, EVENT_BOS, EVENT_CHOCH
from financial_dashboard.engines.structure_location import StructureLocationOutcomeStatus
from financial_dashboard.engines.support_resistance_engine import SupportResistanceConfig
from financial_dashboard.mtf_replay import FOUNDATION_MARKET_STRUCTURE_TIMEFRAMES
from financial_dashboard.structure_location_replay import (
    CausalBarClock,
    CachedStructureLocationMTFRunner,
    replay_foundation_structure_location,
)
from tests.test_offline_recovery_and_mtf import _MutableProvider, _bist_5m


def _store(tmp_path) -> ParquetOHLCVStore:
    store = ParquetOHLCVStore(tmp_path)
    MarketDataPipeline(_MutableProvider(_bist_5m()), store).refresh_bist_5m(
        symbol="THYAO",
        start=datetime.fromisoformat("2026-07-20T10:00:00+03:00"),
        end=datetime.fromisoformat("2026-08-04T18:00:00+03:00"),
    )
    return store


def test_combined_foundation_replay_keeps_each_domain_and_timeframe_independently_visible(tmp_path) -> None:
    run = replay_foundation_structure_location(_store(tmp_path), symbol="THYAO")

    assert run.timeframes == FOUNDATION_MARKET_STRUCTURE_TIMEFRAMES
    assert tuple(run.replays) == run.timeframes
    for timeframe, replay in run.replays.items():
        assert replay.timeframe == timeframe
        assert replay.market_structure.timeframe == timeframe
        assert replay.support_resistance.timeframe == timeframe
        assert replay.market_structure.bar_count == replay.support_resistance.bar_count
        assert replay.market_structure.bar_count == len(replay.input_batch.frame)
        assert replay.market_structure.result is not None
        assert replay.support_resistance.result is not None
        assert replay.support_resistance.export.contract_version == 2
        assert all(zone.symbol == "THYAO" for zone in replay.support_resistance.zones)
        assert all(zone.timeframe == timeframe for zone in replay.support_resistance.zones)
        assert all(
            zone.zone_uid.startswith(f"THYAO:{timeframe}:")
            for zone in replay.support_resistance.zones
        )
        assert all(
            event.event_uid.startswith(f"THYAO:{timeframe}:")
            for event in replay.support_resistance.lifecycle_events
        )
        assert all(
            event.confirmation_close is not None
            for event in replay.market_structure.events
        )

    eligible_events = [
        event
        for replay in run.replays.values()
        for event in replay.market_structure.events
        if event.event_type in {EVENT_BOS, EVENT_CHOCH}
    ]
    assert len(run.location_outcomes) == len(eligible_events)
    assert {outcome.event_uid for outcome in run.location_outcomes} == {
        event.event_uid for event in eligible_events
    }
    assert all(
        outcome.status
        in {
            StructureLocationOutcomeStatus.LINKED,
            StructureLocationOutcomeStatus.COMPUTED_NO_CAUSAL_ZONE_MATCH,
        }
        for outcome in run.location_outcomes
    )
    assert run.event_zone_links == tuple(
        sorted(
            (
                link
                for outcome in run.location_outcomes
                for link in outcome.links
            ),
            key=lambda link: (
                pd.Timestamp(link.event_available_at),
                link.event_uid,
                -link.score,
                link.zone_uid,
            ),
        )
    )
    assert all(
        link.zone_available_at <= link.event_available_at
        for link in run.event_zone_links
    )
    assert all(len(cluster.timeframes) >= 2 for cluster in run.confluence)


def test_combined_replay_is_restart_deterministic(tmp_path) -> None:
    store = _store(tmp_path)

    first = CachedStructureLocationMTFRunner(store).run_foundation(symbol="THYAO")
    restarted = CachedStructureLocationMTFRunner(
        ParquetOHLCVStore(tmp_path)
    ).run_foundation(symbol="THYAO")

    assert first.confluence == restarted.confluence
    assert first.event_zone_links == restarted.event_zone_links
    for timeframe in first.timeframes:
        first_replay = first.replay_for(timeframe)
        restarted_replay = restarted.replay_for(timeframe)
        assert first_replay.market_structure == restarted_replay.market_structure
        assert first_replay.support_resistance == restarted_replay.support_resistance


def test_causal_clock_supports_explicit_session_daily_close_without_changing_intraday_math() -> None:
    clock = CausalBarClock(
        durations=(
            ("1h", pd.Timedelta(hours=1)),
            ("1d", pd.Timedelta(hours=8)),
        )
    )
    start = pd.Timestamp("2026-08-21T10:00:00+03:00")

    assert clock.available_at(start, "1h") == pd.Timestamp("2026-08-21T11:00:00+03:00")
    assert clock.available_at(start, "1D") == pd.Timestamp("2026-08-21T18:00:00+03:00")


def test_runner_wires_explicit_engine_configs_into_each_independent_replay(
    tmp_path,
    monkeypatch,
) -> None:
    market_config = MarketStructureConfig(profile="Hassas")
    break_config = BreakConfig(profile="Hassas")
    support_config = SupportResistanceConfig(min_range_age=25)
    captured_market: list[tuple[MarketStructureConfig | None, BreakConfig | None]] = []
    captured_support: list[SupportResistanceConfig | None] = []
    real_market_engine = replay_module.MarketStructureEngine
    real_support_engine = replay_module.SupportResistanceRangeEngine

    def market_factory(*, config=None, break_config=None):
        captured_market.append((config, break_config))
        return real_market_engine(config=config, break_config=break_config)

    def support_factory(*, config=None):
        captured_support.append(config)
        return real_support_engine(config=config)

    monkeypatch.setattr(replay_module, "MarketStructureEngine", market_factory)
    monkeypatch.setattr(replay_module, "SupportResistanceRangeEngine", support_factory)
    runner = CachedStructureLocationMTFRunner(
        _store(tmp_path),
        market_structure_config=market_config,
        break_config=break_config,
        support_resistance_config=support_config,
    )

    runner.run(symbol="THYAO", timeframes=("1h", "30m"))

    assert captured_market == [
        (market_config, break_config),
        (market_config, break_config),
    ]
    assert captured_support == [support_config, support_config]
