from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from financial_dashboard.data.quality import DataQualityStatus

from .models import Direction, EngineResult


class TimeframeRole(StrEnum):
    MACRO_CONTEXT = "MACRO_CONTEXT"
    STRUCTURAL_CONTEXT = "STRUCTURAL_CONTEXT"
    PRIMARY_STRUCTURE = "PRIMARY_STRUCTURE"
    TACTICAL_STRUCTURE = "TACTICAL_STRUCTURE"
    TRIGGER_CONTEXT = "TRIGGER_CONTEXT"
    REFINEMENT = "REFINEMENT"


TIMEFRAME_ROLE_MAP: dict[str, TimeframeRole] = {
    "1d": TimeframeRole.MACRO_CONTEXT,
    "4h": TimeframeRole.STRUCTURAL_CONTEXT,
    "2h": TimeframeRole.PRIMARY_STRUCTURE,
    "1h": TimeframeRole.TACTICAL_STRUCTURE,
    "30m": TimeframeRole.TRIGGER_CONTEXT,
    "15m": TimeframeRole.REFINEMENT,
}


def role_for_timeframe(timeframe: str) -> TimeframeRole:
    key = timeframe.strip().lower()
    try:
        return TIMEFRAME_ROLE_MAP[key]
    except KeyError as exc:
        raise ValueError(f"unsupported MTF Story timeframe: {timeframe}") from exc


class ContextState(StrEnum):
    BULLISH_CONTEXT = "BULLISH_CONTEXT"
    BEARISH_CONTEXT = "BEARISH_CONTEXT"
    MIXED_CONTEXT = "MIXED_CONTEXT"
    TRANSITION_CONTEXT = "TRANSITION_CONTEXT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class TriggerState(StrEnum):
    BULLISH_TRIGGER = "BULLISH_TRIGGER"
    BEARISH_TRIGGER = "BEARISH_TRIGGER"
    REVERSAL_TRIGGER = "REVERSAL_TRIGGER"
    BREAKOUT_TRIGGER = "BREAKOUT_TRIGGER"
    NO_TRIGGER = "NO_TRIGGER"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class MTFStoryState(StrEnum):
    TREND_CONTINUATION = "TREND_CONTINUATION"
    COUNTER_TREND_RALLY = "COUNTER_TREND_RALLY"
    COUNTER_TREND_DROP = "COUNTER_TREND_DROP"
    REVERSAL_BUILDING = "REVERSAL_BUILDING"
    REVERSAL_CONFIRMED = "REVERSAL_CONFIRMED"
    COMPRESSION = "COMPRESSION"
    BREAKOUT_BUILDING = "BREAKOUT_BUILDING"
    BREAKOUT_CONFIRMED = "BREAKOUT_CONFIRMED"
    RANGE_MIXED = "RANGE_MIXED"
    STRUCTURAL_CONFLICT = "STRUCTURAL_CONFLICT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ConflictSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


@dataclass(frozen=True, slots=True)
class StoryConflict:
    code: str
    message: str
    severity: ConflictSeverity = ConflictSeverity.WARNING
    timeframes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RawTimeframeEvidence:
    """Uninterpreted engine evidence for one timeframe."""

    timeframe: str
    role: TimeframeRole
    data_quality: DataQualityStatus
    market_structure: EngineResult | None = None
    market_structure_export: object | None = None
    pattern_compression: EngineResult | None = None
    pattern_export: object | None = None

    def __post_init__(self) -> None:
        expected = role_for_timeframe(self.timeframe)
        if self.role is not expected:
            raise ValueError(
                f"timeframe {self.timeframe} requires role {expected.value}, got {self.role.value}"
            )


@dataclass(frozen=True, slots=True)
class TimeframeStoryState:
    """Normalized evidence consumed by context/trigger classifiers.

    Structural direction and actual pattern/breakout direction remain separate by
    contract. In particular, a bullish pattern breakout does not rewrite a bearish
    Market Structure state.
    """

    timeframe: str
    role: TimeframeRole
    data_quality: DataQualityStatus
    timestamp: Any = None
    structural_direction: Direction = Direction.NEUTRAL
    structural_state: str | None = None
    structural_score: float | None = None
    structural_quality: float | None = None
    pattern_direction: Direction = Direction.NEUTRAL
    pattern_classic_direction: Direction = Direction.NEUTRAL
    pattern_state: str | None = None
    pattern_type: str | None = None
    pattern_quality: float | None = None
    breakout_direction: Direction = Direction.NEUTRAL
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        expected = role_for_timeframe(self.timeframe)
        if self.role is not expected:
            raise ValueError(
                f"timeframe {self.timeframe} requires role {expected.value}, got {self.role.value}"
            )
        for name, value in (
            ("structural_score", self.structural_score),
            ("structural_quality", self.structural_quality),
            ("pattern_quality", self.pattern_quality),
        ):
            if value is not None and not 0.0 <= float(value) <= 100.0:
                raise ValueError(f"{name} must be within 0..100")

    @property
    def usable(self) -> bool:
        return self.data_quality is not DataQualityStatus.INVALID


@dataclass(frozen=True, slots=True)
class ContextAssessment:
    """Structural 1D/4H/2H context classification output.

    This contract deliberately does not contain BUY/SELL, trigger, entry, or final
    story fields. Tur 3 only establishes hierarchical market context.
    """

    state: ContextState
    direction: Direction
    anchor_timeframe: str | None
    usable_timeframes: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    conflicts: tuple[StoryConflict, ...] = ()


@dataclass(frozen=True, slots=True)
class MTFStoryResult:
    state: MTFStoryState
    timestamp: Any
    dominant_direction: Direction
    macro_direction: Direction
    context_state: ContextState
    trigger_state: TriggerState
    quality: float
    confidence: float
    timeframe_states: tuple[TimeframeStoryState, ...] = ()
    reasons: tuple[str, ...] = ()
    conflicts: tuple[StoryConflict, ...] = ()
    is_confirmed: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.quality) <= 100.0:
            raise ValueError("quality must be within 0..100")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be within 0..1")

        seen: set[str] = set()
        for state in self.timeframe_states:
            key = state.timeframe.strip().lower()
            if key in seen:
                raise ValueError(f"duplicate timeframe state: {state.timeframe}")
            seen.add(key)
