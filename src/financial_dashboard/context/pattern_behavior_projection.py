from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Callable

from financial_dashboard.engines.pattern_compression_core import (
    ST_BREAK_ATTEMPT,
    ST_BREAK_CANDIDATE,
    ST_BREAK_CONFIRMED,
    ST_BREAK_FAILED,
    ST_BREAK_TIMEOUT,
    ST_CANDIDATE,
    ST_COMPLETED,
    ST_COMPRESSING,
    ST_DEFINED,
    ST_GEOMETRY,
    ST_INVALID,
    ST_MATURING,
    ST_NONE,
    ST_PREP,
    ST_RETESTING,
    ST_RETEST_OK,
    ST_RETEST_WAIT,
    ST_WEAK,
)

from .envelope import ContextDataQuality, ContextDomain, FactRef, normalize_context_data_quality
from .lineage import families_for


AvailabilityResolver = Callable[[Any, str], Any]


class PatternBehaviorPhase(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    NO_PATTERN = "NO_PATTERN"
    FORMING = "FORMING"
    MATURE_COMPRESSION = "MATURE_COMPRESSION"
    BREAK_ATTEMPT = "BREAK_ATTEMPT"
    BREAK_CONFIRMING = "BREAK_CONFIRMING"
    BREAK_CONFIRMED = "BREAK_CONFIRMED"
    POST_BREAK_RETEST = "POST_BREAK_RETEST"
    RETEST_HELD = "RETEST_HELD"
    BREAK_FAILED = "BREAK_FAILED"
    WEAKENING = "WEAKENING"
    INVALIDATED = "INVALIDATED"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True, slots=True)
class PatternBehaviorTimeframeProjection:
    timeframe: str
    ref: FactRef
    phase: PatternBehaviorPhase
    native_state: str
    pattern_state_code: int | None
    pattern_type_code: int | None
    classic_direction: int
    identity: float | None
    age_bars: int | None
    bars_since_known: int | None
    progress: float | None
    contraction: float | None
    raw_quality: float | None
    selection_score: float | None
    export_quality: float | None
    upper_touches: int
    lower_touches: int
    quality_frozen: bool
    break_state_code: int | None
    break_level: float | None
    break_strength: float | None
    retest_state_code: int | None
    retest_tolerance: float | None


@dataclass(frozen=True, slots=True)
class PatternBehaviorProjection:
    symbol: str
    timeframes: tuple[str, ...]
    timeframe_facts: tuple[PatternBehaviorTimeframeProjection, ...]

    @property
    def refs(self) -> tuple[FactRef, ...]:
        return tuple(item.ref for item in self.timeframe_facts)

    def for_timeframe(self, timeframe: str) -> PatternBehaviorTimeframeProjection:
        normalized = timeframe.strip().lower()
        for item in self.timeframe_facts:
            if item.timeframe == normalized:
                return item
        raise KeyError(f"pattern behavior timeframe not found: {timeframe}")

    def available_at(self, as_of: Any) -> "PatternBehaviorProjection":
        return replace(
            self,
            timeframe_facts=tuple(
                item for item in self.timeframe_facts if item.ref.is_available_at(as_of)
            ),
        )


def _phase(native_state: str, *, unavailable: bool) -> PatternBehaviorPhase:
    if unavailable:
        return PatternBehaviorPhase.UNAVAILABLE
    if native_state == ST_NONE:
        return PatternBehaviorPhase.NO_PATTERN
    if native_state in {ST_CANDIDATE, ST_GEOMETRY, ST_DEFINED}:
        return PatternBehaviorPhase.FORMING
    if native_state in {ST_MATURING, ST_COMPRESSING, ST_PREP}:
        return PatternBehaviorPhase.MATURE_COMPRESSION
    if native_state == ST_BREAK_ATTEMPT:
        return PatternBehaviorPhase.BREAK_ATTEMPT
    if native_state == ST_BREAK_CANDIDATE:
        return PatternBehaviorPhase.BREAK_CONFIRMING
    if native_state == ST_BREAK_CONFIRMED:
        return PatternBehaviorPhase.BREAK_CONFIRMED
    if native_state in {ST_RETEST_WAIT, ST_RETESTING}:
        return PatternBehaviorPhase.POST_BREAK_RETEST
    if native_state == ST_RETEST_OK:
        return PatternBehaviorPhase.RETEST_HELD
    if native_state in {ST_BREAK_TIMEOUT, ST_BREAK_FAILED}:
        return PatternBehaviorPhase.BREAK_FAILED
    if native_state == ST_WEAK:
        return PatternBehaviorPhase.WEAKENING
    if native_state == ST_INVALID:
        return PatternBehaviorPhase.INVALIDATED
    if native_state == ST_COMPLETED:
        return PatternBehaviorPhase.COMPLETED
    return PatternBehaviorPhase.NO_PATTERN


