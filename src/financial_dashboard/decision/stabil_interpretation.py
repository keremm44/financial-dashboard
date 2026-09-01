from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from financial_dashboard.context.envelope import ContextDataQuality, FactRef

from .market_state import StabilMarketState
from .structural import DecisionHorizon


class StabilHorizonState(StrEnum):
    """Horizon-specific meaning built only from native Stabil facts.

    These states are descriptive Decision evidence. They do not own BUY/SELL,
    Eligibility, Structure direction, or thesis lifecycle.
    """

    UNKNOWN = "UNKNOWN"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    ADVANCING_FOUNDATION = "ADVANCING_FOUNDATION"
    STABLE_FOUNDATION = "STABLE_FOUNDATION"
    LAGGING_FOUNDATION = "LAGGING_FOUNDATION"
    FALLING_FOUNDATION = "FALLING_FOUNDATION"
    SUPPORT_TESTING = "SUPPORT_TESTING"
    HOLDING_ABOVE = "HOLDING_ABOVE"
    DISTANT_ABOVE = "DISTANT_ABOVE"
    RANGE_AROUND_SUPPORT = "RANGE_AROUND_SUPPORT"
    BREAKDOWN_ATTEMPT = "BREAKDOWN_ATTEMPT"
    BREAKDOWN_ACCEPTED = "BREAKDOWN_ACCEPTED"
    DOWNSIDE_CONTINUATION = "DOWNSIDE_CONTINUATION"
    RECLAIMING = "RECLAIMING"
    RECOVERY_CONFIRMED = "RECOVERY_CONFIRMED"
    RECOVERY_FAILED = "RECOVERY_FAILED"


@dataclass(frozen=True, slots=True)
class StabilHorizonAssessment:
    """Read-only LT/ST interpretation of one frozen factual Stabil state."""

    horizon: DecisionHorizon
    state: StabilHorizonState
    data_quality: ContextDataQuality
    validity: str | None
    progression: str | None
    motion: str | None
    relation: str | None
    interaction: str | None
    reasons: tuple[str, ...]
    source_refs: tuple[FactRef, ...]


def _token(value: str | None) -> str:
    return "UNAVAILABLE" if value is None else str(value).strip().upper()


def _refs(stabil: StabilMarketState | None) -> tuple[FactRef, ...]:
    if stabil is None:
        return ()
    refs: list[FactRef] = []
    if stabil.support_ref is not None:
        refs.append(stabil.support_ref)
    refs.extend(event.ref for event in stabil.events)
    by_key = {ref.deterministic_key: ref for ref in refs}
    return tuple(by_key[key] for key in sorted(by_key))


def _assessment(
    stabil: StabilMarketState | None,
    horizon: DecisionHorizon,
    state: StabilHorizonState,
    *reasons: str,
) -> StabilHorizonAssessment:
    quality = ContextDataQuality.UNAVAILABLE if stabil is None else stabil.data_quality
    return StabilHorizonAssessment(
        horizon=horizon,
        state=state,
        data_quality=quality,
        validity=None if stabil is None else stabil.validity,
        progression=None if stabil is None else stabil.progression,
        motion=None if stabil is None else stabil.motion,
        relation=None if stabil is None else stabil.relation,
        interaction=None if stabil is None else stabil.interaction,
        reasons=tuple(reasons),
        source_refs=_refs(stabil),
    )


def _common_terminal_state(
    stabil: StabilMarketState,
    horizon: DecisionHorizon,
) -> StabilHorizonAssessment | None:
    validity = _token(stabil.validity)
    interaction = _token(stabil.interaction)

    if stabil.data_quality is not ContextDataQuality.VALID:
        return _assessment(
            stabil,
            horizon,
            StabilHorizonState.UNKNOWN,
            f"STABIL_DATA:{stabil.data_quality.value}",
        )
    if validity == "NO_SUPPORT" or stabil.support_ref is None:
        return _assessment(
            stabil,
            horizon,
            StabilHorizonState.NOT_ESTABLISHED,
            "STABIL_SUPPORT_NOT_ESTABLISHED",
        )
    if validity == "BELOW_FLOOR" or interaction == "DOWNSIDE_CONTINUATION":
        return _assessment(
            stabil,
            horizon,
            StabilHorizonState.DOWNSIDE_CONTINUATION,
            f"VALIDITY:{validity}",
            f"INTERACTION:{interaction}",
        )
    if interaction == "RECOVERY_FAILED":
        return _assessment(stabil, horizon, StabilHorizonState.RECOVERY_FAILED, "RECOVERY_FAILED")
    if interaction == "BREAKDOWN_ACCEPTED":
        return _assessment(stabil, horizon, StabilHorizonState.BREAKDOWN_ACCEPTED, "BREAKDOWN_ACCEPTED")
    if interaction == "BREAKDOWN_ATTEMPT" or validity == "BREACHED":
        return _assessment(stabil, horizon, StabilHorizonState.BREAKDOWN_ATTEMPT, "BREAKDOWN_ATTEMPT")
    if interaction == "RECLAIM_ATTEMPT":
        return _assessment(stabil, horizon, StabilHorizonState.RECLAIMING, "RECLAIM_ATTEMPT")
    if interaction == "RECOVERY_CONFIRMED":
        return _assessment(stabil, horizon, StabilHorizonState.RECOVERY_CONFIRMED, "RECOVERY_CONFIRMED")
    if interaction == "RANGE_AROUND_SUPPORT":
        return _assessment(stabil, horizon, StabilHorizonState.RANGE_AROUND_SUPPORT, "RANGE_AROUND_SUPPORT")
    return None


