from __future__ import annotations

import pandas as pd

from financial_dashboard.data.engine_input import prepare_engine_input
from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.engines import ParticipationLifecycleConfig, VolumeParticipationConfig
from financial_dashboard.engines.market_structure_events import (
    MarketStructureEventRecord,
    StructureEventConfirmation,
    StructureEventOutcome,
    StructureEventRelevance,
    StructureEventValidity,
)
from financial_dashboard.engines.market_structure_state import BosMaturity, EVENT_BOS
from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.volume_evidence import (
    VolumeEvidenceDataQuality,
)
from financial_dashboard.mtf_replay import (
    CachedMarketStructureMTFRunner,
    MTFReplayResult,
    MarketStructureTimeframeSnapshot,
    TimeframeReplay,
)
from financial_dashboard.structure_location_replay import CachedStructureLocationMTFRunner
from financial_dashboard.volume_mtf_replay import (
    VOLUME_EVIDENCE_TIMEFRAMES,
    VolumeMTFEvidenceReplayRunner,
)


def _config() -> VolumeParticipationConfig:
    return VolumeParticipationConfig(
        minimum_history=8,
        atr_length=3,
        volume_short_length=2,
        volume_average_length=3,
        volume_long_length=5,
        percentile_length=5,
        slope_lookback=1,
        persistence_length=2,
        flow_short_length=2,
        flow_medium_length=3,
        progress_lookback=2,
        participation_minimum_evidence=4,
        participation_confirmation_bars=1,
    )


