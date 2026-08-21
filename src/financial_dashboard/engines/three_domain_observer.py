from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha1
from math import isfinite
from typing import Any, Iterable

from .market_structure_events import (
    MarketStructureEventRecord,
    StructureEventConfirmation,
    StructureEventOutcome,
    StructureEventRelevance,
    StructureEventValidity,
)
from .market_structure_state import EVENT_BOS, EVENT_CHOCH
from .models import Direction
from .mtf_story_models import (
    ContextAssessment,
    ContextState,
    MTFStoryResult,
    TimeframeStoryState,
    TriggerAssessment,
    TriggerState,
)
from .structure_location import (
    StructureLocationOutcome,
    ZoneConfluenceCluster,
)
from .support_resistance_zones import SupportResistanceZone, ZoneSide


FOUNDATION_OBSERVER_TIMEFRAMES = ("1d", "4h", "2h", "1h", "30m")


class MTFPressureState(StrEnum):
    BEARISH_PRESSURE = "BEARISH_PRESSURE"
    BEARISH_PRESSURE_WEAKENING = "BEARISH_PRESSURE_WEAKENING"
    BALANCED_PRESSURE = "BALANCED_PRESSURE"
    BULLISH_PRESSURE_WEAKENING = "BULLISH_PRESSURE_WEAKENING"
    BULLISH_PRESSURE = "BULLISH_PRESSURE"
    TRANSITIONAL_PRESSURE = "TRANSITIONAL_PRESSURE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class PressureChange(StrEnum):
    STABLE = "STABLE"
    WEAKENING = "WEAKENING"
    TRANSITION = "TRANSITION"
    INSUFFICIENT = "INSUFFICIENT"


class RecoveryStatus(StrEnum):
    NONE = "NONE"
    LOWER_TIMEFRAME_REACTION_ONLY = "LOWER_TIMEFRAME_REACTION_ONLY"
    HIGHER_TIMEFRAME_RECOVERY_BUILDING = "HIGHER_TIMEFRAME_RECOVERY_BUILDING"
    HIGHER_TIMEFRAME_DOWNSIDE_TRANSITION = "HIGHER_TIMEFRAME_DOWNSIDE_TRANSITION"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class MTFPressureSnapshot:
    """Conservative MTF pressure with recovery evidence, never a trade decision."""

    state: MTFPressureState
    anchor_direction: Direction
    lower_timeframe_direction: Direction
    change: PressureChange
    recovery_status: RecoveryStatus
    context: ContextAssessment
    trigger: TriggerAssessment
    story: MTFStoryResult
    timeframe_states: tuple[TimeframeStoryState, ...]
    reasons: tuple[str, ...] = ()

    @property
    def is_weakening(self) -> bool:
        return self.change is PressureChange.WEAKENING


class StructureProgressionStage(StrEnum):
    NONE = "NONE"
    M30_CHOCH = "M30_CHOCH"
    M30_BOS = "M30_BOS"
    H1_CHOCH = "H1_CHOCH"
    H1_BOS = "H1_BOS"
    H2_CHOCH = "H2_CHOCH"
    H2_BOS = "H2_BOS"
    H4_CHOCH = "H4_CHOCH"
    H4_BOS = "H4_BOS"
    D1_CHOCH = "D1_CHOCH"
    D1_BOS = "D1_BOS"


_STAGE_BY_EVENT: dict[tuple[str, str], tuple[StructureProgressionStage, int]] = {
    ("30m", EVENT_CHOCH): (StructureProgressionStage.M30_CHOCH, 1),
    ("30m", EVENT_BOS): (StructureProgressionStage.M30_BOS, 2),
    ("1h", EVENT_CHOCH): (StructureProgressionStage.H1_CHOCH, 3),
    ("1h", EVENT_BOS): (StructureProgressionStage.H1_BOS, 4),
    ("2h", EVENT_CHOCH): (StructureProgressionStage.H2_CHOCH, 5),
    ("2h", EVENT_BOS): (StructureProgressionStage.H2_BOS, 6),
    ("4h", EVENT_CHOCH): (StructureProgressionStage.H4_CHOCH, 7),
    ("4h", EVENT_BOS): (StructureProgressionStage.H4_BOS, 8),
    ("1d", EVENT_CHOCH): (StructureProgressionStage.D1_CHOCH, 9),
    ("1d", EVENT_BOS): (StructureProgressionStage.D1_BOS, 10),
}


