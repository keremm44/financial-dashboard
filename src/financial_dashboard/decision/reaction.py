from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

import pandas as pd

from financial_dashboard.analysis_config import BAR_DURATIONS
from financial_dashboard.context.envelope import ContextDataQuality, FactRef
from financial_dashboard.context.fvg_engulfing_projection import FvgEngulfingLifecycleProjection
from financial_dashboard.context.order_block_behavior_projection import (
    OrderBlockBehaviorProjection,
)

from .structural import StructuralDirection


class ReactionState(StrEnum):
    CONFIRMED = "CONFIRMED"
    DEVELOPING = "DEVELOPING"
    ABSENT = "ABSENT"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ReactionRelevancePolicy:
    """Reaction scope (relevance) policy; calibratable, never a directional vote.

    Terminal (dead) zones only contribute a failure vote while they are recent
    enough and near enough to the current price to still describe the active
    reaction path. Live zones never expire by age. ``None`` bounds disable a
    limit (legacy unbounded behaviour uses ``ReactionRelevancePolicy`` with both
    bounds set to ``None``).
    """

    max_age_bars: int | None = 50
    max_distance_atr: float | None = 5.0
    supersession: bool = True
    # Which terminal OB outcomes still cast a directional failure vote. Lifecycle
    # completion (zone filled/used, candidate expired unconfirmed, engine cleanup)
    # is not a directional contradiction; only an explicit FAILED interaction or a
    # confirmed block that price gapped through against it is. ``None`` keeps the
    # legacy "every terminal zone votes" behaviour for A/B diagnostics.
    ob_failure_modes: tuple[str, ...] | None = ("INTERACTION_FAILED", "CONSUMED_GAP_THROUGH")

    def __post_init__(self) -> None:
        if self.max_age_bars is not None and self.max_age_bars < 0:
            raise ValueError("max_age_bars must be >= 0 when provided")
        if self.max_distance_atr is not None and self.max_distance_atr < 0:
            raise ValueError("max_distance_atr must be >= 0 when provided")
        if self.ob_failure_modes is not None:
            modes = tuple(str(mode).strip().upper() for mode in self.ob_failure_modes)
            if not modes or not all(modes):
                raise ValueError("ob_failure_modes must contain non-empty mode names when provided")
            object.__setattr__(self, "ob_failure_modes", modes)

    @property
    def label(self) -> str:
        if self.max_age_bars is None and self.max_distance_atr is None:
            return "UNBOUNDED"
        return (
            f"A={self.max_age_bars if self.max_age_bars is not None else 'inf'};"
            f"D={self.max_distance_atr if self.max_distance_atr is not None else 'inf'};"
            f"SUPERSESSION={'ON' if self.supersession else 'OFF'}"
        )


_OB_TERMINAL_STATES = {"CONSUMED", "EXPIRED_CANDIDATE"}
_OB_TERMINAL_INTERACTIONS = {"FAILED"}


def _ob_terminal(observation) -> bool:
    state = str(observation.state).strip().upper()
    interaction = str(observation.interaction).strip().upper()
    return state in _OB_TERMINAL_STATES or interaction in _OB_TERMINAL_INTERACTIONS


def ob_failure_vote(observation, modes: tuple[str, ...] | None) -> bool:
    """Whether one terminal OB observation still casts a directional failure vote.

    ``modes=None`` keeps the legacy behaviour where every terminal zone votes.
    Otherwise only the selected terminal outcomes vote:
    ``INTERACTION_FAILED`` (explicit failed interaction) and
    ``CONSUMED_GAP_THROUGH`` (a confirmed block price gapped through against).
    """

    state = str(observation.state).strip().upper()
    interaction = str(observation.interaction).strip().upper()
    reason = str(observation.terminal_reason or "").strip().upper()
    if not _ob_terminal(observation):
        return False
    if modes is None:
        return True
    votes: set[str] = set()
    if interaction == "FAILED":
        votes.add("INTERACTION_FAILED")
    if state == "CONSUMED" and reason == "GAP_THROUGH":
        votes.add("CONSUMED_GAP_THROUGH")
    return bool(votes & set(modes))


