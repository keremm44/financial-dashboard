from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Iterable, Mapping

from .envelope import ContextDataQuality, FactRef
from .projections import (
    LiquidityProjection,
    ReactionEvidenceProjection,
    ReactionObservation,
    StabilSupportProjection,
    StructuralFactsProjection,
)
from .zone_interaction import ZoneInteractionState, classify_zone_interaction, interval_distance


class QualifiedZoneSide(StrEnum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class ZoneAnchorKind(StrEnum):
    SUPPORT_RESISTANCE = "SUPPORT_RESISTANCE"
    PROTECTED_LEVEL = "PROTECTED_LEVEL"
    STABIL_SUPPORT = "STABIL_SUPPORT"


class ZoneFreshness(StrEnum):
    CURRENT = "CURRENT"
    AGING = "AGING"
    HISTORICAL = "HISTORICAL"
    UNKNOWN = "UNKNOWN"


class ZoneRelevance(StrEnum):
    AT_PRICE = "AT_PRICE"
    NEAR = "NEAR"
    RELEVANT = "RELEVANT"
    DISTANT = "DISTANT"
    HISTORICAL = "HISTORICAL"


class ZoneQualification(StrEnum):
    UNQUALIFIED = "UNQUALIFIED"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


@dataclass(frozen=True, slots=True)
class ZoneIntelligenceConfig:
    attachment_atr: float = 0.35
    near_atr: float = 0.50
    relevant_atr: float = 2.00

    def __post_init__(self) -> None:
        if self.attachment_atr < 0:
            raise ValueError("attachment_atr must be non-negative")
        if self.near_atr < 0:
            raise ValueError("near_atr must be non-negative")
        if self.relevant_atr < self.near_atr:
            raise ValueError("relevant_atr must be >= near_atr")


@dataclass(frozen=True, slots=True)
class StructuralZoneRef:
    source_id: str
    timeframe: str
    scope: str
    role: str
    price: float


@dataclass(frozen=True, slots=True)
class QualifiedZone:
    zone_id: str
    side: QualifiedZoneSide
    anchor_kind: ZoneAnchorKind
    anchor_timeframe: str
    low: float
    high: float
    center: float
    native_lifecycle: str
    intrinsic_sr_quality: float | None
    intrinsic_sr_touches: int | None
    boundary_stability: float | None
    structural_refs: tuple[StructuralZoneRef, ...]
    stabil_refs: tuple[str, ...]
    reaction_refs: tuple[FactRef, ...]
    confirmation_refs: tuple[FactRef, ...]
    objective_refs: tuple[FactRef, ...]
    anchor_refs: tuple[str, ...]
    freshness: ZoneFreshness
    relevance: ZoneRelevance
    distance_atr: float
    interaction: ZoneInteractionState
    qualification: ZoneQualification
    qualification_basis: tuple[str, ...]
    data_quality: ContextDataQuality
    reference_atr: float
    htf_parent_zone_id: str | None = None
    child_zone_ids: tuple[str, ...] = ()
    source_created_at: Any | None = None
    source_updated_at: Any | None = None

    def __post_init__(self) -> None:
        if not self.zone_id.strip():
            raise ValueError("zone_id must be non-empty")
        if self.high < self.low:
            raise ValueError("qualified-zone high must be >= low")
        if self.reference_atr <= 0:
            raise ValueError("qualified-zone reference_atr must be positive")

    @property
    def is_currently_qualified(self) -> bool:
        return self.qualification is not ZoneQualification.UNQUALIFIED

    @property
    def has_structural_significance(self) -> bool:
        return bool(self.structural_refs) or self.anchor_kind is ZoneAnchorKind.PROTECTED_LEVEL

    @property
    def has_reaction_support(self) -> bool:
        return bool(self.reaction_refs)


@dataclass(frozen=True, slots=True)
class ZoneIntelligenceSnapshot:
    symbol: str
    as_of: Any
    current_price: float
    zones: tuple[QualifiedZone, ...]
    nearest_qualified_support: QualifiedZone | None
    nearest_qualified_resistance: QualifiedZone | None
    strongest_relevant_support: QualifiedZone | None
    strongest_relevant_resistance: QualifiedZone | None
    htf_primary_support: QualifiedZone | None
    htf_primary_resistance: QualifiedZone | None


_TIMEFRAME_RANK = {
    "15m": 0,
    "30m": 1,
    "1h": 2,
    "2h": 3,
    "4h": 4,
    "1d": 5,
    "1w": 6,
}
_QUALIFICATION_RANK = {
    ZoneQualification.UNQUALIFIED: 0,
    ZoneQualification.MODERATE: 1,
    ZoneQualification.HIGH: 2,
    ZoneQualification.VERY_HIGH: 3,
}
_TERMINAL_LIFECYCLES = {"BROKEN", "ARCHIVED", "INVALIDATED", "NO_SUPPORT"}
_AGING_LIFECYCLES = {"WEAK", "BREAK_ATTEMPT", "BREAK_CANDIDATE", "BREACHED", "BELOW_FLOOR"}


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _side(value: Any) -> QualifiedZoneSide:
    token = _value(value).strip().upper()
    if token == "SUPPORT":
        return QualifiedZoneSide.SUPPORT
    if token == "RESISTANCE":
        return QualifiedZoneSide.RESISTANCE
    raise ValueError(f"unsupported zone side: {value!r}")


def _freshness(lifecycle: str) -> ZoneFreshness:
    token = lifecycle.strip().upper()
    if token in _TERMINAL_LIFECYCLES:
        return ZoneFreshness.HISTORICAL
    if token in _AGING_LIFECYCLES:
        return ZoneFreshness.AGING
    if token:
        return ZoneFreshness.CURRENT
    return ZoneFreshness.UNKNOWN


def _relevance(distance_atr: float, freshness: ZoneFreshness, cfg: ZoneIntelligenceConfig) -> ZoneRelevance:
    if freshness is ZoneFreshness.HISTORICAL:
        return ZoneRelevance.HISTORICAL
    if distance_atr <= 1e-12:
        return ZoneRelevance.AT_PRICE
    if distance_atr <= cfg.near_atr:
        return ZoneRelevance.NEAR
    if distance_atr <= cfg.relevant_atr:
        return ZoneRelevance.RELEVANT
    return ZoneRelevance.DISTANT


def _gap_atr(low_a: float, high_a: float, low_b: float, high_b: float, atr: float) -> float:
    if high_a < low_b:
        gap = low_b - high_a
    elif high_b < low_a:
        gap = low_a - high_b
    else:
        gap = 0.0
    return gap / max(float(atr), 1e-12)


def _reaction_side(item: ReactionObservation) -> QualifiedZoneSide | None:
    roles = {str(role).upper() for role in item.roles}
    if "DEMAND" in roles:
        return QualifiedZoneSide.SUPPORT
    if "SUPPLY" in roles:
        return QualifiedZoneSide.RESISTANCE
    return None


def _qualification(zone: QualifiedZone) -> tuple[ZoneQualification, tuple[str, ...]]:
    if zone.freshness is ZoneFreshness.HISTORICAL or zone.interaction in {
        ZoneInteractionState.ACCEPTED_THROUGH,
        ZoneInteractionState.INVALIDATED,
        ZoneInteractionState.HISTORICAL_REFERENCE,
    }:
        return ZoneQualification.UNQUALIFIED, ("ANCHOR_NOT_CURRENT",)

    basis: list[str] = ["CURRENT_NATIVE_ANCHOR"]
    structural = zone.has_structural_significance
    reaction = zone.has_reaction_support
    if structural:
        basis.append("STRUCTURAL_SIGNIFICANCE")
    if zone.stabil_refs:
        basis.append("STABIL_SUPPORT_CONTEXT")
    if reaction:
        basis.append("REACTION_CONTRIBUTOR")
    if zone.confirmation_refs:
        basis.append("INTERACTION_CONFIRMATION_PRESENT")
    if zone.objective_refs:
        basis.append("OBJECTIVE_OVERLAY_PRESENT")

    if structural and reaction:
        result = ZoneQualification.VERY_HIGH
    elif structural or reaction:
        result = ZoneQualification.HIGH
    else:
        result = ZoneQualification.MODERATE

    if zone.interaction in {ZoneInteractionState.WEAKENING, ZoneInteractionState.BEING_CONSUMED}:
        basis.append("INTERACTION_DEGRADING")
        if result is ZoneQualification.VERY_HIGH:
            result = ZoneQualification.HIGH
    return result, tuple(basis)


def _nearest_row(
    rows: list[dict[str, Any]],
    *,
    side: QualifiedZoneSide,
    low: float,
    high: float,
    timeframe: str,
    tolerance_atr: float,
) -> int | None:
    candidates: list[tuple[float, int]] = []
    for index, row in enumerate(rows):
        if row["side"] is not side:
            continue
        gap = _gap_atr(
            float(row["low"]),
            float(row["high"]),
            low,
            high,
            float(row["reference_atr"]),
        )
        if gap <= tolerance_atr:
            same_tf_tiebreak = 0.0 if row["anchor_timeframe"] == timeframe else 0.001
            candidates.append((gap + same_tf_tiebreak, index))
    return None if not candidates else min(candidates)[1]


def _structural_refs(structure: StructuralFactsProjection, as_of: Any) -> tuple[StructuralZoneRef, ...]:
    refs: list[StructuralZoneRef] = []
    for tf in structure.timeframe_facts:
        if tf.as_of is not None and tf.as_of > as_of:
            continue
        for scope in (tf.external, tf.internal):
            if scope is None:
                continue
            if scope.protected_low is not None and scope.protected_low_identity:
                refs.append(
                    StructuralZoneRef(
                        source_id=f"MS:{tf.timeframe}:{scope.scope}:PROTECTED_LOW:{scope.protected_low_identity}",
                        timeframe=tf.timeframe,
                        scope=scope.scope,
                        role="PROTECTED_LOW",
                        price=float(scope.protected_low),
                    )
                )
            if scope.protected_high is not None and scope.protected_high_identity:
                refs.append(
                    StructuralZoneRef(
                        source_id=f"MS:{tf.timeframe}:{scope.scope}:PROTECTED_HIGH:{scope.protected_high_identity}",
                        timeframe=tf.timeframe,
                        scope=scope.scope,
                        role="PROTECTED_HIGH",
                        price=float(scope.protected_high),
                    )
                )
    return tuple(sorted(refs, key=lambda item: (item.timeframe, item.scope, item.role, item.source_id)))


def _sr_rows(structure_location: Any, structure: StructuralFactsProjection, as_of: Any) -> list[dict[str, Any]]:
    quality_by_tf = {item.timeframe: item.data_quality for item in structure.timeframe_facts}
    rows: list[dict[str, Any]] = []
    for timeframe in structure_location.timeframes:
        replay = structure_location.replay_for(timeframe)
        snapshot = replay.support_resistance
        if snapshot.available_at is not None and snapshot.available_at > as_of:
            continue
        for zone in snapshot.zones:
            rows.append(
                {
                    "zone_id": str(zone.zone_uid),
                    "side": _side(zone.side),
                    "anchor_kind": ZoneAnchorKind.SUPPORT_RESISTANCE,
                    "anchor_timeframe": timeframe,
                    "low": float(zone.low),
                    "high": float(zone.high),
                    "native_lifecycle": _value(zone.lifecycle),
                    "intrinsic_sr_quality": float(zone.quality),
                    "intrinsic_sr_touches": int(zone.touches),
                    "boundary_stability": float(zone.boundary_stability),
                    "reference_atr": max(float(zone.reference_atr), 1e-12),
                    "source_created_at": zone.created_at,
                    "source_updated_at": zone.last_updated_at,
                    "anchor_refs": [str(zone.zone_uid)],
                    "structural_refs": [],
                    "stabil_refs": [],
                    "reaction_refs": [],
                    "confirmation_refs": [],
                    "objective_refs": [],
                    "data_quality": quality_by_tf.get(timeframe, ContextDataQuality.VALID),
                    "native_event": None,
                }
            )
    return rows


def _attach_structure(
    rows: list[dict[str, Any]],
    refs: Iterable[StructuralZoneRef],
    atrs: Mapping[str, float],
    cfg: ZoneIntelligenceConfig,
) -> None:
    for ref in refs:
        side = QualifiedZoneSide.SUPPORT if ref.role == "PROTECTED_LOW" else QualifiedZoneSide.RESISTANCE
        index = _nearest_row(
            rows,
            side=side,
            low=ref.price,
            high=ref.price,
            timeframe=ref.timeframe,
            tolerance_atr=cfg.attachment_atr,
        )
        if index is None:
            rows.append(
                {
                    "zone_id": f"QZ:{ref.source_id}",
                    "side": side,
                    "anchor_kind": ZoneAnchorKind.PROTECTED_LEVEL,
                    "anchor_timeframe": ref.timeframe,
                    "low": ref.price,
                    "high": ref.price,
                    "native_lifecycle": "CURRENT",
                    "intrinsic_sr_quality": None,
                    "intrinsic_sr_touches": None,
                    "boundary_stability": None,
                    "reference_atr": max(float(atrs.get(ref.timeframe, 1.0)), 1e-12),
                    "source_created_at": None,
                    "source_updated_at": None,
                    "anchor_refs": [ref.source_id],
                    "structural_refs": [ref],
                    "stabil_refs": [],
                    "reaction_refs": [],
                    "confirmation_refs": [],
                    "objective_refs": [],
                    "data_quality": ContextDataQuality.VALID,
                    "native_event": None,
                }
            )
        else:
            rows[index]["structural_refs"].append(ref)
            rows[index]["anchor_refs"].append(ref.source_id)


def _attach_stabil(
    rows: list[dict[str, Any]],
    stabil: StabilSupportProjection | None,
    atrs: Mapping[str, float],
    cfg: ZoneIntelligenceConfig,
) -> None:
    if stabil is None or stabil.support_ref is None or stabil.support_level is None:
        return
    low = float(stabil.support_floor if stabil.support_floor is not None else stabil.support_level)
    high = float(stabil.support_level)
    index = _nearest_row(
        rows,
        side=QualifiedZoneSide.SUPPORT,
        low=min(low, high),
        high=max(low, high),
        timeframe=stabil.timeframe,
        tolerance_atr=cfg.attachment_atr,
    )
    source_id = stabil.support_ref.native_id
    latest_event = stabil.events[-1].event_type if stabil.events else None
    if index is None:
        rows.append(
            {
                "zone_id": f"QZ:{source_id}",
                "side": QualifiedZoneSide.SUPPORT,
                "anchor_kind": ZoneAnchorKind.STABIL_SUPPORT,
                "anchor_timeframe": stabil.timeframe,
                "low": min(low, high),
                "high": max(low, high),
                "native_lifecycle": stabil.validity,
                "intrinsic_sr_quality": None,
                "intrinsic_sr_touches": None,
                "boundary_stability": None,
                "reference_atr": max(float(atrs.get(stabil.timeframe, 1.0)), 1e-12),
                "source_created_at": stabil.support_ref.origin_time,
                "source_updated_at": stabil.as_of,
                "anchor_refs": [source_id],
                "structural_refs": [],
                "stabil_refs": [source_id],
                "reaction_refs": [],
                "confirmation_refs": [],
                "objective_refs": [],
                "data_quality": stabil.data_quality,
                "native_event": latest_event,
            }
        )
    else:
        rows[index]["stabil_refs"].append(source_id)
        rows[index]["anchor_refs"].append(source_id)
        if latest_event:
            rows[index]["native_event"] = latest_event


def _attach_reaction_and_liquidity(
    rows: list[dict[str, Any]],
    reaction: ReactionEvidenceProjection | None,
    liquidity: LiquidityProjection | None,
    as_of: Any,
    cfg: ZoneIntelligenceConfig,
) -> None:
    if reaction is not None:
        for item in (*reaction.reaction_zones, *reaction.confirmations):
            if not item.ref.is_available_at(as_of):
                continue
            side = _reaction_side(item)
            if side is None:
                continue
            index = _nearest_row(
                rows,
                side=side,
                low=float(item.low),
                high=float(item.high),
                timeframe=item.ref.timeframe,
                tolerance_atr=cfg.attachment_atr,
            )
            if index is None:
                continue
            key = "confirmation_refs" if item.semantic_role == "CONFIRMATION" else "reaction_refs"
            rows[index][key].append(item.ref)

    if liquidity is not None:
        for item in liquidity.observations:
            if not item.target_eligible or not item.ref.is_available_at(as_of):
                continue
            candidates: list[tuple[float, int]] = []
            for index, row in enumerate(rows):
                gap = _gap_atr(
                    float(row["low"]),
                    float(row["high"]),
                    float(item.low),
                    float(item.high),
                    float(row["reference_atr"]),
                )
                if gap <= cfg.attachment_atr:
                    candidates.append((gap, index))
            if candidates:
                rows[min(candidates)[1]]["objective_refs"].append(item.ref)


def _finalize(row: dict[str, Any], current_price: float, cfg: ZoneIntelligenceConfig) -> QualifiedZone:
    low = min(float(row["low"]), float(row["high"]))
    high = max(float(row["low"]), float(row["high"]))
    atr = max(float(row["reference_atr"]), 1e-12)
    lifecycle = str(row["native_lifecycle"])
    freshness = _freshness(lifecycle)
    distance_atr = interval_distance(current_price, low, high) / atr
    interaction = classify_zone_interaction(
        side=row["side"].value,
        low=low,
        high=high,
        current_price=current_price,
        reference_atr=atr,
        native_lifecycle=lifecycle,
        native_event=row.get("native_event"),
        near_atr=cfg.attachment_atr,
    )
    zone = QualifiedZone(
        zone_id=str(row["zone_id"]),
        side=row["side"],
        anchor_kind=row["anchor_kind"],
        anchor_timeframe=str(row["anchor_timeframe"]),
        low=low,
        high=high,
        center=(low + high) * 0.5,
        native_lifecycle=lifecycle,
        intrinsic_sr_quality=row["intrinsic_sr_quality"],
        intrinsic_sr_touches=row["intrinsic_sr_touches"],
        boundary_stability=row["boundary_stability"],
        structural_refs=tuple(sorted(set(row["structural_refs"]), key=lambda item: item.source_id)),
        stabil_refs=tuple(sorted(set(row["stabil_refs"]))),
        reaction_refs=tuple(sorted(set(row["reaction_refs"]), key=lambda ref: ref.deterministic_key)),
        confirmation_refs=tuple(sorted(set(row["confirmation_refs"]), key=lambda ref: ref.deterministic_key)),
        objective_refs=tuple(sorted(set(row["objective_refs"]), key=lambda ref: ref.deterministic_key)),
        anchor_refs=tuple(sorted(set(row["anchor_refs"]))),
        freshness=freshness,
        relevance=_relevance(distance_atr, freshness, cfg),
        distance_atr=float(distance_atr),
        interaction=interaction,
        qualification=ZoneQualification.MODERATE,
        qualification_basis=(),
        data_quality=row["data_quality"],
        reference_atr=atr,
        source_created_at=row["source_created_at"],
        source_updated_at=row["source_updated_at"],
    )
    qualification, basis = _qualification(zone)
    return replace(zone, qualification=qualification, qualification_basis=basis)


def _overlaps(a: QualifiedZone, b: QualifiedZone) -> bool:
    return not (a.high < b.low or b.high < a.low)


def _apply_hierarchy(zones: tuple[QualifiedZone, ...]) -> tuple[QualifiedZone, ...]:
    parents: dict[str, str] = {}
    children: dict[str, list[str]] = {}
    for zone in zones:
        higher = [
            candidate
            for candidate in zones
            if candidate.side is zone.side
            and candidate.zone_id != zone.zone_id
            and _TIMEFRAME_RANK.get(candidate.anchor_timeframe, -1) > _TIMEFRAME_RANK.get(zone.anchor_timeframe, -1)
            and _overlaps(zone, candidate)
        ]
        if not higher:
            continue
        parent = min(
            higher,
            key=lambda candidate: (
                _TIMEFRAME_RANK.get(candidate.anchor_timeframe, -1),
                candidate.distance_atr,
                candidate.zone_id,
            ),
        )
        parents[zone.zone_id] = parent.zone_id
        children.setdefault(parent.zone_id, []).append(zone.zone_id)
    return tuple(
        replace(
            zone,
            htf_parent_zone_id=parents.get(zone.zone_id),
            child_zone_ids=tuple(sorted(children.get(zone.zone_id, ()))),
        )
        for zone in zones
    )


def _nearest(zones: Iterable[QualifiedZone], side: QualifiedZoneSide) -> QualifiedZone | None:
    eligible = [zone for zone in zones if zone.side is side and zone.is_currently_qualified]
    return min(eligible, key=lambda zone: (zone.distance_atr, zone.zone_id), default=None)


def _strongest(zones: Iterable[QualifiedZone], side: QualifiedZoneSide) -> QualifiedZone | None:
    eligible = [
        zone
        for zone in zones
        if zone.side is side
        and zone.is_currently_qualified
        and zone.relevance in {ZoneRelevance.AT_PRICE, ZoneRelevance.NEAR, ZoneRelevance.RELEVANT}
    ]
    return max(
        eligible,
        key=lambda zone: (
            _QUALIFICATION_RANK[zone.qualification],
            int(zone.has_structural_significance),
            int(zone.has_reaction_support),
            -zone.distance_atr,
            zone.zone_id,
        ),
        default=None,
    )


def _htf(zones: Iterable[QualifiedZone], side: QualifiedZoneSide) -> QualifiedZone | None:
    eligible = [zone for zone in zones if zone.side is side and zone.is_currently_qualified]
    return max(
        eligible,
        key=lambda zone: (
            _TIMEFRAME_RANK.get(zone.anchor_timeframe, -1),
            _QUALIFICATION_RANK[zone.qualification],
            -zone.distance_atr,
            zone.zone_id,
        ),
        default=None,
    )


def build_zone_intelligence(
    *,
    symbol: str,
    as_of: Any,
    current_price: float,
    structure_location: Any,
    structural: StructuralFactsProjection,
    reaction: ReactionEvidenceProjection | None = None,
    liquidity: LiquidityProjection | None = None,
    stabil_support: StabilSupportProjection | None = None,
    reference_atr_by_timeframe: Mapping[str, float] | None = None,
    config: ZoneIntelligenceConfig | None = None,
) -> ZoneIntelligenceSnapshot:
    if as_of is None:
        raise ValueError("Zone Intelligence requires as_of")
    cfg = config or ZoneIntelligenceConfig()
    atrs = dict(reference_atr_by_timeframe or {})
    rows = _sr_rows(structure_location, structural, as_of)
    for row in rows:
        atrs.setdefault(row["anchor_timeframe"], float(row["reference_atr"]))

    _attach_structure(rows, _structural_refs(structural, as_of), atrs, cfg)
    _attach_stabil(rows, stabil_support, atrs, cfg)
    _attach_reaction_and_liquidity(rows, reaction, liquidity, as_of, cfg)

    zones = tuple(
        sorted(
            (_finalize(row, float(current_price), cfg) for row in rows),
            key=lambda zone: (zone.side.value, zone.distance_atr, zone.zone_id),
        )
    )
    zones = _apply_hierarchy(zones)
    return ZoneIntelligenceSnapshot(
        symbol=symbol,
        as_of=as_of,
        current_price=float(current_price),
        zones=zones,
        nearest_qualified_support=_nearest(zones, QualifiedZoneSide.SUPPORT),
        nearest_qualified_resistance=_nearest(zones, QualifiedZoneSide.RESISTANCE),
        strongest_relevant_support=_strongest(zones, QualifiedZoneSide.SUPPORT),
        strongest_relevant_resistance=_strongest(zones, QualifiedZoneSide.RESISTANCE),
        htf_primary_support=_htf(zones, QualifiedZoneSide.SUPPORT),
        htf_primary_resistance=_htf(zones, QualifiedZoneSide.RESISTANCE),
    )


__all__ = [
    "QualifiedZone",
    "QualifiedZoneSide",
    "StructuralZoneRef",
    "ZoneAnchorKind",
    "ZoneFreshness",
    "ZoneIntelligenceConfig",
    "ZoneIntelligenceSnapshot",
    "ZoneQualification",
    "ZoneRelevance",
    "build_zone_intelligence",
]
