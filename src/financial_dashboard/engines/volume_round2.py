from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha1
from typing import Any, Protocol, Sequence

import pandas as pd

from .market_structure_events import MarketStructureEventRecord
from .volume_evidence import (
    StructureVolumeLink,
    StructureVolumeRelation,
    VolumeEvidenceSnapshot,
    VolumeEvidenceStatus,
)
from .volume_participation_final import FinalParticipationState


_TIMEFRAME_ORDER = ("30m", "1h", "2h", "4h", "1d")
_TIMEFRAME_RANK = {timeframe: index for index, timeframe in enumerate(_TIMEFRAME_ORDER)}
_TIMEFRAME_WEIGHT = {
    "30m": 0.40,
    "1h": 0.55,
    "2h": 0.70,
    "4h": 0.85,
    "1d": 1.00,
}
_MATURE_STATES = {
    FinalParticipationState.UP_CONFIRMED.value,
    FinalParticipationState.DOWN_CONFIRMED.value,
    FinalParticipationState.UP_PROTECTED.value,
    FinalParticipationState.DOWN_PROTECTED.value,
    FinalParticipationState.UP_BREAK_SUPPORTED.value,
    FinalParticipationState.DOWN_BREAK_SUPPORTED.value,
    FinalParticipationState.LOWER_ABSORPTION_CONFIRMED.value,
    FinalParticipationState.UPPER_ABSORPTION_CONFIRMED.value,
}
_CANDIDATE_STATES = {
    FinalParticipationState.UP_CANDIDATE.value,
    FinalParticipationState.DOWN_CANDIDATE.value,
    FinalParticipationState.LOWER_ABSORPTION_CANDIDATE.value,
    FinalParticipationState.UPPER_ABSORPTION_CANDIDATE.value,
}


class AvailabilityClock(Protocol):
    durations: tuple[tuple[str, pd.Timedelta], ...]

    def available_at(self, timestamp: Any, timeframe: str) -> pd.Timestamp: ...


class VolumeMTFPressureState(StrEnum):
    UNKNOWN = "UNKNOWN"
    BALANCED = "BALANCED"
    MIXED = "MIXED"
    BULLISH_CONTEXT = "BULLISH_CONTEXT"
    BEARISH_CONTEXT = "BEARISH_CONTEXT"


class LowerTimeframeInflowState(StrEnum):
    NO_LOWER_TIMEFRAME = "NO_LOWER_TIMEFRAME"
    UNKNOWN = "UNKNOWN"
    WEAK = "WEAK"
    ALIGNED = "ALIGNED"
    OPPOSED = "OPPOSED"
    MIXED = "MIXED"
    SHOCK_UNCONFIRMED = "SHOCK_UNCONFIRMED"


class LowerTimeframeImportance(StrEnum):
    ENRICHMENT_ONLY = "ENRICHMENT_ONLY"
    ELEVATED_SAME_TIMEFRAME_WEAK = "ELEVATED_SAME_TIMEFRAME_WEAK"
    ELEVATED_SAME_TIMEFRAME_UNAVAILABLE = "ELEVATED_SAME_TIMEFRAME_UNAVAILABLE"


class StructureVolumeRiskState(StrEnum):
    UNKNOWN = "UNKNOWN"
    CLEAR = "CLEAR"
    BLOCKED_CONFIRMED_OPPOSITION = "BLOCKED_CONFIRMED_OPPOSITION"
    BLOCKED_VOLUME_CONFLICT = "BLOCKED_VOLUME_CONFLICT"
    BLOCKED_FAKE_ABSORPTION_RISK = "BLOCKED_FAKE_ABSORPTION_RISK"
    MONITORING_OPPOSITION_WEAKENED = "MONITORING_OPPOSITION_WEAKENED"
    RELEASED_ALIGNED_RECOVERY = "RELEASED_ALIGNED_RECOVERY"
    RELEASED_STRUCTURE_SUPERSEDED = "RELEASED_STRUCTURE_SUPERSEDED"
    RELEASED_FAKE_RECLAIM_RESOLVED = "RELEASED_FAKE_RECLAIM_RESOLVED"


class StructureVolumeRiskTrigger(StrEnum):
    NONE = "NONE"
    CONFIRMED_OPPOSING_VOLUME = "CONFIRMED_OPPOSING_VOLUME"
    CONFIRMED_VOLUME_CONFLICT = "CONFIRMED_VOLUME_CONFLICT"
    STRUCTURE_DIRECTION_RECLAIM = "STRUCTURE_DIRECTION_RECLAIM"
    OPPOSITION_WEAKENED = "OPPOSITION_WEAKENED"
    ALIGNED_RECOVERY = "ALIGNED_RECOVERY"
    AUTHORITATIVE_STRUCTURE_SUPERSESSION = "AUTHORITATIVE_STRUCTURE_SUPERSESSION"
    COMPLETED_FAKE_RECLAIM_RESOLUTION = "COMPLETED_FAKE_RECLAIM_RESOLUTION"


class VolumeShockStage(StrEnum):
    DETECTED_UNCONFIRMED = "DETECTED_UNCONFIRMED"
    DIRECTIONLESS_UNRESOLVED = "DIRECTIONLESS_UNRESOLVED"
    FOLLOW_THROUGH_CONFIRMED = "FOLLOW_THROUGH_CONFIRMED"
    ABSORPTION_RISK = "ABSORPTION_RISK"
    FAKE_SUSPECTED = "FAKE_SUSPECTED"
    RECLAIMED = "RECLAIMED"


