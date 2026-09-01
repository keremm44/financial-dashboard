from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from financial_dashboard.context.envelope import FactRef

from .stabil_interpretation import StabilHorizonAssessment, StabilHorizonState
from .structural import DecisionHorizon


class StabilPolicyEffect(StrEnum):
    """Fresh-LONG policy effect of one horizon-specific Stabil evidence row."""

    NEUTRAL = "NEUTRAL"
    SUPPORTIVE = "SUPPORTIVE"
    RISK_CONTEXT = "RISK_CONTEXT"
    WAIT = "WAIT"
    HARD_CONTRADICTION = "HARD_CONTRADICTION"


@dataclass(frozen=True, slots=True)
class StabilPolicyContribution:
    row: StabilHorizonState
    effect: StabilPolicyEffect


@dataclass(frozen=True, slots=True)
class StabilEntryPolicyAssessment:
    """Approved fresh-LONG Stabil policy, separate from Structure and action ownership.

    Multiple native facts may make several matrix rows applicable at the same as_of.
    The result is deterministic: HARD_CONTRADICTION > WAIT > RISK_CONTEXT >
    SUPPORTIVE > NEUTRAL. There is no score or vote. Rows tied at the winning effect
    remain visible together instead of inventing a second within-effect ranking.
    """

    horizon: DecisionHorizon
    effect: StabilPolicyEffect
    winning_rows: tuple[StabilHorizonState, ...]
    contributions: tuple[StabilPolicyContribution, ...]
    reasons: tuple[str, ...]
    source_refs: tuple[FactRef, ...]


_LT_EFFECTS: dict[StabilHorizonState, StabilPolicyEffect] = {
    StabilHorizonState.UNKNOWN: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.NOT_ESTABLISHED: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.ADVANCING_FOUNDATION: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.STABLE_FOUNDATION: StabilPolicyEffect.NEUTRAL,
    StabilHorizonState.LAGGING_FOUNDATION: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.FALLING_FOUNDATION: StabilPolicyEffect.HARD_CONTRADICTION,
    StabilHorizonState.SUPPORT_TESTING: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.HOLDING_ABOVE: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.DISTANT_ABOVE: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.RANGE_AROUND_SUPPORT: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.BREAKDOWN_ATTEMPT: StabilPolicyEffect.WAIT,
    StabilHorizonState.BREAKDOWN_ACCEPTED: StabilPolicyEffect.HARD_CONTRADICTION,
    StabilHorizonState.DOWNSIDE_CONTINUATION: StabilPolicyEffect.HARD_CONTRADICTION,
    StabilHorizonState.RECLAIMING: StabilPolicyEffect.WAIT,
    StabilHorizonState.RECOVERY_CONFIRMED: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.RECOVERY_FAILED: StabilPolicyEffect.HARD_CONTRADICTION,
}

_ST_EFFECTS: dict[StabilHorizonState, StabilPolicyEffect] = {
    StabilHorizonState.UNKNOWN: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.NOT_ESTABLISHED: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.ADVANCING_FOUNDATION: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.STABLE_FOUNDATION: StabilPolicyEffect.NEUTRAL,
    StabilHorizonState.LAGGING_FOUNDATION: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.FALLING_FOUNDATION: StabilPolicyEffect.HARD_CONTRADICTION,
    StabilHorizonState.SUPPORT_TESTING: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.HOLDING_ABOVE: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.DISTANT_ABOVE: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.RANGE_AROUND_SUPPORT: StabilPolicyEffect.NEUTRAL,
    StabilHorizonState.BREAKDOWN_ATTEMPT: StabilPolicyEffect.WAIT,
    StabilHorizonState.BREAKDOWN_ACCEPTED: StabilPolicyEffect.HARD_CONTRADICTION,
    StabilHorizonState.DOWNSIDE_CONTINUATION: StabilPolicyEffect.HARD_CONTRADICTION,
    StabilHorizonState.RECLAIMING: StabilPolicyEffect.RISK_CONTEXT,
    StabilHorizonState.RECOVERY_CONFIRMED: StabilPolicyEffect.SUPPORTIVE,
    StabilHorizonState.RECOVERY_FAILED: StabilPolicyEffect.HARD_CONTRADICTION,
}

_EFFECT_PRIORITY: dict[StabilPolicyEffect, int] = {
    StabilPolicyEffect.NEUTRAL: 0,
    StabilPolicyEffect.SUPPORTIVE: 1,
    StabilPolicyEffect.RISK_CONTEXT: 2,
    StabilPolicyEffect.WAIT: 3,
    StabilPolicyEffect.HARD_CONTRADICTION: 4,
}
_ROW_ORDER = {row: index for index, row in enumerate(StabilHorizonState)}


def policy_effect_for_row(
    horizon: DecisionHorizon,
    row: StabilHorizonState,
) -> StabilPolicyEffect:
    matrix = _LT_EFFECTS if horizon is DecisionHorizon.LONG_TERM else _ST_EFFECTS
    return matrix[row]