@dataclass(frozen=True, slots=True)
class CausalStructureEventObservation:
    event: MarketStructureEventRecord
    available_at: Any


@dataclass(frozen=True, slots=True)
class DirectionalStructureProgression:
    """Highest directly confirmed external event for one direction.

    A lower-timeframe event cannot infer or promote a higher timeframe.  Rank only
    changes when a direct BOS/CHoCH fact exists on that timeframe.
    """

    direction: Direction
    stage: StructureProgressionStage
    rank: int
    timeframe: str | None
    event_uid: str | None
    event_type: str | None
    directly_confirmed_timeframes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StructureProgressionSnapshot:
    symbol: str | None
    as_of: Any
    timeframes: tuple[str, ...]
    upward: DirectionalStructureProgression
    downward: DirectionalStructureProgression
    latest_external_events: tuple[MarketStructureEventRecord, ...]
    latest_internal_events: tuple[MarketStructureEventRecord, ...]

    def latest_external_for(self, timeframe: str) -> MarketStructureEventRecord | None:
        normalized = timeframe.strip().lower()
        return next(
            (event for event in self.latest_external_events if event.timeframe == normalized),
            None,
        )

    def latest_internal_for(self, timeframe: str) -> MarketStructureEventRecord | None:
        normalized = timeframe.strip().lower()
        return next(
            (event for event in self.latest_internal_events if event.timeframe == normalized),
            None,
        )


class ZoneRoleConflictKind(StrEnum):
    OVERLAP = "OVERLAP"
    COMPRESSION_GAP = "COMPRESSION_GAP"


@dataclass(frozen=True, slots=True)
class OpposingZoneConflictConfig:
    max_gap_atr: float = 0.10

    def __post_init__(self) -> None:
        if not isfinite(self.max_gap_atr) or self.max_gap_atr < 0.0:
            raise ValueError("max_gap_atr must be finite and >= 0")


@dataclass(frozen=True, slots=True)
class OpposingZoneConflict:
    conflict_uid: str
    symbol: str
    support_zone_uid: str
    resistance_zone_uid: str
    support_timeframe: str
    resistance_timeframe: str
    kind: ZoneRoleConflictKind
    overlap_low: float | None
    overlap_high: float | None
    gap: float
    gap_atr: float


class LocationContextState(StrEnum):
    NO_ESTABLISHED_LOCATION = "NO_ESTABLISHED_LOCATION"
    ESTABLISHED_ZONES = "ESTABLISHED_ZONES"
    SUPPORT_CONFLUENCE = "SUPPORT_CONFLUENCE"
    RESISTANCE_CONFLUENCE = "RESISTANCE_CONFLUENCE"
    TWO_SIDED_CONFLUENCE = "TWO_SIDED_CONFLUENCE"
    OPPOSING_ZONE_CONFLICT = "OPPOSING_ZONE_CONFLICT"


@dataclass(frozen=True, slots=True)
class LocationContextSnapshot:
    symbol: str | None
    state: LocationContextState
    established_zones: tuple[SupportResistanceZone, ...]
    confluence: tuple[ZoneConfluenceCluster, ...]
    opposing_conflicts: tuple[OpposingZoneConflict, ...]
    event_outcomes: tuple[StructureLocationOutcome, ...]
    linked_event_count: int
    no_match_event_count: int


class ObserverTensionCode(StrEnum):
    LOWER_TF_OPPOSES_GENERAL_PRESSURE = "LOWER_TF_OPPOSES_GENERAL_PRESSURE"
    HIGHER_TF_STRUCTURE_OPPOSES_GENERAL_PRESSURE = (
        "HIGHER_TF_STRUCTURE_OPPOSES_GENERAL_PRESSURE"
    )
    OPPOSING_LOCATION_ZONES = "OPPOSING_LOCATION_ZONES"


class CombinedObservationState(StrEnum):
    DOMAINS_REPORTED = "DOMAINS_REPORTED"
    CROSS_DOMAIN_TENSION = "CROSS_DOMAIN_TENSION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True, slots=True)