class StructuralPropagationPhase(StrEnum):
    LOWER_TIMEFRAME_CONTEXT = "LOWER_TIMEFRAME_CONTEXT"
    ORIGIN_TIMEFRAME_FOLLOW = "ORIGIN_TIMEFRAME_FOLLOW"
    HIGHER_TIMEFRAME_FOLLOW = "HIGHER_TIMEFRAME_FOLLOW"


class VolumeStructurePropagationState(StrEnum):
    NO_STRUCTURE = "NO_STRUCTURE"
    LOWER_INTERNAL_ONLY = "LOWER_INTERNAL_ONLY"
    LOWER_EXTERNAL_PRESENT = "LOWER_EXTERNAL_PRESENT"
    SAME_TIMEFRAME_DIRECT_CONFIRMATION = "SAME_TIMEFRAME_DIRECT_CONFIRMATION"
    HIGHER_TIMEFRAME_DIRECT_CONFIRMATION = "HIGHER_TIMEFRAME_DIRECT_CONFIRMATION"
    CONFLICTED = "CONFLICTED"


class CorrelatedVolumeChannel(StrEnum):
    HAM_FLOW = "HAM_FLOW"
    VOLUME_PARTICIPATION = "VOLUME_PARTICIPATION"
    AUCTION_VOLUME_PROFILE = "AUCTION_VOLUME_PROFILE"


@dataclass(frozen=True, slots=True)
class VolumeMTFContribution:
    timeframe: str
    state: str
    status: VolumeEvidenceStatus
    evidence_direction: int
    signal_factor: float
    configured_weight: float
    normalized_contribution: float
    available_at: Any
    shock_excluded: bool


@dataclass(frozen=True, slots=True)
class VolumeMTFPressureContext:
    state: VolumeMTFPressureState
    directional_score: float
    evidence_coverage: float
    contributions: tuple[VolumeMTFContribution, ...]
    aggregation_policy: str = "NORMALIZED_CATEGORICAL_CONTEXT_NO_RAW_VOLUME_SUM"
    raw_volume_summed: bool = False
    decision_authority: str = "CONTEXT_ONLY"


@dataclass(frozen=True, slots=True)
class LowerTimeframeVolumeInflow:
    event_uid: str
    target_timeframe: str
    source_timeframe: str
    state: LowerTimeframeInflowState
    signal: int
    observed_count: int
    usable_count: int
    aligned_confirmed_count: int
    opposed_confirmed_count: int
    shock_count: int
    latest_available_at: Any
    can_confirm_target_timeframe: bool = False
    raw_volume_summed: bool = False


@dataclass(frozen=True, slots=True)
class StructureVolumeMTFAssessment:
    event_uid: str
    symbol: str
    timeframe: str
    scope: str
    event_type: str
    event_direction: int
    confirmed_at: Any
    same_timeframe_relation: StructureVolumeRelation
    lower_timeframe_importance: LowerTimeframeImportance
    lower_timeframe_state: LowerTimeframeInflowState
    lower_timeframe_score: float
    lower_timeframe_inflows: tuple[LowerTimeframeVolumeInflow, ...]
    same_timeframe_authoritative: bool = True
    lower_timeframe_can_confirm: bool = False
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StructureVolumeRiskTransition:
    bar_index: int
    timestamp: Any
    available_at: Any
    previous_state: StructureVolumeRiskState
    state: StructureVolumeRiskState
    trigger: StructureVolumeRiskTrigger
    source_state: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class StructureVolumeRiskAssessment:
    event_uid: str
    symbol: str
    timeframe: str
    scope: str
    event_type: str
    event_direction: int
    state: StructureVolumeRiskState
    is_blocked: bool
    activated_at: Any
    released_at: Any
    release_trigger: StructureVolumeRiskTrigger
    transitions: tuple[StructureVolumeRiskTransition, ...]
    hard_block_authority: str = "SAME_TIMEFRAME_CONFIRMED_STRUCTURE_VOLUME_ONLY"
    lower_timeframe_can_hard_block: bool = False


@dataclass(frozen=True, slots=True)
class VolumeShockTransition:
    bar_index: int
    timestamp: Any
    available_at: Any
    stage: VolumeShockStage
    reason: str


@dataclass(frozen=True, slots=True)
class VolumeShockLifecycle:
    shock_uid: str
    symbol: str
    timeframe: str
    shock_bar: int
    shock_at: Any
    direction: int
    final_stage: VolumeShockStage
    transitions: tuple[VolumeShockTransition, ...]
    confirmation_deadline_bar: int
    monitor_deadline_bar: int
    immediate_confirmation_allowed: bool = False
    entry_authority: bool = False


@dataclass(frozen=True, slots=True)
class StructuralPropagationStep:
    event_uid: str
    timeframe: str
    scope: str
    event_type: str
    event_direction: int
    confirmed_at: Any
    available_at: Any
    phase: StructuralPropagationPhase
    directly_confirmed: bool = True
    promoted_or_inferred: bool = False


@dataclass(frozen=True, slots=True)
class VolumeStructurePropagation:
    symbol: str
    origin_timeframe: str
    origin_bar: int
    origin_at: Any
    volume_direction: int
    volume_state: str
    state: VolumeStructurePropagationState
    steps: tuple[StructuralPropagationStep, ...]
    highest_direct_timeframe: str | None
    target_confirmation_invented: bool = False
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CorrelatedVolumeDeduplication:
    source_family: str
    registered_channels: tuple[CorrelatedVolumeChannel, ...]
    active_channels: tuple[CorrelatedVolumeChannel, ...]
    representative_channel: CorrelatedVolumeChannel
    independent_vote_cap: int
    raw_mtf_volume_summed: bool
    policy: str


