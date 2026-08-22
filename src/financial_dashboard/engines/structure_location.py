from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha1
from itertools import combinations
from statistics import fmean, median
from typing import Any, Iterable

from .market_structure_events import MarketStructureEventRecord
from .models import Direction
from .support_resistance_zones import (
    SupportResistanceZone,
    ZoneLifecycle,
    ZoneSide,
)


class StructureLocationAnchor(StrEnum):
    BROKEN_LEVEL = "BROKEN_LEVEL"
    ORIGIN_PRICE = "ORIGIN_PRICE"
    CONFIRMATION_CLOSE = "CONFIRMATION_CLOSE"
    CONFIRMATION_RANGE = "CONFIRMATION_RANGE"


class StructureZoneRelation(StrEnum):
    INSIDE_ZONE = "INSIDE_ZONE"
    NEAR_ZONE = "NEAR_ZONE"
    CANDLE_INTERSECTS_ZONE = "CANDLE_INTERSECTS_ZONE"


class StructureLocationMeaning(StrEnum):
    BREAKS_RESISTANCE = "BREAKS_RESISTANCE"
    BREAKS_SUPPORT = "BREAKS_SUPPORT"
    ORIGINATES_AT_SUPPORT = "ORIGINATES_AT_SUPPORT"
    ORIGINATES_AT_RESISTANCE = "ORIGINATES_AT_RESISTANCE"
    CONFIRMS_AT_SUPPORT = "CONFIRMS_AT_SUPPORT"
    CONFIRMS_AT_RESISTANCE = "CONFIRMS_AT_RESISTANCE"
    ZONE_BREAK_CONFIRMED = "ZONE_BREAK_CONFIRMED"
    STRUCTURE_AT_ZONE = "STRUCTURE_AT_ZONE"


class StructureLocationOutcomeStatus(StrEnum):
    """A completed causal location computation, with or without a spatial match."""

    LINKED = "LINKED"
    COMPUTED_NO_CAUSAL_ZONE_MATCH = "COMPUTED_NO_CAUSAL_ZONE_MATCH"


@dataclass(frozen=True, slots=True)
class ZoneConfluenceConfig:
    max_gap_atr: float = 0.20
    max_cluster_span_atr: float = 1.75
    minimum_timeframes: int = 2
    timeframe_weights: tuple[tuple[str, float], ...] = (
        ("1d", 1.00),
        ("4h", 0.86),
        ("2h", 0.72),
        ("1h", 0.58),
        ("30m", 0.45),
    )

    def __post_init__(self) -> None:
        if self.max_gap_atr < 0.0:
            raise ValueError("max_gap_atr must be >= 0")
        if self.max_cluster_span_atr <= 0.0:
            raise ValueError("max_cluster_span_atr must be > 0")
        if self.minimum_timeframes < 2:
            raise ValueError("minimum_timeframes must be >= 2")
        normalized_weights = tuple(
            (timeframe.strip().lower(), float(weight))
            for timeframe, weight in self.timeframe_weights
        )
        keys = tuple(timeframe for timeframe, _ in normalized_weights)
        if not all(keys) or len(set(keys)) != len(keys):
            raise ValueError("timeframe_weights must use unique, non-empty timeframes")
        if any(weight <= 0.0 for _, weight in normalized_weights):
            raise ValueError("timeframe weights must be > 0")
        object.__setattr__(self, "timeframe_weights", normalized_weights)

    def weight_for(self, timeframe: str | None) -> float:
        normalized = "" if timeframe is None else timeframe.strip().lower()
        return dict(self.timeframe_weights).get(normalized, 0.35)


@dataclass(frozen=True, slots=True)
class ZoneConfluenceCluster:
    """Descriptive overlap strength; ``score`` is not a probability or action."""

    cluster_uid: str
    symbol: str
    side: ZoneSide
    member_zone_uids: tuple[str, ...]
    timeframes: tuple[str, ...]
    envelope_low: float
    envelope_high: float
    common_low: float | None
    common_high: float | None
    reference_price: float
    geometry_score: float
    quality_score: float
    maturity_score: float
    timeframe_coverage: float
    score: float


@dataclass(frozen=True, slots=True)
class CausalZoneObservation:
    symbol: str
    timeframe: str
    bar_index: int
    observed_at: Any
    available_at: Any
    zones: tuple[SupportResistanceZone, ...]