class ThreeDomainObservation:
    """Descriptive three-domain view. It intentionally has no action field."""

    observer_uid: str
    symbol: str
    as_of: Any
    state: CombinedObservationState
    pressure: MTFPressureSnapshot
    structure: StructureProgressionSnapshot
    location: LocationContextSnapshot
    tensions: tuple[ObserverTensionCode, ...] = ()
    facts: tuple[str, ...] = ()
    contract_version: int = 1


def build_mtf_pressure(
    context: ContextAssessment,
    trigger: TriggerAssessment,
    story: MTFStoryResult,
    timeframe_states: Iterable[TimeframeStoryState],
) -> MTFPressureSnapshot:
    """Map the existing MTF story to a deliberately conservative pressure view."""

    states = tuple(timeframe_states)
    if story.context_state is not context.state:
        raise ValueError("story context state does not match pressure context")
    if story.trigger_state is not trigger.state:
        raise ValueError("story trigger state does not match pressure trigger")
    if story.timeframe_states and story.timeframe_states != states:
        raise ValueError("story timeframe states do not match pressure evidence")
    anchor = context.direction
    lower = trigger.direction
    reasons: list[str] = []

    if (
        context.state is ContextState.INSUFFICIENT_DATA
        or trigger.state is TriggerState.INSUFFICIENT_DATA
    ):
        state = MTFPressureState.INSUFFICIENT_DATA
        change = PressureChange.INSUFFICIENT
        recovery_status = RecoveryStatus.INSUFFICIENT
        anchor = Direction.NEUTRAL
        reasons.append("PRESSURE:INSUFFICIENT_CONTEXT_OR_LOWER_TIMEFRAME_DATA")
    elif context.state is ContextState.TRANSITION_CONTEXT:
        state = MTFPressureState.TRANSITIONAL_PRESSURE
        change = PressureChange.TRANSITION
        recovery_status = (
            RecoveryStatus.HIGHER_TIMEFRAME_RECOVERY_BUILDING
            if anchor is Direction.UP
            else RecoveryStatus.HIGHER_TIMEFRAME_DOWNSIDE_TRANSITION
            if anchor is Direction.DOWN
            else RecoveryStatus.NONE
        )
        reasons.append(f"PRESSURE:HIGHER_TIMEFRAME_TRANSITION:{anchor.name}")
    elif context.state is ContextState.BEARISH_CONTEXT:
        if lower is Direction.UP:
            state = MTFPressureState.BEARISH_PRESSURE_WEAKENING
            change = PressureChange.WEAKENING
            recovery_status = RecoveryStatus.LOWER_TIMEFRAME_REACTION_ONLY
            reasons.extend(
                (
                    "PRESSURE:LOWER_TIMEFRAMES_OPPOSE_BEARISH_CONTEXT",
                    "PRESSURE:WEAKENING_IS_NOT_BULLISH_RECOVERY",
                )
            )
        else:
            state = MTFPressureState.BEARISH_PRESSURE
            change = PressureChange.STABLE
            recovery_status = RecoveryStatus.NONE
            reasons.append("PRESSURE:BEARISH_HIGHER_TIMEFRAME_ANCHOR_RETAINED")
    elif context.state is ContextState.BULLISH_CONTEXT:
        if lower is Direction.DOWN:
            state = MTFPressureState.BULLISH_PRESSURE_WEAKENING
            change = PressureChange.WEAKENING
            recovery_status = RecoveryStatus.LOWER_TIMEFRAME_REACTION_ONLY
            reasons.extend(
                (
                    "PRESSURE:LOWER_TIMEFRAMES_OPPOSE_BULLISH_CONTEXT",
                    "PRESSURE:WEAKENING_IS_NOT_BEARISH_RECOVERY",
                )
            )
        else:
            state = MTFPressureState.BULLISH_PRESSURE
            change = PressureChange.STABLE
            recovery_status = RecoveryStatus.NONE
            reasons.append("PRESSURE:BULLISH_HIGHER_TIMEFRAME_ANCHOR_RETAINED")
    else:
        state = MTFPressureState.BALANCED_PRESSURE
        change = PressureChange.STABLE
        recovery_status = RecoveryStatus.NONE
        anchor = Direction.NEUTRAL
        reasons.append("PRESSURE:HIGHER_TIMEFRAME_CONTEXT_MIXED")

    return MTFPressureSnapshot(
        state=state,
        anchor_direction=anchor,
        lower_timeframe_direction=lower,
        change=change,
        recovery_status=recovery_status,
        context=context,
        trigger=trigger,
        story=story,
        timeframe_states=states,
        reasons=tuple(reasons),
    )


