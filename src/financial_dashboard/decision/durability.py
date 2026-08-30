from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from financial_dashboard.context.envelope import (
    ContextDataQuality,
    FactRef,
    normalize_context_data_quality,
)
from financial_dashboard.context.projections import StabilSupportProjection


class DurabilityState(StrEnum):
    HEALTHY = "HEALTHY"
    SOFTENING = "SOFTENING"
    FRACTURED = "FRACTURED"
    BROKEN = "BROKEN"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class DurabilityAssessment:
    """Long-horizon foundation health without structural-direction authority."""

    state: DurabilityState
    data_quality: ContextDataQuality
    reasons: tuple[str, ...]
    source_refs: tuple[FactRef, ...]


def _refs(projection: StabilSupportProjection | None) -> tuple[FactRef, ...]:
    if projection is None:
        return ()
    refs: list[FactRef] = []
    if projection.support_ref is not None:
        refs.append(projection.support_ref)
    refs.extend(event.ref for event in projection.events)
    return tuple(sorted(refs, key=lambda ref: ref.deterministic_key))


def assess_durability(
    stabil: StabilSupportProjection | None,
) -> DurabilityAssessment:
    """Classify Stabil foundation health using only native lifecycle semantics.

    DATA_LIMITED remains lower-quality evidence but is still usable when Stabil has
    actually emitted an observed lifecycle/behavior state. WARMING_UP/UNAVAILABLE
    remain UNKNOWN. No source fact is promoted or mutated at the Decision boundary.
    """

    if stabil is None:
        return DurabilityAssessment(
            state=DurabilityState.UNKNOWN,
            data_quality=ContextDataQuality.UNAVAILABLE,
            reasons=("STABIL_UNAVAILABLE",),
            source_refs=(),
        )

    refs = _refs(stabil)
    quality = normalize_context_data_quality(stabil.data_quality)
    if quality not in {ContextDataQuality.VALID, ContextDataQuality.DATA_LIMITED}:
        return DurabilityAssessment(
            state=DurabilityState.UNKNOWN,
            data_quality=quality,
            reasons=(f"STABIL_DATA_{quality.value}",),
            source_refs=refs,
        )

    validity = stabil.validity.strip().upper()
    behavior = stabil.behavior
    interaction = "UNAVAILABLE" if behavior is None else behavior.interaction.strip().upper()
    motion = "UNAVAILABLE" if behavior is None else behavior.motion.strip().upper()
    progression = stabil.progression.strip().upper()
    quality_reason = (
        "STABIL_DATA_LIMITED_BUT_OBSERVED"
        if quality is ContextDataQuality.DATA_LIMITED
        else "STABIL_DATA_VALID"
    )

    if validity == "BELOW_FLOOR" or interaction == "DOWNSIDE_CONTINUATION":
        return DurabilityAssessment(
            DurabilityState.BROKEN,
            quality,
            (quality_reason, "STABIL_FOUNDATION_BROKEN", f"VALIDITY:{validity}", f"INTERACTION:{interaction}"),
            refs,
        )

    if validity == "BREACHED" or interaction in {"BREAKDOWN_ACCEPTED", "RECOVERY_FAILED"}:
        return DurabilityAssessment(
            DurabilityState.FRACTURED,
            quality,
            (quality_reason, "STABIL_FOUNDATION_FRACTURED", f"VALIDITY:{validity}", f"INTERACTION:{interaction}"),
            refs,
        )

    if validity == "NO_SUPPORT" or stabil.support_ref is None:
        return DurabilityAssessment(
            DurabilityState.UNKNOWN,
            quality,
            (quality_reason, "STABIL_SUPPORT_NOT_ESTABLISHED"),
            refs,
        )

    softening = (
        progression == "REBASED_LOWER"
        or motion in {"FALLING", "FLAT_AFTER_FALL"}
        or interaction in {
            "APPROACHING_SUPPORT",
            "TESTING_SUPPORT",
            "BREAKDOWN_ATTEMPT",
            "RECLAIM_ATTEMPT",
            "RANGE_AROUND_SUPPORT",
        }
    )
    if softening:
        return DurabilityAssessment(
            DurabilityState.SOFTENING,
            quality,
            (
                quality_reason,
                "STABIL_FOUNDATION_SOFTENING",
                f"PROGRESSION:{progression}",
                f"MOTION:{motion}",
                f"INTERACTION:{interaction}",
            ),
            refs,
        )

    return DurabilityAssessment(
        DurabilityState.HEALTHY,
        quality,
        (quality_reason, "STABIL_FOUNDATION_HEALTHY", f"VALIDITY:{validity}", f"INTERACTION:{interaction}"),
        refs,
    )


__all__ = ["DurabilityAssessment", "DurabilityState", "assess_durability"]
