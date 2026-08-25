from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from financial_dashboard.context.envelope import ContextDataQuality, FactRef
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

    This function intentionally cannot modify LT/ST Structure. It adds no distance,
    persistence, ATR or score threshold; all category changes come from existing
    Stabil lifecycle/behavior states.
    """

    if stabil is None:
        return DurabilityAssessment(
            state=DurabilityState.UNKNOWN,
            data_quality=ContextDataQuality.UNAVAILABLE,
            reasons=("STABIL_UNAVAILABLE",),
            source_refs=(),
        )

    refs = _refs(stabil)
    if stabil.data_quality is not ContextDataQuality.VALID:
        return DurabilityAssessment(
            state=DurabilityState.UNKNOWN,
            data_quality=stabil.data_quality,
            reasons=(f"STABIL_DATA_{stabil.data_quality.value}",),
            source_refs=refs,
        )

    validity = stabil.validity.strip().upper()
    behavior = stabil.behavior
    interaction = "UNAVAILABLE" if behavior is None else behavior.interaction.strip().upper()
    motion = "UNAVAILABLE" if behavior is None else behavior.motion.strip().upper()
    progression = stabil.progression.strip().upper()

    if validity == "BELOW_FLOOR" or interaction == "DOWNSIDE_CONTINUATION":
        return DurabilityAssessment(
            DurabilityState.BROKEN,
            stabil.data_quality,
            ("STABIL_FOUNDATION_BROKEN", f"VALIDITY:{validity}", f"INTERACTION:{interaction}"),
            refs,
        )

    if validity == "BREACHED" or interaction in {"BREAKDOWN_ACCEPTED", "RECOVERY_FAILED"}:
        return DurabilityAssessment(
            DurabilityState.FRACTURED,
            stabil.data_quality,
            ("STABIL_FOUNDATION_FRACTURED", f"VALIDITY:{validity}", f"INTERACTION:{interaction}"),
            refs,
        )

    if validity == "NO_SUPPORT" or stabil.support_ref is None:
        return DurabilityAssessment(
            DurabilityState.UNKNOWN,
            stabil.data_quality,
            ("STABIL_SUPPORT_NOT_ESTABLISHED",),
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
        or stabil.reclaim_count > 0
    )
    if softening:
        return DurabilityAssessment(
            DurabilityState.SOFTENING,
            stabil.data_quality,
            (
                "STABIL_FOUNDATION_SOFTENING",
                f"PROGRESSION:{progression}",
                f"MOTION:{motion}",
                f"INTERACTION:{interaction}",
            ),
            refs,
        )

    return DurabilityAssessment(
        DurabilityState.HEALTHY,
        stabil.data_quality,
        ("STABIL_FOUNDATION_HEALTHY", f"VALIDITY:{validity}", f"INTERACTION:{interaction}"),
        refs,
    )


__all__ = ["DurabilityAssessment", "DurabilityState", "assess_durability"]
