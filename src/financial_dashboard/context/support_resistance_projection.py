from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .envelope import ContextDomain, FactRef, normalize_context_data_quality
from .lineage import families_for


@dataclass(frozen=True, slots=True)
class SupportResistanceZoneProjection:
    zone_id: str
    side: str
    low: float
    high: float
    center: float
    lifecycle: str
    quality: float
    touches: int
    boundary_stability: float
    reference_atr: float
    created_at: Any | None
    updated_at: Any | None


@dataclass(frozen=True, slots=True)
class SupportResistanceTimeframeProjection:
    timeframe: str
    ref: FactRef
    state: str | None
    range_identity: int | None
    upper_center: float | None
    upper_top: float | None
    upper_bottom: float | None
    lower_center: float | None
    lower_top: float | None
    lower_bottom: float | None
    mid_price: float | None
    quality: float | None
    boundary_stability: float | None
    identity_score: float | None
    upper_touches: int
    lower_touches: int
    upper_close_violations: int
    lower_close_violations: int
    break_direction: int
    break_candidate_index: int | None
    break_confirmed_index: int | None
    break_boundary: float | None
    break_buffer: float | None
    price_location: str | None
    nearest_support_low: float | None
    nearest_support_high: float | None
    nearest_resistance_low: float | None
    nearest_resistance_high: float | None
    role_reversal_support_low: float | None
    role_reversal_support_high: float | None
    role_reversal_resistance_low: float | None
    role_reversal_resistance_high: float | None
    reference_atr: float | None
    zones: tuple[SupportResistanceZoneProjection, ...]


@dataclass(frozen=True, slots=True)
class SupportResistanceProjection:
    symbol: str
    timeframes: tuple[str, ...]
    timeframe_facts: tuple[SupportResistanceTimeframeProjection, ...]

    @property
    def refs(self) -> tuple[FactRef, ...]:
        return tuple(item.ref for item in self.timeframe_facts)

    def for_timeframe(self, timeframe: str) -> SupportResistanceTimeframeProjection:
        normalized = timeframe.strip().lower()
        for item in self.timeframe_facts:
            if item.timeframe == normalized:
                return item
        raise KeyError(f"support/resistance projection timeframe not found: {timeframe}")

    def available_at(self, as_of: Any) -> "SupportResistanceProjection":
        return replace(
            self,
            timeframe_facts=tuple(
                item for item in self.timeframe_facts if item.ref.is_available_at(as_of)
            ),
        )


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _zone_projection(zone: Any) -> SupportResistanceZoneProjection:
    center = getattr(zone, "center", None)
    if center is None:
        center = (float(zone.low) + float(zone.high)) * 0.5
    return SupportResistanceZoneProjection(
        zone_id=str(getattr(zone, "zone_uid", getattr(zone, "identity", "UNKNOWN"))),
        side=_enum_value(zone.side),
        low=float(zone.low),
        high=float(zone.high),
        center=float(center),
        lifecycle=_enum_value(zone.lifecycle),
        quality=float(zone.quality),
        touches=int(zone.touches),
        boundary_stability=float(zone.boundary_stability),
        reference_atr=float(zone.reference_atr),
        created_at=getattr(zone, "created_at", None),
        updated_at=getattr(zone, "last_updated_at", None),
    )


def project_support_resistance(
    structure_location: Any,
    *,
    data_quality_by_timeframe: Mapping[str, Any],
) -> SupportResistanceProjection:
    """Expose target-bounded native S/R exports as a causal immutable read model."""

    rows: list[SupportResistanceTimeframeProjection] = []
    for timeframe in structure_location.timeframes:
        replay = structure_location.replay_for(timeframe)
        snapshot = replay.support_resistance
        if snapshot.as_of is None or snapshot.available_at is None:
            continue
        export = snapshot.export
        quality = normalize_context_data_quality(data_quality_by_timeframe[timeframe])
        state = None if export.state is None else str(export.state)
        causal_family, source_family = families_for(
            ContextDomain.SUPPORT_RESISTANCE,
            fact_type="RANGE_EXPORT",
        )
        ref = FactRef(
            domain=ContextDomain.SUPPORT_RESISTANCE,
            fact_type="RANGE_EXPORT",
            symbol=structure_location.symbol,
            timeframe=timeframe,
            native_id=(
                f"SR_RANGE:{timeframe}:{export.range_identity}:"
                f"{snapshot.as_of}"
            ),
            native_state=state or "UNAVAILABLE",
            origin_time=snapshot.as_of,
            confirmed_at=snapshot.as_of,
            available_at=snapshot.available_at,
            lineage_id=None,
            causal_family=causal_family,
            source_family=source_family,
            data_quality=quality,
        )
        rows.append(
            SupportResistanceTimeframeProjection(
                timeframe=timeframe,
                ref=ref,
                state=state,
                range_identity=export.range_identity,
                upper_center=export.upper_center,
                upper_top=export.upper_top,
                upper_bottom=export.upper_bottom,
                lower_center=export.lower_center,
                lower_top=export.lower_top,
                lower_bottom=export.lower_bottom,
                mid_price=export.mid_price,
                quality=export.quality,
                boundary_stability=export.boundary_stability,
                identity_score=export.identity_score,
                upper_touches=int(export.upper_touches),
                lower_touches=int(export.lower_touches),
                upper_close_violations=int(export.upper_close_violations),
                lower_close_violations=int(export.lower_close_violations),
                break_direction=int(export.break_direction),
                break_candidate_index=export.break_candidate_index,
                break_confirmed_index=export.break_confirmed_index,
                break_boundary=export.break_boundary,
                break_buffer=export.break_buffer,
                price_location=export.price_location,
                nearest_support_low=export.nearest_support_low,
                nearest_support_high=export.nearest_support_high,
                nearest_resistance_low=export.nearest_resistance_low,
                nearest_resistance_high=export.nearest_resistance_high,
                role_reversal_support_low=export.role_reversal_support_low,
                role_reversal_support_high=export.role_reversal_support_high,
                role_reversal_resistance_low=export.role_reversal_resistance_low,
                role_reversal_resistance_high=export.role_reversal_resistance_high,
                reference_atr=export.reference_atr,
                zones=tuple(_zone_projection(zone) for zone in snapshot.zones),
            )
        )

    return SupportResistanceProjection(
        symbol=structure_location.symbol,
        timeframes=tuple(structure_location.timeframes),
        timeframe_facts=tuple(sorted(rows, key=lambda item: item.ref.deterministic_key)),
    )


__all__ = [
    "SupportResistanceProjection",
    "SupportResistanceTimeframeProjection",
    "SupportResistanceZoneProjection",
    "project_support_resistance",
]