def _frame(timeframe: str, count: int = 18, *, offset: float = 0.0) -> pd.DataFrame:
    step = {
        "30m": pd.Timedelta(minutes=30),
        "1h": pd.Timedelta(hours=1),
        "2h": pd.Timedelta(hours=2),
        "4h": pd.Timedelta(hours=4),
        "1d": pd.Timedelta(days=1),
    }[timeframe]
    rows: list[dict[str, object]] = []
    close = 100.0 + offset
    for index in range(count):
        open_price = close
        close = open_price + (0.65 if index % 4 else -0.20)
        rows.append(
            {
                "timestamp": pd.Timestamp("2025-01-01") + index * step,
                "open": open_price,
                "high": max(open_price, close) + 0.30,
                "low": min(open_price, close) - 0.25,
                "close": close,
                "volume": 2_000.0 + index * 70.0 + offset,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return pd.DataFrame(rows)


def _event(timestamp: object) -> MarketStructureEventRecord:
    return MarketStructureEventRecord(
        event_uid="ASELS:1h:EXTERNAL:11",
        identity=11,
        scope="EXTERNAL",
        event_type=EVENT_BOS,
        direction=Direction.UP,
        candidate_bar=9,
        event_bar=10,
        candidate_at=pd.Timestamp(timestamp) - pd.Timedelta(hours=1),
        confirmed_at=timestamp,
        broken_swing_identity=5,
        broken_source_bar=7,
        broken_source_at=pd.Timestamp(timestamp) - pd.Timedelta(hours=3),
        broken_level=104.0,
        origin_swing_identity=4,
        origin_source_bar=6,
        origin_source_at=pd.Timestamp(timestamp) - pd.Timedelta(hours=4),
        origin_price=101.0,
        quality=80.0,
        evidence_text="confirmed",
        confirmation_status=StructureEventConfirmation.CONFIRMED,
        validity=StructureEventValidity.VALID,
        relevance=StructureEventRelevance.CURRENT,
        outcome=StructureEventOutcome.OBSERVED,
        symbol="ASELS",
        timeframe="1h",
        bos_maturity=BosMaturity.CONTINUATION,
    )


def _structure_result(
    store: ParquetOHLCVStore,
    *,
    timeframes: tuple[str, ...],
    with_event: bool = False,
) -> MTFReplayResult:
    replays: dict[str, TimeframeReplay] = {}
    snapshots: list[MarketStructureTimeframeSnapshot] = []
    for timeframe in timeframes:
        batch = prepare_engine_input(store.load("ASELS", timeframe))
        events = (
            (_event(batch.frame.iloc[10]["timestamp"]),)
            if with_event and timeframe == "1h"
            else ()
        )
        snapshot = MarketStructureTimeframeSnapshot(
            symbol="ASELS",
            timeframe=timeframe,
            as_of=batch.frame.iloc[-1]["timestamp"],
            bar_count=len(batch.frame),
            result=None,
            export=None,
            events=events,
            external_scope=None,
            internal_scope=None,
        )
        snapshots.append(snapshot)
        replays[timeframe] = TimeframeReplay(
            timeframe=timeframe,
            input_batch=batch,
            results=(),
            snapshot=None,
            structure=snapshot,
        )
    return MTFReplayResult(
        symbol="ASELS",
        timeframes=timeframes,
        replays=replays,
        structure_snapshots=tuple(snapshots),
    )


def test_five_timeframes_replay_independently_with_full_history_and_event_links(tmp_path) -> None:
    store = ParquetOHLCVStore(tmp_path)
    for index, timeframe in enumerate(VOLUME_EVIDENCE_TIMEFRAMES):
        store.merge_and_save(
            _frame(timeframe, offset=index * 10.0),
            symbol="ASELS",
            timeframe=timeframe,
            source="test",
        )
    structure = _structure_result(
        store,
        timeframes=VOLUME_EVIDENCE_TIMEFRAMES,
        with_event=True,
    )
    runner = VolumeMTFEvidenceReplayRunner(
        store,
        config=_config(),
        lifecycle_config=ParticipationLifecycleConfig(pivot_length=2),
    )

    result = runner.replay("ASELS", structure_replay=structure)

    assert result.timeframes == VOLUME_EVIDENCE_TIMEFRAMES
    assert result.total_bar_count == 18 * 5
    for timeframe in VOLUME_EVIDENCE_TIMEFRAMES:
        replay = result.replay_for(timeframe)
        assert replay.timeframe == timeframe
        assert replay.bar_count == 18
        assert len(replay.history) == len(replay.input_batch.frame)
        assert replay.history[0].bar_index == 0
        assert replay.history[-1].bar_index == 17
    one_hour = result.replay_for("1h")
    assert tuple(link.event_uid for link in one_hour.event_links) == (
        "ASELS:1h:EXTERNAL:11",
    )


def test_runner_builds_independent_structure_replay_when_not_supplied(tmp_path) -> None:
    store = ParquetOHLCVStore(tmp_path)
    store.merge_and_save(
        _frame("1h"),
        symbol="ASELS",
        timeframe="1h",
        source="test",
    )
    replay = VolumeMTFEvidenceReplayRunner(
        store,
        config=_config(),
        lifecycle_config=ParticipationLifecycleConfig(pivot_length=2),
    ).replay("ASELS", timeframes=("1h",)).replay_for("1h")

    assert replay.bar_count == 18
    assert replay.latest == replay.history[-1]
    assert all(link.timeframe == "1h" for link in replay.event_links)


def test_incomplete_tail_is_excluded_and_cannot_change_confirmed_history(tmp_path) -> None:
    store = ParquetOHLCVStore(tmp_path)
    complete = _frame("1h")
    open_tail = complete.iloc[-1].copy()
    open_tail["timestamp"] = pd.Timestamp(open_tail["timestamp"]) + pd.Timedelta(hours=1)
    open_tail["close"] = float(open_tail["close"]) + 50.0
    open_tail["high"] = float(open_tail["close"]) + 0.5
    open_tail["is_closed"] = False
    cached = pd.concat([complete, pd.DataFrame([open_tail])], ignore_index=True)
    store.merge_and_save(cached, symbol="ASELS", timeframe="1h", source="test")
    structure = _structure_result(store, timeframes=("1h",))

    runner = VolumeMTFEvidenceReplayRunner(
        store,
        config=_config(),
        lifecycle_config=ParticipationLifecycleConfig(pivot_length=2),
    )
    with_tail = runner.replay(
        "ASELS",
        timeframes=("1h",),
        structure_replay=structure,
    ).replay_for("1h")

    clean_store = ParquetOHLCVStore(tmp_path / "clean")
    clean_store.merge_and_save(complete, symbol="ASELS", timeframe="1h", source="test")
    clean_structure = _structure_result(clean_store, timeframes=("1h",))
    clean = VolumeMTFEvidenceReplayRunner(
        clean_store,
        config=_config(),
        lifecycle_config=ParticipationLifecycleConfig(pivot_length=2),
    ).replay(
        "ASELS",
        timeframes=("1h",),
        structure_replay=clean_structure,
    ).replay_for("1h")

    assert with_tail.replay_data_quality is VolumeEvidenceDataQuality.INCOMPLETE_TAIL
    assert with_tail.excluded_tail_bar_count == 1
    assert with_tail.history == clean.history


def test_changing_one_timeframe_does_not_change_another(tmp_path) -> None:
    store = ParquetOHLCVStore(tmp_path)
    for timeframe in ("1d", "30m"):
        store.merge_and_save(
            _frame(timeframe),
            symbol="ASELS",
            timeframe=timeframe,
            source="test",
        )
    runner = VolumeMTFEvidenceReplayRunner(
        store,
        config=_config(),
        lifecycle_config=ParticipationLifecycleConfig(pivot_length=2),
    )
    before = runner.replay(
        "ASELS",
        timeframes=("1d", "30m"),
        structure_replay=_structure_result(store, timeframes=("1d", "30m")),
    )

    extra = _frame("30m", count=19).iloc[[-1]]
    store.merge_and_save(extra, symbol="ASELS", timeframe="30m", source="test")
    after = runner.replay(
        "ASELS",
        timeframes=("1d", "30m"),
        structure_replay=_structure_result(store, timeframes=("1d", "30m")),
    )

    assert before.replay_for("1d").history == after.replay_for("1d").history
    assert after.replay_for("30m").bar_count == before.replay_for("30m").bar_count + 1


def test_both_authoritative_structure_result_shapes_produce_the_same_volume_replay(
    tmp_path,
) -> None:
    store = ParquetOHLCVStore(tmp_path)
    for timeframe in ("1h", "30m"):
        store.merge_and_save(
            _frame(timeframe),
            symbol="ASELS",
            timeframe=timeframe,
            source="test",
        )
    timeframes = ("1h", "30m")
    structure_only = CachedMarketStructureMTFRunner(store).run(
        symbol="ASELS",
        timeframes=timeframes,
    )
    structure_location = CachedStructureLocationMTFRunner(store).run(
        symbol="ASELS",
        timeframes=timeframes,
    )
    runner = VolumeMTFEvidenceReplayRunner(
        store,
        config=_config(),
        lifecycle_config=ParticipationLifecycleConfig(pivot_length=2),
    )

    from_structure_only = runner.replay(
        "ASELS",
        timeframes=timeframes,
        structure_replay=structure_only,
    )
    from_structure_location = runner.replay(
        "ASELS",
        timeframes=timeframes,
        structure_replay=structure_location,
    )

    assert from_structure_only.timeframes == from_structure_location.timeframes
    assert from_structure_only.round2 == from_structure_location.round2
    for timeframe in timeframes:
        assert (
            from_structure_only.replay_for(timeframe).history
            == from_structure_location.replay_for(timeframe).history
        )
        assert (
            from_structure_only.replay_for(timeframe).event_links
            == from_structure_location.replay_for(timeframe).event_links
        )


def test_replay_is_causal_for_every_prefix_after_structure_confirmation(tmp_path) -> None:
    store = ParquetOHLCVStore(tmp_path)
    full_frame = _frame("1h")
    store.merge_and_save(
        full_frame,
        symbol="ASELS",
        timeframe="1h",
        source="test",
    )
    runner = VolumeMTFEvidenceReplayRunner(
        store,
        config=_config(),
        lifecycle_config=ParticipationLifecycleConfig(pivot_length=2),
    )
    full = runner.replay(
        "ASELS",
        timeframes=("1h",),
        structure_replay=_structure_result(
            store,
            timeframes=("1h",),
            with_event=True,
        ),
    ).replay_for("1h")

    for count in range(11, len(full_frame) + 1):
        prefix_store = ParquetOHLCVStore(tmp_path / f"prefix-{count}")
        prefix_store.merge_and_save(
            full_frame.iloc[:count],
            symbol="ASELS",
            timeframe="1h",
            source="test",
        )
        prefix = VolumeMTFEvidenceReplayRunner(
            prefix_store,
            config=_config(),
            lifecycle_config=ParticipationLifecycleConfig(pivot_length=2),
        ).replay(
            "ASELS",
            timeframes=("1h",),
            structure_replay=_structure_result(
                prefix_store,
                timeframes=("1h",),
                with_event=True,
            ),
        ).replay_for("1h")

        assert prefix.history == full.history[:count]
        assert prefix.latest == full.history[count - 1]
        assert prefix.event_links[0].assessed_at == prefix.latest.timestamp
        assert all(
            bar_index <= prefix.latest.bar_index
            for window in prefix.event_links[0].windows
            for bar_index in window.observed_bar_indices
        )


def test_restart_from_the_same_cache_is_bitwise_deterministic_at_contract_level(
    tmp_path,
) -> None:
    store = ParquetOHLCVStore(tmp_path)
    for timeframe in ("2h", "1h", "30m"):
        store.merge_and_save(
            _frame(timeframe),
            symbol="ASELS",
            timeframe=timeframe,
            source="test",
        )
    timeframes = ("2h", "1h", "30m")
    runner = VolumeMTFEvidenceReplayRunner(
        store,
        config=_config(),
        lifecycle_config=ParticipationLifecycleConfig(pivot_length=2),
    )
    first = runner.replay(
        "ASELS",
        timeframes=timeframes,
        structure_replay=_structure_result(store, timeframes=timeframes, with_event=True),
    )

    restarted_store = ParquetOHLCVStore(tmp_path)
    restarted = VolumeMTFEvidenceReplayRunner(
        restarted_store,
        config=_config(),
        lifecycle_config=ParticipationLifecycleConfig(pivot_length=2),
    ).replay(
        "ASELS",
        timeframes=timeframes,
        structure_replay=_structure_result(
            restarted_store,
            timeframes=timeframes,
            with_event=True,
        ),
    )

    assert first.round2 == restarted.round2
    assert first.total_bar_count == restarted.total_bar_count
    for timeframe in timeframes:
        assert first.replay_for(timeframe).history == restarted.replay_for(timeframe).history
        assert (
            first.replay_for(timeframe).event_links
            == restarted.replay_for(timeframe).event_links
        )