def _token(value: str | None) -> str:
    return "UNAVAILABLE" if value is None else str(value).strip().upper()


def _applicable_rows(stabil: StabilHorizonAssessment) -> tuple[StabilHorizonState, ...]:
    # Missing/untrusted support is intentionally risk context only. Do not infer a
    # stronger state from stale-looking descriptive fields when support is absent.
    if stabil.state in {StabilHorizonState.UNKNOWN, StabilHorizonState.NOT_ESTABLISHED}:
        return (stabil.state,)

    rows: set[StabilHorizonState] = {stabil.state}
    validity = _token(stabil.validity)
    motion = _token(stabil.motion)
    progression = _token(stabil.progression)
    relation = _token(stabil.relation)
    interaction = _token(stabil.interaction)

    # Native validity subsumes softer descriptive rows. BELOW_FLOOR is always in the
    # hard downside family. BREACHED is at least a fresh breakdown attempt; a heavier
    # interaction below can still win through the common precedence rule.
    if validity == "BELOW_FLOOR":
        rows.add(StabilHorizonState.DOWNSIDE_CONTINUATION)
    elif validity == "BREACHED":
        rows.add(StabilHorizonState.BREAKDOWN_ATTEMPT)

    interaction_rows = {
        "DOWNSIDE_CONTINUATION": StabilHorizonState.DOWNSIDE_CONTINUATION,
        "RECOVERY_FAILED": StabilHorizonState.RECOVERY_FAILED,
        "BREAKDOWN_ACCEPTED": StabilHorizonState.BREAKDOWN_ACCEPTED,
        "BREAKDOWN_ATTEMPT": StabilHorizonState.BREAKDOWN_ATTEMPT,
        "RECLAIM_ATTEMPT": StabilHorizonState.RECLAIMING,
        "RECOVERY_CONFIRMED": StabilHorizonState.RECOVERY_CONFIRMED,
        "RANGE_AROUND_SUPPORT": StabilHorizonState.RANGE_AROUND_SUPPORT,
    }
    if interaction in interaction_rows:
        rows.add(interaction_rows[interaction])
    if interaction in {"TESTING_SUPPORT", "APPROACHING_SUPPORT"} or relation == "AT_SUPPORT":
        rows.add(StabilHorizonState.SUPPORT_TESTING)

    # Foundation and distance facts remain independently visible so co-occurrence is
    # not lost behind the descriptive interpretation's single headline state.
    if motion == "FALLING" or progression == "REBASED_LOWER":
        rows.add(StabilHorizonState.FALLING_FOUNDATION)
    if relation == "ABOVE_FAR":
        rows.add(StabilHorizonState.DISTANT_ABOVE)
        if motion in {"FLAT", "FLAT_AFTER_RISE", "FLAT_AFTER_FALL"}:
            rows.add(StabilHorizonState.LAGGING_FOUNDATION)
    if motion == "RISING" or progression == "REBASED_HIGHER" or interaction == "SUPPORTED_ADVANCE":
        rows.add(StabilHorizonState.ADVANCING_FOUNDATION)
    if interaction == "HOLDING_ABOVE" and relation in {"ABOVE_NEAR", "AT_SUPPORT"}:
        rows.add(StabilHorizonState.HOLDING_ABOVE)

    return tuple(sorted(rows, key=_ROW_ORDER.__getitem__))


def assess_stabil_entry_policy(stabil: StabilHorizonAssessment) -> StabilEntryPolicyAssessment:
    """Apply the reviewed Stabil matrix to one causal horizon interpretation.

    This assessment is specifically about *fresh LONG entry*. It never changes Market
    Structure direction, never emits BUY/SELL, and has no position-exit authority.
    Eligibility decides whether its WAIT/hard-contradiction effect is relevant to the
    structural side currently being considered.
    """

    rows = _applicable_rows(stabil)
    contributions = tuple(
        StabilPolicyContribution(row=row, effect=policy_effect_for_row(stabil.horizon, row))
        for row in rows
    )
    winning_effect = max(
        (item.effect for item in contributions),
        key=_EFFECT_PRIORITY.__getitem__,
    )
    winning_rows = tuple(
        item.row for item in contributions if item.effect is winning_effect
    )
    return StabilEntryPolicyAssessment(
        horizon=stabil.horizon,
        effect=winning_effect,
        winning_rows=winning_rows,
        contributions=contributions,
        reasons=tuple(
            f"{item.row.value}:{item.effect.value}" for item in contributions
        ),
        source_refs=stabil.source_refs,
    )


__all__ = [
    "StabilEntryPolicyAssessment",
    "StabilPolicyContribution",
    "StabilPolicyEffect",
    "assess_stabil_entry_policy",
    "policy_effect_for_row",
]
