from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Iterable, Mapping

from .envelope import ContextDataQuality, FactRef
from .projections import (
    LiquidityObservation,
    LiquidityProjection,
    ReactionEvidenceProjection,
    ReactionObservation,
    StabilSupportProjection,
    StructuralFactsProjection,
)
from .zone_interaction import (
    ZoneInteractionState,
    classify_zone_interaction,
    interval_distance,
)


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
_TERMINAL_LIFECYCLES = {"BROKEN", "ARCHIVED", "INVALIDATED"}
_AGING_LIFECYCLES = {"WEAK", "BREAK_ATTEMPT", "BREAK_CANDIDATE"}


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _side_value(value: Any) -> QualifiedZoneSide:
    normalized = _enum_value(value).strip().upper()
    if normalized == "SUPPORT":
        return QualifiedZoneSide.SUPPORT
    if normalized == "RESISTANCE":
        return QualifiedZoneSide.RESISTANCE
    raise ValueError(f"unsupported qualified-zone side: {value!r}")


def _freshness(lifecycle: str) -> ZoneFreshness:
    token = lifecycle.strip().upper()
    if token in _TERMINAL_LIFECYCLES:
        return ZoneFreshness.HISTORICAL
    if token in _AGING_LIFECYCLES:
        return ZoneFreshness.AGING
    if token:
        return ZoneFreshness.CURRENT
    return ZoneFreshness.UNKNOWN


def _relevance(
    *,
    distance_atr: float,
    freshness: ZoneFreshness,
    config: ZoneIntelligenceConfig,
) -> ZoneRelevance:
    if freshness is ZoneFreshness.HISTORICAL:
        return ZoneRelevance.HISTORICAL
    if distance_atr <= 1e-12:
        return ZoneRelevance.AT_PRICE
    if distance_atr <= config.near_atr:
        return ZoneRelevance.NEAR
    if distance_atr <= config.relevant_atr:
        return ZoneRelevance.RELEVANT
    return ZoneRelevance.DISTANT


def _qualification(zone: QualifiedZone) -> tuple[ZoneQualification, tuple[str, ...]]:
    """Apply semantic gates, not a weighted score or confirmation count.

    Exact thresholds are deliberately absent.  A current native anchor is MODERATE;
    structural significance or a spatially relevant reaction source may raise it to
    HIGH; both together may raise it to VERY_HIGH.  Weak/consuming interaction caps
    the label at HIGH, while terminal/historical anchors are UNQUALIFIED.
    """

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
    if reaction:
        basis.append("REACTION_CONTRIBUTOR")
    if zone.objective_refs:
        basis.append("OBJECTIVE_OVERLAY_PRESENT")
    if zone.confirmation_refs:
        basis.append("INTERACTION_CONFIRMATION_PRESENT")

    if structural and reaction:
        qualification = ZoneQualification.VERY_HIGH
    elif structural or reaction:
        qualification = ZoneQualification.HIGH
    else:
        qualification = ZoneQualification.MODERATE

    if zone.interaction in {ZoneInteractionState.WEAKENING, ZoneInteractionState.BEING_CONSUMED}:
        basis.append("INTERACTION_DEGRADING")
        if qualification is ZoneQualification.VERY_HIGH:
            qualification = ZoneQualification.HIGH
    return qualification, tuple(basis)


def _gap_atr(
    *,
    low_a: float,
    high_a: float,
    low_b: float,
    high_b: float,
    reference_atr: float,
) -> float:
    if high_a < low_b:
        gap = low_b - high_a
    elif high_b < low_a:
        gap = low_a - high_b
    else:
        gap = 0.0
    return float(gap) / max(float(reference_atr), 1e-12)


def _reaction_side(item: ReactionObservation) -> QualifiedZoneSide | None:
    roles = {str(role).upper() for role in item.roles}
    if "DEMAND" in roles:
        return QualifiedZoneSide.SUPPORT
    if "SUPPLY" in roles:
        return QualifiedZoneSide.RESISTANCE
    return None


def _is_available(ref: FactRef, as_of: Any) -> bool:
    return ref.is_available_at(as_of)