@dataclass(frozen=True, slots=True)
class VolumeRound2Assessment:
    symbol: str
    as_of: Any
    pressure: VolumeMTFPressureContext
    event_assessments: tuple[StructureVolumeMTFAssessment, ...]
    risks: tuple[StructureVolumeRiskAssessment, ...]
    shocks: tuple[VolumeShockLifecycle, ...]
    structural_propagations: tuple[VolumeStructurePropagation, ...]
    deduplication: CorrelatedVolumeDeduplication


@dataclass(frozen=True, slots=True)
class VolumeShockLifecycleConfig:
    confirmation_bars: int = 2
    monitor_bars: int = 5

    def __post_init__(self) -> None:
        if isinstance(self.confirmation_bars, bool) or self.confirmation_bars <= 0:
            raise ValueError("confirmation_bars must be a positive integer")
        if isinstance(self.monitor_bars, bool) or self.monitor_bars < self.confirmation_bars:
            raise ValueError("monitor_bars must be an integer >= confirmation_bars")


def _is_shock(snapshot: VolumeEvidenceSnapshot) -> bool:
    return bool(snapshot.audit_export.one_bar_shock)


def _mature_direction(snapshot: VolumeEvidenceSnapshot) -> int:
    if _is_shock(snapshot) or snapshot.state not in _MATURE_STATES:
        return 0
    return snapshot.evidence_direction if snapshot.evidence_direction in {-1, 1} else 0


def _candidate_direction(snapshot: VolumeEvidenceSnapshot) -> int:
    if _is_shock(snapshot) or snapshot.state not in _CANDIDATE_STATES:
        return 0
    return snapshot.evidence_direction if snapshot.evidence_direction in {-1, 1} else 0


def _availability(
    snapshot: VolumeEvidenceSnapshot,
    clock: AvailabilityClock,
) -> pd.Timestamp:
    return clock.available_at(snapshot.timestamp, snapshot.timeframe)


def build_mtf_pressure_context(
    timeframe_replays: Sequence[Any],
    *,
    clock: AvailabilityClock,
) -> VolumeMTFPressureContext:
    contributions: list[VolumeMTFContribution] = []
    weighted_signal = 0.0
    available_weight = 0.0
    configured_weight = sum(
        _TIMEFRAME_WEIGHT.get(replay.timeframe, 0.0) for replay in timeframe_replays
    )
    positive = negative = False

    for replay in timeframe_replays:
        latest = replay.latest
        if latest is None:
            continue
        weight = _TIMEFRAME_WEIGHT.get(replay.timeframe, 0.0)
        if latest.has_usable_measurement:
            available_weight += weight
        mature = _mature_direction(latest)
        candidate = _candidate_direction(latest)
        factor = float(mature) if mature else 0.35 * float(candidate)
        weighted_signal += weight * factor
        positive = positive or factor > 0
        negative = negative or factor < 0
        contributions.append(
            VolumeMTFContribution(
                timeframe=replay.timeframe,
                state=latest.state,
                status=latest.status,
                evidence_direction=latest.evidence_direction,
                signal_factor=factor,
                configured_weight=weight,
                normalized_contribution=(
                    0.0 if configured_weight <= 0 else weight * factor / configured_weight
                ),
                available_at=_availability(latest, clock),
                shock_excluded=_is_shock(latest),
            )
        )

    score = 0.0 if configured_weight <= 0 else weighted_signal / configured_weight
    coverage = 0.0 if configured_weight <= 0 else available_weight / configured_weight
    if not contributions or coverage <= 0:
        state = VolumeMTFPressureState.UNKNOWN
    elif positive and negative and abs(score) < 0.20:
        state = VolumeMTFPressureState.MIXED
    elif score >= 0.20:
        state = VolumeMTFPressureState.BULLISH_CONTEXT
    elif score <= -0.20:
        state = VolumeMTFPressureState.BEARISH_CONTEXT
    else:
        state = VolumeMTFPressureState.BALANCED
    return VolumeMTFPressureContext(
        state=state,
        directional_score=max(-1.0, min(1.0, score)),
        evidence_coverage=max(0.0, min(1.0, coverage)),
        contributions=tuple(contributions),
    )


def _lower_timeframes(target_timeframe: str, available: set[str]) -> tuple[str, ...]:
    target_rank = _TIMEFRAME_RANK.get(target_timeframe)
    if target_rank is None:
        return ()
    return tuple(
        timeframe
        for timeframe in _TIMEFRAME_ORDER[:target_rank]
        if timeframe in available
    )


