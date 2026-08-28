from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .envelope import ContextDataQuality, ContextDomain, FactRef, normalize_context_data_quality
from .lineage import families_for


def _quality(value: Any) -> ContextDataQuality:
    return normalize_context_data_quality(value)


def _ref(
    *,
    domain: ContextDomain,
    fact_type: str,
    symbol: str,
    timeframe: str,
    native_id: str,
    native_state: str,
    origin_time: Any,
    observed_at: Any,
    available_at: Any,
    data_quality: ContextDataQuality,
    lineage_id: str,
) -> FactRef:
    causal_family, source_family = families_for(domain, fact_type=fact_type)
    return FactRef(
        domain=domain,
        fact_type=fact_type,
        symbol=symbol,
        timeframe=timeframe,
        native_id=native_id,
        native_state=native_state,
        origin_time=origin_time,
        confirmed_at=observed_at,
        available_at=available_at,
        lineage_id=lineage_id,
        causal_family=causal_family,
        source_family=source_family,
        data_quality=data_quality,
    )


@dataclass(frozen=True, slots=True)
class FvgLifecycleProjection:
    ref: FactRef
    identity: str
    direction: int
    state: str
    lower_boundary: float
    upper_boundary: float
    quality: float
    gap_atr: float
    formation_atr: float
    formation_index: int
    first_test_index: int | None
    wick_fill_ratio: float
    close_fill_ratio: float
    maximum_fill_ratio: float
    reaction_evidence_count: int
    reaction_confirmed: bool
    failed_reaction: bool
    full_fill: bool
    invalid: bool
    invalid_reason: str
    invalid_close_count: int


@dataclass(frozen=True, slots=True)
class EngulfingLifecycleProjection:
    ref: FactRef
    identity: str
    direction: int
    state: str
    lower_boundary: float
    upper_boundary: float
    quality: float
    body_atr: float
    formation_index: int
    first_test_index: int | None
    maximum_retrace_ratio: float
    continuation_evidence_count: int
    continuation_confirmed: bool
    weakened: bool
    weakened_index: int | None
    invalid: bool
    completion_reason: str


@dataclass(frozen=True, slots=True)
class FvgEngulfingLifecycleProjection:
    symbol: str
    timeframes: tuple[str, ...]
    fvg: tuple[FvgLifecycleProjection, ...]
    engulfing: tuple[EngulfingLifecycleProjection, ...]

    @property
    def refs(self) -> tuple[FactRef, ...]:
        return tuple(item.ref for item in (*self.fvg, *self.engulfing))

    def available_at(self, as_of: Any) -> "FvgEngulfingLifecycleProjection":
        return replace(
            self,
            fvg=tuple(item for item in self.fvg if item.ref.is_available_at(as_of)),
            engulfing=tuple(item for item in self.engulfing if item.ref.is_available_at(as_of)),
        )


def project_fvg_engulfing_lifecycle(
    replay: Any | None,
    *,
    data_quality_by_timeframe: Mapping[str, Any],
) -> FvgEngulfingLifecycleProjection | None:
    if replay is None:
        return None

    fvg_rows: list[FvgLifecycleProjection] = []
    engulf_rows: list[EngulfingLifecycleProjection] = []
    fvg_by_tf = getattr(replay, "fvg_lifecycle", None) or {}
    engulf_by_tf = getattr(replay, "engulfing_lifecycle", None) or {}

    for timeframe in replay.timeframes:
        snapshot = replay.snapshots.get(timeframe)
        if snapshot is None:
            continue
        quality = _quality(data_quality_by_timeframe[timeframe])
        for item in fvg_by_tf.get(timeframe, ()):
            ref = _ref(
                domain=ContextDomain.FVG,
                fact_type="FVG_LIFECYCLE",
                symbol=replay.symbol,
                timeframe=timeframe,
                native_id=f"FVG_LIFECYCLE:{timeframe}:{item.identity}:{snapshot.as_of}",
                native_state=str(item.state),
                origin_time=item.formation_time,
                observed_at=snapshot.as_of,
                available_at=snapshot.available_at,
                data_quality=quality,
                lineage_id=str(item.identity),
            )
            fvg_rows.append(
                FvgLifecycleProjection(
                    ref=ref,
                    identity=str(item.identity),
                    direction=int(item.direction),
                    state=str(item.state),
                    lower_boundary=float(item.lower_boundary),
                    upper_boundary=float(item.upper_boundary),
                    quality=float(item.quality),
                    gap_atr=float(item.gap_atr),
                    formation_atr=float(item.formation_atr),
                    formation_index=int(item.formation_index),
                    first_test_index=item.first_test_index,
                    wick_fill_ratio=float(item.wick_fill_ratio),
                    close_fill_ratio=float(item.close_fill_ratio),
                    maximum_fill_ratio=float(item.maximum_fill_ratio),
                    reaction_evidence_count=int(item.reaction_evidence_count),
                    reaction_confirmed=bool(item.reaction_confirmed),
                    failed_reaction=bool(item.failed_reaction),
                    full_fill=bool(item.full_fill),
                    invalid=bool(item.invalid),
                    invalid_reason=str(item.invalid_reason),
                    invalid_close_count=int(item.invalid_close_count),
                )
            )

        for item in engulf_by_tf.get(timeframe, ()):
            ref = _ref(
                domain=ContextDomain.ENGULFING,
                fact_type="ENGULFING_LIFECYCLE",
                symbol=replay.symbol,
                timeframe=timeframe,
                native_id=f"ENGULFING_LIFECYCLE:{timeframe}:{item.identity}:{snapshot.as_of}",
                native_state=str(item.state),
                origin_time=item.formation_time,
                observed_at=snapshot.as_of,
                available_at=snapshot.available_at,
                data_quality=quality,
                lineage_id=str(item.identity),
            )
            engulf_rows.append(
                EngulfingLifecycleProjection(
                    ref=ref,
                    identity=str(item.identity),
                    direction=int(item.direction),
                    state=str(item.state),
                    lower_boundary=float(item.lower_boundary),
                    upper_boundary=float(item.upper_boundary),
                    quality=float(item.quality),
                    body_atr=float(item.body_atr),
                    formation_index=int(item.formation_index),
                    first_test_index=item.first_test_index,
                    maximum_retrace_ratio=float(item.maximum_retrace_ratio),
                    continuation_evidence_count=int(item.continuation_evidence_count),
                    continuation_confirmed=bool(item.continuation_confirmed),
                    weakened=bool(item.weakened),
                    weakened_index=item.weakened_index,
                    invalid=bool(item.invalid),
                    completion_reason=str(item.completion_reason),
                )
            )

    return FvgEngulfingLifecycleProjection(
        symbol=replay.symbol,
        timeframes=tuple(replay.timeframes),
        fvg=tuple(sorted(fvg_rows, key=lambda item: item.ref.deterministic_key)),
        engulfing=tuple(sorted(engulf_rows, key=lambda item: item.ref.deterministic_key)),
    )


__all__ = [
    "EngulfingLifecycleProjection",
    "FvgEngulfingLifecycleProjection",
    "FvgLifecycleProjection",
    "project_fvg_engulfing_lifecycle",
]
