from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .envelope import ContextDomain, FactRef, normalize_context_data_quality
from .lineage import families_for


@dataclass(frozen=True, slots=True)
class LiquidityLandscapeObservation:
    timeframe: str
    ref: FactRef
    landscape: str


@dataclass(frozen=True, slots=True)
class LiquidityLandscapeProjection:
    symbol: str
    timeframes: tuple[str, ...]
    observations: tuple[LiquidityLandscapeObservation, ...]

    @property
    def refs(self) -> tuple[FactRef, ...]:
        return tuple(item.ref for item in self.observations)

    def for_timeframe(self, timeframe: str) -> LiquidityLandscapeObservation:
        normalized = timeframe.strip().lower()
        for item in self.observations:
            if item.timeframe == normalized:
                return item
        raise KeyError(f"liquidity landscape timeframe not found: {timeframe}")

    def available_at(self, as_of: Any) -> "LiquidityLandscapeProjection":
        return replace(
            self,
            observations=tuple(
                item for item in self.observations if item.ref.is_available_at(as_of)
            ),
        )


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def project_liquidity_landscape(
    replay: Any | None,
    *,
    data_quality_by_timeframe: Mapping[str, Any],
) -> LiquidityLandscapeProjection | None:
    """Project the native nearby-objective landscape without adding direction.

    The native liquidity tracker owns the landscape classification. This read model
    only gives that state an immutable causal FactRef so downstream decision input
    can preserve availability, quality and family metadata.
    """

    if replay is None:
        return None

    behavior_by_timeframe = getattr(replay, "liquidity_behavior", None) or {}
    rows: list[LiquidityLandscapeObservation] = []
    for timeframe, behavior in behavior_by_timeframe.items():
        snapshot = replay.snapshots.get(timeframe)
        as_of = getattr(behavior, "as_of", None)
        available_at = None if snapshot is None else getattr(snapshot, "available_at", None)
        if as_of is None or available_at is None:
            continue

        quality = normalize_context_data_quality(data_quality_by_timeframe[timeframe])
        landscape = _enum_value(behavior.landscape)
        causal_family, source_family = families_for(
            ContextDomain.LIQUIDITY,
            fact_type="LANDSCAPE",
        )
        ref = FactRef(
            domain=ContextDomain.LIQUIDITY,
            fact_type="LANDSCAPE",
            symbol=replay.symbol,
            timeframe=timeframe,
            native_id=f"LIQ_LANDSCAPE:{timeframe}:{as_of}",
            native_state=landscape,
            origin_time=as_of,
            confirmed_at=as_of,
            available_at=available_at,
            lineage_id=None,
            causal_family=causal_family,
            source_family=source_family,
            data_quality=quality,
        )
        rows.append(
            LiquidityLandscapeObservation(
                timeframe=timeframe,
                ref=ref,
                landscape=landscape,
            )
        )

    return LiquidityLandscapeProjection(
        symbol=replay.symbol,
        timeframes=tuple(replay.timeframes),
        observations=tuple(sorted(rows, key=lambda item: item.ref.deterministic_key)),
    )


__all__ = [
    "LiquidityLandscapeObservation",
    "LiquidityLandscapeProjection",
    "project_liquidity_landscape",
]