def _structure_refs(structure: StructuralFactsProjection, *, as_of: Any) -> tuple[StructuralZoneRef, ...]:
    refs: list[StructuralZoneRef] = []
    for timeframe_fact in structure.timeframe_facts:
        for scope in (timeframe_fact.external, timeframe_fact.internal):
            if scope is None:
                continue
            if scope.protected_low is not None and scope.protected_low_identity:
                refs.append(
                    StructuralZoneRef(
                        source_id=(
                            f"MS:{timeframe_fact.timeframe}:{scope.scope}:"
                            f"PROTECTED_LOW:{scope.protected_low_identity}"
                        ),
                        timeframe=timeframe_fact.timeframe,
                        scope=scope.scope,
                        role="PROTECTED_LOW",
                        price=float(scope.protected_low),
                    )
                )
            if scope.protected_high is not None and scope.protected_high_identity:
                refs.append(
                    StructuralZoneRef(
                        source_id=(
                            f"MS:{timeframe_fact.timeframe}:{scope.scope}:"
                            f"PROTECTED_HIGH:{scope.protected_high_identity}"
                        ),
                        timeframe=timeframe_fact.timeframe,
                        scope=scope.scope,
                        role="PROTECTED_HIGH",
                        price=float(scope.protected_high),
                    )
                )
    return tuple(sorted(refs, key=lambda item: (item.timeframe, item.scope, item.role, item.source_id)))


def _sr_anchor_rows(structure_location: Any, *, as_of: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for timeframe in structure_location.timeframes:
        snapshot = structure_location.replay_for(timeframe).support_resistance
        for zone in snapshot.zones:
            if snapshot.available_at is not None and snapshot.available_at > as_of:
                continue
            rows.append(
                {
                    "zone_id": str(zone.zone_uid),
                    "side": _side_value(zone.side),
                    "anchor_kind": ZoneAnchorKind.SUPPORT_RESISTANCE,
                    "anchor_timeframe": timeframe,
                    "low": float(zone.low),
                    "high": float(zone.high),
                    "native_lifecycle": _enum_value(zone.lifecycle),
                    "intrinsic_sr_quality": float(zone.quality),
                    "intrinsic_sr_touches": int(zone.touches),
                    "boundary_stability": float(zone.boundary_stability),
                    "reference_atr": max(float(zone.reference_atr), 1e-12),
                    "source_created_at": zone.created_at,
                    "source_updated_at": zone.last_updated_at,
                    "anchor_refs": (str(zone.zone_uid),),
                    "data_quality": ContextDataQuality.VALID,
                    "native_event": None,
                }
            )
    return rows


def _nearest_anchor_index(
    rows: list[dict[str, Any]],
    *,
    side: QualifiedZoneSide,
    low: float,
    high: float,
    timeframe: str,
    attachment_atr: float,
) -> int | None:
    candidates: list[tuple[float, int]] = []
    for index, row in enumerate(rows):
        if row["side"] is not side:
            continue
        gap = _gap_atr(
            low_a=float(row["low"]),
            high_a=float(row["high"]),
            low_b=low,
            high_b=high,
            reference_atr=float(row["reference_atr"]),
        )
        if gap <= attachment_atr:
            same_tf_penalty = 0.0 if row["anchor_timeframe"] == timeframe else 0.001
            candidates.append((gap + same_tf_penalty, index))
    if not candidates:
        return None
    return min(candidates)[1]


def _attach_structural_refs(
    rows: list[dict[str, Any]],
    refs: Iterable[StructuralZoneRef],
    *,
    reference_atr_by_timeframe: Mapping[str, float],
    config: ZoneIntelligenceConfig,
) -> None:
    for ref in refs:
        side = QualifiedZoneSide.SUPPORT if ref.role == "PROTECTED_LOW" else QualifiedZoneSide.RESISTANCE
        index = _nearest_anchor_index(
            rows,
            side=side,
            low=ref.price,
            high=ref.price,
            timeframe=ref.timeframe,
            attachment_atr=config.attachment_atr,
        )
        if index is None:
            atr = max(float(reference_atr_by_timeframe.get(ref.timeframe, 1.0)), 1e-12)
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
                    "reference_atr": atr,
                    "source_created_at": None,
                    "source_updated_at": None,
                    "anchor_refs": (ref.source_id,),
                    "structural_refs": [ref],
                    "data_quality": ContextDataQuality.VALID,
                    "native_event": None,
                }
            )
        else:
            rows[index].setdefault("structural_refs", []).append(ref)
            rows[index]["anchor_refs"] = tuple(sorted(set(rows[index]["anchor_refs"]) | {ref.source_id}))