def _fact_ref(
    *,
    symbol: str,
    timeframe: str,
    timestamp: Any,
    available_at: Any,
    native_state: str,
    data_quality: ContextDataQuality,
) -> FactRef:
    causal_family, source_family = families_for(
        ContextDomain.PATTERN,
        fact_type="PATTERN_BEHAVIOR",
    )
    return FactRef(
        domain=ContextDomain.PATTERN,
        fact_type="PATTERN_BEHAVIOR",
        symbol=symbol,
        timeframe=timeframe,
        native_id=f"PATTERN_BEHAVIOR:{timeframe}:{timestamp}",
        native_state=native_state,
        origin_time=timestamp,
        confirmed_at=timestamp,
        available_at=available_at,
        lineage_id=None,
        causal_family=causal_family,
        source_family=source_family,
        data_quality=data_quality,
    )


def project_pattern_behavior(
    replay: Any | None,
    *,
    available_at: AvailabilityResolver,
) -> PatternBehaviorProjection | None:
    if replay is None:
        return None

    rows: list[PatternBehaviorTimeframeProjection] = []
    for snapshot in replay.pattern_snapshots:
        if snapshot.as_of is None:
            continue
        source_quality = replay.structure_location.replay_for(
            snapshot.timeframe
        ).input_batch.source_quality.status
        quality = normalize_context_data_quality(source_quality)
        unavailable = quality is not ContextDataQuality.VALID
        native_state = str(snapshot.native_state or ST_NONE)
        phase = _phase(native_state, unavailable=unavailable)
        export = snapshot.export
        pattern_state_code = None if export is None else export.state
        identity = None if export is None else export.identity
        ref_state = f"{phase.value}:{native_state}"
        ref = _fact_ref(
            symbol=replay.symbol,
            timeframe=snapshot.timeframe,
            timestamp=snapshot.as_of,
            available_at=available_at(snapshot.as_of, snapshot.timeframe),
            native_state=ref_state,
            data_quality=quality,
        )
        current_index = max(0, int(snapshot.bar_count) - 1)
        age_bars = (
            None
            if snapshot.active_start_bar is None
            else max(0, current_index - int(snapshot.active_start_bar))
        )
        bars_since_known = (
            None
            if snapshot.active_known_bar is None
            else max(0, current_index - int(snapshot.active_known_bar))
        )
        rows.append(
            PatternBehaviorTimeframeProjection(
                timeframe=snapshot.timeframe,
                ref=ref,
                phase=phase,
                native_state=native_state,
                pattern_state_code=pattern_state_code,
                pattern_type_code=None if export is None else export.pattern_type,
                classic_direction=0 if export is None or export.classic_direction is None else int(export.classic_direction),
                identity=identity,
                age_bars=age_bars,
                bars_since_known=bars_since_known,
                progress=snapshot.progress,
                contraction=snapshot.contraction,
                raw_quality=snapshot.raw_quality,
                selection_score=snapshot.selection_score,
                export_quality=None if export is None else export.quality,
                upper_touches=int(snapshot.upper_touches),
                lower_touches=int(snapshot.lower_touches),
                quality_frozen=bool(snapshot.quality_frozen),
                break_state_code=None if export is None else export.break_state,
                break_level=None if export is None else export.break_level,
                break_strength=None if export is None else export.break_strength,
                retest_state_code=None if export is None else export.retest_state,
                retest_tolerance=None if export is None else export.retest_tolerance,
            )
        )

    return PatternBehaviorProjection(
        symbol=replay.symbol,
        timeframes=tuple(replay.timeframes),
        timeframe_facts=tuple(rows),
    )


__all__ = [
    "PatternBehaviorPhase",
    "PatternBehaviorProjection",
    "PatternBehaviorTimeframeProjection",
    "project_pattern_behavior",
]
