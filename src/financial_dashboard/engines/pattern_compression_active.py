from __future__ import annotations

import copy
from dataclasses import dataclass
from collections.abc import Sequence

from .pattern_compression_core import (
    MAX_PATH_SAMPLE,
    PATTERN_NONE,
    ST_BREAK_ATTEMPT,
    ST_BREAK_CANDIDATE,
    ST_BREAK_CONFIRMED,
    ST_BREAK_FAILED,
    ST_BREAK_TIMEOUT,
    ST_CANDIDATE,
    ST_COMPLETED,
    ST_COMPRESSING,
    ST_DEFINED,
    ST_GEOMETRY,
    ST_INVALID,
    ST_MATURING,
    ST_PREP,
    ST_RETESTING,
    ST_RETEST_OK,
    ST_RETEST_WAIT,
    ST_WEAK,
    PatternCandidate,
    PatternCompressionConfig,
    age_quality,
    clamp,
    cleanliness_quality,
    contraction_quality,
    inverse_smoothstep,
    is_flag,
    is_pennant,
    line_price,
    progress_quality,
)
from .pattern_compression_geometry import PatternGeometryEvaluator
from .pattern_compression_selection import effective_raw_quality
from .pattern_compression_specialized import SpecializedPatternEvaluator, depth_quality, duration_quality, is_specialized


BREAK_LIFECYCLE_STATES = frozenset(
    {
        ST_BREAK_ATTEMPT,
        ST_BREAK_CANDIDATE,
        ST_BREAK_CONFIRMED,
        ST_RETEST_WAIT,
        ST_RETESTING,
        ST_RETEST_OK,
        ST_BREAK_TIMEOUT,
        ST_COMPLETED,
        ST_BREAK_FAILED,
    }
)
TERMINAL_STATES = frozenset({ST_COMPLETED, ST_BREAK_FAILED, ST_INVALID})


def is_break_lifecycle(state: str) -> bool:
    return state in BREAK_LIFECYCLE_STATES


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def reset_quality_snapshot(candidate: PatternCandidate) -> PatternCandidate:
    result = copy.deepcopy(candidate)
    result.quality_frozen = False
    result.frozen_raw_quality = None
    result.frozen_upper_boundary_at_break = None
    result.frozen_lower_boundary_at_break = None
    result.frozen_break_buffer = None
    result.frozen_retest_tolerance = None
    result.frozen_atr_at_break = None
    result.frozen_classic_dir = 0
    result.frozen_pattern_type = PATTERN_NONE
    result.break_snapshot_bar = None
    result.break_snapshot_price = None
    result.break_snapshot_direction = 0
    result.break_snapshot_quality = None
    result.break_strength = None
    result.break_body_score = None
    result.break_close_score = None
    result.break_penetration_score = None
    result.break_expansion_score = None
    result.break_volume_score = None
    result.break_confirmation_strength = None
    return result


def efficiency_between(
    closes: Sequence[float],
    *,
    start_bar: int,
    end_bar: int,
    current_bar: int,
    maximum_bars: int,
    min_tick: float,
) -> float:
    bounded_end = min(end_bar, current_bar)
    duration = max(0, bounded_end - start_bar)
    sample_bars = min(duration, maximum_bars)
    total_path = 0.0
    if sample_bars > 0:
        for step in range(sample_bars):
            absolute_bar = bounded_end - step
            if absolute_bar >= 1:
                total_path += abs(float(closes[absolute_bar]) - float(closes[absolute_bar - 1]))
    start_sample = bounded_end - sample_bars
    net_move = abs(float(closes[bounded_end]) - float(closes[start_sample])) if sample_bars > 0 else 0.0
    return clamp(net_move / max(total_path, net_move, min_tick), 0.0, 1.0)


