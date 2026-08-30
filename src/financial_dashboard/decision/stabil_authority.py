from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from financial_dashboard.context.envelope import (
    ContextDataQuality,
    FactRef,
    normalize_context_data_quality,
)
from financial_dashboard.context.projections import StabilSupportProjection


class StabilDecisionState(StrEnum):
    UNKNOWN = "UNKNOWN"
    BULLISH_PROGRESS = "BULLISH_PROGRESS"
    BULLISH_SUPPORTED = "BULLISH_SUPPORTED"
    BULLISH_SOFTENING = "BULLISH_SOFTENING"
    BALANCE = "BALANCE"
    RECOVERY_DEVELOPING = "RECOVERY_DEVELOPING"
    RECOVERY_CONFIRMED = "RECOVERY_CONFIRMED"
    BREAKDOWN_DEVELOPING = "BREAKDOWN_DEVELOPING"
    BREAKDOWN_CONFIRMED = "BREAKDOWN_CONFIRMED"
    BEARISH_CONTINUATION = "BEARISH_CONTINUATION"


@dataclass(frozen=True, slots=True)
class StabilDecisionAssessment:
    state: StabilDecisionState
    data_quality: ContextDataQuality
    reasons: tuple[str, ...]
    source_refs: tuple[FactRef, ...]

    @property
    def recovery_confirmed(self) -> bool:
        return self.state is StabilDecisionState.RECOVERY_CONFIRMED

    @property
    def recovery_developing(self) -> bool:
        return self.state is StabilDecisionState.RECOVERY_DEVELOPING

    @property
    def breakdown_developing(self) -> bool:
        return self.state is StabilDecisionState.BREAKDOWN_DEVELOPING

    @property
    def breakdown_confirmed(self) -> bool:
        return self.state in {
            StabilDecisionState.BREAKDOWN_CONFIRMED,
            StabilDecisionState.BEARISH_CONTINUATION,
        }

    @property
    def opposes_early_long(self) -> bool:
        return self.state in {
            StabilDecisionState.BREAKDOWN_DEVELOPING,
            StabilDecisionState.BREAKDOWN_CONFIRMED,
            StabilDecisionState.BEARISH_CONTINUATION,
        }

    @property
    def usable(self) -> bool:
        return self.data_quality in {
            ContextDataQuality.VALID,
            ContextDataQuality.DATA_LIMITED,
        } and self.state is not StabilDecisionState.UNKNOWN


def _refs(stabil: StabilSupportProjection | None) -> tuple[FactRef, ...]:
    if stabil is None:
        return ()
    values: dict[tuple[object, ...], FactRef] = {}
    if stabil.support_ref is not None:
        values[stabil.support_ref.deterministic_key] = stabil.support_ref
    for event in stabil.events:
        values[event.ref.deterministic_key] = event.ref
    return tuple(sorted(values.values(), key=lambda ref: ref.deterministic_key))


def assess_stabil_authority(
    stabil: StabilSupportProjection | None,
) -> StabilDecisionAssessment:
    """Translate native daily Stabil facts into Decision market-state authority.

    This is deliberately not a BUY/SELL signal. It consumes only the already-causal
    Stabil projection and keeps the source domain immutable. DATA_LIMITED is usable
    when native lifecycle/behavior state is actually observed; the lower quality is
    preserved on the assessment and never promoted to VALID. WARMING_UP/UNAVAILABLE
    remain non-authoritative.
    """

    if stabil is None:
        return StabilDecisionAssessment(
            StabilDecisionState.UNKNOWN,
            ContextDataQuality.UNAVAILABLE,
            ("STABIL_AUTHORITY_UNAVAILABLE",),
            (),
        )

    refs = _refs(stabil)
    quality = normalize_context_data_quality(stabil.data_quality)
    if quality not in {ContextDataQuality.VALID, ContextDataQuality.DATA_LIMITED}:
        return StabilDecisionAssessment(
            StabilDecisionState.UNKNOWN,
            quality,
            (f"STABIL_AUTHORITY_DATA_{quality.value}",),
            refs,
        )

    behavior = stabil.behavior
    interaction = "UNAVAILABLE" if behavior is None else str(behavior.interaction).strip().upper()
    motion = "UNAVAILABLE" if behavior is None else str(behavior.motion).strip().upper()
    relation = "UNAVAILABLE" if behavior is None else str(behavior.relation).strip().upper()
    validity = str(stabil.validity).strip().upper()

    if interaction == "RECOVERY_CONFIRMED":
        state = StabilDecisionState.RECOVERY_CONFIRMED
    elif interaction == "RECLAIM_ATTEMPT":
        state = StabilDecisionState.RECOVERY_DEVELOPING
    elif interaction == "DOWNSIDE_CONTINUATION":
        state = StabilDecisionState.BEARISH_CONTINUATION
    elif interaction == "BREAKDOWN_ACCEPTED":
        state = StabilDecisionState.BREAKDOWN_CONFIRMED
    elif interaction in {"BREAKDOWN_ATTEMPT", "RECOVERY_FAILED"}:
        state = StabilDecisionState.BREAKDOWN_DEVELOPING
    elif interaction == "RANGE_AROUND_SUPPORT":
        state = StabilDecisionState.BALANCE
    elif interaction in {"APPROACHING_SUPPORT", "TESTING_SUPPORT"}:
        state = StabilDecisionState.BULLISH_SOFTENING
    elif interaction == "SUPPORTED_ADVANCE":
        state = StabilDecisionState.BULLISH_PROGRESS
    elif interaction == "HOLDING_ABOVE":
        if motion in {"RISING", "FLAT_AFTER_RISE"}:
            state = StabilDecisionState.BULLISH_SUPPORTED
        elif motion in {"FALLING", "FLAT_AFTER_FALL"}:
            state = StabilDecisionState.BULLISH_SOFTENING
        else:
            state = StabilDecisionState.BALANCE
    elif validity == "BELOW_FLOOR":
        state = StabilDecisionState.BEARISH_CONTINUATION
    elif validity == "BREACHED":
        state = StabilDecisionState.BREAKDOWN_CONFIRMED
    else:
        state = StabilDecisionState.UNKNOWN

    quality_reason = (
        "STABIL_AUTHORITY_DATA_LIMITED_BUT_OBSERVED"
        if quality is ContextDataQuality.DATA_LIMITED
        else "STABIL_AUTHORITY_DATA_VALID"
    )
    return StabilDecisionAssessment(
        state,
        quality,
        (
            quality_reason,
            f"STABIL_PRIMARY_STATE:{state.value}",
            f"STABIL_INTERACTION:{interaction}",
            f"STABIL_MOTION:{motion}",
            f"STABIL_RELATION:{relation}",
        ),
        refs,
    )


__all__ = [
    "StabilDecisionAssessment",
    "StabilDecisionState",
    "assess_stabil_authority",
]