def _event_order(observation: CausalStructureEventObservation) -> tuple[Any, int, int]:
    return (
        observation.available_at,
        observation.event.event_bar,
        observation.event.identity,
    )


def _latest_scope_events(
    observations: Iterable[CausalStructureEventObservation],
    *,
    as_of: Any,
    scope: str,
    timeframes: tuple[str, ...],
) -> tuple[MarketStructureEventRecord, ...]:
    allowed = set(timeframes)
    causal = tuple(observations)
    availability_by_uid = {
        observation.event.event_uid: observation.available_at
        for observation in causal
    }
    latest: dict[str, CausalStructureEventObservation] = {}
    for observation in causal:
        event = observation.event
        timeframe = (event.timeframe or "").strip().lower()
        if (
            observation.available_at > as_of
            or event.scope != scope
            or event.event_type not in {EVENT_BOS, EVENT_CHOCH}
            or timeframe not in allowed
        ):
            continue
        previous = latest.get(timeframe)
        if previous is None or _event_order(observation) > _event_order(previous):
            latest[timeframe] = observation

    def event_as_of(event: MarketStructureEventRecord) -> MarketStructureEventRecord:
        failure_available_at = (
            availability_by_uid.get(event.failed_by_event_uid)
            if event.failed_by_event_uid is not None
            else None
        )
        if (
            event.validity is StructureEventValidity.FAILED
            and failure_available_at is not None
            and failure_available_at > as_of
        ):
            return replace(
                event,
                validity=StructureEventValidity.VALID,
                relevance=StructureEventRelevance.CURRENT,
                outcome=StructureEventOutcome.PENDING,
                failed_by_event_uid=None,
            )
        return event

    return tuple(
        event_as_of(latest[timeframe].event)
        for timeframe in timeframes
        if timeframe in latest
    )


def _directional_progression(
    direction: Direction,
    latest_external: tuple[MarketStructureEventRecord, ...],
    timeframes: tuple[str, ...],
) -> DirectionalStructureProgression:
    candidates: list[tuple[int, MarketStructureEventRecord, StructureProgressionStage]] = []
    confirmed_timeframes: list[str] = []
    for event in latest_external:
        timeframe = (event.timeframe or "").strip().lower()
        if (
            event.direction is not direction
            or event.confirmation_status is not StructureEventConfirmation.CONFIRMED
            or event.validity is not StructureEventValidity.VALID
        ):
            continue
        stage_record = _STAGE_BY_EVENT.get((timeframe, event.event_type))
        if stage_record is None:
            continue
        stage, rank = stage_record
        candidates.append((rank, event, stage))
        confirmed_timeframes.append(timeframe)

    if not candidates:
        return DirectionalStructureProgression(
            direction=direction,
            stage=StructureProgressionStage.NONE,
            rank=0,
            timeframe=None,
            event_uid=None,
            event_type=None,
        )

    rank, event, stage = max(candidates, key=lambda item: (item[0], item[1].event_bar))
    ordered_confirmed = tuple(
        timeframe for timeframe in timeframes if timeframe in set(confirmed_timeframes)
    )
    return DirectionalStructureProgression(
        direction=direction,
        stage=stage,
        rank=rank,
        timeframe=event.timeframe,
        event_uid=event.event_uid,
        event_type=event.event_type,
        directly_confirmed_timeframes=ordered_confirmed,
    )