def refresh_active_candidate(
    candidate: PatternCandidate,
    *,
    evaluator: PatternGeometryEvaluator,
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    violation_end_bar: int,
) -> PatternCandidate:
    result = copy.deepcopy(candidate)
    if not result.valid:
        return result

    config = evaluator.config
    profile = evaluator.profile
    current_bar = evaluator.current_bar
    safe_atr = evaluator.safe_atr
    required_geometry = (
        result.hb1,
        result.hp1,
        result.hb2,
        result.hp2,
        result.lb1,
        result.lp1,
        result.lb2,
        result.lp2,
        result.start_bar,
    )
    if any(value is None for value in required_geometry):
        return result

    result.upper_now = line_price(int(result.hb1), float(result.hp1), int(result.hb2), float(result.hp2), current_bar)
    result.lower_now = line_price(int(result.lb1), float(result.lp1), int(result.lb2), float(result.lp2), current_bar)
    if result.quality_frozen:
        result.violation_scan_mode = "Dondurulmuş"
        return result

    quality_end_bar = max(int(result.start_bar), min(current_bar, violation_end_bar))
    quality_upper = line_price(int(result.hb1), float(result.hp1), int(result.hb2), float(result.hp2), quality_end_bar)
    quality_lower = line_price(int(result.lb1), float(result.lp1), int(result.lb2), float(result.lp2), quality_end_bar)
    upper_start = line_price(int(result.hb1), float(result.hp1), int(result.hb2), float(result.hp2), int(result.start_bar))
    lower_start = line_price(int(result.lb1), float(result.lp1), int(result.lb2), float(result.lp2), int(result.start_bar))
    result.start_width = upper_start - lower_start
    result.current_width = quality_upper - quality_lower
    result.contraction = (
        (result.start_width - result.current_width) / result.start_width
        if result.start_width > config.min_tick
        else None
    )
    active_age_for_progress = quality_end_bar - int(result.start_bar)
    if result.apex_bar is not None and result.apex_bar > int(result.start_bar):
        result.progress = clamp(
            float(active_age_for_progress) / max(1.0, float(result.apex_bar - int(result.start_bar))),
            0.0,
            2.0,
        )
    else:
        result.progress = clamp(
            float(active_age_for_progress) / max(1.0, float(profile.max_consolidation_bars)),
            0.0,
            2.0,
        )

    tolerance = max(config.min_tick * 2.0, safe_atr * profile.touch_atr_mult)
    result.violation = max(
        max(0.0, float(highs[current_bar]) - float(result.upper_now)) / max(tolerance, config.min_tick),
        max(0.0, float(result.lower_now) - float(lows[current_bar])) / max(tolerance, config.min_tick),
    )

    stats = evaluator.boundary_violation_stats_range(
        upper_x1=int(result.hb1),
        upper_y1=float(result.hp1),
        upper_x2=int(result.hb2),
        upper_y2=float(result.hp2),
        lower_x1=int(result.lb1),
        lower_y1=float(result.lp1),
        lower_x2=int(result.lb2),
        lower_y2=float(result.lp2),
        geometry_start_bar=int(result.start_bar),
        requested_start_bar=int(result.start_bar),
        requested_end_bar=quality_end_bar,
        apply_maximum_window=True,
    )
    result.historical_upper_close_violations = stats.upper_close
    result.historical_lower_close_violations = stats.lower_close
    result.historical_upper_wick_violations = stats.upper_wick
    result.historical_lower_wick_violations = stats.lower_wick
    result.historical_close_violations = stats.close_count
    result.historical_wick_violations = stats.wick_count
    result.max_historical_violation = stats.max_violation
    result.historical_violation_penalty = stats.penalty
    result.historical_scanned_bars = stats.scanned_bars
    result.violation_history_truncated = stats.truncated
    result.last_violation_processed_bar = quality_end_bar
    result.violation_geometry_key = f"{result.hb1}:{result.hb2}:{result.lb1}:{result.lb2}"
    result.violation_scan_mode = "Tam"

    current_contraction_score = contraction_quality(result.contraction, profile.min_contraction)
    current_progress_score = progress_quality(result.progress)
    current_maturity_score = clamp(
        age_quality(quality_end_bar - int(result.start_bar), profile.min_age) * 0.58
        + current_progress_score * 0.42,
        0.0,
        100.0,
    )
    current_cleanliness = cleanliness_quality(result.historical_violation_penalty)
    result.contraction_score = current_contraction_score
    result.maturity_score = current_maturity_score
    result.geometry_score = (
        result.slope_shape_score
        if is_flag(result.pattern_type)
        else clamp(result.slope_shape_score * 0.65 + current_contraction_score * 0.35, 0.0, 100.0)
    )

    if is_specialized(result.pattern_type):
        required_pole = (
            result.pole_end_bar,
            result.pole_end_price,
            result.pole_magnitude,
            result.pole_efficiency,
        )
        if all(value is not None for value in required_pole):
            specialized = SpecializedPatternEvaluator(
                store=evaluator.store,
                highs=highs,
                lows=lows,
                closes=closes,
            )
            observed_high, observed_low = specialized.range_between(
                start_bar=int(result.pole_end_bar),
                end_bar=quality_end_bar,
                maximum_bars=MAX_PATH_SAMPLE,
            )
            consolidation_low = min(float(result.lp1), float(result.lp2), observed_low)
            consolidation_high = max(float(result.hp1), float(result.hp2), observed_high)
            active_height = max(consolidation_high - consolidation_low, config.min_tick)
            pole_magnitude = max(float(result.pole_magnitude), config.min_tick)
            result.correction_depth = clamp(
                (float(result.pole_end_price) - consolidation_low) / pole_magnitude
                if result.pole_dir == 1
                else (consolidation_high - float(result.pole_end_price)) / pole_magnitude,
                0.0,
                2.0,
            )
            result.duration_ratio = float(max(1, quality_end_bar - int(result.start_bar))) / max(1.0, float(result.pole_duration))
            result.consolidation_height_ratio = active_height / pole_magnitude
            result.consolidation_efficiency = efficiency_between(
                closes,
                start_bar=int(result.start_bar),
                end_bar=quality_end_bar,
                current_bar=current_bar,
                maximum_bars=MAX_PATH_SAMPLE,
                min_tick=config.min_tick,
            )
            depth_score = depth_quality(result.correction_depth)
            duration_score = duration_quality(result.duration_ratio)
            calmness = (
                100.0
                if result.consolidation_efficiency <= float(result.pole_efficiency)
                else inverse_smoothstep(
                    0.00,
                    0.28,
                    result.consolidation_efficiency - float(result.pole_efficiency),
                )
                * 100.0
            )
            if is_flag(result.pattern_type):
                result.raw_quality = clamp(
                    result.pole_quality * 0.28
                    + depth_score * 0.20
                    + duration_score * 0.12
                    + result.geometry_score * 0.16
                    + result.touch_score * 0.12
                    + calmness * 0.07
                    + current_cleanliness * 0.05,
                    0.0,
                    100.0,
                )
            else:
                result.raw_quality = clamp(
                    result.pole_quality * 0.26
                    + depth_score * 0.18
                    + duration_score * 0.12
                    + result.geometry_score * 0.20
                    + result.touch_score * 0.10
                    + calmness * 0.07
                    + current_cleanliness * 0.07,
                    0.0,
                    100.0,
                )
    else:
        result.raw_quality = clamp(
            result.geometry_score * 0.38
            + result.touch_score * 0.32
            + current_maturity_score * 0.18
            + current_cleanliness * 0.12,
            0.0,
            100.0,
        )
    return result


