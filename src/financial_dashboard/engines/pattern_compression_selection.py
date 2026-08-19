from __future__ import annotations

from dataclasses import dataclass

from .pattern_compression_core import (
    PATTERN_NONE,
    PROFILE_SELECTIVE,
    PROFILE_SENSITIVE,
    ST_COMPRESSING,
    ST_DEFINED,
    ST_MATURING,
    ST_NONE,
    ST_PREP,
    PatternCandidate,
    PatternCompressionConfig,
    clamp,
    line_price,
)


def effective_raw_quality(candidate: PatternCandidate) -> float:
    if candidate.quality_frozen and candidate.frozen_raw_quality is not None:
        return float(candidate.frozen_raw_quality)
    return float(candidate.raw_quality)


def identity_compatible(current: PatternCandidate, incoming: PatternCandidate) -> bool:
    same_family = (
        current.valid
        and incoming.valid
        and current.family != PATTERN_NONE
        and current.family == incoming.family
    )
    direction_compatible = (
        current.classic_dir == 0
        or incoming.classic_dir == 0
        or current.classic_dir == incoming.classic_dir
    )
    return bool(same_family and direction_compatible)


def pivot_overlap_count(first: PatternCandidate, second: PatternCandidate) -> int:
    count = 0
    count += int(first.hb1 == second.hb1 or first.hb1 == second.hb2)
    count += int(first.hb2 == second.hb1 or first.hb2 == second.hb2)
    count += int(first.lb1 == second.lb1 or first.lb1 == second.lb2)
    count += int(first.lb2 == second.lb1 or first.lb2 == second.lb2)
    return count


def overlap_ratio(upper_a: float, lower_a: float, upper_b: float, lower_b: float, *, min_tick: float) -> float:
    intersection = max(0.0, min(upper_a, upper_b) - max(lower_a, lower_b))
    union_value = max(upper_a, upper_b) - min(lower_a, lower_b)
    return clamp(intersection / union_value, 0.0, 1.0) if union_value > min_tick else 0.0


def pair_geometry_atr(first: PatternCandidate, second: PatternCandidate, *, safe_atr: float, min_tick: float) -> float:
    first_atr = max(float(first.geometry_atr if first.geometry_atr is not None else safe_atr), min_tick * 10.0)
    second_atr = max(float(second.geometry_atr if second.geometry_atr is not None else safe_atr), min_tick * 10.0)
    return max((first_atr + second_atr) * 0.5, min_tick * 10.0)


def continuity_score(
    current: PatternCandidate,
    incoming: PatternCandidate,
    *,
    bar_index: int,
    safe_atr: float,
    config: PatternCompressionConfig,
) -> float:
    if not identity_compatible(current, incoming):
        return 0.0
    profile = config.resolve()
    score = 25.0 if current.pattern_type == incoming.pattern_type else 0.0
    start_distance = abs(int(current.start_bar) - int(incoming.start_bar))
    if start_distance <= max(profile.min_touch_gap * 2, profile.pivot_len * 2):
        score += 15.0
    elif start_distance <= profile.min_age:
        score += 6.0
    score += float(pivot_overlap_count(current, incoming)) / 4.0 * 25.0

    current_upper = line_price(int(current.hb1), float(current.hp1), int(current.hb2), float(current.hp2), bar_index)
    current_lower = line_price(int(current.lb1), float(current.lp1), int(current.lb2), float(current.lp2), bar_index)
    incoming_upper = line_price(int(incoming.hb1), float(incoming.hp1), int(incoming.hb2), float(incoming.hp2), bar_index)
    incoming_lower = line_price(int(incoming.lb1), float(incoming.lp1), int(incoming.lb2), float(incoming.lp2), bar_index)
    continuity_atr = pair_geometry_atr(current, incoming, safe_atr=safe_atr, min_tick=config.min_tick)
    boundary_distance = (
        abs(current_upper - incoming_upper) + abs(current_lower - incoming_lower)
    ) / max(2.0 * continuity_atr, config.min_tick)
    score += clamp(1.0 - boundary_distance, 0.0, 1.0) * 20.0
    score += overlap_ratio(
        current_upper,
        current_lower,
        incoming_upper,
        incoming_lower,
        min_tick=config.min_tick,
    ) * 15.0
    return score


def selection_score(
    candidate: PatternCandidate,
    *,
    active: PatternCandidate,
    pattern_state: str,
    bar_index: int,
    close: float,
    safe_atr: float,
    config: PatternCompressionConfig,
    terminal: bool = False,
) -> float:
    recency_priority = (
        clamp(16.0 - float(bar_index - int(candidate.end_bar)) * 0.65, -10.0, 16.0)
        if candidate.end_bar is not None
        else 0.0
    )
    upper_distance = abs(close - float(candidate.upper_now)) / max(safe_atr, config.min_tick)
    lower_distance = abs(close - float(candidate.lower_now)) / max(safe_atr, config.min_tick)
    proximity_priority = clamp(10.0 - min(upper_distance, lower_distance) * 3.5, -8.0, 10.0)
    same_active_identity = active.valid and candidate.identity != 0 and candidate.identity == active.identity
    continuity_value = (
        100.0
        if same_active_identity
        else continuity_score(active, candidate, bar_index=bar_index, safe_atr=safe_atr, config=config)
        if active.valid and not terminal
        else 0.0
    )
    continuity_priority = continuity_value * 0.22
    partial_overlap_penalty = (
        8.0
        if active.valid and not same_active_identity and 20.0 <= continuity_value < 60.0
        else 0.0
    )
    return recency_priority + proximity_priority + continuity_priority - partial_overlap_penalty


