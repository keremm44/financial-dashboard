from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Callable

from .envelope import ContextDataQuality, ContextDomain, FactRef, normalize_context_data_quality
from .lineage import families_for


AvailabilityResolver = Callable[[Any, str], Any]


class ParticipationTrend(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    NONE = "NONE"
    BUILDING = "BUILDING"
    CONFIRMED = "CONFIRMED"
    PROTECTED = "PROTECTED"
    FADING = "FADING"
    ENDED = "ENDED"


class EffortResultBehavior(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    NEUTRAL = "NEUTRAL"
    EFFICIENT = "EFFICIENT"
    WEAK_RESULT = "WEAK_RESULT"


class AbsorptionBehavior(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    NONE = "NONE"
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    RESOLVED = "RESOLVED"


class BreakParticipationBehavior(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    NONE = "NONE"
    DEVELOPING = "DEVELOPING"
    SUPPORTED = "SUPPORTED"
    PROTECTED = "PROTECTED"
    UNSUPPORTED = "UNSUPPORTED"
    RECLAIMED = "RECLAIMED"


class ShockBehavior(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    NONE = "NONE"
    ONE_BAR = "ONE_BAR"


@dataclass(frozen=True, slots=True)
class ParticipationBehaviorTimeframeProjection:
    timeframe: str
    ref: FactRef
    status: str
    final_state: str
    evidence_direction: int
    participation_trend: ParticipationTrend
    effort_result: EffortResultBehavior
    absorption: AbsorptionBehavior
    break_participation: BreakParticipationBehavior
    shock: ShockBehavior
    participation_direction: int
    participation_stage: str
    controlled_pullback: bool
    controlled_reaction: bool
    absorption_side: str
    absorption_stage: str
    break_direction: int
    break_stage: str
    heavy_conflict: bool
    shock_direction: int
    rvol: float | None
    relative_traded_value: float | None
    directional_value_pressure_5: float | None
    directional_value_pressure_10: float | None
    net_progress_atr: float | None
    directional_efficiency: float | None
    effort_result_class: str | None


@dataclass(frozen=True, slots=True)
class ParticipationBehaviorProjection:
    symbol: str
    timeframes: tuple[str, ...]
    timeframe_facts: tuple[ParticipationBehaviorTimeframeProjection, ...]

    @property
    def refs(self) -> tuple[FactRef, ...]:
        return tuple(item.ref for item in self.timeframe_facts)

    def for_timeframe(self, timeframe: str) -> ParticipationBehaviorTimeframeProjection:
        normalized = timeframe.strip().lower()
        for item in self.timeframe_facts:
            if item.timeframe == normalized:
                return item
        raise KeyError(f"participation behavior timeframe not found: {timeframe}")

    def available_at(self, as_of: Any) -> "ParticipationBehaviorProjection":
        return replace(
            self,
            timeframe_facts=tuple(
                item for item in self.timeframe_facts if item.ref.is_available_at(as_of)
            ),
        )


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
        ContextDomain.VOLUME,
        fact_type="PARTICIPATION_BEHAVIOR",
    )
    return FactRef(
        domain=ContextDomain.VOLUME,
        fact_type="PARTICIPATION_BEHAVIOR",
        symbol=symbol,
        timeframe=timeframe,
        native_id=f"VOL_BEHAVIOR:{timeframe}:{timestamp}",
        native_state=native_state,
        origin_time=timestamp,
        confirmed_at=timestamp,
        available_at=available_at,
        lineage_id=None,
        causal_family=causal_family,
        source_family=source_family,
        data_quality=data_quality,
    )


def _is_unavailable(status: str) -> bool:
    return status in {"WARMUP", "VOLUME_UNAVAILABLE"}


def _participation_trend(status: str, final_state: str, export: Any) -> ParticipationTrend:
    if _is_unavailable(status):
        return ParticipationTrend.UNAVAILABLE
    stage = str(getattr(export, "participation_stage", "NONE") or "NONE").upper()
    if stage == "CONFIRMED":
        return ParticipationTrend.CONFIRMED
    if stage == "PROTECTED":
        return ParticipationTrend.PROTECTED
    if stage == "WEAKENING":
        return ParticipationTrend.FADING
    if stage == "CLOSED":
        return ParticipationTrend.ENDED
    if "CANDIDATE" in final_state or final_state in {
        "PARTICIPATION_RISING",
        "PARTICIPATION_ABNORMAL_VOLUME",
        "PARTICIPATION_ABNORMAL_CAPITAL",
    }:
        return ParticipationTrend.BUILDING
    return ParticipationTrend.NONE


def _effort_result(status: str, export: Any) -> EffortResultBehavior:
    if _is_unavailable(status):
        return EffortResultBehavior.UNAVAILABLE
    value = str(getattr(export, "effort_result_class", "NEUTRAL") or "NEUTRAL").upper()
    if value in {"RISING_EFFORT_STRONG_RESULT", "HIGH_EFFORT_STRONG_RESULT"}:
        return EffortResultBehavior.EFFICIENT
    if value in {"HIGH_EFFORT_WEAK_RESULT", "VERY_HIGH_EFFORT_WEAK_RESULT"}:
        return EffortResultBehavior.WEAK_RESULT
    return EffortResultBehavior.NEUTRAL


def _absorption(status: str, export: Any) -> AbsorptionBehavior:
    if _is_unavailable(status):
        return AbsorptionBehavior.UNAVAILABLE
    stage = str(getattr(export, "absorption_stage", "NONE") or "NONE").upper()
    if stage == "CANDIDATE":
        return AbsorptionBehavior.CANDIDATE
    if stage == "CONFIRMED":
        return AbsorptionBehavior.CONFIRMED
    if stage in {"INVALIDATED", "EXPIRED"}:
        return AbsorptionBehavior.RESOLVED
    return AbsorptionBehavior.NONE


def _break_participation(status: str, export: Any) -> BreakParticipationBehavior:
    if _is_unavailable(status):
        return BreakParticipationBehavior.UNAVAILABLE
    stage = str(getattr(export, "break_stage", "NONE") or "NONE").upper()
    mapping = {
        "DEVELOPING": BreakParticipationBehavior.DEVELOPING,
        "SUPPORTED": BreakParticipationBehavior.SUPPORTED,
        "PROTECTED": BreakParticipationBehavior.PROTECTED,
        "UNSUPPORTED": BreakParticipationBehavior.UNSUPPORTED,
        "RECLAIMED": BreakParticipationBehavior.RECLAIMED,
    }
    return mapping.get(stage, BreakParticipationBehavior.NONE)


def _shock(status: str, export: Any) -> ShockBehavior:
    if _is_unavailable(status):
        return ShockBehavior.UNAVAILABLE
    return ShockBehavior.ONE_BAR if bool(getattr(export, "one_bar_shock", False)) else ShockBehavior.NONE


def project_participation_behavior(
    replay: Any | None,
    *,
    available_at: AvailabilityResolver,
) -> ParticipationBehaviorProjection | None:
    if replay is None:
        return None

    rows: list[ParticipationBehaviorTimeframeProjection] = []
    for timeframe_replay in replay.timeframe_replays:
        latest = timeframe_replay.latest
        export = latest.audit_export
        status = str(getattr(latest.status, "value", latest.status))
        final_state = str(latest.state)
        quality = normalize_context_data_quality(latest.data_quality)
        trend = _participation_trend(status, final_state, export)
        effort = _effort_result(status, export)
        absorption = _absorption(status, export)
        break_behavior = _break_participation(status, export)
        shock = _shock(status, export)
        native_state = ":".join(
            (
                trend.value,
                effort.value,
                absorption.value,
                break_behavior.value,
                shock.value,
            )
        )
        ref = _fact_ref(
            symbol=replay.symbol,
            timeframe=timeframe_replay.timeframe,
            timestamp=latest.timestamp,
            available_at=available_at(latest.timestamp, timeframe_replay.timeframe),
            native_state=native_state,
            data_quality=quality,
        )
        rows.append(
            ParticipationBehaviorTimeframeProjection(
                timeframe=timeframe_replay.timeframe,
                ref=ref,
                status=status,
                final_state=final_state,
                evidence_direction=int(latest.evidence_direction),
                participation_trend=trend,
                effort_result=effort,
                absorption=absorption,
                break_participation=break_behavior,
                shock=shock,
                participation_direction=int(getattr(export, "participation_direction", 0)),
                participation_stage=str(getattr(export, "participation_stage", "NONE") or "NONE"),
                controlled_pullback=bool(getattr(export, "controlled_pullback", False)),
                controlled_reaction=bool(getattr(export, "controlled_reaction", False)),
                absorption_side=str(getattr(export, "absorption_side", "NONE") or "NONE"),
                absorption_stage=str(getattr(export, "absorption_stage", "NONE") or "NONE"),
                break_direction=int(getattr(export, "break_direction", 0)),
                break_stage=str(getattr(export, "break_stage", "NONE") or "NONE"),
                heavy_conflict=bool(getattr(export, "heavy_conflict", False)),
                shock_direction=int(getattr(export, "shock_direction", 0)),
                rvol=getattr(export, "rvol", None),
                relative_traded_value=getattr(export, "relative_traded_value", None),
                directional_value_pressure_5=getattr(export, "directional_value_pressure_5", None),
                directional_value_pressure_10=getattr(export, "directional_value_pressure_10", None),
                net_progress_atr=getattr(export, "net_progress_atr", None),
                directional_efficiency=getattr(export, "directional_efficiency", None),
                effort_result_class=getattr(export, "effort_result_class", None),
            )
        )

    return ParticipationBehaviorProjection(
        symbol=replay.symbol,
        timeframes=tuple(replay.timeframes),
        timeframe_facts=tuple(rows),
    )


__all__ = [
    "AbsorptionBehavior",
    "BreakParticipationBehavior",
    "EffortResultBehavior",
    "ParticipationBehaviorProjection",
    "ParticipationBehaviorTimeframeProjection",
    "ParticipationTrend",
    "ShockBehavior",
    "project_participation_behavior",
]