@dataclass(frozen=True, slots=True)
class StructureZoneLinkConfig:
    max_distance_atr: float = 0.22
    include_cross_timeframe: bool = True

    def __post_init__(self) -> None:
        if self.max_distance_atr < 0.0:
            raise ValueError("max_distance_atr must be >= 0")


@dataclass(frozen=True, slots=True)
class StructureZoneLink:
    """Causal event-location evidence; ``score`` is descriptive, not probability."""

    link_uid: str
    event_uid: str
    event_type: str
    event_scope: str
    event_direction: Direction
    event_timeframe: str
    event_available_at: Any
    zone_uid: str
    zone_timeframe: str
    zone_side: ZoneSide
    zone_lifecycle: ZoneLifecycle
    zone_observed_at: Any
    zone_available_at: Any
    anchor: StructureLocationAnchor
    anchor_price: float | None
    relation: StructureZoneRelation
    meaning: StructureLocationMeaning
    distance: float
    distance_atr: float
    score: float
    same_timeframe: bool


@dataclass(frozen=True, slots=True)
class StructureLocationOutcome:
    """Explicit proof that event-location matching was computed causally."""

    outcome_uid: str
    event_uid: str
    event_type: str
    event_scope: str
    event_direction: Direction
    event_timeframe: str
    event_available_at: Any
    status: StructureLocationOutcomeStatus
    causal_timeframes: tuple[str, ...]
    causal_zone_count: int
    links: tuple[StructureZoneLink, ...]

    @property
    def has_link(self) -> bool:
        return self.status is StructureLocationOutcomeStatus.LINKED


_LIFECYCLE_MATURITY = {
    ZoneLifecycle.FORMING: 0.20,
    ZoneLifecycle.CONFIRMED: 0.76,
    ZoneLifecycle.ACTIVE: 1.00,
    ZoneLifecycle.WEAK: 0.58,
    ZoneLifecycle.BREAK_ATTEMPT: 0.68,
    ZoneLifecycle.BREAK_CANDIDATE: 0.62,
    ZoneLifecycle.BREAK_FAILED: 0.72,
    ZoneLifecycle.BROKEN: 0.74,
    ZoneLifecycle.ARCHIVED: 0.20,
    ZoneLifecycle.INVALIDATED: 0.42,
}


def _interval_gap(first: SupportResistanceZone, second: SupportResistanceZone) -> float:
    return max(0.0, max(first.low, second.low) - min(first.high, second.high))


def _pair_geometry(
    first: SupportResistanceZone,
    second: SupportResistanceZone,
    *,
    max_gap_atr: float,
) -> float:
    intersection = max(0.0, min(first.high, second.high) - max(first.low, second.low))
    minimum_width = max(min(first.width, second.width), 1e-12)
    overlap_coefficient = min(1.0, intersection / minimum_width)
    reference_atr = max(min(first.reference_atr, second.reference_atr), 1e-12)
    gap_atr = _interval_gap(first, second) / reference_atr
    proximity = max(0.0, 1.0 - gap_atr / max(max_gap_atr, 1e-12))
    return min(1.0, overlap_coefficient * 0.65 + proximity * 0.35)


def _compatible(
    candidate: SupportResistanceZone,
    members: list[SupportResistanceZone],
    config: ZoneConfluenceConfig,
) -> bool:
    if any(member.symbol != candidate.symbol for member in members):
        return False
    if any(member.timeframe == candidate.timeframe for member in members):
        return False
    if any(
        _interval_gap(candidate, member)
        / max(min(candidate.reference_atr, member.reference_atr), 1e-12)
        > config.max_gap_atr
        for member in members
    ):
        return False
    combined = [*members, candidate]
    span = max(zone.high for zone in combined) - min(zone.low for zone in combined)
    reference_atr = max(median(zone.reference_atr for zone in combined), 1e-12)
    return span / reference_atr <= config.max_cluster_span_atr


