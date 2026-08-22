from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd

from financial_dashboard.data.engine_input import prepare_engine_input
from financial_dashboard.engines.market_structure_events import (
    MarketStructureEventRecord,
    StructureEventConfirmation,
    StructureEventOutcome,
    StructureEventRelevance,
    StructureEventValidity,
)
from financial_dashboard.engines.market_structure_state import (
    BosMaturity,
    EVENT_BOS,
    EVENT_CHOCH,
)
from financial_dashboard.engines.models import Direction
from financial_dashboard.engines.volume_evidence import (
    ParticipationWithoutStructure,
    StructureVolumeLink,
    StructureVolumeRelation,
    VolumeEvidenceDataQuality,
    VolumeEvidenceSnapshot,
    VolumeEvidenceStatus,
    link_structure_event_to_volume,
)
from financial_dashboard.engines.volume_participation_engine import (
    VolumeParticipationMetrics,
)
from financial_dashboard.engines.volume_participation_final import (
    FinalParticipationState,
    UnifiedParticipationExport,
)
from financial_dashboard.engines.volume_round2 import (
    CorrelatedVolumeChannel,
    LowerTimeframeImportance,
    LowerTimeframeInflowState,
    StructureVolumeRiskState,
    StructureVolumeRiskTrigger,
    VolumeShockStage,
    VolumeStructurePropagationState,
    build_correlated_volume_deduplication,
    build_event_mtf_assessments,
    build_shock_lifecycles,
    build_structural_propagations,
    build_structure_volume_risk,
)
from financial_dashboard.structure_location_replay import CausalBarClock


CLOCK = CausalBarClock()


def _event(
    *,
    timeframe: str = "1h",
    event_bar: int = 2,
    timestamp: object = pd.Timestamp("2025-01-01 02:00:00"),
    direction: Direction = Direction.UP,
    scope: str = "EXTERNAL",
    identity: int = 7,
    event_type: str = EVENT_BOS,
) -> MarketStructureEventRecord:
    confirmed_at = pd.Timestamp(timestamp)
    return MarketStructureEventRecord(
        event_uid=f"ASELS:{timeframe}:{scope}:{identity}",
        identity=identity,
        scope=scope,
        event_type=event_type,
        direction=direction,
        candidate_bar=max(0, event_bar - 1),
        event_bar=event_bar,
        candidate_at=confirmed_at - dict(CLOCK.durations)[timeframe],
        confirmed_at=confirmed_at,
        broken_swing_identity=4,
        broken_source_bar=0,
        broken_source_at=pd.Timestamp("2025-01-01"),
        broken_level=101.0,
        origin_swing_identity=3,
        origin_source_bar=0,
        origin_source_at=pd.Timestamp("2025-01-01"),
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
    timeframe: str = "1h",
    direction: int = 0,
    shock: bool = False,
    shock_direction: int = 0,
    status: VolumeEvidenceStatus = VolumeEvidenceStatus.READY,
) -> VolumeEvidenceSnapshot:
    step = dict(CLOCK.durations)[timeframe]
    ready = status in {
        VolumeEvidenceStatus.READY,
        VolumeEvidenceStatus.LOW_PARTICIPATION,
    }
    return VolumeEvidenceSnapshot(
        symbol="ASELS",
        timeframe=timeframe,
        bar_index=bar_index,
        timestamp=pd.Timestamp("2025-01-01") + bar_index * step,
        segment_id=0,
        status=status,
        data_quality=VolumeEvidenceDataQuality.READY,
        state=state.value,
        evidence_direction=direction,
        metrics=VolumeParticipationMetrics(
            data_ready=ready,
            volume_usable=ready,
            capital_usable=ready,
        ),
        audit_export=UnifiedParticipationExport(
            state=state.value,
            support_direction=direction * 2,
            engine_direction=direction,
            one_bar_shock=shock,
            shock_direction=shock_direction,
        ),
    )


def _frame(timeframe: str, closes: list[float]) -> pd.DataFrame:
    step = dict(CLOCK.durations)[timeframe]
    rows = []
    prior = closes[0]
    for index, close in enumerate(closes):
        rows.append(
            {
                "timestamp": pd.Timestamp("2025-01-01") + index * step,
                "open": prior,
                "high": max(prior, close) + 0.5,
                "low": min(prior, close) - 0.5,
                "close": close,
                "volume": 1_000.0,
                "is_closed": True,
                "is_complete": True,
            }
        )
        prior = close
    return pd.DataFrame(rows)


def _replay(
    timeframe: str,
    history: list[VolumeEvidenceSnapshot],
    *,
    closes: list[float] | None = None,
    event_links: tuple[StructureVolumeLink, ...] = (),
    unlinked: tuple[ParticipationWithoutStructure, ...] = (),
):
    resolved_closes = closes or [100.0 + index for index in range(len(history))]
    return SimpleNamespace(
        timeframe=timeframe,
        history=tuple(history),
        latest=history[-1],
        input_batch=prepare_engine_input(_frame(timeframe, resolved_closes)),
        event_links=event_links,
        participation_without_structure=unlinked,
    )


