from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.fvg_engulfing_projection import FvgEngulfingLifecycleProjection
from financial_dashboard.context.order_block_behavior_projection import OrderBlockBehaviorProjection

from .structural import StructuralDirection


class ReactionState(StrEnum):
    CONFIRMED = "CONFIRMED"
    DEVELOPING = "DEVELOPING"
    ABSENT = "ABSENT"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ReactionAssessment:
    state: ReactionState
    failure_present: bool
    confirmation_present: bool
    developing_present: bool
    data_quality: ContextDataQuality
    reasons: tuple[str, ...]
    source_refs: tuple[FactRef, ...]


def _direction_value(side: StructuralDirection) -> int:
    if side is StructuralDirection.LONG:
        return 1
    if side is StructuralDirection.SHORT:
        return -1
    return 0


def _unique_refs(refs: list[FactRef]) -> tuple[FactRef, ...]:
    by_key = {ref.deterministic_key: ref for ref in refs}
    return tuple(sorted(by_key.values(), key=lambda ref: ref.deterministic_key))


def assess_reaction(
    side: StructuralDirection,
    *,
    order_blocks: OrderBlockBehaviorProjection | None = None,
    fvg_engulfing: FvgEngulfingLifecycleProjection | None = None,
    timeframes: tuple[str, ...] = ("1d", "4h", "2h", "1h", "30m"),
) -> ReactionAssessment:
    """Assess reaction lifecycle relative to an already-established Structure side.

    OB/FVG can describe whether a reaction is developing, confirmed or failed.
    Engulfing may confirm an existing reaction path but cannot create one when no
    reaction-zone interaction exists. No supporting fact can change ``side``.
    """

    direction = _direction_value(side)
    if direction == 0:
        return ReactionAssessment(
            ReactionState.UNKNOWN,
            False,
            False,
            False,
            ContextDataQuality.UNAVAILABLE,
            ("REACTION_SIDE_UNRESOLVED",),
            (),
        )

    allowed_tfs = {tf.strip().lower() for tf in timeframes}
    refs: list[FactRef] = []
    confirmed = False
    developing = False
    failed = False
    usable_zone_seen = False
    unavailable_seen = False
    reasons: list[str] = []

    if order_blocks is not None:
        for item in order_blocks.observations:
            if item.timeframe not in allowed_tfs or (1 if item.bullish else -1) != direction:
                continue
            refs.append(item.ref)
            if item.ref.data_quality is not ContextDataQuality.VALID:
                unavailable_seen = True
                continue
            usable_zone_seen = True
            state = item.state.strip().upper()
            interaction = item.interaction.strip().upper()
            if interaction == "REACTION_CONFIRMED" or state == "REACTION_CONFIRMED":
                confirmed = True
                reasons.append(f"OB_CONFIRMED:{item.timeframe}:{item.identity}")
            elif interaction == "FAILED" or state in {"CONSUMED", "EXPIRED_CANDIDATE"}:
                failed = True
                reasons.append(f"OB_FAILED:{item.timeframe}:{item.identity}")
            elif item.active and interaction in {
                "APPROACHING",
                "ENTERED",
                "DWELLING_INSIDE",
                "EXITING_FAVORABLE",
                "HOLDING_FAVORABLE",
            }:
                developing = True
                reasons.append(f"OB_DEVELOPING:{item.timeframe}:{item.identity}:{interaction}")

    if fvg_engulfing is not None:
        for item in fvg_engulfing.fvg:
            if item.ref.timeframe not in allowed_tfs or int(item.direction) != direction:
                continue
            refs.append(item.ref)
            if item.ref.data_quality is not ContextDataQuality.VALID:
                unavailable_seen = True
                continue
            usable_zone_seen = True
            if item.failed_reaction or item.invalid or item.full_fill:
                failed = True
                reasons.append(f"FVG_FAILED:{item.ref.timeframe}:{item.identity}")
            elif item.reaction_confirmed:
                confirmed = True
                reasons.append(f"FVG_CONFIRMED:{item.ref.timeframe}:{item.identity}")
            elif item.first_test_index is not None:
                developing = True
                reasons.append(f"FVG_DEVELOPING:{item.ref.timeframe}:{item.identity}")

        # Engulfing is confirmation-only. It can strengthen an already-existing
        # reaction path but may not manufacture reaction by itself.
        if usable_zone_seen:
            for item in fvg_engulfing.engulfing:
                if item.ref.timeframe not in allowed_tfs or int(item.direction) != direction:
                    continue
                refs.append(item.ref)
                if item.ref.data_quality is not ContextDataQuality.VALID:
                    unavailable_seen = True
                    continue
                if item.invalid:
                    failed = True
                    reasons.append(f"ENGULFING_FAILED:{item.ref.timeframe}:{item.identity}")
                elif item.continuation_confirmed:
                    confirmed = True
                    reasons.append(f"ENGULFING_CONFIRMED:{item.ref.timeframe}:{item.identity}")
                elif item.first_test_index is not None and not item.weakened:
                    developing = True
                    reasons.append(f"ENGULFING_DEVELOPING:{item.ref.timeframe}:{item.identity}")

    source_refs = _unique_refs(refs)
    quality = (
        ContextDataQuality.VALID
        if usable_zone_seen
        else ContextDataQuality.UNAVAILABLE
        if unavailable_seen or not source_refs
        else ContextDataQuality.UNAVAILABLE
    )

    if confirmed:
        state = ReactionState.CONFIRMED
    elif developing:
        state = ReactionState.DEVELOPING
    elif failed:
        state = ReactionState.FAILED
    elif usable_zone_seen:
        state = ReactionState.ABSENT
    else:
        state = ReactionState.UNKNOWN

    if not reasons:
        reasons.append(
            "REACTION_ZONE_PRESENT_NO_INTERACTION"
            if state is ReactionState.ABSENT
            else "REACTION_EVIDENCE_UNAVAILABLE"
        )
    if confirmed and failed:
        reasons.append("REACTION_MIXED_CONFIRMED_AND_FAILED_LINEAGES")

    return ReactionAssessment(
        state=state,
        failure_present=failed,
        confirmation_present=confirmed,
        developing_present=developing,
        data_quality=quality,
        reasons=tuple(reasons),
        source_refs=source_refs,
    )


__all__ = ["ReactionAssessment", "ReactionState", "assess_reaction"]