def _inflow_for_source(
    link: StructureVolumeLink,
    source_replay: Any,
    *,
    clock: AvailabilityClock,
    final_as_of: pd.Timestamp,
) -> LowerTimeframeVolumeInflow:
    target_start = pd.Timestamp(link.confirmed_at)
    target_available = clock.available_at(link.confirmed_at, link.timeframe)
    target_duration = dict(clock.durations)[link.timeframe]
    follow_end = min(final_as_of, target_available + (2 * target_duration))
    selected = tuple(
        snapshot
        for snapshot in source_replay.history
        if target_start < _availability(snapshot, clock) <= follow_end
    )
    usable = tuple(snapshot for snapshot in selected if snapshot.has_usable_measurement)
    aligned = sum(
        _mature_direction(snapshot) == link.event_direction for snapshot in usable
    )
    opposed = sum(
        _mature_direction(snapshot) == -link.event_direction for snapshot in usable
    )
    shocks = sum(_is_shock(snapshot) for snapshot in usable)
    if aligned and opposed:
        state = LowerTimeframeInflowState.MIXED
        signal = 0
    elif aligned:
        state = LowerTimeframeInflowState.ALIGNED
        signal = 1
    elif opposed:
        state = LowerTimeframeInflowState.OPPOSED
        signal = -1
    elif shocks:
        state = LowerTimeframeInflowState.SHOCK_UNCONFIRMED
        signal = 0
    elif usable:
        state = LowerTimeframeInflowState.WEAK
        signal = 0
    else:
        state = LowerTimeframeInflowState.UNKNOWN
        signal = 0
    latest_available = (
        None if not selected else max(_availability(snapshot, clock) for snapshot in selected)
    )
    return LowerTimeframeVolumeInflow(
        event_uid=link.event_uid,
        target_timeframe=link.timeframe,
        source_timeframe=source_replay.timeframe,
        state=state,
        signal=signal,
        observed_count=len(selected),
        usable_count=len(usable),
        aligned_confirmed_count=aligned,
        opposed_confirmed_count=opposed,
        shock_count=shocks,
        latest_available_at=latest_available,
    )


def _importance_for_relation(
    relation: StructureVolumeRelation,
) -> LowerTimeframeImportance:
    if relation is StructureVolumeRelation.STRUCTURE_VOLUME_UNKNOWN:
        return LowerTimeframeImportance.ELEVATED_SAME_TIMEFRAME_UNAVAILABLE
    if relation is StructureVolumeRelation.STRUCTURE_PARTICIPATION_WEAK:
        return LowerTimeframeImportance.ELEVATED_SAME_TIMEFRAME_WEAK
    return LowerTimeframeImportance.ENRICHMENT_ONLY


def build_event_mtf_assessments(
    timeframe_replays: Sequence[Any],
    *,
    clock: AvailabilityClock,
    final_as_of: pd.Timestamp,
) -> tuple[StructureVolumeMTFAssessment, ...]:
    by_timeframe = {replay.timeframe: replay for replay in timeframe_replays}
    available = set(by_timeframe)
    assessments: list[StructureVolumeMTFAssessment] = []
    for replay in timeframe_replays:
        for link in replay.event_links:
            lower = _lower_timeframes(link.timeframe, available)
            inflows = tuple(
                _inflow_for_source(
                    link,
                    by_timeframe[source_timeframe],
                    clock=clock,
                    final_as_of=final_as_of,
                )
                for source_timeframe in lower
            )
            denominator = sum(_TIMEFRAME_WEIGHT[timeframe] for timeframe in lower)
            score = (
                0.0
                if denominator <= 0
                else sum(
                    _TIMEFRAME_WEIGHT[inflow.source_timeframe] * inflow.signal
                    for inflow in inflows
                )
                / denominator
            )
            states = {inflow.state for inflow in inflows}
            if not lower:
                lower_state = LowerTimeframeInflowState.NO_LOWER_TIMEFRAME
            elif (
                LowerTimeframeInflowState.ALIGNED in states
                and LowerTimeframeInflowState.OPPOSED in states
            ) or LowerTimeframeInflowState.MIXED in states:
                lower_state = LowerTimeframeInflowState.MIXED
            elif LowerTimeframeInflowState.ALIGNED in states:
                lower_state = LowerTimeframeInflowState.ALIGNED
            elif LowerTimeframeInflowState.OPPOSED in states:
                lower_state = LowerTimeframeInflowState.OPPOSED
            elif LowerTimeframeInflowState.SHOCK_UNCONFIRMED in states:
                lower_state = LowerTimeframeInflowState.SHOCK_UNCONFIRMED
            elif LowerTimeframeInflowState.WEAK in states:
                lower_state = LowerTimeframeInflowState.WEAK
            else:
                lower_state = LowerTimeframeInflowState.UNKNOWN
            importance = _importance_for_relation(link.relation)
            reasons = [
                "Same-timeframe Structure and Volume remain the only authority for their timeframe.",
                "Lower-timeframe Volume is categorical context; raw volumes are not summed.",
            ]
            if importance is not LowerTimeframeImportance.ENRICHMENT_ONLY:
                reasons.append(
                    "Lower-timeframe inspection is elevated because same-timeframe Volume is weak or unavailable."
                )
            assessments.append(
                StructureVolumeMTFAssessment(
                    event_uid=link.event_uid,
                    symbol=link.symbol,
                    timeframe=link.timeframe,
                    scope=link.scope,
                    event_type=link.event_type,
                    event_direction=link.event_direction,
                    confirmed_at=link.confirmed_at,
                    same_timeframe_relation=link.relation,
                    lower_timeframe_importance=importance,
                    lower_timeframe_state=lower_state,
                    lower_timeframe_score=max(-1.0, min(1.0, score)),
                    lower_timeframe_inflows=inflows,
                    reasons=tuple(reasons),
                )
            )
    return tuple(assessments)


def _row_by_bar(frame: pd.DataFrame) -> dict[int, pd.Series]:
    return {index: row for index, (_, row) in enumerate(frame.iterrows())}


def _accepted_structure_level(
    event: MarketStructureEventRecord,
    close: float,
) -> bool:
    if event.broken_level is None:
        return False
    if int(event.direction) > 0:
        return close >= float(event.broken_level)
    return close <= float(event.broken_level)