def test_shock_flag_overrides_same_bar_source_confirmation_for_structure_relation() -> None:
    history = [
        _snapshot(index, FinalParticipationState.NEUTRAL)
        for index in range(5)
    ]
    history[2] = _snapshot(
        2,
        FinalParticipationState.UP_CONFIRMED,
        direction=1,
        shock=True,
        shock_direction=1,
    )

    link = link_structure_event_to_volume(_event(), history, as_of_bar=2)

    assert link.relation is StructureVolumeRelation.STRUCTURE_SHOCK_UNCONFIRMED
    assert link.windows[1].aligned_confirmed_count == 0
    assert link.windows[1].shock_count == 1


def test_lower_timeframe_inflow_is_elevated_but_cannot_promote_target_confirmation() -> None:
    event = _event(
        timeframe="2h",
        event_bar=2,
        timestamp=pd.Timestamp("2025-01-01 04:00:00"),
    )
    target_history = [
        _snapshot(
            index,
            FinalParticipationState.VOLUME_UNAVAILABLE,
            timeframe="2h",
            status=VolumeEvidenceStatus.VOLUME_UNAVAILABLE,
        )
        for index in range(6)
    ]
    link = link_structure_event_to_volume(event, target_history)
    target = _replay("2h", target_history, event_links=(link,))
    lower_history = [
        _snapshot(index, FinalParticipationState.NEUTRAL)
        for index in range(10)
    ]
    lower_history[4] = _snapshot(
        4,
        FinalParticipationState.UP_CONFIRMED,
        direction=1,
    )
    lower = _replay("1h", lower_history)

    assessment = build_event_mtf_assessments(
        (target, lower),
        clock=CLOCK,
        final_as_of=CLOCK.available_at(lower_history[-1].timestamp, "1h"),
    )[0]

    assert assessment.same_timeframe_relation is StructureVolumeRelation.STRUCTURE_VOLUME_UNKNOWN
    assert (
        assessment.lower_timeframe_importance
        is LowerTimeframeImportance.ELEVATED_SAME_TIMEFRAME_UNAVAILABLE
    )
    assert assessment.lower_timeframe_state is LowerTimeframeInflowState.ALIGNED
    assert assessment.lower_timeframe_score > 0
    assert assessment.same_timeframe_authoritative
    assert not assessment.lower_timeframe_can_confirm
    assert all(not inflow.can_confirm_target_timeframe for inflow in assessment.lower_timeframe_inflows)
    assert all(not inflow.raw_volume_summed for inflow in assessment.lower_timeframe_inflows)


def test_confirmed_opposition_stays_blocked_when_pressure_only_weakens() -> None:
    event = _event()
    history = [
        _snapshot(index, FinalParticipationState.NEUTRAL)
        for index in range(6)
    ]
    history[2] = _snapshot(
        2,
        FinalParticipationState.DOWN_CONFIRMED,
        direction=-1,
    )
    history[3] = _snapshot(3, FinalParticipationState.DOWN_WEAKENING)
    replay = _replay("1h", history)

    risk = build_structure_volume_risk(
        event,
        replay,
        same_scope_events=(event,),
        clock=CLOCK,
    )

    assert risk.state is StructureVolumeRiskState.MONITORING_OPPOSITION_WEAKENED
    assert risk.is_blocked
    assert risk.release_trigger is StructureVolumeRiskTrigger.NONE
    assert tuple(transition.trigger for transition in risk.transitions) == (
        StructureVolumeRiskTrigger.CONFIRMED_OPPOSING_VOLUME,
        StructureVolumeRiskTrigger.OPPOSITION_WEAKENED,
    )


def test_opposition_releases_only_on_aligned_recovery_resolution_or_supersession() -> None:
    event = _event()
    base = [_snapshot(index, FinalParticipationState.NEUTRAL) for index in range(7)]
    base[2] = _snapshot(2, FinalParticipationState.DOWN_CONFIRMED, direction=-1)

    aligned = list(base)
    aligned[4] = _snapshot(4, FinalParticipationState.UP_CONFIRMED, direction=1)
    aligned_risk = build_structure_volume_risk(
        event,
        _replay("1h", aligned),
        same_scope_events=(event,),
        clock=CLOCK,
    )
    assert aligned_risk.state is StructureVolumeRiskState.RELEASED_ALIGNED_RECOVERY
    assert aligned_risk.release_trigger is StructureVolumeRiskTrigger.ALIGNED_RECOVERY
    assert not aligned_risk.is_blocked

    reclaimed = list(base)
    reclaimed[4] = _snapshot(4, FinalParticipationState.DOWN_BREAK_RECLAIMED)
    reclaimed_risk = build_structure_volume_risk(
        event,
        _replay("1h", reclaimed, closes=[100.0, 100.5, 100.0, 100.5, 102.0, 102.0, 102.0]),
        same_scope_events=(event,),
        clock=CLOCK,
    )
    assert reclaimed_risk.state is StructureVolumeRiskState.RELEASED_FAKE_RECLAIM_RESOLVED
    assert (
        reclaimed_risk.release_trigger
        is StructureVolumeRiskTrigger.COMPLETED_FAKE_RECLAIM_RESOLUTION
    )

    superseding = _event(
        event_bar=4,
        timestamp=pd.Timestamp("2025-01-01 04:00:00"),
        identity=8,
        event_type=EVENT_CHOCH,
    )
    superseded_risk = build_structure_volume_risk(
        event,
        _replay("1h", base),
        same_scope_events=(event, superseding),
        clock=CLOCK,
    )
    assert superseded_risk.state is StructureVolumeRiskState.RELEASED_STRUCTURE_SUPERSEDED
    assert (
        superseded_risk.release_trigger
        is StructureVolumeRiskTrigger.AUTHORITATIVE_STRUCTURE_SUPERSESSION
    )