def hard_geometry_invalid(candidate: PatternCandidate, *, geometry_atr: float, config: PatternCompressionConfig) -> bool:
    if not candidate.valid:
        return False
    profile = config.resolve()
    safe_geometry_atr = max(geometry_atr, config.min_tick * 10.0)
    if is_flag(candidate.pattern_type):
        average_slope_norm = ((float(candidate.upper_slope) + float(candidate.lower_slope)) * 0.5) / safe_geometry_atr
        parallel_broken = abs(float(candidate.upper_slope) - float(candidate.lower_slope)) / safe_geometry_atr > profile.parallel_slope_norm_tol * 1.8
        same_direction_too_strong = (
            average_slope_norm > profile.flat_slope_norm_tol * 0.55
            if candidate.pole_dir == 1
            else average_slope_norm < -profile.flat_slope_norm_tol * 0.55
        )
        return bool(
            float(candidate.correction_depth or 0.0) > 0.80
            or float(candidate.duration_ratio or 0.0) > 4.0
            or float(candidate.consolidation_height_ratio or 0.0) > 0.70
            or parallel_broken
            or same_direction_too_strong
        )
    if is_pennant(candidate.pattern_type):
        return bool(
            float(candidate.correction_depth or 0.0) > 0.80
            or float(candidate.duration_ratio or 0.0) > 4.0
            or float(candidate.progress or 0.0) > 1.02
            or float(candidate.current_width or 0.0) <= config.min_tick * 3.0
            or float(candidate.contraction or 0.0) < profile.min_contraction * 0.55
        )
    return bool(
        float(candidate.progress or 0.0) > 1.02
        or float(candidate.current_width or 0.0) <= config.min_tick * 3.0
        or float(candidate.contraction or 0.0) < profile.min_contraction * 0.55
    )