def _cluster_record(
    members: list[SupportResistanceZone],
    config: ZoneConfluenceConfig,
) -> ZoneConfluenceCluster:
    ordered = sorted(members, key=lambda zone: (zone.timeframe or "", zone.zone_uid))
    member_uids = tuple(zone.zone_uid for zone in ordered)
    timeframes = tuple(sorted({zone.timeframe or "" for zone in ordered}))
    digest = sha1("|".join(member_uids).encode("utf-8")).hexdigest()[:12]
    pair_scores = [
        _pair_geometry(first, second, max_gap_atr=config.max_gap_atr)
        for first, second in combinations(ordered, 2)
    ]
    geometry = fmean(pair_scores) if pair_scores else 0.0
    weights = [max(config.weight_for(zone.timeframe), 1e-12) for zone in ordered]
    weight_sum = sum(weights)
    quality = sum(weight * max(0.0, min(100.0, zone.quality)) for weight, zone in zip(weights, ordered, strict=True)) / weight_sum
    maturity = sum(weight * _LIFECYCLE_MATURITY[zone.lifecycle] for weight, zone in zip(weights, ordered, strict=True)) / weight_sum
    total_foundation_weight = max(sum(weight for _, weight in config.timeframe_weights), 1e-12)
    coverage = min(1.0, sum(config.weight_for(timeframe) for timeframe in timeframes) / total_foundation_weight)
    centers = [zone.center for zone in ordered]
    reference_price = sum(weight * center for weight, center in zip(weights, centers, strict=True)) / weight_sum
    common_low = max(zone.low for zone in ordered)
    common_high = min(zone.high for zone in ordered)
    if common_low > common_high:
        common_low = common_high = None
    score = 100.0 * (
        geometry * 0.42
        + (quality / 100.0) * 0.25
        + maturity * 0.20
        + coverage * 0.13
    )
    return ZoneConfluenceCluster(
        cluster_uid=f"CONFLUENCE:{ordered[0].side.value}:{digest}",
        symbol=ordered[0].symbol or "",
        side=ordered[0].side,
        member_zone_uids=member_uids,
        timeframes=timeframes,
        envelope_low=min(zone.low for zone in ordered),
        envelope_high=max(zone.high for zone in ordered),
        common_low=common_low,
        common_high=common_high,
        reference_price=reference_price,
        geometry_score=round(geometry * 100.0, 3),
        quality_score=round(quality, 3),
        maturity_score=round(maturity * 100.0, 3),
        timeframe_coverage=round(coverage * 100.0, 3),
        score=round(max(0.0, min(100.0, score)), 3),
    )


def build_zone_confluence(
    zones: Iterable[SupportResistanceZone],
    config: ZoneConfluenceConfig | None = None,
) -> tuple[ZoneConfluenceCluster, ...]:
    """Cluster established zones by role; opposing roles are never called confluence."""

    config = config or ZoneConfluenceConfig()
    output: list[ZoneConfluenceCluster] = []
    for side in (ZoneSide.SUPPORT, ZoneSide.RESISTANCE):
        candidates = sorted(
            (
                zone
                for zone in zones
                if zone.side is side
                and zone.is_confluence_eligible
                and zone.symbol is not None
                and zone.timeframe is not None
            ),
            key=lambda zone: (zone.center, zone.timeframe or "", zone.zone_uid),
        )
        groups: list[list[SupportResistanceZone]] = []
        for candidate in candidates:
            compatible_groups = [
                group for group in groups if _compatible(candidate, group, config)
            ]
            if not compatible_groups:
                groups.append([candidate])
                continue
            best = max(
                compatible_groups,
                key=lambda group: fmean(
                    _pair_geometry(candidate, member, max_gap_atr=config.max_gap_atr)
                    for member in group
                ),
            )
            best.append(candidate)
        output.extend(
            _cluster_record(group, config)
            for group in groups
            if len({zone.timeframe for zone in group}) >= config.minimum_timeframes
        )
    return tuple(
        sorted(
            output,
            key=lambda cluster: (
                cluster.symbol,
                cluster.side.value,
                -cluster.score,
                cluster.cluster_uid,
            ),
        )
    )


def _distance_to_zone(price: float, zone: SupportResistanceZone) -> float:
    if zone.low <= price <= zone.high:
        return 0.0
    return min(abs(price - zone.low), abs(price - zone.high))