def test_one_bar_shock_needs_later_follow_through_and_tracks_fake_reclaim() -> None:
    followed = [_snapshot(index, FinalParticipationState.NEUTRAL) for index in range(7)]
    followed[2] = _snapshot(
        2,
        FinalParticipationState.UP_CONFIRMED,
        direction=1,
        shock=True,
        shock_direction=1,
    )
    followed[3] = _snapshot(3, FinalParticipationState.UP_CONFIRMED, direction=1)
    followed_lifecycle = build_shock_lifecycles(
        _replay(
            "1h",
            followed,
            closes=[100.0, 100.0, 102.0, 103.0, 103.2, 103.3, 103.4],
        ),
        clock=CLOCK,
    )[0]

    assert followed_lifecycle.final_stage is VolumeShockStage.FOLLOW_THROUGH_CONFIRMED
    assert tuple(transition.stage for transition in followed_lifecycle.transitions) == (
        VolumeShockStage.DETECTED_UNCONFIRMED,
        VolumeShockStage.FOLLOW_THROUGH_CONFIRMED,
    )
    assert not followed_lifecycle.immediate_confirmation_allowed
    assert not followed_lifecycle.entry_authority

    failed = list(followed)
    failed[3] = _snapshot(3, FinalParticipationState.NEUTRAL)
    failed_lifecycle = build_shock_lifecycles(
        _replay(
            "1h",
            failed,
            closes=[100.0, 100.0, 102.0, 99.5, 99.0, 99.0, 99.0],
        ),
        clock=CLOCK,
    )[0]
    assert failed_lifecycle.final_stage is VolumeShockStage.RECLAIMED


def test_volume_without_structure_lists_direct_ichoch_echoch_progression_without_promotion() -> None:
    history = [_snapshot(index, FinalParticipationState.NEUTRAL, timeframe="2h") for index in range(8)]
    history[2] = _snapshot(
        2,
        FinalParticipationState.UP_CONFIRMED,
        timeframe="2h",
        direction=1,
    )
    origin = ParticipationWithoutStructure(
        symbol="ASELS",
        timeframe="2h",
        bar_index=2,
        timestamp=history[2].timestamp,
        state=history[2].state,
        evidence_direction=1,
        status=VolumeEvidenceStatus.READY,
    )
    replay = _replay("2h", history, unlinked=(origin,))
    lower_internal = _event(
        timeframe="1h",
        event_bar=3,
        timestamp=pd.Timestamp("2025-01-01 03:00:00"),
        scope="INTERNAL",
        identity=3,
        event_type=EVENT_CHOCH,
    )
    higher_external = _event(
        timeframe="4h",
        event_bar=2,
        timestamp=pd.Timestamp("2025-01-01 08:00:00"),
        scope="EXTERNAL",
        identity=9,
        event_type=EVENT_CHOCH,
    )
    structure_snapshots = (
        SimpleNamespace(events=(lower_internal,)),
        SimpleNamespace(events=(higher_external,)),
    )

    propagation = build_structural_propagations(
        (replay,),
        structure_snapshots,
        clock=CLOCK,
        final_as_of=pd.Timestamp("2025-01-02 00:00:00"),
    )[0]

    assert propagation.state is VolumeStructurePropagationState.HIGHER_TIMEFRAME_DIRECT_CONFIRMATION
    assert propagation.highest_direct_timeframe == "4h"
    assert tuple(step.scope for step in propagation.steps) == ("INTERNAL", "EXTERNAL")
    assert all(step.directly_confirmed for step in propagation.steps)
    assert all(not step.promoted_or_inferred for step in propagation.steps)
    assert not propagation.target_confirmation_invented


def test_shared_source_dedup_caps_channels_at_one_and_never_sums_raw_mtf_volume() -> None:
    dedup = build_correlated_volume_deduplication(
        (
            CorrelatedVolumeChannel.HAM_FLOW,
            CorrelatedVolumeChannel.VOLUME_PARTICIPATION,
            CorrelatedVolumeChannel.AUCTION_VOLUME_PROFILE,
        )
    )

    assert dedup.source_family == "OHLCV_SOURCE_VOLUME"
    assert dedup.independent_vote_cap == 1
    assert not dedup.raw_mtf_volume_summed
    assert dedup.representative_channel is CorrelatedVolumeChannel.VOLUME_PARTICIPATION


def test_round2_contracts_are_immutable() -> None:
    dedup = build_correlated_volume_deduplication()
    assert replace(dedup) == dedup