def _attach_stabil_support(
    rows: list[dict[str, Any]],
    stabil: StabilSupportProjection | None,
    *,
    reference_atr_by_timeframe: Mapping[str, float],
    config: ZoneIntelligenceConfig,
) -> None:
    if stabil is None or stabil.support_ref is None or stabil.support_level is None:
        return
    low = float(stabil.support_floor if stabil.support_floor is not None else stabil.support_level)
    high = float(stabil.support_level)
    index = _nearest_anchor_index(
        rows,
        side=QualifiedZoneSide.SUPPORT,
        low=low,
        high=high,
        timeframe=stabil.timeframe,
        attachment_atr=config.attachment_atr,
    )
    anchor_ref = stabil.support_ref.native_id
    latest_event = stabil.events[-1].event_type if stabil.events else None
    if index is None:
        atr = max(float(reference_atr_by_timeframe.get(stabil.timeframe, 1.0)), 1e-12)
        if stabil.distance_atr not in {None, 0.0} and stabil.support_level is not None:
            implied = abs((float(stabil.support_level) - float(stabil.support_level - stabil.distance_atr * atr)))
            atr = max(atr, implied / max(abs(float(stabil.distance_atr)), 1e-12))
        rows.append(
            {
                "zone_id": f"QZ:{anchor_ref}",
                "side": QualifiedZoneSide.SUPPORT,
                "anchor_kind": ZoneAnchorKind.STABIL_SUPPORT,
                "anchor_timeframe": stabil.timeframe,
                "low": min(low, high),
                "high": max(low, high),
                "native_lifecycle": stabil.validity,
                "intrinsic_sr_quality": None,
                "intrinsic_sr_touches": None,
                "boundary_stability": None,
                "reference_atr": atr,
                "source_created_at": stabil.support_ref.origin_time,
                "source_updated_at": stabil.as_of,
                "anchor_refs": (anchor_ref,),
                "data_quality": stabil.data_quality,
                "native_event": latest_event,
            }
        )
    else:
        rows[index]["anchor_refs"] = tuple(sorted(set(rows[index]["anchor_refs"]) | {anchor_ref}))
        rows[index].setdefault("stabil_refs", []).append(anchor_ref)
        if latest_event:
            rows[index]["native_event"] = latest_event


def _attach_reaction_and_objectives(
    rows: list[dict[str, Any]],
    *,
    reaction: ReactionEvidenceProjection | None,
    liquidity: LiquidityProjection | None,
    as_of: Any,
    config: ZoneIntelligenceConfig,
) -> None:
    if reaction is not None:
        for item in (*reaction.reaction_zones, *reaction.confirmations):
            if not _is_available(item.ref, as_of):
                continue
            side = _reaction_side(item)
            if side is None:
                continue
            index = _nearest_anchor_index(
                rows,
                side=side,
                low=float(item.low),
                high=float(item.high),
                timeframe=item.ref.timeframe,
                attachment_atr=config.attachment_atr,
            )
            if index is None:
                continue
            key = "confirmation_refs" if item.semantic_role == "CONFIRMATION" else "reaction_refs"
            rows[index].setdefault(key, []).append(item.ref)

    if liquidity is not None:
        for item in liquidity.observations:
            if not item.target_eligible or not _is_available(item.ref, as_of):
                continue
            side = (
                QualifiedZoneSide.RESISTANCE
                if float(item.low) >= min(float(row["center"] if "center" in row else (row["low"] + row["high"]) * 0.5) for row in rows) if rows
                else QualifiedZoneSide.SUPPORT
            )
            # Liquidity is an objective overlay, never a zone contributor.  Match both
            # sides spatially and let geometry choose the nearest compatible anchor.
            candidates: list[tuple[float, int]] = []
            for index, row in enumerate(rows):
                gap = _gap_atr(
                    low_a=float(row["low"]),
                    high_a=float(row["high"]),
                    low_b=float(item.low),
                    high_b=float(item.high),
                    reference_atr=float(row["reference_atr"]),
                )
                if gap <= config.attachment_atr:
                    candidates.append((gap, index))
            if candidates:
                rows[min(candidates)[1]].setdefault("objective_refs", []).append(item.ref)