def _meaning(
    event: MarketStructureEventRecord,
    zone: SupportResistanceZone,
    anchor: StructureLocationAnchor,
    observation: CausalZoneObservation,
) -> StructureLocationMeaning:
    terminal_now = (
        zone.lifecycle in {ZoneLifecycle.BROKEN, ZoneLifecycle.INVALIDATED}
        and zone.last_transition_bar == observation.bar_index
    )
    if terminal_now:
        return StructureLocationMeaning.ZONE_BREAK_CONFIRMED
    if anchor is StructureLocationAnchor.BROKEN_LEVEL:
        if event.direction is Direction.UP and zone.side is ZoneSide.RESISTANCE:
            return StructureLocationMeaning.BREAKS_RESISTANCE
        if event.direction is Direction.DOWN and zone.side is ZoneSide.SUPPORT:
            return StructureLocationMeaning.BREAKS_SUPPORT
    if anchor is StructureLocationAnchor.ORIGIN_PRICE:
        if event.direction is Direction.UP and zone.side is ZoneSide.SUPPORT:
            return StructureLocationMeaning.ORIGINATES_AT_SUPPORT
        if event.direction is Direction.DOWN and zone.side is ZoneSide.RESISTANCE:
            return StructureLocationMeaning.ORIGINATES_AT_RESISTANCE
    if anchor in {
        StructureLocationAnchor.CONFIRMATION_CLOSE,
        StructureLocationAnchor.CONFIRMATION_RANGE,
    }:
        return (
            StructureLocationMeaning.CONFIRMS_AT_SUPPORT
            if zone.side is ZoneSide.SUPPORT
            else StructureLocationMeaning.CONFIRMS_AT_RESISTANCE
        )
    return StructureLocationMeaning.STRUCTURE_AT_ZONE


def _latest_causal_observations(
    event: MarketStructureEventRecord,
    observations: Iterable[CausalZoneObservation],
    *,
    event_available_at: Any,
    config: StructureZoneLinkConfig,
) -> tuple[CausalZoneObservation, ...]:
    event_timeframe = (event.timeframe or "").strip().lower()
    latest: dict[tuple[str, str], CausalZoneObservation] = {}
    for observation in observations:
        if observation.available_at > event_available_at:
            continue
        if event.symbol is not None and observation.symbol != event.symbol:
            continue
        normalized_timeframe = observation.timeframe.strip().lower()
        if not config.include_cross_timeframe and normalized_timeframe != event_timeframe:
            continue
        key = (observation.symbol, normalized_timeframe)
        previous = latest.get(key)
        if previous is None or (
            observation.available_at,
            observation.bar_index,
        ) > (
            previous.available_at,
            previous.bar_index,
        ):
            latest[key] = observation
    return tuple(latest[key] for key in sorted(latest))


def link_structure_event_to_zones(
    event: MarketStructureEventRecord,
    observations: Iterable[CausalZoneObservation],
    *,
    event_available_at: Any,
    config: StructureZoneLinkConfig | None = None,
) -> tuple[StructureZoneLink, ...]:
    """Link the latest zone state available per timeframe at event confirmation."""

    config = config or StructureZoneLinkConfig()
    event_timeframe = (event.timeframe or "").strip().lower()
    links: list[StructureZoneLink] = []
    anchors = (
        (StructureLocationAnchor.BROKEN_LEVEL, event.broken_level, 1.00),
        (StructureLocationAnchor.ORIGIN_PRICE, event.origin_price, 0.92),
        (StructureLocationAnchor.CONFIRMATION_CLOSE, event.confirmation_close, 0.78),
    )
    causal_observations = _latest_causal_observations(
        event,
        observations,
        event_available_at=event_available_at,
        config=config,
    )
    for observation in causal_observations:
        observation_timeframe = observation.timeframe.strip().lower()
        for zone in observation.zones:
            if zone.symbol is not None and zone.symbol != observation.symbol:
                continue
            if event.symbol is not None and zone.symbol != event.symbol:
                continue
            terminal_now = (
                zone.lifecycle in {ZoneLifecycle.BROKEN, ZoneLifecycle.INVALIDATED}
                and zone.last_transition_bar == observation.bar_index
            )
            if not zone.is_confluence_eligible and not terminal_now:
                continue

            candidates: list[
                tuple[int, float, int, StructureLocationAnchor, float | None, StructureZoneRelation, float]
            ] = []
            for anchor_index, (anchor, price, specificity) in enumerate(anchors):
                if price is None:
                    continue
                distance = _distance_to_zone(float(price), zone)
                distance_atr = distance / max(zone.reference_atr, 1e-12)
                if distance == 0.0:
                    candidates.append(
                        (0, distance_atr, anchor_index, anchor, float(price), StructureZoneRelation.INSIDE_ZONE, specificity)
                    )
                elif distance_atr <= config.max_distance_atr:
                    candidates.append(
                        (1, distance_atr, anchor_index, anchor, float(price), StructureZoneRelation.NEAR_ZONE, specificity)
                    )

            if not candidates and event.confirmation_low is not None and event.confirmation_high is not None:
                intersects = min(float(event.confirmation_high), zone.high) >= max(float(event.confirmation_low), zone.low)
                if intersects:
                    candidates.append(
                        (
                            2,
                            0.0,
                            3,
                            StructureLocationAnchor.CONFIRMATION_RANGE,
                            None,
                            StructureZoneRelation.CANDLE_INTERSECTS_ZONE,
                            0.58,
                        )
                    )
            if not candidates:
                continue

            _, distance_atr, _, anchor, anchor_price, relation, specificity = min(candidates)
            distance = 0.0 if anchor_price is None else _distance_to_zone(anchor_price, zone)
            proximity = (
                1.0
                if relation is StructureZoneRelation.INSIDE_ZONE
                else 0.62
                if relation is StructureZoneRelation.CANDLE_INTERSECTS_ZONE
                else max(0.0, 1.0 - distance_atr / max(config.max_distance_atr, 1e-12))
            )
            quality = max(0.0, min(1.0, zone.quality / 100.0))
            maturity = _LIFECYCLE_MATURITY[zone.lifecycle]
            score = 100.0 * (
                proximity * 0.42
                + quality * 0.25
                + maturity * 0.20
                + specificity * 0.13
            )
            link_seed = f"{event.event_uid}|{zone.zone_uid}|{anchor.value}"
            digest = sha1(link_seed.encode("utf-8")).hexdigest()[:14]
            links.append(
                StructureZoneLink(
                    link_uid=f"STRUCTURE_ZONE_LINK:{digest}",
                    event_uid=event.event_uid,
                    event_type=event.event_type,
                    event_scope=event.scope,
                    event_direction=event.direction,
                    event_timeframe=event_timeframe,
                    event_available_at=event_available_at,
                    zone_uid=zone.zone_uid,
                    zone_timeframe=observation_timeframe,
                    zone_side=zone.side,
                    zone_lifecycle=zone.lifecycle,
                    zone_observed_at=observation.observed_at,
                    zone_available_at=observation.available_at,
                    anchor=anchor,
                    anchor_price=anchor_price,
                    relation=relation,
                    meaning=_meaning(event, zone, anchor, observation),
                    distance=round(distance, 10),
                    distance_atr=round(distance_atr, 10),
                    score=round(max(0.0, min(100.0, score)), 3),
                    same_timeframe=observation_timeframe == event_timeframe,
                )
            )
    return tuple(sorted(links, key=lambda link: (-link.score, link.zone_timeframe, link.zone_uid)))