def _fvg_terminal(row) -> bool:
    # A live ``failed_reaction`` is a genuine current failure, not a terminal zone.
    return bool(row.invalid or row.full_fill)


def _engulfing_terminal(row) -> bool:
    return bool(row.invalid)


def _tf_duration(timeframe: str) -> pd.Timedelta | None:
    try:
        return pd.Timedelta(BAR_DURATIONS[timeframe])
    except KeyError:
        return None


def _derived_age_bars(ref: FactRef, timeframe: str) -> int | None:
    """Age in the zone's own timeframe bars, derived from causal ref timestamps."""

    if ref.origin_time is None or ref.confirmed_at is None:
        return None
    try:
        delta = pd.Timestamp(ref.confirmed_at) - pd.Timestamp(ref.origin_time)
    except (ValueError, TypeError):
        return None
    duration = _tf_duration(timeframe)
    if duration is None or duration <= pd.Timedelta(0):
        return None
    if delta < pd.Timedelta(0):
        return None
    return int(delta // duration)


def _fvg_distance_atr(row, price: float) -> float | None:
    atr = float(row.formation_atr)
    if not atr > 0:
        return None
    lower, upper = float(row.lower_boundary), float(row.upper_boundary)
    if price < lower:
        distance = lower - price
    elif price > upper:
        distance = price - upper
    else:
        distance = 0.0
    return distance / atr


def zone_is_relevant(
    *,
    terminal: bool,
    age_bars: int | None,
    distance_atr: float | None,
    policy: ReactionRelevancePolicy,
) -> bool:
    """Relevance rule shared by every zone family.

    ``relevant(z) = (not terminal(z) or age(z) <= A)
                     and (dist(z) <= D or (dist unknown and not terminal(z)))``

    Live zones never expire by age and tolerate an unknown distance. Terminal
    zones with an unknown age or distance fail closed (they cannot prove they
    still describe the active reaction path).
    """

    if terminal:
        if policy.max_age_bars is not None:
            if age_bars is None or age_bars > policy.max_age_bars:
                return False
        if policy.max_distance_atr is not None:
            if distance_atr is None or distance_atr > policy.max_distance_atr:
                return False
        return True

    if (
        policy.max_distance_atr is not None
        and distance_atr is not None
        and distance_atr > policy.max_distance_atr
    ):
        return False
    return True


def select_relevant_zones(
    order_blocks: OrderBlockBehaviorProjection | None,
    fvg_engulfing: FvgEngulfingLifecycleProjection | None,
    *,
    current_price: float,
    policy: ReactionRelevancePolicy,
) -> tuple[OrderBlockBehaviorProjection | None, FvgEngulfingLifecycleProjection | None]:
    """Pre-filter projections to the price-relevant reaction sphere.

    Purely a scope reduction: source projections are never mutated, and rows are
    only removed, never reclassified. Supersession is intentionally not applied
    here; it needs confirmed/failed classification and stays inside
    :func:`assess_reaction`.
    """

    price = float(current_price)

    filtered_ob = order_blocks
    if order_blocks is not None:
        kept = tuple(
            item
            for item in order_blocks.observations
            if zone_is_relevant(
                terminal=_ob_terminal(item),
                age_bars=None if item.age_bars is None else int(item.age_bars),
                distance_atr=(
                    None if item.distance_atr is None else float(item.distance_atr)
                ),
                policy=policy,
            )
        )
        if len(kept) != len(order_blocks.observations):
            filtered_ob = replace(order_blocks, observations=kept)

    filtered_fvg = fvg_engulfing
    if fvg_engulfing is not None:
        kept_fvg = tuple(
            row
            for row in fvg_engulfing.fvg
            if zone_is_relevant(
                terminal=_fvg_terminal(row),
                age_bars=_derived_age_bars(row.ref, row.ref.timeframe),
                distance_atr=_fvg_distance_atr(row, price),
                policy=policy,
            )
        )
        kept_engulfing = tuple(
            row
            for row in fvg_engulfing.engulfing
            if zone_is_relevant(
                terminal=_engulfing_terminal(row),
                age_bars=_derived_age_bars(row.ref, row.ref.timeframe),
                distance_atr=None,
                policy=policy,
            )
        )
        if (
            len(kept_fvg) != len(fvg_engulfing.fvg)
            or len(kept_engulfing) != len(fvg_engulfing.engulfing)
        ):
            filtered_fvg = replace(fvg_engulfing, fvg=kept_fvg, engulfing=kept_engulfing)

    return filtered_ob, filtered_fvg




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


def _ranges_overlap(lower_a: float, upper_a: float, lower_b: float, upper_b: float) -> bool:
    return max(float(lower_a), float(lower_b)) <= min(float(upper_a), float(upper_b))


def assess_reaction(
    side: StructuralDirection,
    *,
    order_blocks: OrderBlockBehaviorProjection | None = None,
    fvg_engulfing: FvgEngulfingLifecycleProjection | None = None,
    timeframes: tuple[str, ...] = ("1d", "4h", "2h", "1h", "30m"),
    relevance: ReactionRelevancePolicy | None = None,
) -> ReactionAssessment:
    """Assess reaction lifecycle relative to an already-established Structure side.

    OB/FVG can describe whether a reaction is developing, confirmed or failed.
    Engulfing is confirmation-only and is consumed here only when it belongs to the
    same timeframe and spatially overlaps an already-usable same-side OB/FVG zone.
    This prevents an unrelated same-direction engulfing elsewhere on the chart from
    manufacturing confirmation for a different reaction path.

    ``relevance`` only controls supersession here: a failure whose range overlaps a
    same-timeframe confirmed reaction is released (``*_FAILED_SUPERSEDED``) instead
    of voting. Age/distance scoping is applied upstream via
    :func:`select_relevant_zones`.
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
    ob_failure_modes = relevance.ob_failure_modes if relevance is not None else None
    refs: list[FactRef] = []
    confirmed = False
    developing = False
    usable_zone_seen = False
    unavailable_seen = False
    reasons: list[str] = []
    usable_zone_ranges: list[tuple[str, float, float]] = []
    failure_records: list[tuple[str, str, str, float, float]] = []
    confirmed_ranges: list[tuple[str, float, float]] = []

    if order_blocks is not None:
        for item in order_blocks.observations:
            timeframe = item.timeframe.strip().lower()
            if timeframe not in allowed_tfs or (1 if item.bullish else -1) != direction:
                continue
            refs.append(item.ref)
            if item.ref.data_quality is not ContextDataQuality.VALID:
                unavailable_seen = True
                continue
            usable_zone_seen = True
            usable_zone_ranges.append((timeframe, float(item.bottom), float(item.top)))
            state = item.state.strip().upper()
            interaction = item.interaction.strip().upper()
            if interaction == "REACTION_CONFIRMED" or state == "REACTION_CONFIRMED":
                confirmed = True
                confirmed_ranges.append((timeframe, float(item.bottom), float(item.top)))
                reasons.append(f"OB_CONFIRMED:{item.timeframe}:{item.identity}")
            elif ob_failure_vote(item, ob_failure_modes):
                failure_records.append(
                    ("OB", item.timeframe, item.identity, float(item.bottom), float(item.top))
                )
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
            timeframe = item.ref.timeframe.strip().lower()
            if timeframe not in allowed_tfs or int(item.direction) != direction:
                continue
            refs.append(item.ref)
            if item.ref.data_quality is not ContextDataQuality.VALID:
                unavailable_seen = True
                continue
            usable_zone_seen = True
            lower = float(item.lower_boundary)
            upper = float(item.upper_boundary)
            usable_zone_ranges.append((timeframe, lower, upper))
            if item.failed_reaction:
                # Only a live failed reaction is a directional failure vote. A
                # fully filled or invalidated gap is a lifecycle COMPLETION
                # (normal gap-fill price discovery), not a contradiction: gaps
                # without continuation get filled more often than not, so
                # counting completion as failure made MATERIAL conflict
                # statistically inevitable across a ~100-zone set.
                failure_records.append(("FVG", item.ref.timeframe, item.identity, lower, upper))
            elif item.reaction_confirmed:
                confirmed = True
                confirmed_ranges.append((timeframe, lower, upper))
                reasons.append(f"FVG_CONFIRMED:{item.ref.timeframe}:{item.identity}")
            elif item.invalid or item.full_fill:
                reasons.append(f"FVG_LIFECYCLE_COMPLETED:{item.ref.timeframe}:{item.identity}")
            elif item.first_test_index is not None:
                developing = True
                reasons.append(f"FVG_DEVELOPING:{item.ref.timeframe}:{item.identity}")

        # Engulfing is confirmation-only. Require a real same-TF spatial relation to
        # an already-usable zone; side agreement alone is not a causal relationship.
        for item in fvg_engulfing.engulfing:
            timeframe = item.ref.timeframe.strip().lower()
            if timeframe not in allowed_tfs or int(item.direction) != direction:
                continue
            related = any(
                zone_tf == timeframe
                and _ranges_overlap(
                    item.lower_boundary,
                    item.upper_boundary,
                    zone_lower,
                    zone_upper,
                )
                for zone_tf, zone_lower, zone_upper in usable_zone_ranges
            )
            if not related:
                continue
            refs.append(item.ref)
            if item.ref.data_quality is not ContextDataQuality.VALID:
                unavailable_seen = True
                continue
            if item.invalid:
                failure_records.append(
                    (
                        "ENGULFING",
                        item.ref.timeframe,
                        item.identity,
                        float(item.lower_boundary),
                        float(item.upper_boundary),
                    )
                )
            elif item.continuation_confirmed:
                confirmed = True
                confirmed_ranges.append(
                    (timeframe, float(item.lower_boundary), float(item.upper_boundary))
                )
                reasons.append(f"ENGULFING_CONFIRMED:{item.ref.timeframe}:{item.identity}")
            elif item.first_test_index is not None and not item.weakened:
                developing = True
                reasons.append(f"ENGULFING_DEVELOPING:{item.ref.timeframe}:{item.identity}")

    # Supersession release: a failed zone whose range overlaps a same-timeframe
    # confirmed reaction no longer votes; the confirmed path supersedes the failure.
    if relevance is not None and relevance.supersession and failure_records:
        kept_records: list[tuple[str, str, str, float, float]] = []
        for kind, timeframe, identity, lower, upper in failure_records:
            normalized = timeframe.strip().lower()
            superseded = any(
                zone_tf == normalized
                and _ranges_overlap(lower, upper, zone_lower, zone_upper)
                for zone_tf, zone_lower, zone_upper in confirmed_ranges
            )
            if superseded:
                reasons.append(f"{kind}_FAILED_SUPERSEDED:{timeframe}:{identity}")
            else:
                kept_records.append((kind, timeframe, identity, lower, upper))
        failure_records = kept_records

    failed = bool(failure_records)
    for kind, timeframe, identity, _lower, _upper in failure_records:
        reasons.append(f"{kind}_FAILED:{timeframe}:{identity}")

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


__all__ = [
    "ReactionAssessment",
    "ReactionRelevancePolicy",
    "ReactionState",
    "assess_reaction",
    "ob_failure_vote",
    "select_relevant_zones",
    "zone_is_relevant",
]