def _explicit_fake_reclaim_resolution(
    snapshot: VolumeEvidenceSnapshot,
    event_direction: int,
) -> bool:
    if event_direction > 0:
        return snapshot.state in {
            FinalParticipationState.UPPER_ABSORPTION_INVALIDATED.value,
            FinalParticipationState.DOWN_BREAK_RECLAIMED.value,
        }
    return snapshot.state in {
        FinalParticipationState.LOWER_ABSORPTION_INVALIDATED.value,
        FinalParticipationState.UP_BREAK_RECLAIMED.value,
    }


def _structure_direction_reclaim(
    snapshot: VolumeEvidenceSnapshot,
    event_direction: int,
) -> bool:
    return (
        event_direction > 0
        and snapshot.state == FinalParticipationState.UP_BREAK_RECLAIMED.value
    ) or (
        event_direction < 0
        and snapshot.state == FinalParticipationState.DOWN_BREAK_RECLAIMED.value
    )


def _opposing_absorption(
    snapshot: VolumeEvidenceSnapshot,
    event_direction: int,
) -> bool:
    return (
        event_direction > 0
        and snapshot.state == FinalParticipationState.UPPER_ABSORPTION_CONFIRMED.value
    ) or (
        event_direction < 0
        and snapshot.state == FinalParticipationState.LOWER_ABSORPTION_CONFIRMED.value
    )


def build_structure_volume_risk(
    event: MarketStructureEventRecord,
    replay: Any,
    *,
    same_scope_events: Sequence[MarketStructureEventRecord],
    clock: AvailabilityClock,
) -> StructureVolumeRiskAssessment:
    state = StructureVolumeRiskState.UNKNOWN
    transitions: list[StructureVolumeRiskTransition] = []
    activated_at = released_at = None
    release_trigger = StructureVolumeRiskTrigger.NONE
    blocked_once = False
    rows = _row_by_bar(replay.input_batch.frame)
    later_events = sorted(
        (
            candidate
            for candidate in same_scope_events
            if candidate.timeframe == event.timeframe
            and candidate.scope == event.scope
            and candidate.event_bar > event.event_bar
        ),
        key=lambda candidate: candidate.event_bar,
    )
    next_event = later_events[0] if later_events else None

    def transition(
        snapshot: VolumeEvidenceSnapshot,
        next_state: StructureVolumeRiskState,
        trigger: StructureVolumeRiskTrigger,
        reason: str,
    ) -> None:
        nonlocal state, activated_at, released_at, release_trigger, blocked_once
        if state is next_state:
            return
        available_at = _availability(snapshot, clock)
        transitions.append(
            StructureVolumeRiskTransition(
                bar_index=snapshot.bar_index,
                timestamp=snapshot.timestamp,
                available_at=available_at,
                previous_state=state,
                state=next_state,
                trigger=trigger,
                source_state=snapshot.state,
                reason=reason,
            )
        )
        state = next_state
        if next_state in {
            StructureVolumeRiskState.BLOCKED_CONFIRMED_OPPOSITION,
            StructureVolumeRiskState.BLOCKED_VOLUME_CONFLICT,
            StructureVolumeRiskState.BLOCKED_FAKE_ABSORPTION_RISK,
        }:
            blocked_once = True
            activated_at = activated_at or available_at
            released_at = None
            release_trigger = StructureVolumeRiskTrigger.NONE
        elif next_state in {
            StructureVolumeRiskState.RELEASED_ALIGNED_RECOVERY,
            StructureVolumeRiskState.RELEASED_STRUCTURE_SUPERSEDED,
            StructureVolumeRiskState.RELEASED_FAKE_RECLAIM_RESOLVED,
        }:
            released_at = available_at
            release_trigger = trigger

    for snapshot in replay.history:
        if snapshot.bar_index < event.event_bar:
            continue
        if next_event is not None and snapshot.bar_index >= next_event.event_bar:
            if blocked_once:
                transition(
                    snapshot,
                    StructureVolumeRiskState.RELEASED_STRUCTURE_SUPERSEDED,
                    StructureVolumeRiskTrigger.AUTHORITATIVE_STRUCTURE_SUPERSESSION,
                    f"Authoritative same-scope Structure event {next_event.event_uid} superseded the monitored event.",
                )
            elif state is StructureVolumeRiskState.UNKNOWN:
                state = StructureVolumeRiskState.CLEAR
            break
        if not snapshot.has_usable_measurement:
            continue
        if state is StructureVolumeRiskState.UNKNOWN:
            state = StructureVolumeRiskState.CLEAR
        if _is_shock(snapshot):
            if blocked_once and state not in {
                StructureVolumeRiskState.RELEASED_ALIGNED_RECOVERY,
                StructureVolumeRiskState.RELEASED_FAKE_RECLAIM_RESOLVED,
            }:
                transition(
                    snapshot,
                    StructureVolumeRiskState.MONITORING_OPPOSITION_WEAKENED,
                    StructureVolumeRiskTrigger.OPPOSITION_WEAKENED,
                    "A one-bar shock is unconfirmed; it cannot release the active opposition block.",
                )
            continue

        direction = _mature_direction(snapshot)
        if direction == -int(event.direction):
            transition(
                snapshot,
                StructureVolumeRiskState.BLOCKED_CONFIRMED_OPPOSITION,
                StructureVolumeRiskTrigger.CONFIRMED_OPPOSING_VOLUME,
                "Confirmed same-timeframe Volume opposes the authoritative Structure direction.",
            )
            continue
        if snapshot.state == FinalParticipationState.CONFLICT.value:
            transition(
                snapshot,
                StructureVolumeRiskState.BLOCKED_VOLUME_CONFLICT,
                StructureVolumeRiskTrigger.CONFIRMED_VOLUME_CONFLICT,
                "Same-timeframe Volume conflict remains unresolved.",
            )
            continue
        if _structure_direction_reclaim(snapshot, int(event.direction)):
            transition(
                snapshot,
                StructureVolumeRiskState.BLOCKED_FAKE_ABSORPTION_RISK,
                StructureVolumeRiskTrigger.STRUCTURE_DIRECTION_RECLAIM,
                "The Volume break aligned with Structure was reclaimed; fake/absorption risk is blocked.",
            )
            continue

        row = rows.get(snapshot.bar_index)
        close = None if row is None else float(row["close"])
        if (
            blocked_once
            and _explicit_fake_reclaim_resolution(snapshot, int(event.direction))
            and close is not None
            and _accepted_structure_level(event, close)
        ):
            transition(
                snapshot,
                StructureVolumeRiskState.RELEASED_FAKE_RECLAIM_RESOLVED,
                StructureVolumeRiskTrigger.COMPLETED_FAKE_RECLAIM_RESOLUTION,
                "Opposing fake/reclaim state resolved and price re-accepted the authoritative Structure level.",
            )
            continue
        if blocked_once and direction == int(event.direction):
            transition(
                snapshot,
                StructureVolumeRiskState.RELEASED_ALIGNED_RECOVERY,
                StructureVolumeRiskTrigger.ALIGNED_RECOVERY,
                "Confirmed same-timeframe aligned Volume established recovery.",
            )
            continue
        if blocked_once and state not in {
            StructureVolumeRiskState.RELEASED_ALIGNED_RECOVERY,
            StructureVolumeRiskState.RELEASED_FAKE_RECLAIM_RESOLVED,
        }:
            transition(
                snapshot,
                StructureVolumeRiskState.MONITORING_OPPOSITION_WEAKENED,
                StructureVolumeRiskTrigger.OPPOSITION_WEAKENED,
                "Opposing pressure weakened, but no aligned recovery or explicit resolution was confirmed.",
            )

    blocked = state in {
        StructureVolumeRiskState.BLOCKED_CONFIRMED_OPPOSITION,
        StructureVolumeRiskState.BLOCKED_VOLUME_CONFLICT,
        StructureVolumeRiskState.BLOCKED_FAKE_ABSORPTION_RISK,
        StructureVolumeRiskState.MONITORING_OPPOSITION_WEAKENED,
    }
    return StructureVolumeRiskAssessment(
        event_uid=event.event_uid,
        symbol=event.symbol or replay.latest.symbol,
        timeframe=event.timeframe or replay.timeframe,
        scope=event.scope,
        event_type=event.event_type,
        event_direction=int(event.direction),
        state=state,
        is_blocked=blocked,
        activated_at=activated_at,
        released_at=released_at,
        release_trigger=release_trigger,
        transitions=tuple(transitions),
    )