def quality_priority_gap(config: PatternCompressionConfig) -> float:
    if config.profile == PROFILE_SENSITIVE:
        return 5.0
    if config.profile == PROFILE_SELECTIVE:
        return 7.0
    return 6.0


def replacement_margin(state: str) -> float:
    if state == ST_PREP:
        return 14.0
    if state == ST_COMPRESSING:
        return 12.0
    if state == ST_MATURING:
        return 10.0
    if state == ST_DEFINED:
        return 8.0
    return 6.0


def candidate_preferred(
    incoming: PatternCandidate,
    current_best: PatternCandidate,
    *,
    config: PatternCompressionConfig,
) -> bool:
    preferred = not current_best.valid
    if incoming.valid and current_best.valid:
        incoming_quality = effective_raw_quality(incoming)
        current_quality = effective_raw_quality(current_best)
        quality_difference = incoming_quality - current_quality
        if abs(quality_difference) >= quality_priority_gap(config):
            preferred = quality_difference > 0.0
        elif incoming.selection_score != current_best.selection_score:
            preferred = incoming.selection_score > current_best.selection_score
        else:
            preferred = incoming_quality > current_quality
    return preferred


def should_replace_active(
    active: PatternCandidate,
    incoming: PatternCandidate,
    *,
    state: str,
    lifecycle_can_update: bool,
    terminal: bool,
    config: PatternCompressionConfig,
) -> tuple[bool, str]:
    if not active.valid or terminal or state == ST_NONE:
        return True, "Yok"
    quality_gap = quality_priority_gap(config)
    incoming_quality = effective_raw_quality(incoming)
    active_quality = effective_raw_quality(active)
    materially_better = incoming_quality >= active_quality + quality_gap
    near_quality = abs(incoming_quality - active_quality) < quality_gap
    context_wins = near_quality and incoming.selection_score >= active.selection_score + replacement_margin(state)
    replace = lifecycle_can_update and (materially_better or context_wins)
    if replace:
        return True, "Yok"
    if not lifecycle_can_update:
        return False, "Kırılım lifecycle kilidi"
    if not near_quality and not materially_better:
        return False, "Formation quality daha zayıf"
    return False, f"Selection priority marjı yetersiz: +{replacement_margin(state):.1f}"


@dataclass(frozen=True, slots=True)
class CompletedPatternReference:
    pattern_type: str
    start_bar: int
    end_bar: int
    hb1: int
    hb2: int
    lb1: int
    lb2: int
    upper_at_end: float
    lower_at_end: float
    geometry_atr: float

    @classmethod
    def from_candidate(cls, candidate: PatternCandidate, *, safe_atr: float, min_tick: float) -> "CompletedPatternReference":
        return cls(
            pattern_type=candidate.pattern_type,
            start_bar=int(candidate.start_bar),
            end_bar=int(candidate.end_bar),
            hb1=int(candidate.hb1),
            hb2=int(candidate.hb2),
            lb1=int(candidate.lb1),
            lb2=int(candidate.lb2),
            upper_at_end=line_price(int(candidate.hb1), float(candidate.hp1), int(candidate.hb2), float(candidate.hp2), int(candidate.end_bar)),
            lower_at_end=line_price(int(candidate.lb1), float(candidate.lp1), int(candidate.lb2), float(candidate.lp2), int(candidate.end_bar)),
            geometry_atr=max(float(candidate.geometry_atr if candidate.geometry_atr is not None else safe_atr), min_tick * 10.0),
        )


def same_completed_structure(
    candidate: PatternCandidate,
    completed: CompletedPatternReference | None,
    *,
    safe_atr: float,
    config: PatternCompressionConfig,
) -> bool:
    if completed is None or not candidate.valid:
        return False
    profile = config.resolve()
    same_type = candidate.pattern_type == completed.pattern_type
    near_start = abs(int(candidate.start_bar) - completed.start_bar) <= max(profile.min_touch_gap * 2, profile.pivot_len * 2)
    near_end = abs(int(candidate.end_bar) - completed.end_bar) <= max(profile.min_touch_gap * 3, profile.min_age)
    overlap = 0
    overlap += int(candidate.hb1 == completed.hb1 or candidate.hb1 == completed.hb2)
    overlap += int(candidate.hb2 == completed.hb1 or candidate.hb2 == completed.hb2)
    overlap += int(candidate.lb1 == completed.lb1 or candidate.lb1 == completed.lb2)
    overlap += int(candidate.lb2 == completed.lb1 or candidate.lb2 == completed.lb2)
    candidate_upper = line_price(int(candidate.hb1), float(candidate.hp1), int(candidate.hb2), float(candidate.hp2), int(candidate.end_bar))
    candidate_lower = line_price(int(candidate.lb1), float(candidate.lp1), int(candidate.lb2), float(candidate.lp2), int(candidate.end_bar))
    candidate_atr = max(float(candidate.geometry_atr if candidate.geometry_atr is not None else safe_atr), config.min_tick * 10.0)
    pair_atr = max((candidate_atr + completed.geometry_atr) * 0.5, config.min_tick * 10.0)
    boundary_distance = (
        abs(candidate_upper - completed.upper_at_end) + abs(candidate_lower - completed.lower_at_end)
    ) / max(2.0 * pair_atr, config.min_tick)
    similar_boundaries = boundary_distance <= 0.85
    return bool(same_type and near_start and near_end and overlap >= 3 and similar_boundaries)