def _finalize_row(
    row: dict[str, Any],
    *,
    current_price: float,
    as_of: Any,
    config: ZoneIntelligenceConfig,
) -> QualifiedZone:
    low = float(row["low"])
    high = float(row["high"])
    reference_atr = max(float(row["reference_atr"]), 1e-12)
    lifecycle = str(row["native_lifecycle"])
    freshness = _freshness(lifecycle)
    distance_atr = interval_distance(current_price, low, high) / reference_atr
    relevance = _relevance(distance_atr=distance_atr, freshness=freshness, config=config)
    interaction = classify_zone_interaction(
        side=row["side"].value,
        low=low,
        high=high,
        current_price=current_price,
        reference_atr=reference_atr,
        native_lifecycle=lifecycle,
        native_event=row.get("native_event"),
        near_atr=config.attachment_atr,
    )
    zone = QualifiedZone(
        zone_id=str(row["zone_id"]),
        side=row["side"],
        anchor_kind=row["anchor_kind"],
        anchor_timeframe=str(row["anchor_timeframe"]),
        low=min(low, high),
        high=max(low, high),
        center=(low + high) * 0.5,
        native_lifecycle=lifecycle,
        intrinsic_sr_quality=row.get("intrinsic_sr_quality"),
        intrinsic_sr_touches=row.get("intrinsic_sr_touches"),
        boundary_stability=row.get("boundary_stability"),
        structural_refs=tuple(sorted(row.get("structural_refs", ()), key=lambda item: item.source_id)),
        reaction_refs=tuple(sorted(set(row.get("reaction_refs", ())), key=lambda ref: ref.deterministic_key)),
        confirmation_refs=tuple(sorted(set(row.get("confirmation_refs", ())), key=lambda ref: ref.deterministic_key)),
        objective_refs=tuple(sorted(set(row.get("objective_refs", ())), key=lambda ref: ref.deterministic_key)),
        anchor_refs=tuple(sorted(set(row.get("anchor_refs", ())))),
        freshness=freshness,
        relevance=relevance,
        distance_atr=float(distance_atr),
        interaction=interaction,
        qualification=ZoneQualification.MODERATE,
        qualification_basis=(),
        data_quality=row.get("data_quality", ContextDataQuality.VALID),
        reference_atr=reference_atr,
        source_created_at=row.get("source_created_at"),
        source_updated_at=row.get("source_updated_at"),
    )
    qualification, basis = _qualification(zone)
    return replace(zone, qualification=qualification, qualification_basis=basis)


def _nearest(zones: Iterable[QualifiedZone], side: QualifiedZoneSide) -> QualifiedZone | None:
    eligible = [zone for zone in zones if zone.side is side and zone.is_currently_qualified]
    return min(eligible, key=lambda zone: (zone.distance_atr, zone.zone_id), default=None)


def _strongest_relevant(zones: Iterable[QualifiedZone], side: QualifiedZoneSide) -> QualifiedZone | None:
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


def _htf_primary(zones: Iterable[QualifiedZone], side: QualifiedZoneSide) -> QualifiedZone | None:
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
    """Build a read-only qualified location view from already-produced domain facts."""

    if as_of is None:
        raise ValueError("Zone Intelligence requires as_of")
    cfg = config or ZoneIntelligenceConfig()
    atrs = dict(reference_atr_by_timeframe or {})
    rows = _sr_anchor_rows(structure_location, as_of=as_of)
    for row in rows:
        atrs.setdefault(row["anchor_timeframe"], float(row["reference_atr"]))

    structural_refs = _structure_refs(structural, as_of=as_of)
    _attach_structural_refs(rows, structural_refs, reference_atr_by_timeframe=atrs, config=cfg)
    _attach_stabil_support(rows, stabil_support, reference_atr_by_timeframe=atrs, config=cfg)
    _attach_reaction_and_objectives(
        rows,
        reaction=reaction,
        liquidity=liquidity,
        as_of=as_of,
        config=cfg,
    )

    zones = tuple(
        sorted(
            (_finalize_row(row, current_price=float(current_price), as_of=as_of, config=cfg) for row in rows),
            key=lambda zone: (zone.side.value, zone.distance_atr, zone.zone_id),
        )
    )
    return ZoneIntelligenceSnapshot(
        symbol=symbol,
        as_of=as_of,
        current_price=float(current_price),
        zones=zones,
        nearest_qualified_support=_nearest(zones, QualifiedZoneSide.SUPPORT),
        nearest_qualified_resistance=_nearest(zones, QualifiedZoneSide.RESISTANCE),
        strongest_relevant_support=_strongest_relevant(zones, QualifiedZoneSide.SUPPORT),
        strongest_relevant_resistance=_strongest_relevant(zones, QualifiedZoneSide.RESISTANCE),
        htf_primary_support=_htf_primary(zones, QualifiedZoneSide.SUPPORT),
        htf_primary_resistance=_htf_primary(zones, QualifiedZoneSide.RESISTANCE),
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