def evaluate_structure_event_location(
    event: MarketStructureEventRecord,
    observations: Iterable[CausalZoneObservation],
    *,
    event_available_at: Any,
    config: StructureZoneLinkConfig | None = None,
) -> StructureLocationOutcome:
    """Compute one explicit causal location outcome for a structural event.

    A no-match record is deliberately retained.  It means matching ran against the
    zone states available by confirmation time; it is not a missing calculation.
    """

    config = config or StructureZoneLinkConfig()
    event_timeframe = (event.timeframe or "").strip().lower()
    causal_observations = _latest_causal_observations(
        event,
        observations,
        event_available_at=event_available_at,
        config=config,
    )
    causal_zone_uids = {
        zone.zone_uid
        for observation in causal_observations
        for zone in observation.zones
        if (zone.symbol is None or zone.symbol == observation.symbol)
        and (event.symbol is None or zone.symbol == event.symbol)
        and (
            zone.is_confluence_eligible
            or (
                zone.lifecycle in {ZoneLifecycle.BROKEN, ZoneLifecycle.INVALIDATED}
                and zone.last_transition_bar == observation.bar_index
            )
        )
    }
    links = link_structure_event_to_zones(
        event,
        causal_observations,
        event_available_at=event_available_at,
        config=config,
    )
    status = (
        StructureLocationOutcomeStatus.LINKED
        if links
        else StructureLocationOutcomeStatus.COMPUTED_NO_CAUSAL_ZONE_MATCH
    )
    digest = sha1(
        f"{event.event_uid}|{event_available_at}|LOCATION_OUTCOME".encode("utf-8")
    ).hexdigest()[:14]
    return StructureLocationOutcome(
        outcome_uid=f"STRUCTURE_LOCATION_OUTCOME:{digest}",
        event_uid=event.event_uid,
        event_type=event.event_type,
        event_scope=event.scope,
        event_direction=event.direction,
        event_timeframe=event_timeframe,
        event_available_at=event_available_at,
        status=status,
        causal_timeframes=tuple(
            sorted(
                {
                    observation.timeframe.strip().lower()
                    for observation in causal_observations
                }
            )
        ),
        causal_zone_count=len(causal_zone_uids),
        links=links,
    )