def build_structure_progression(
    observations: Iterable[CausalStructureEventObservation],
    *,
    as_of: Any,
    timeframes: tuple[str, ...] = FOUNDATION_OBSERVER_TIMEFRAMES,
    symbol: str | None = None,
) -> StructureProgressionSnapshot:
    """Build external progression while preserving internal events separately."""

    normalized = tuple(timeframe.strip().lower() for timeframe in timeframes)
    if not normalized or not all(normalized):
        raise ValueError("at least one non-empty timeframe is required")
    if len(set(normalized)) != len(normalized):
        raise ValueError("timeframes must be unique after normalization")
    unsupported = tuple(timeframe for timeframe in normalized if timeframe not in FOUNDATION_OBSERVER_TIMEFRAMES)
    if unsupported:
        raise ValueError("unsupported progression timeframes: " + ", ".join(unsupported))

    causal = tuple(observations)
    observed_symbols = {
        observation.event.symbol
        for observation in causal
        if observation.event.symbol is not None
    }
    if symbol is None:
        if len(observed_symbols) > 1:
            raise ValueError("structure progression cannot mix symbols")
        resolved_symbol = next(iter(observed_symbols), None)
    else:
        resolved_symbol = symbol
        foreign = observed_symbols - {symbol}
        if foreign:
            raise ValueError("structure progression contains a foreign symbol")
    latest_external = _latest_scope_events(
        causal,
        as_of=as_of,
        scope="EXTERNAL",
        timeframes=normalized,
    )
    latest_internal = _latest_scope_events(
        causal,
        as_of=as_of,
        scope="INTERNAL",
        timeframes=normalized,
    )
    return StructureProgressionSnapshot(
        symbol=resolved_symbol,
        as_of=as_of,
        timeframes=normalized,
        upward=_directional_progression(Direction.UP, latest_external, normalized),
        downward=_directional_progression(Direction.DOWN, latest_external, normalized),
        latest_external_events=latest_external,
        latest_internal_events=latest_internal,
    )


def _zone_gap(first: SupportResistanceZone, second: SupportResistanceZone) -> float:
    return max(0.0, max(first.low, second.low) - min(first.high, second.high))


def find_opposing_zone_conflicts(
    zones: Iterable[SupportResistanceZone],
    config: OpposingZoneConflictConfig | None = None,
) -> tuple[OpposingZoneConflict, ...]:
    """Represent opposing-role overlap/proximity as conflict, never confluence."""

    config = config or OpposingZoneConflictConfig()
    established = tuple(
        zone
        for zone in zones
        if zone.is_confluence_eligible
        and zone.symbol is not None
        and zone.timeframe is not None
    )
    for zone in established:
        if (
            not all(isfinite(value) for value in (zone.low, zone.high, zone.reference_atr))
            or zone.low > zone.high
            or zone.reference_atr <= 0.0
        ):
            raise ValueError(
                f"zone {zone.zone_uid} requires finite ordered bounds and positive ATR"
            )
    supports = sorted(
        (zone for zone in established if zone.side is ZoneSide.SUPPORT),
        key=lambda zone: zone.zone_uid,
    )
    resistances = sorted(
        (zone for zone in established if zone.side is ZoneSide.RESISTANCE),
        key=lambda zone: zone.zone_uid,
    )
    conflicts: list[OpposingZoneConflict] = []
    for support in supports:
        for resistance in resistances:
            if support.symbol != resistance.symbol:
                continue
            gap = _zone_gap(support, resistance)
            reference_atr = max(min(support.reference_atr, resistance.reference_atr), 1e-12)
            gap_atr = gap / reference_atr
            if gap_atr > config.max_gap_atr:
                continue
            overlap_low = max(support.low, resistance.low)
            overlap_high = min(support.high, resistance.high)
            overlaps = overlap_low <= overlap_high
            seed = f"{support.zone_uid}|{resistance.zone_uid}|OPPOSING_ZONE"
            digest = sha1(seed.encode("utf-8")).hexdigest()[:14]
            conflicts.append(
                OpposingZoneConflict(
                    conflict_uid=f"OPPOSING_ZONE_CONFLICT:{digest}",
                    symbol=support.symbol,
                    support_zone_uid=support.zone_uid,
                    resistance_zone_uid=resistance.zone_uid,
                    support_timeframe=support.timeframe or "",
                    resistance_timeframe=resistance.timeframe or "",
                    kind=(
                        ZoneRoleConflictKind.OVERLAP
                        if overlaps
                        else ZoneRoleConflictKind.COMPRESSION_GAP
                    ),
                    overlap_low=overlap_low if overlaps else None,
                    overlap_high=overlap_high if overlaps else None,
                    gap=round(gap, 10),
                    gap_atr=round(gap_atr, 10),
                )
            )
    return tuple(sorted(conflicts, key=lambda conflict: conflict.conflict_uid))