def _long_term_interpretation(stabil: StabilMarketState) -> StabilHorizonAssessment:
    horizon = DecisionHorizon.LONG_TERM
    terminal = _common_terminal_state(stabil, horizon)
    if terminal is not None:
        return terminal

    progression = _token(stabil.progression)
    motion = _token(stabil.motion)
    relation = _token(stabil.relation)
    interaction = _token(stabil.interaction)

    if motion == "FALLING" or progression == "REBASED_LOWER":
        return _assessment(
            stabil,
            horizon,
            StabilHorizonState.FALLING_FOUNDATION,
            f"MOTION:{motion}",
            f"PROGRESSION:{progression}",
        )
    if interaction in {"TESTING_SUPPORT", "APPROACHING_SUPPORT"}:
        return _assessment(
            stabil,
            horizon,
            StabilHorizonState.SUPPORT_TESTING,
            f"INTERACTION:{interaction}",
        )
    if relation == "ABOVE_FAR" and motion in {"FLAT", "FLAT_AFTER_RISE", "FLAT_AFTER_FALL"}:
        return _assessment(
            stabil,
            horizon,
            StabilHorizonState.LAGGING_FOUNDATION,
            f"RELATION:{relation}",
            f"MOTION:{motion}",
        )
    if motion == "RISING" or progression == "REBASED_HIGHER" or interaction == "SUPPORTED_ADVANCE":
        return _assessment(
            stabil,
            horizon,
            StabilHorizonState.ADVANCING_FOUNDATION,
            f"MOTION:{motion}",
            f"PROGRESSION:{progression}",
            f"INTERACTION:{interaction}",
        )
    return _assessment(
        stabil,
        horizon,
        StabilHorizonState.STABLE_FOUNDATION,
        f"MOTION:{motion}",
        f"RELATION:{relation}",
        f"INTERACTION:{interaction}",
    )


def _short_term_interpretation(stabil: StabilMarketState) -> StabilHorizonAssessment:
    horizon = DecisionHorizon.SHORT_TERM
    terminal = _common_terminal_state(stabil, horizon)
    if terminal is not None:
        return terminal

    progression = _token(stabil.progression)
    motion = _token(stabil.motion)
    relation = _token(stabil.relation)
    interaction = _token(stabil.interaction)

    if interaction in {"TESTING_SUPPORT", "APPROACHING_SUPPORT"} or relation == "AT_SUPPORT":
        return _assessment(
            stabil,
            horizon,
            StabilHorizonState.SUPPORT_TESTING,
            f"INTERACTION:{interaction}",
            f"RELATION:{relation}",
        )
    if motion == "FALLING" or progression == "REBASED_LOWER":
        return _assessment(
            stabil,
            horizon,
            StabilHorizonState.FALLING_FOUNDATION,
            f"MOTION:{motion}",
            f"PROGRESSION:{progression}",
        )
    if relation == "ABOVE_FAR":
        return _assessment(
            stabil,
            horizon,
            StabilHorizonState.DISTANT_ABOVE,
            "ST_PRICE_DISTANT_FROM_DAILY_SUPPORT",
        )
    return _assessment(
        stabil,
        horizon,
        StabilHorizonState.HOLDING_ABOVE,
        f"MOTION:{motion}",
        f"RELATION:{relation}",
        f"INTERACTION:{interaction}",
    )


def assess_stabil_horizon(
    stabil: StabilMarketState | None,
    horizon: DecisionHorizon,
) -> StabilHorizonAssessment:
    """Interpret the same causal Stabil facts differently for LT and ST.

    No numeric threshold is introduced here. Any near/far, persistence or range
    boundary was already established by the native Stabil behavior layer.
    """

    if stabil is None:
        return _assessment(stabil, horizon, StabilHorizonState.UNKNOWN, "STABIL_UNAVAILABLE")
    if horizon is DecisionHorizon.LONG_TERM:
        return _long_term_interpretation(stabil)
    return _short_term_interpretation(stabil)


__all__ = [
    "StabilHorizonAssessment",
    "StabilHorizonState",
    "assess_stabil_horizon",
]