def _shock_uid(symbol: str, timeframe: str, snapshot: VolumeEvidenceSnapshot) -> str:
    payload = f"{symbol}|{timeframe}|{snapshot.bar_index}|{pd.Timestamp(snapshot.timestamp).isoformat()}"
    return f"VOL-SHOCK-{sha1(payload.encode('utf-8')).hexdigest()[:16]}"


def _shock_absorption(snapshot: VolumeEvidenceSnapshot, direction: int) -> bool:
    return (
        direction > 0
        and snapshot.state == FinalParticipationState.UPPER_ABSORPTION_CONFIRMED.value
    ) or (
        direction < 0
        and snapshot.state == FinalParticipationState.LOWER_ABSORPTION_CONFIRMED.value
    )


def build_shock_lifecycles(
    replay: Any,
    *,
    clock: AvailabilityClock,
    config: VolumeShockLifecycleConfig | None = None,
) -> tuple[VolumeShockLifecycle, ...]:
    resolved_config = config or VolumeShockLifecycleConfig()
    rows = _row_by_bar(replay.input_batch.frame)
    history_by_bar = {snapshot.bar_index: snapshot for snapshot in replay.history}
    lifecycles: list[VolumeShockLifecycle] = []

    for shock in replay.history:
        if not shock.has_usable_measurement or not _is_shock(shock):
            continue
        row = rows[shock.bar_index]
        shock_open = float(row["open"])
        shock_close = float(row["close"])
        midpoint = (float(row["high"]) + float(row["low"])) / 2.0
        direction = int(shock.audit_export.shock_direction)
        confirmation_deadline = shock.bar_index + resolved_config.confirmation_bars
        monitor_deadline = shock.bar_index + resolved_config.monitor_bars
        transitions = [
            VolumeShockTransition(
                bar_index=shock.bar_index,
                timestamp=shock.timestamp,
                available_at=_availability(shock, clock),
                stage=VolumeShockStage.DETECTED_UNCONFIRMED,
                reason="A one-bar Volume explosion is retained as unconfirmed shock evidence.",
            )
        ]
        stage = VolumeShockStage.DETECTED_UNCONFIRMED
        last_bar = len(replay.history) - 1

        if direction == 0:
            if last_bar >= confirmation_deadline:
                stage = VolumeShockStage.DIRECTIONLESS_UNRESOLVED
                deadline = history_by_bar[confirmation_deadline]
                transitions.append(
                    VolumeShockTransition(
                        bar_index=deadline.bar_index,
                        timestamp=deadline.timestamp,
                        available_at=_availability(deadline, clock),
                        stage=stage,
                        reason="The shock had no deterministic direction and cannot become confirmation.",
                    )
                )
        else:
            upper = min(monitor_deadline, last_bar)
            for bar_index in range(shock.bar_index + 1, upper + 1):
                current = history_by_bar[bar_index]
                close = float(rows[bar_index]["close"])
                reclaimed = (
                    direction > 0 and close <= shock_open
                ) or (
                    direction < 0 and close >= shock_open
                )
                if reclaimed:
                    stage = VolumeShockStage.RECLAIMED
                    transitions.append(
                        VolumeShockTransition(
                            bar_index=bar_index,
                            timestamp=current.timestamp,
                            available_at=_availability(current, clock),
                            stage=stage,
                            reason="Price reclaimed the shock origin; the explosion resolved as failed/fake participation.",
                        )
                    )
                    break
                if _shock_absorption(current, direction):
                    if stage is not VolumeShockStage.ABSORPTION_RISK:
                        stage = VolumeShockStage.ABSORPTION_RISK
                        transitions.append(
                            VolumeShockTransition(
                                bar_index=bar_index,
                                timestamp=current.timestamp,
                                available_at=_availability(current, clock),
                                stage=stage,
                                reason="Confirmed opposing absorption appeared after the shock.",
                            )
                        )
                    continue
                if bar_index <= confirmation_deadline:
                    progress = (
                        direction > 0 and close > shock_close
                    ) or (
                        direction < 0 and close < shock_close
                    )
                    if _mature_direction(current) == direction and progress:
                        stage = VolumeShockStage.FOLLOW_THROUGH_CONFIRMED
                        transitions.append(
                            VolumeShockTransition(
                                bar_index=bar_index,
                                timestamp=current.timestamp,
                                available_at=_availability(current, clock),
                                stage=stage,
                                reason="A later non-shock bar confirmed aligned Volume and price follow-through.",
                            )
                        )
                        continue
                    midpoint_lost = (
                        direction > 0 and close < midpoint
                    ) or (
                        direction < 0 and close > midpoint
                    )
                    if midpoint_lost and stage is VolumeShockStage.DETECTED_UNCONFIRMED:
                        stage = VolumeShockStage.FAKE_SUSPECTED
                        transitions.append(
                            VolumeShockTransition(
                                bar_index=bar_index,
                                timestamp=current.timestamp,
                                available_at=_availability(current, clock),
                                stage=stage,
                                reason="Price lost the shock midpoint before independent follow-through confirmation.",
                            )
                        )
                if (
                    bar_index == confirmation_deadline
                    and stage is VolumeShockStage.DETECTED_UNCONFIRMED
                ):
                    stage = VolumeShockStage.FAKE_SUSPECTED
                    transitions.append(
                        VolumeShockTransition(
                            bar_index=bar_index,
                            timestamp=current.timestamp,
                            available_at=_availability(current, clock),
                            stage=stage,
                            reason="The causal confirmation window ended without aligned follow-through.",
                        )
                    )

        lifecycles.append(
            VolumeShockLifecycle(
                shock_uid=_shock_uid(replay.latest.symbol, replay.timeframe, shock),
                symbol=replay.latest.symbol,
                timeframe=replay.timeframe,
                shock_bar=shock.bar_index,
                shock_at=shock.timestamp,
                direction=direction,
                final_stage=stage,
                transitions=tuple(transitions),
                confirmation_deadline_bar=confirmation_deadline,
                monitor_deadline_bar=monitor_deadline,
            )
        )
    return tuple(lifecycles)