def build_location_context(
    zones: Iterable[SupportResistanceZone],
    confluence: Iterable[ZoneConfluenceCluster],
    event_outcomes: Iterable[StructureLocationOutcome],
    *,
    symbol: str | None = None,
    conflict_config: OpposingZoneConflictConfig | None = None,
) -> LocationContextSnapshot:
    all_zones = tuple(zones)
    clusters = tuple(confluence)
    observed_symbols = {
        *(
            zone.symbol
            for zone in all_zones
            if zone.symbol is not None
        ),
        *(cluster.symbol for cluster in clusters),
    }
    if symbol is None:
        if len(observed_symbols) > 1:
            raise ValueError("location context cannot mix symbols")
        resolved_symbol = next(iter(observed_symbols), None)
    else:
        resolved_symbol = symbol
        if observed_symbols - {symbol}:
            raise ValueError("location context contains a foreign symbol")
    established = tuple(
        sorted(
            (zone for zone in all_zones if zone.is_confluence_eligible),
            key=lambda zone: zone.zone_uid,
        )
    )
    outcomes = tuple(event_outcomes)
    conflicts = find_opposing_zone_conflicts(established, conflict_config)
    support_confluence = any(cluster.side is ZoneSide.SUPPORT for cluster in clusters)
    resistance_confluence = any(cluster.side is ZoneSide.RESISTANCE for cluster in clusters)

    if conflicts:
        state = LocationContextState.OPPOSING_ZONE_CONFLICT
    elif support_confluence and resistance_confluence:
        state = LocationContextState.TWO_SIDED_CONFLUENCE
    elif support_confluence:
        state = LocationContextState.SUPPORT_CONFLUENCE
    elif resistance_confluence:
        state = LocationContextState.RESISTANCE_CONFLUENCE
    elif established:
        state = LocationContextState.ESTABLISHED_ZONES
    else:
        state = LocationContextState.NO_ESTABLISHED_LOCATION

    return LocationContextSnapshot(
        symbol=resolved_symbol,
        state=state,
        established_zones=established,
        confluence=clusters,
        opposing_conflicts=conflicts,
        event_outcomes=outcomes,
        linked_event_count=sum(1 for outcome in outcomes if outcome.has_link),
        no_match_event_count=sum(1 for outcome in outcomes if not outcome.has_link),
    )


def combine_three_domains(
    *,
    symbol: str,
    as_of: Any,
    pressure: MTFPressureSnapshot,
    structure: StructureProgressionSnapshot,
    location: LocationContextSnapshot,
) -> ThreeDomainObservation:
    if structure.symbol not in {None, symbol}:
        raise ValueError("structure progression symbol does not match observer symbol")
    if location.symbol not in {None, symbol}:
        raise ValueError("location context symbol does not match observer symbol")
    tensions: list[ObserverTensionCode] = []
    facts: list[str] = [
        f"PRESSURE:{pressure.state.value}",
        f"STRUCTURE_UP:{structure.upward.stage.value}",
        f"STRUCTURE_DOWN:{structure.downward.stage.value}",
        f"LOCATION:{location.state.value}",
    ]

    opposing = (
        structure.upward
        if pressure.anchor_direction is Direction.DOWN
        else structure.downward
        if pressure.anchor_direction is Direction.UP
        else None
    )
    if opposing is not None and opposing.rank > 0:
        tensions.append(
            ObserverTensionCode.LOWER_TF_OPPOSES_GENERAL_PRESSURE
            if opposing.rank <= 4
            else ObserverTensionCode.HIGHER_TF_STRUCTURE_OPPOSES_GENERAL_PRESSURE
        )
    if location.opposing_conflicts:
        tensions.append(ObserverTensionCode.OPPOSING_LOCATION_ZONES)

    if pressure.state is MTFPressureState.INSUFFICIENT_DATA:
        state = CombinedObservationState.INSUFFICIENT_DATA
    elif tensions:
        state = CombinedObservationState.CROSS_DOMAIN_TENSION
    else:
        state = CombinedObservationState.DOMAINS_REPORTED

    digest = sha1(f"{symbol}|{as_of}|THREE_DOMAIN".encode("utf-8")).hexdigest()[:14]
    return ThreeDomainObservation(
        observer_uid=f"THREE_DOMAIN_OBSERVER:{digest}",
        symbol=symbol,
        as_of=as_of,
        state=state,
        pressure=pressure,
        structure=structure,
        location=location,
        tensions=tuple(dict.fromkeys(tensions)),
        facts=tuple(facts),
    )