@dataclass(frozen=True, slots=True)
class NormalStateEvaluation:
    state: str
    invalid_reason: str
    defined: bool
    mature: bool
    prep: bool
    weak: bool
    invalid: bool
    hard_geometry_invalid: bool
    active_minimum_quality: float


def evaluate_normal_state(
    candidate: PatternCandidate,
    *,
    current_state: str,
    bar_index: int,
    close: float,
    safe_atr: float,
    config: PatternCompressionConfig,
) -> NormalStateEvaluation:
    if not candidate.valid:
        return NormalStateEvaluation(
            state=ST_NONE,
            invalid_reason="Yeterli teyitli geometri yok",
            defined=False,
            mature=False,
            prep=False,
            weak=False,
            invalid=False,
            hard_geometry_invalid=False,
            active_minimum_quality=config.resolve().min_raw_quality,
        )
    if is_terminal(current_state):
        return NormalStateEvaluation(
            state=current_state,
            invalid_reason="Yok",
            defined=False,
            mature=False,
            prep=False,
            weak=False,
            invalid=current_state == ST_INVALID,
            hard_geometry_invalid=current_state == ST_INVALID,
            active_minimum_quality=config.resolve().min_raw_quality,
        )

    profile = config.resolve()
    quality = effective_raw_quality(candidate)
    pattern_age = bar_index - int(candidate.start_bar)
    tolerance = max(config.min_tick * 2.0, safe_atr * profile.touch_atr_mult)
    near_upper = abs(close - float(candidate.upper_now)) / max(tolerance, config.min_tick)
    near_lower = abs(close - float(candidate.lower_now)) / max(tolerance, config.min_tick)
    active_minimum_quality = profile.min_specialized_quality if is_specialized(candidate.pattern_type) else profile.min_raw_quality
    mature_threshold = min(82.0, active_minimum_quality + 12.0)
    required_age = max(6, int(profile.min_age / 2)) if is_specialized(candidate.pattern_type) else profile.min_age
    defined = (
        candidate.known_bar is not None
        and bar_index >= candidate.known_bar
        and pattern_age >= required_age
        and candidate.upper_touches >= 2
        and candidate.lower_touches >= 2
        and quality >= active_minimum_quality
    )
    mature = defined and quality >= mature_threshold and (
        is_flag(candidate.pattern_type) or float(candidate.contraction or 0.0) >= profile.min_contraction
    )
    prep = mature and (near_upper <= 1.35 or near_lower <= 1.35)

    weak = False
    invalid = False
    hard_invalid = False
    reason = "Yok"
    if not is_break_lifecycle(current_state):
        geometry_atr = max(float(candidate.geometry_atr if candidate.geometry_atr is not None else safe_atr), config.min_tick * 10.0)
        hard_invalid = hard_geometry_invalid(candidate, geometry_atr=geometry_atr, config=config)
        maximum_accepted = 0.90 if config.profile == "Hassas" else 0.55 if config.profile == "Seçici" else 0.72
        history_severe = (
            candidate.historical_close_violations >= 2
            or candidate.max_historical_violation > maximum_accepted
            or candidate.historical_violation_penalty >= 62.0
        )
        history_weak = (
            history_severe
            or candidate.historical_close_violations >= 1
            or candidate.historical_wick_violations >= 3
            or candidate.historical_violation_penalty >= 28.0
        )
        history_reason = (
            "Sınır ihlalleri arttı; kırılım davranışı izleniyor"
            if history_severe
            else "Geçmiş sınır ihlalleri kaliteyi düşürüyor"
        )
        if is_flag(candidate.pattern_type):
            average_slope_norm = ((float(candidate.upper_slope) + float(candidate.lower_slope)) * 0.5) / geometry_atr
            parallel_broken = abs(float(candidate.upper_slope) - float(candidate.lower_slope)) / geometry_atr > profile.parallel_slope_norm_tol * 1.8
            same_direction_too_strong = (
                average_slope_norm > profile.flat_slope_norm_tol * 0.55
                if candidate.pole_dir == 1
                else average_slope_norm < -profile.flat_slope_norm_tol * 0.55
            )
            invalid = hard_invalid
            weak = not invalid and (
                history_weak
                or float(candidate.correction_depth or 0.0) > 0.65
                or float(candidate.duration_ratio or 0.0) > 2.7
                or quality < profile.min_specialized_quality
            )
            reason = (
                "Direğin büyük kısmı geri alındı" if float(candidate.correction_depth or 0.0) > 0.80
                else "Konsolidasyon süresi aşırı uzadı" if float(candidate.duration_ratio or 0.0) > 4.0
                else "Konsolidasyon direğe göre fazla geniş" if float(candidate.consolidation_height_ratio or 0.0) > 0.70
                else "Paralel bayrak geometrisi bozuldu" if parallel_broken
                else "Kanal direkle aynı yönde güçlendi" if same_direction_too_strong
                else history_reason if history_weak
                else "Bayrak kalitesi zayıflıyor" if weak
                else "Yok"
            )
        elif is_pennant(candidate.pattern_type):
            invalid = hard_invalid
            weak = not invalid and (
                history_weak
                or float(candidate.correction_depth or 0.0) > 0.65
                or float(candidate.duration_ratio or 0.0) > 2.7
                or float(candidate.progress or 0.0) > 0.90
                or quality < profile.min_specialized_quality
            )
            reason = (
                "Direğin büyük kısmı geri alındı" if float(candidate.correction_depth or 0.0) > 0.80
                else "Flama süresi aşırı uzadı" if float(candidate.duration_ratio or 0.0) > 4.0
                else "Flama apex bölgesini geçti" if float(candidate.progress or 0.0) > 1.02
                else "Flama genişliği geçersiz" if float(candidate.current_width or 0.0) <= config.min_tick * 3.0
                else "Flama yakınsaması bozuldu" if float(candidate.contraction or 0.0) < profile.min_contraction * 0.55
                else history_reason if history_weak
                else "Flama kalitesi zayıflıyor" if weak
                else "Yok"
            )
        else:
            invalid = hard_invalid
            weak = not invalid and (
                history_weak
                or float(candidate.progress or 0.0) > 0.90
                or candidate.violation > 1.2
                or float(candidate.contraction or 0.0) < profile.min_contraction * 1.05
            )
            reason = (
                "Apex bölgesi geçildi" if float(candidate.progress or 0.0) > 1.02
                else "Üst/alt sınır geometrisi geçersiz" if float(candidate.current_width or 0.0) <= config.min_tick * 3.0
                else "Daralma bozuldu" if float(candidate.contraction or 0.0) < profile.min_contraction * 0.55
                else history_reason if history_weak
                else "Apex'e yaklaşıyor" if float(candidate.progress or 0.0) > 0.90
                else "Çizgi ihlalleri artıyor" if candidate.violation > 1.2
                else "Daralma zayıflıyor" if weak
                else "Yok"
            )

    if invalid:
        state = ST_INVALID
    elif current_state == ST_BREAK_TIMEOUT:
        state = ST_PREP if prep else ST_WEAK if weak else ST_DEFINED if defined else ST_GEOMETRY
    elif weak:
        state = ST_WEAK
    elif prep:
        state = ST_PREP
    elif mature:
        state = ST_MATURING if is_flag(candidate.pattern_type) else ST_COMPRESSING if float(candidate.contraction or 0.0) >= 0.50 else ST_MATURING
    elif defined:
        state = ST_DEFINED
    elif pattern_age >= max(5, required_age - 4):
        state = ST_GEOMETRY
    else:
        state = ST_CANDIDATE

    return NormalStateEvaluation(
        state=state,
        invalid_reason=reason,
        defined=defined,
        mature=mature,
        prep=prep,
        weak=weak,
        invalid=invalid,
        hard_geometry_invalid=hard_invalid,
        active_minimum_quality=active_minimum_quality,
    )
