from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .stabil_support_lifecycle import DailySupportObservation, StabilSupportLifecycleSnapshot, SupportProgression


class SupportMotion(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    RISING = "RISING"
    FALLING = "FALLING"
    FLAT = "FLAT"
    FLAT_AFTER_RISE = "FLAT_AFTER_RISE"
    FLAT_AFTER_FALL = "FLAT_AFTER_FALL"


class PriceSupportRelation(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    AT_SUPPORT = "AT_SUPPORT"
    ABOVE_NEAR = "ABOVE_NEAR"
    ABOVE_FAR = "ABOVE_FAR"
    BELOW_NEAR = "BELOW_NEAR"
    BELOW_FAR = "BELOW_FAR"


class SupportInteractionState(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    HOLDING_ABOVE = "HOLDING_ABOVE"
    SUPPORTED_ADVANCE = "SUPPORTED_ADVANCE"
    APPROACHING_SUPPORT = "APPROACHING_SUPPORT"
    TESTING_SUPPORT = "TESTING_SUPPORT"
    BREAKDOWN_ATTEMPT = "BREAKDOWN_ATTEMPT"
    BREAKDOWN_ACCEPTED = "BREAKDOWN_ACCEPTED"
    DOWNSIDE_CONTINUATION = "DOWNSIDE_CONTINUATION"
    RECLAIM_ATTEMPT = "RECLAIM_ATTEMPT"
    RECOVERY_CONFIRMED = "RECOVERY_CONFIRMED"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    RANGE_AROUND_SUPPORT = "RANGE_AROUND_SUPPORT"


@dataclass(frozen=True, slots=True)
class StabilSupportBehaviorConfig:
    near_atr: float = 0.75
    persistence_bars: int = 2
    flatten_after_bars: int = 4
    range_bars: int = 8
    range_min_crosses: int = 2

    def __post_init__(self) -> None:
        if self.near_atr <= 0:
            raise ValueError("near_atr must be positive")
        if self.persistence_bars < 1:
            raise ValueError("persistence_bars must be >= 1")
        if self.flatten_after_bars < 1:
            raise ValueError("flatten_after_bars must be >= 1")
        if self.range_bars < 1:
            raise ValueError("range_bars must be >= 1")
        if self.range_min_crosses < 1:
            raise ValueError("range_min_crosses must be >= 1")


@dataclass(frozen=True, slots=True)
class StabilSupportBehaviorSnapshot:
    motion: SupportMotion = SupportMotion.UNAVAILABLE
    relation: PriceSupportRelation = PriceSupportRelation.UNAVAILABLE
    interaction: SupportInteractionState = SupportInteractionState.UNAVAILABLE
    bars_since_rebase: int | None = None
    cross_count: int = 0
    last_rebase_step_atr: float | None = None
    reclaim_active: bool = False


def _progression(previous: float | None, current: float | None, min_tick: float) -> SupportProgression:
    if current is None:
        return SupportProgression.NONE
    if previous is None:
        return SupportProgression.INITIAL
    delta = float(current) - float(previous)
    if delta > min_tick:
        return SupportProgression.REBASED_HIGHER
    if delta < -min_tick:
        return SupportProgression.REBASED_LOWER
    return SupportProgression.SAME


def _motion(
    progression: SupportProgression,
    *,
    bars_since_rebase: int,
    flatten_after_bars: int,
) -> SupportMotion:
    if progression is SupportProgression.REBASED_HIGHER:
        return (
            SupportMotion.RISING
            if bars_since_rebase < flatten_after_bars
            else SupportMotion.FLAT_AFTER_RISE
        )
    if progression is SupportProgression.REBASED_LOWER:
        return (
            SupportMotion.FALLING
            if bars_since_rebase < flatten_after_bars
            else SupportMotion.FLAT_AFTER_FALL
        )
    if progression in {SupportProgression.INITIAL, SupportProgression.SAME}:
        return SupportMotion.FLAT
    return SupportMotion.UNAVAILABLE


def _relation(
    obs: DailySupportObservation,
    *,
    support: float,
    near_atr: float,
) -> PriceSupportRelation:
    close = float(obs.close)
    touch = float(obs.low) <= support <= float(obs.high)
    if close >= support and touch:
        return PriceSupportRelation.AT_SUPPORT
    if obs.atr is None or abs(float(obs.atr)) <= 1e-12:
        return PriceSupportRelation.UNAVAILABLE
    distance_atr = (close - support) / float(obs.atr)
    if close >= support:
        return (
            PriceSupportRelation.ABOVE_NEAR
            if distance_atr <= near_atr
            else PriceSupportRelation.ABOVE_FAR
        )
    return (
        PriceSupportRelation.BELOW_NEAR
        if abs(distance_atr) <= near_atr
        else PriceSupportRelation.BELOW_FAR
    )


def build_support_behavior(
    observations: Iterable[DailySupportObservation],
    lifecycle: StabilSupportLifecycleSnapshot,
    *,
    config: StabilSupportBehaviorConfig | None = None,
    min_tick: float = 0.01,
) -> StabilSupportBehaviorSnapshot:
    """Describe price/support behaviour without creating trading authority.

    The lifecycle owns factual breach/reclaim events. This layer adds the orthogonal
    semantics needed by later decision code: support-step motion, price proximity,
    persistence and whether a reclaim is merely attempted or structurally accepted.
    Only the supplied causal observation prefix is inspected.
    """

    cfg = config or StabilSupportBehaviorConfig()
    items = tuple(observations)
    if not items or lifecycle.support_level is None:
        return StabilSupportBehaviorSnapshot()

    current_identity: tuple[object, object, float] | None = None
    current_support: float | None = None
    current_rebase_index: int | None = None
    last_progression = SupportProgression.NONE
    last_rebase_step_atr: float | None = None

    previous_below: bool | None = None
    previous_distance_price: float | None = None
    bars_above = 0
    bars_below = 0
    cross_count = 0
    reclaim_active = False
    reclaim_failed = False
    relation = PriceSupportRelation.UNAVAILABLE
    distance_delta_atr: float | None = None

    for index, obs in enumerate(items):
        identity = obs.support_identity
        if identity is None or obs.support_level is None:
            current_identity = None
            current_support = None
            current_rebase_index = None
            last_progression = SupportProgression.NONE
            last_rebase_step_atr = None
            previous_below = None
            previous_distance_price = None
            bars_above = 0
            bars_below = 0
            cross_count = 0
            reclaim_active = False
            reclaim_failed = False
            relation = PriceSupportRelation.UNAVAILABLE
            distance_delta_atr = None
            continue

        rebased = identity != current_identity
        if rebased:
            old_support = current_support
            last_progression = _progression(old_support, obs.support_level, min_tick)
            if old_support is not None and obs.atr is not None and abs(float(obs.atr)) > 1e-12:
                last_rebase_step_atr = (float(obs.support_level) - float(old_support)) / float(obs.atr)
            else:
                last_rebase_step_atr = None
            current_identity = identity
            current_support = float(obs.support_level)
            current_rebase_index = index
            previous_below = None
            previous_distance_price = None
            bars_above = 0
            bars_below = 0
            cross_count = 0
            reclaim_active = False
            reclaim_failed = False

        assert current_support is not None
        close = float(obs.close)
        below = close < current_support
        relation = _relation(obs, support=current_support, near_atr=cfg.near_atr)

        if previous_below is not None and below != previous_below:
            cross_count += 1

        if below:
            bars_below += 1
            bars_above = 0
            if previous_below is False and reclaim_active:
                reclaim_failed = True
            reclaim_active = False
        else:
            bars_above += 1
            bars_below = 0
            if previous_below is True:
                reclaim_active = True
                reclaim_failed = False

        current_distance_price = close - current_support
        if (
            previous_distance_price is not None
            and obs.atr is not None
            and abs(float(obs.atr)) > 1e-12
        ):
            distance_delta_atr = (current_distance_price - previous_distance_price) / float(obs.atr)
        else:
            distance_delta_atr = None
        previous_distance_price = current_distance_price
        previous_below = below

    if current_support is None or current_rebase_index is None:
        return StabilSupportBehaviorSnapshot()

    bars_since_rebase = len(items) - 1 - current_rebase_index
    motion = _motion(
        last_progression,
        bars_since_rebase=bars_since_rebase,
        flatten_after_bars=cfg.flatten_after_bars,
    )

    flat_motion = motion in {
        SupportMotion.FLAT,
        SupportMotion.FLAT_AFTER_RISE,
        SupportMotion.FLAT_AFTER_FALL,
    }
    near_relation = relation in {
        PriceSupportRelation.AT_SUPPORT,
        PriceSupportRelation.ABOVE_NEAR,
        PriceSupportRelation.BELOW_NEAR,
    }

    if (
        flat_motion
        and bars_since_rebase >= cfg.range_bars
        and cross_count >= cfg.range_min_crosses
        and near_relation
    ):
        interaction = SupportInteractionState.RANGE_AROUND_SUPPORT
    elif lifecycle.close_below_support:
        if reclaim_failed and bars_below < cfg.persistence_bars:
            interaction = SupportInteractionState.RECOVERY_FAILED
        elif bars_below >= cfg.persistence_bars:
            if motion is SupportMotion.FALLING and relation is PriceSupportRelation.BELOW_FAR:
                interaction = SupportInteractionState.DOWNSIDE_CONTINUATION
            else:
                interaction = SupportInteractionState.BREAKDOWN_ACCEPTED
        else:
            interaction = SupportInteractionState.BREAKDOWN_ATTEMPT
    elif reclaim_active:
        if bars_above >= cfg.persistence_bars and motion is not SupportMotion.FALLING:
            interaction = SupportInteractionState.RECOVERY_CONFIRMED
        else:
            interaction = SupportInteractionState.RECLAIM_ATTEMPT
    elif relation is PriceSupportRelation.AT_SUPPORT:
        interaction = SupportInteractionState.TESTING_SUPPORT
    elif (
        relation is PriceSupportRelation.ABOVE_NEAR
        and distance_delta_atr is not None
        and distance_delta_atr < 0.0
    ):
        interaction = SupportInteractionState.APPROACHING_SUPPORT
    elif (
        relation is PriceSupportRelation.ABOVE_FAR
        and distance_delta_atr is not None
        and distance_delta_atr > 0.0
        and motion in {SupportMotion.RISING, SupportMotion.FLAT_AFTER_RISE}
    ):
        interaction = SupportInteractionState.SUPPORTED_ADVANCE
    elif relation in {PriceSupportRelation.ABOVE_NEAR, PriceSupportRelation.ABOVE_FAR}:
        interaction = SupportInteractionState.HOLDING_ABOVE
    else:
        interaction = SupportInteractionState.UNAVAILABLE

    return StabilSupportBehaviorSnapshot(
        motion=motion,
        relation=relation,
        interaction=interaction,
        bars_since_rebase=bars_since_rebase,
        cross_count=cross_count,
        last_rebase_step_atr=last_rebase_step_atr,
        reclaim_active=reclaim_active,
    )


__all__ = [
    "PriceSupportRelation",
    "StabilSupportBehaviorConfig",
    "StabilSupportBehaviorSnapshot",
    "SupportInteractionState",
    "SupportMotion",
    "build_support_behavior",
]