def _event_available_at(
    event: MarketStructureEventRecord,
    clock: AvailabilityClock,
) -> pd.Timestamp:
    if event.timeframe is None:
        raise ValueError("Structure event must be namespaced with a timeframe")
    return clock.available_at(event.confirmed_at, event.timeframe)


def _all_structure_events(structure_snapshots: Sequence[Any]) -> tuple[MarketStructureEventRecord, ...]:
    return tuple(
        event
        for snapshot in structure_snapshots
        for event in snapshot.events
        if event.timeframe in _TIMEFRAME_RANK and event.confirmed_at is not None
    )


def build_structural_propagations(
    timeframe_replays: Sequence[Any],
    structure_snapshots: Sequence[Any],
    *,
    clock: AvailabilityClock,
    final_as_of: pd.Timestamp,
) -> tuple[VolumeStructurePropagation, ...]:
    events = _all_structure_events(structure_snapshots)
    propagations: list[VolumeStructurePropagation] = []

    for replay in timeframe_replays:
        latest_by_direction: dict[int, Any] = {}
        for record in replay.participation_without_structure:
            if record.evidence_direction in {-1, 1}:
                latest_by_direction[record.evidence_direction] = record
        for direction, origin in sorted(latest_by_direction.items()):
            origin_available = clock.available_at(origin.timestamp, origin.timeframe)
            origin_rank = _TIMEFRAME_RANK[origin.timeframe]
            selected: list[tuple[MarketStructureEventRecord, StructuralPropagationPhase]] = []
            for event in events:
                event_rank = _TIMEFRAME_RANK[event.timeframe or ""]
                available_at = _event_available_at(event, clock)
                if available_at > final_as_of:
                    continue
                if event_rank < origin_rank and available_at <= origin_available:
                    phase = StructuralPropagationPhase.LOWER_TIMEFRAME_CONTEXT
                elif event_rank == origin_rank and available_at > origin_available:
                    phase = StructuralPropagationPhase.ORIGIN_TIMEFRAME_FOLLOW
                elif event_rank > origin_rank and available_at > origin_available:
                    phase = StructuralPropagationPhase.HIGHER_TIMEFRAME_FOLLOW
                else:
                    continue
                selected.append((event, phase))

            selected.sort(key=lambda item: (_event_available_at(item[0], clock), item[0].event_uid))
            steps = tuple(
                StructuralPropagationStep(
                    event_uid=event.event_uid,
                    timeframe=event.timeframe or "",
                    scope=event.scope,
                    event_type=event.event_type,
                    event_direction=int(event.direction),
                    confirmed_at=event.confirmed_at,
                    available_at=_event_available_at(event, clock),
                    phase=phase,
                )
                for event, phase in selected
            )
            aligned = tuple(step for step in steps if step.event_direction == direction)
            opposed = tuple(step for step in steps if step.event_direction == -direction)
            follow_aligned = tuple(
                step
                for step in aligned
                if step.phase is not StructuralPropagationPhase.LOWER_TIMEFRAME_CONTEXT
            )
            if aligned and opposed:
                state = VolumeStructurePropagationState.CONFLICTED
            elif any(
                step.phase is StructuralPropagationPhase.HIGHER_TIMEFRAME_FOLLOW
                for step in follow_aligned
            ):
                state = VolumeStructurePropagationState.HIGHER_TIMEFRAME_DIRECT_CONFIRMATION
            elif any(
                step.phase is StructuralPropagationPhase.ORIGIN_TIMEFRAME_FOLLOW
                for step in follow_aligned
            ):
                state = VolumeStructurePropagationState.SAME_TIMEFRAME_DIRECT_CONFIRMATION
            elif any(step.scope.strip().upper() == "EXTERNAL" for step in aligned):
                state = VolumeStructurePropagationState.LOWER_EXTERNAL_PRESENT
            elif aligned:
                state = VolumeStructurePropagationState.LOWER_INTERNAL_ONLY
            else:
                state = VolumeStructurePropagationState.NO_STRUCTURE
            highest = (
                None
                if not aligned
                else max(aligned, key=lambda step: _TIMEFRAME_RANK[step.timeframe]).timeframe
            )
            propagations.append(
                VolumeStructurePropagation(
                    symbol=origin.symbol,
                    origin_timeframe=origin.timeframe,
                    origin_bar=origin.bar_index,
                    origin_at=origin.timestamp,
                    volume_direction=direction,
                    volume_state=origin.state,
                    state=state,
                    steps=steps,
                    highest_direct_timeframe=highest,
                    reasons=(
                        "Only directly confirmed i/eCHoCH and i/eBOS events are listed.",
                        "Lower-timeframe progression never invents or promotes a higher-timeframe Structure event.",
                    ),
                )
            )
    return tuple(propagations)


