from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.participation_behavior_projection import (
    BreakParticipationBehavior,
    EffortResultBehavior,
    ParticipationBehaviorProjection,
    ParticipationTrend,
)

from .structural import StructuralDirection


class ParticipationState(StrEnum):
    SUPPORTIVE = "SUPPORTIVE"
    NEUTRAL = "NEUTRAL"
    WEAK = "WEAK"
    OPPOSING = "OPPOSING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ParticipationAssessment:
    state: ParticipationState
    heavy_conflict: bool
    unsupported_break: bool
    data_quality: ContextDataQuality
    reasons: tuple[str, ...]
    source_refs: tuple[FactRef, ...]


def _direction_value(side: StructuralDirection) -> int:
    if side is StructuralDirection.LONG:
        return 1
    if side is StructuralDirection.SHORT:
        return -1
    return 0


def assess_participation(
    side: StructuralDirection,
    participation: ParticipationBehaviorProjection | None,
    *,
    timeframe: str,
) -> ParticipationAssessment:
    """Classify participation severity relative to Structure without voting on side."""

    direction = _direction_value(side)
    normalized = timeframe.strip().lower()
    if direction == 0:
        return ParticipationAssessment(
            ParticipationState.UNKNOWN,
            False,
            False,
            ContextDataQuality.UNAVAILABLE,
            ("PARTICIPATION_SIDE_UNRESOLVED",),
            (),
        )
    if participation is None:
        return ParticipationAssessment(
            ParticipationState.UNKNOWN,
            False,
            False,
            ContextDataQuality.UNAVAILABLE,
            (f"PARTICIPATION_UNAVAILABLE:{normalized}",),
            (),
        )
    try:
        row = participation.for_timeframe(normalized)
    except KeyError:
        return ParticipationAssessment(
            ParticipationState.UNKNOWN,
            False,
            False,
            ContextDataQuality.UNAVAILABLE,
            (f"PARTICIPATION_TIMEFRAME_UNAVAILABLE:{normalized}",),
            (),
        )

    quality = row.ref.data_quality
    if quality is not ContextDataQuality.VALID or row.participation_trend is ParticipationTrend.UNAVAILABLE:
        return ParticipationAssessment(
            ParticipationState.UNKNOWN,
            bool(row.heavy_conflict),
            row.break_participation is BreakParticipationBehavior.UNSUPPORTED,
            quality,
            (f"PARTICIPATION_DATA_{quality.value}:{normalized}",),
            (row.ref,),
        )

    break_unsupported = row.break_participation is BreakParticipationBehavior.UNSUPPORTED
    reasons: list[str] = []

    # Heavy conflict is an explicit native severity flag and remains OPPOSING even
    # if other volume fields are weak. Ordinary LOW_PARTICIPATION, fading/ended
    # participation, weak effort/result and an unsupported same-side break are WEAK
    # before generic directional evidence is considered.
    if row.heavy_conflict:
        return ParticipationAssessment(
            ParticipationState.OPPOSING,
            True,
            break_unsupported,
            quality,
            ("PARTICIPATION_HEAVY_CONFLICT",),
            (row.ref,),
        )

    weak = (
        row.status.strip().upper() == "LOW_PARTICIPATION"
        or row.participation_trend in {ParticipationTrend.FADING, ParticipationTrend.ENDED}
        or row.effort_result is EffortResultBehavior.WEAK_RESULT
        or break_unsupported
    )
    if weak:
        if row.status.strip().upper() == "LOW_PARTICIPATION":
            reasons.append("LOW_PARTICIPATION")
        if row.participation_trend in {ParticipationTrend.FADING, ParticipationTrend.ENDED}:
            reasons.append(f"PARTICIPATION_{row.participation_trend.value}")
        if row.effort_result is EffortResultBehavior.WEAK_RESULT:
            reasons.append("WEAK_EFFORT_RESULT")
        if break_unsupported:
            reasons.append("UNSUPPORTED_BREAK")
        return ParticipationAssessment(
            ParticipationState.WEAK,
            False,
            break_unsupported,
            quality,
            tuple(reasons),
            (row.ref,),
        )

    opposed_participation = (
        row.participation_direction == -direction
        and row.participation_trend
        in {ParticipationTrend.BUILDING, ParticipationTrend.CONFIRMED, ParticipationTrend.PROTECTED}
    )
    opposed_break = (
        row.break_direction == -direction
        and row.break_participation
        in {BreakParticipationBehavior.SUPPORTED, BreakParticipationBehavior.PROTECTED}
    )
    opposed_evidence = row.evidence_direction == -direction and row.evidence_direction != 0
    if opposed_participation or opposed_break or opposed_evidence:
        if opposed_participation:
            reasons.append("PARTICIPATION_TREND_OPPOSES_STRUCTURE")
        if opposed_break:
            reasons.append("SUPPORTED_BREAK_OPPOSES_STRUCTURE")
        if opposed_evidence:
            reasons.append("PARTICIPATION_EVIDENCE_OPPOSES_STRUCTURE")
        return ParticipationAssessment(
            ParticipationState.OPPOSING,
            False,
            False,
            quality,
            tuple(reasons),
            (row.ref,),
        )

    aligned_participation = (
        row.participation_direction == direction
        and row.participation_trend
        in {ParticipationTrend.BUILDING, ParticipationTrend.CONFIRMED, ParticipationTrend.PROTECTED}
    )
    aligned_break = (
        row.break_direction == direction
        and row.break_participation
        in {BreakParticipationBehavior.SUPPORTED, BreakParticipationBehavior.PROTECTED}
    )
    aligned_evidence = row.evidence_direction == direction and row.evidence_direction != 0
    if aligned_participation or aligned_break or aligned_evidence:
        if aligned_participation:
            reasons.append("PARTICIPATION_TREND_SUPPORTS_STRUCTURE")
        if aligned_break:
            reasons.append("SUPPORTED_BREAK_ALIGNS_STRUCTURE")
        if aligned_evidence:
            reasons.append("PARTICIPATION_EVIDENCE_ALIGNS_STRUCTURE")
        return ParticipationAssessment(
            ParticipationState.SUPPORTIVE,
            False,
            False,
            quality,
            tuple(reasons),
            (row.ref,),
        )

    return ParticipationAssessment(
        ParticipationState.NEUTRAL,
        False,
        False,
        quality,
        ("PARTICIPATION_VALID_WITHOUT_DIRECTIONAL_SEVERITY",),
        (row.ref,),
    )


__all__ = ["ParticipationAssessment", "ParticipationState", "assess_participation"]
