from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pandas as pd
import pytest

from financial_dashboard.engines import (
    FinalParticipationState,
    ParticipationLifecycleConfig,
    UnifiedParticipationExport,
    VolumeParticipationConfig,
    VolumeParticipationEngine,
    VolumeParticipationMetrics,
)
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
    StructureVolumeRelation,
    StructureVolumeTiming,
    VolumeEvidenceDataQuality,
    VolumeEvidenceEngine,
    VolumeEvidenceSnapshot,
    VolumeEvidenceStatus,
    find_participation_without_structure,
    link_structure_event_to_volume,
    link_structure_events_to_volume,
)


def _config() -> VolumeParticipationConfig:
    return VolumeParticipationConfig(
        minimum_history=10,
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


def _frame(count: int = 28) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    close = 100.0
    for index in range(count):
        move = 0.8 if index % 5 != 0 else -0.25
        open_price = close
        close = open_price + move
        rows.append(
            {
                "timestamp": pd.Timestamp("2025-01-01") + pd.Timedelta(hours=index),
                "open": open_price,
                "high": max(open_price, close) + 0.35,
                "low": min(open_price, close) - 0.30,
                "close": close,
                "volume": 1_000.0 + (index % 7) * 180.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
    return pd.DataFrame(rows)


def test_evidence_history_preserves_existing_math_and_is_immutable() -> None:
    frame = _frame()
    config = _config()
    lifecycle = ParticipationLifecycleConfig(pivot_length=2)

    source = VolumeParticipationEngine(config, lifecycle)
    source_exports: list[UnifiedParticipationExport] = []
    for _, row in frame.iterrows():
        source.update(row)
        source_exports.append(source.final_export)

    engine = VolumeEvidenceEngine(
        symbol="ASELS",
        timeframe="1h",
        config=config,
        lifecycle_config=lifecycle,
    )
    history = engine.replay(frame)

    assert len(history) == len(frame)
    assert tuple(snapshot.metrics for snapshot in history) == source.metrics_history
    assert tuple(snapshot.audit_export for snapshot in history) == tuple(source_exports)
    assert history[-1].audit_export == source.final_export
    assert all(snapshot.is_confirmed for snapshot in history)
    for prohibited in ("action", "recommendation", "entry", "buy", "sell"):
        assert not hasattr(history[-1], prohibited)
    assert {snapshot.status for snapshot in history} <= {
        VolumeEvidenceStatus.READY,
        VolumeEvidenceStatus.WARMUP,
        VolumeEvidenceStatus.LOW_PARTICIPATION,
        VolumeEvidenceStatus.VOLUME_UNAVAILABLE,
    }
    with pytest.raises(FrozenInstanceError):
        history[-1].state = "mutated"  # type: ignore[misc]


def test_replay_incremental_prefix_and_incomplete_tail_are_causal() -> None:
    frame = _frame()
    kwargs = {
        "symbol": "ASELS",
        "timeframe": "1h",
        "config": _config(),
        "lifecycle_config": ParticipationLifecycleConfig(pivot_length=2),
    }
    replayed = VolumeEvidenceEngine(**kwargs)
    replay_history = replayed.replay(frame)

    incremental = VolumeEvidenceEngine(**kwargs)
    incremental_history = tuple(incremental.update(row) for _, row in frame.iterrows())
    assert incremental_history == replay_history

    prefix = VolumeEvidenceEngine(**kwargs)
    assert prefix.replay(frame.iloc[:17]) == replay_history[:17]

    before = replayed.snapshot
    open_tail = frame.iloc[-1].copy()
    open_tail["timestamp"] = pd.Timestamp(open_tail["timestamp"]) + pd.Timedelta(hours=1)
    open_tail["is_closed"] = False
    transient = replayed.update(open_tail)
    assert transient.data_quality is VolumeEvidenceDataQuality.INCOMPLETE_TAIL
    assert transient.is_confirmed is False
    assert replayed.snapshot == before
    assert replayed.history == replay_history

    open_tail["is_closed"] = pd.NA
    unknown_flag = replayed.update(open_tail)
    assert unknown_flag.data_quality is VolumeEvidenceDataQuality.INCOMPLETE_TAIL
    assert replayed.history == replay_history


def test_unavailable_volume_is_never_mislabeled_low_participation() -> None:
    frame = _frame(16)
    frame["volume"] = 0.0
    engine = VolumeEvidenceEngine(
        symbol="ASELS",
        timeframe="1h",
        config=_config(),
        lifecycle_config=ParticipationLifecycleConfig(pivot_length=2),
    )
    history = engine.replay(frame)

    assert history[0].status is VolumeEvidenceStatus.WARMUP
    assert history[-1].status is VolumeEvidenceStatus.VOLUME_UNAVAILABLE
    assert all(
        snapshot.status is not VolumeEvidenceStatus.LOW_PARTICIPATION
        for snapshot in history
    )

    missing = _frame(16)
    missing.loc[8, "volume"] = float("nan")
    limited = engine.replay(missing)
    assert limited[8].status is VolumeEvidenceStatus.VOLUME_UNAVAILABLE
    assert limited[8].data_quality is VolumeEvidenceDataQuality.DATA_LIMITED
    assert limited[9].status is VolumeEvidenceStatus.WARMUP

    incremental = VolumeEvidenceEngine(
        symbol="ASELS",
        timeframe="1h",
        config=_config(),
        lifecycle_config=ParticipationLifecycleConfig(pivot_length=2),
    )
    assert tuple(incremental.update(row) for _, row in missing.iterrows()) == limited


def _event(*, timeframe: str = "1h", direction: Direction = Direction.UP) -> MarketStructureEventRecord:
    confirmed_at = pd.Timestamp("2025-01-01 02:00:00")
    return MarketStructureEventRecord(
        event_uid=f"ASELS:{timeframe}:EXTERNAL:7",
        identity=7,
        scope="EXTERNAL",
        event_type=EVENT_BOS,
        direction=direction,
        candidate_bar=1,
        event_bar=2,
        candidate_at=pd.Timestamp("2025-01-01 01:00:00"),
        confirmed_at=confirmed_at,
        broken_swing_identity=4,
        broken_source_bar=0,
        broken_source_at=pd.Timestamp("2025-01-01 00:00:00"),
        broken_level=101.25,
        origin_swing_identity=3,
        origin_source_bar=0,
        origin_source_at=pd.Timestamp("2025-01-01 00:00:00"),
        origin_price=99.0,
        quality=82.0,
        evidence_text="confirmed",
        confirmation_status=StructureEventConfirmation.CONFIRMED,
        validity=StructureEventValidity.VALID,
        relevance=StructureEventRelevance.CURRENT,
        outcome=StructureEventOutcome.OBSERVED,
        symbol="ASELS",
        timeframe=timeframe,
        bos_maturity=BosMaturity.CONTINUATION,
    )


def _snapshot(
    bar_index: int,
    state: FinalParticipationState,
    *,
    direction: int = 0,
    status: VolumeEvidenceStatus = VolumeEvidenceStatus.READY,
    shock_direction: int = 0,
) -> VolumeEvidenceSnapshot:
    data_ready = status in {VolumeEvidenceStatus.READY, VolumeEvidenceStatus.LOW_PARTICIPATION}
    return VolumeEvidenceSnapshot(
        symbol="ASELS",
        timeframe="1h",
        bar_index=bar_index,
        timestamp=pd.Timestamp("2025-01-01") + pd.Timedelta(hours=bar_index),
        segment_id=0,
        status=status,
        data_quality=VolumeEvidenceDataQuality.READY,
        state=state.value,
        evidence_direction=direction,
        metrics=VolumeParticipationMetrics(
            data_ready=data_ready,
            volume_usable=data_ready,
            capital_usable=data_ready,
        ),
        audit_export=UnifiedParticipationExport(
            state=state.value,
            support_direction=direction * 2,
            engine_direction=direction,
            one_bar_shock=state is FinalParticipationState.ONE_BAR_SHOCK,
            shock_direction=shock_direction,
        ),
        is_confirmed=True,
    )


def _neutral_history() -> list[VolumeEvidenceSnapshot]:
    return [
        _snapshot(index, FinalParticipationState.NEUTRAL)
        for index in range(5)
    ]


def test_same_timeframe_structure_link_preserves_authoritative_identity_and_windows() -> None:
    history = _neutral_history()
    history[2] = _snapshot(2, FinalParticipationState.UP_CONFIRMED, direction=1)
    event = _event()

    link = link_structure_event_to_volume(event, history)

    assert link.event_uid == event.event_uid
    assert link.scope == "EXTERNAL"
    assert link.event_type == EVENT_BOS
    assert link.bos_maturity is BosMaturity.CONTINUATION
    assert link.event_direction == 1
    assert link.confirmed_at == event.confirmed_at
    assert link.broken_level == 101.25
    assert link.relation is StructureVolumeRelation.STRUCTURE_SUPPORTED
    assert link.window(StructureVolumeTiming.PRE_EVENT).observed_bar_count == 2
    assert link.window(StructureVolumeTiming.AT_EVENT).aligned_confirmed_count == 1
    assert link.window(StructureVolumeTiming.FOLLOW_THROUGH).observed_bar_count == 2


def test_relation_distinguishes_absorption_shock_weak_and_unknown_without_lookahead() -> None:
    event = _event()

    absorption = _neutral_history()
    absorption[2] = _snapshot(
        2,
        FinalParticipationState.UPPER_ABSORPTION_CONFIRMED,
        direction=-1,
    )
    assert (
        link_structure_event_to_volume(event, absorption).relation
        is StructureVolumeRelation.STRUCTURE_ABSORPTION_RISK
    )

    shock = _neutral_history()
    shock[2] = _snapshot(
        2,
        FinalParticipationState.ONE_BAR_SHOCK,
        shock_direction=1,
    )
    shock[3] = _snapshot(3, FinalParticipationState.UP_CONFIRMED, direction=1)
    at_event = link_structure_event_to_volume(event, shock, as_of_bar=2)
    assert at_event.relation is StructureVolumeRelation.STRUCTURE_SHOCK_UNCONFIRMED
    assert at_event.window(StructureVolumeTiming.FOLLOW_THROUGH).observed_bar_count == 0
    assert (
        link_structure_event_to_volume(event, shock).relation
        is StructureVolumeRelation.STRUCTURE_SUPPORTED
    )

    weak = _neutral_history()
    weak[2] = _snapshot(
        2,
        FinalParticipationState.LOW_PARTICIPATION,
        status=VolumeEvidenceStatus.LOW_PARTICIPATION,
    )
    assert (
        link_structure_event_to_volume(event, weak).relation
        is StructureVolumeRelation.STRUCTURE_PARTICIPATION_WEAK
    )

    unavailable = [
        _snapshot(
            index,
            FinalParticipationState.VOLUME_UNAVAILABLE,
            status=VolumeEvidenceStatus.VOLUME_UNAVAILABLE,
        )
        for index in range(5)
    ]
    assert (
        link_structure_event_to_volume(event, unavailable).relation
        is StructureVolumeRelation.STRUCTURE_VOLUME_UNKNOWN
    )


def test_relation_distinguishes_opposition_conflict_and_scope_identity() -> None:
    event = _event()

    opposed = _neutral_history()
    opposed[2] = _snapshot(2, FinalParticipationState.DOWN_CONFIRMED, direction=-1)
    assert (
        link_structure_event_to_volume(event, opposed).relation
        is StructureVolumeRelation.STRUCTURE_VOLUME_OPPOSED
    )

    conflict = _neutral_history()
    conflict[2] = _snapshot(2, FinalParticipationState.CONFLICT)
    assert (
        link_structure_event_to_volume(event, conflict).relation
        is StructureVolumeRelation.STRUCTURE_VOLUME_CONFLICT
    )

    internal = replace(
        event,
        event_uid="ASELS:1h:INTERNAL:7",
        scope="INTERNAL",
    )
    links = link_structure_events_to_volume((event, internal), conflict)
    assert tuple((link.event_uid, link.scope) for link in links) == (
        ("ASELS:1h:EXTERNAL:7", "EXTERNAL"),
        ("ASELS:1h:INTERNAL:7", "INTERNAL"),
    )


def test_link_blocks_cross_timeframe_leakage_and_records_unlinked_participation() -> None:
    history = _neutral_history()
    history[1] = _snapshot(1, FinalParticipationState.UP_CONFIRMED, direction=1)
    history[2] = _snapshot(2, FinalParticipationState.UP_CONFIRMED, direction=1)
    event = _event()

    records = find_participation_without_structure(history, (event,))
    assert tuple(record.bar_index for record in records) == (1,)
    assert records[0].relation is StructureVolumeRelation.PARTICIPATION_WITHOUT_STRUCTURE
    assert records[0].event_uid is None

    with pytest.raises(ValueError, match="timeframe"):
        link_structure_event_to_volume(_event(timeframe="4h"), history)