def build_correlated_volume_deduplication(
    active_channels: Sequence[CorrelatedVolumeChannel] = (
        CorrelatedVolumeChannel.VOLUME_PARTICIPATION,
    ),
) -> CorrelatedVolumeDeduplication:
    normalized = tuple(active_channels)
    if len(set(normalized)) != len(normalized):
        raise ValueError("active correlated-volume channels must be unique")
    registered = tuple(CorrelatedVolumeChannel)
    unsupported = tuple(channel for channel in normalized if channel not in registered)
    if unsupported:
        raise ValueError(f"unsupported correlated-volume channels: {unsupported}")
    representative = (
        CorrelatedVolumeChannel.VOLUME_PARTICIPATION
        if CorrelatedVolumeChannel.VOLUME_PARTICIPATION in normalized
        else normalized[0]
        if normalized
        else CorrelatedVolumeChannel.VOLUME_PARTICIPATION
    )
    return CorrelatedVolumeDeduplication(
        source_family="OHLCV_SOURCE_VOLUME",
        registered_channels=registered,
        active_channels=normalized,
        representative_channel=representative,
        independent_vote_cap=1,
        raw_mtf_volume_summed=False,
        policy="SHARED_SOURCE_SINGLE_CORRELATED_FAMILY_NO_VOTE_STACKING",
    )


def build_volume_round2_assessment(
    *,
    symbol: str,
    timeframe_replays: Sequence[Any],
    structure_snapshots: Sequence[Any],
    clock: AvailabilityClock,
) -> VolumeRound2Assessment:
    latest_availability = tuple(
        _availability(replay.latest, clock)
        for replay in timeframe_replays
        if replay.latest is not None
    )
    if not latest_availability:
        raise ValueError("Volume Round 2 assessment requires at least one snapshot")
    final_as_of = max(latest_availability)
    pressure = build_mtf_pressure_context(timeframe_replays, clock=clock)
    event_assessments = build_event_mtf_assessments(
        timeframe_replays,
        clock=clock,
        final_as_of=final_as_of,
    )
    all_events = _all_structure_events(structure_snapshots)
    replay_by_timeframe = {replay.timeframe: replay for replay in timeframe_replays}
    linked_uids = {
        link.event_uid
        for replay in timeframe_replays
        for link in replay.event_links
    }
    risks = tuple(
        build_structure_volume_risk(
            event,
            replay_by_timeframe[event.timeframe or ""],
            same_scope_events=all_events,
            clock=clock,
        )
        for event in all_events
        if event.event_uid in linked_uids and event.timeframe in replay_by_timeframe
    )
    shocks = tuple(
        shock
        for replay in timeframe_replays
        for shock in build_shock_lifecycles(replay, clock=clock)
    )
    propagations = build_structural_propagations(
        timeframe_replays,
        structure_snapshots,
        clock=clock,
        final_as_of=final_as_of,
    )
    return VolumeRound2Assessment(
        symbol=symbol.strip().upper(),
        as_of=final_as_of,
        pressure=pressure,
        event_assessments=event_assessments,
        risks=risks,
        shocks=shocks,
        structural_propagations=propagations,
        deduplication=build_correlated_volume_deduplication(),
    )
