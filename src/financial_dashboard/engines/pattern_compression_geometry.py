from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Sequence

from .pattern_compression_core import (
    MAX_HISTORY_OFFSET,
    MAX_VIOLATION_SCAN,
    MIN_SLOPE_NORM_TOL,
    PATTERN_ASCENDING_TRIANGLE,
    PATTERN_DESCENDING_TRIANGLE,
    PATTERN_FALLING_WEDGE,
    PATTERN_NONE,
    PATTERN_RISING_WEDGE,
    PATTERN_SYMMETRICAL_TRIANGLE,
    PROFILE_SELECTIVE,
    PROFILE_SENSITIVE,
    PatternCandidate,
    PatternCompressionConfig,
    PivotStore,
    age_quality,
    clamp,
    classic_direction,
    cleanliness_quality,
    contraction_quality,
    inverse_smoothstep,
    line_price,
    progress_quality,
    slope,
    smoothstep,
)


@dataclass(frozen=True, slots=True)
class ViolationStats:
    upper_close: int = 0
    lower_close: int = 0
    upper_wick: int = 0
    lower_wick: int = 0
    max_upper: float = 0.0
    max_lower: float = 0.0
    penalty: float = 0.0
    scanned_bars: int = 0
    truncated: bool = False

    @property
    def close_count(self) -> int:
        return self.upper_close + self.lower_close

    @property
    def wick_count(self) -> int:
        return self.upper_wick + self.lower_wick

    @property
    def max_violation(self) -> float:
        return max(self.max_upper, self.max_lower)


@dataclass(frozen=True, slots=True)
class PatternGeometryAnalysis:
    candidate: PatternCandidate
    chronological: bool
    touch_basics: bool
    touch_distribution: bool
    generic_type: str
    generic_geometry_supported: bool
    parallel_geometry_supported: bool
    historical_geometry_acceptable: bool
    post_pivot_survival_passed: bool
    pre_geometry_score: float
    cleanliness_score: float
    upper_slope_norm: float
    lower_slope_norm: float
    slope_gap_norm: float
    parallel_like: bool
    converging: bool
    formed_duration: int


def pine_round_int(value: float) -> int:
    """Pine-style nearest integer for bar coordinates; ties round away from zero."""
    if value >= 0.0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def pivot_trend_ok(first_price: float, second_price: float, *, rising: bool, tolerance: float) -> bool:
    if rising:
        return second_price >= first_price - tolerance
    return second_price <= first_price + tolerance


def classify_generic_pattern(
    *,
    touch_basics: bool,
    converging: bool,
    apex_ok: bool,
    parallel_like: bool,
    upper_slope_norm: float,
    lower_slope_norm: float,
    flat_slope_norm_tol: float,
    highs_lower: bool,
    lows_higher: bool,
) -> str:
    if not (touch_basics and converging and apex_ok and not parallel_like):
        return PATTERN_NONE

    horizontal_upper = abs(upper_slope_norm) <= flat_slope_norm_tol
    horizontal_lower = abs(lower_slope_norm) <= flat_slope_norm_tol
    strict_upper_down = upper_slope_norm < -flat_slope_norm_tol
    strict_upper_up = upper_slope_norm > flat_slope_norm_tol
    strict_lower_up = lower_slope_norm > flat_slope_norm_tol
    strict_lower_down = lower_slope_norm < -flat_slope_norm_tol

    if horizontal_upper and strict_lower_up and lows_higher:
        return PATTERN_ASCENDING_TRIANGLE
    if horizontal_lower and strict_upper_down and highs_lower:
        return PATTERN_DESCENDING_TRIANGLE
    if strict_upper_down and strict_lower_up:
        return PATTERN_SYMMETRICAL_TRIANGLE
    if strict_upper_up and strict_lower_up and lower_slope_norm > upper_slope_norm + MIN_SLOPE_NORM_TOL:
        return PATTERN_RISING_WEDGE
    if strict_upper_down and strict_lower_down and upper_slope_norm < lower_slope_norm - MIN_SLOPE_NORM_TOL:
        return PATTERN_FALLING_WEDGE
    return PATTERN_NONE


def violation_penalty_from_stats(
    *,
    profile: str,
    total_close_violations: int,
    total_wick_violations: int,
    maximum_violation: float,
    history_truncated: bool,
) -> float:
    close_penalty = 10.0 if profile == PROFILE_SENSITIVE else 16.0 if profile == PROFILE_SELECTIVE else 13.0
    wick_penalty = 3.0 if profile == PROFILE_SENSITIVE else 7.0 if profile == PROFILE_SELECTIVE else 5.0
    repeat_penalty = 10.0 if total_close_violations >= 2 else 0.0
    truncation_penalty = 4.0 if history_truncated else 0.0
    return clamp(
        float(total_close_violations) * close_penalty
        + float(total_wick_violations) * wick_penalty
        + maximum_violation * 18.0
        + repeat_penalty
        + truncation_penalty,
        0.0,
        70.0,
    )


class PatternGeometryEvaluator:
    """Pure geometry/quality layer ported from Pattern/Compression v0.4.6.

    Bar identifiers are zero-based chronological positions, matching the Pine
    `bar_index` arithmetic used by the source. Breakout/retest lifecycle is not part
    of this layer.
    """

    def __init__(
        self,
        *,
        store: PivotStore,
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
        atrs: Sequence[float | None],
        current_bar: int,
        safe_atr: float | None = None,
    ) -> None:
        if current_bar < 0:
            raise ValueError("current_bar must be non-negative")
        if not (len(highs) == len(lows) == len(closes) == len(atrs)):
            raise ValueError("OHLC/ATR series must have equal lengths")
        if current_bar >= len(highs):
            raise ValueError("current_bar is outside supplied series")
        self.store = store
        self.config = store.config
        self.profile = self.config.resolve()
        self.highs = highs
        self.lows = lows
        self.closes = closes
        self.atrs = atrs
        self.current_bar = current_bar
        fallback = atrs[current_bar] if atrs[current_bar] is not None else None
        self.safe_atr = max(
            float(safe_atr if safe_atr is not None else fallback if fallback is not None else self.config.min_tick * 10.0),
            self.config.min_tick * 10.0,
        )

    def _atr_at(self, bar: int) -> float:
        value = self.atrs[bar] if 0 <= bar < len(self.atrs) else None
        return max(float(value if value is not None else self.safe_atr), self.config.min_tick * 10.0)

    @property
    def maximum_accepted_historical_violation(self) -> float:
        return 0.90 if self.config.profile == PROFILE_SENSITIVE else 0.55 if self.config.profile == PROFILE_SELECTIVE else 0.72

    def boundary_violation_stats_range(
        self,
        *,
        upper_x1: int,
        upper_y1: float,
        upper_x2: int,
        upper_y2: float,
        lower_x1: int,
        lower_y1: float,
        lower_x2: int,
        lower_y2: float,
        geometry_start_bar: int,
        requested_start_bar: int,
        requested_end_bar: int,
        apply_maximum_window: bool,
    ) -> ViolationStats:
        safe_end_bar = min(requested_end_bar, self.current_bar)
        available_start_bar = max(requested_start_bar, max(0, self.current_bar - MAX_HISTORY_OFFSET))
        scan_start_bar = (
            max(available_start_bar, safe_end_bar - MAX_VIOLATION_SCAN + 1)
            if apply_maximum_window
            else available_start_bar
        )
        truncated = apply_maximum_window and scan_start_bar > geometry_start_bar

        upper_close = lower_close = upper_wick = lower_wick = 0
        max_upper = max_lower = 0.0
        scanned = 0
        if safe_end_bar >= scan_start_bar:
            for scan_bar in range(scan_start_bar, safe_end_bar + 1):
                historical_atr = self._atr_at(scan_bar)
                close_mult = 0.05 if self.config.profile == PROFILE_SENSITIVE else 0.07 if self.config.profile == PROFILE_SELECTIVE else 0.06
                wick_mult = 0.13 if self.config.profile == PROFILE_SENSITIVE else 0.18 if self.config.profile == PROFILE_SELECTIVE else 0.15
                close_buffer = max(self.config.min_tick * 2.0, historical_atr * close_mult)
                wick_buffer = max(self.config.min_tick * 2.0, historical_atr * wick_mult)
                upper_boundary = line_price(upper_x1, upper_y1, upper_x2, upper_y2, scan_bar)
                lower_boundary = line_price(lower_x1, lower_y1, lower_x2, lower_y2, scan_bar)

                upper_close_broken = self.closes[scan_bar] > upper_boundary + close_buffer
                lower_close_broken = self.closes[scan_bar] < lower_boundary - close_buffer
                upper_wick_broken = not upper_close_broken and self.highs[scan_bar] > upper_boundary + wick_buffer
                lower_wick_broken = not lower_close_broken and self.lows[scan_bar] < lower_boundary - wick_buffer

                if upper_close_broken:
                    upper_close += 1
                if lower_close_broken:
                    lower_close += 1
                if upper_wick_broken:
                    upper_wick += 1
                if lower_wick_broken:
                    lower_wick += 1

                upper_excess = (
                    (self.closes[scan_bar] - upper_boundary - close_buffer) / historical_atr
                    if upper_close_broken
                    else (self.highs[scan_bar] - upper_boundary - wick_buffer) / historical_atr
                    if upper_wick_broken
                    else 0.0
                )
                lower_excess = (
                    (lower_boundary - self.closes[scan_bar] - close_buffer) / historical_atr
                    if lower_close_broken
                    else (lower_boundary - self.lows[scan_bar] - wick_buffer) / historical_atr
                    if lower_wick_broken
                    else 0.0
                )
                max_upper = max(max_upper, upper_excess)
                max_lower = max(max_lower, lower_excess)
                scanned += 1

        penalty = violation_penalty_from_stats(
            profile=self.config.profile,
            total_close_violations=upper_close + lower_close,
            total_wick_violations=upper_wick + lower_wick,
            maximum_violation=max(max_upper, max_lower),
            history_truncated=truncated,
        )
        return ViolationStats(
            upper_close=upper_close,
            lower_close=lower_close,
            upper_wick=upper_wick,
            lower_wick=lower_wick,
            max_upper=max_upper,
            max_lower=max_lower,
            penalty=penalty,
            scanned_bars=scanned,
            truncated=truncated,
        )

    @staticmethod
    def _merge_violation_stats(first: ViolationStats, second: ViolationStats, *, profile: str) -> ViolationStats:
        upper_close = first.upper_close + second.upper_close
        lower_close = first.lower_close + second.lower_close
        upper_wick = first.upper_wick + second.upper_wick
        lower_wick = first.lower_wick + second.lower_wick
        max_upper = max(first.max_upper, second.max_upper)
        max_lower = max(first.max_lower, second.max_lower)
        truncated = first.truncated or second.truncated
        return ViolationStats(
            upper_close=upper_close,
            lower_close=lower_close,
            upper_wick=upper_wick,
            lower_wick=lower_wick,
            max_upper=max_upper,
            max_lower=max_lower,
            penalty=violation_penalty_from_stats(
                profile=profile,
                total_close_violations=upper_close + lower_close,
                total_wick_violations=upper_wick + lower_wick,
                maximum_violation=max(max_upper, max_lower),
                history_truncated=truncated,
            ),
            scanned_bars=first.scanned_bars + second.scanned_bars,
            truncated=truncated,
        )

    def analyze(self, *, high_a: int, high_b: int, low_a: int, low_b: int) -> PatternGeometryAnalysis:
        hp1 = self.store.high_prices[high_a]
        hp2 = self.store.high_prices[high_b]
        hb1 = self.store.high_bars[high_a]
        hb2 = self.store.high_bars[high_b]
        hc1 = self.store.high_confirm_bars[high_a]
        hc2 = self.store.high_confirm_bars[high_b]
        lp1 = self.store.low_prices[low_a]
        lp2 = self.store.low_prices[low_b]
        lb1 = self.store.low_bars[low_a]
        lb2 = self.store.low_bars[low_b]
        lc1 = self.store.low_confirm_bars[low_a]
        lc2 = self.store.low_confirm_bars[low_b]

        chronological = (hb1 < lb1 < hb2 < lb2) or (lb1 < hb1 < lb2 < hb2)
        start_bar = min(hb1, hb2, lb1, lb2)
        end_bar = max(hb1, hb2, lb1, lb2)
        known_bar = max(hc1, hc2, lc1, lc2)
        age = self.current_bar - start_bar
        candidate_end_bar = end_bar
        formed_duration = max(1, candidate_end_bar - start_bar)
        geometry_atr = max((self._atr_at(start_bar) + self._atr_at(candidate_end_bar)) * 0.5, self.config.min_tick * 10.0)
        touch_tolerance = max(self.config.min_tick * 2.0, geometry_atr * self.profile.touch_atr_mult)

        upper_start = line_price(hb1, hp1, hb2, hp2, start_bar)
        lower_start = line_price(lb1, lp1, lb2, lp2, start_bar)
        upper_now = line_price(hb1, hp1, hb2, hp2, self.current_bar)
        lower_now = line_price(lb1, lp1, lb2, lp2, self.current_bar)
        start_width = upper_start - lower_start
        current_width = upper_now - lower_now
        upper_slope = slope(hb1, hp1, hb2, hp2)
        lower_slope = slope(lb1, lp1, lb2, lp2)
        upper_slope_norm = upper_slope / geometry_atr
        lower_slope_norm = lower_slope / geometry_atr
        slope_gap_norm = upper_slope_norm - lower_slope_norm
        slope_gap = upper_slope - lower_slope
        apex_float = float(start_bar) - start_width / slope_gap if abs(slope_gap) > self.config.min_tick * 0.0001 else None
        apex_bar = pine_round_int(apex_float) if apex_float is not None else None
        contraction = (start_width - current_width) / start_width if start_width > self.config.min_tick else None
        apex_ok = apex_bar is not None and apex_bar > self.current_bar and apex_bar <= start_bar + max(self.profile.min_age * 8, 260)
        progress = (
            clamp(float(self.current_bar - start_bar) / max(1.0, float(apex_bar - start_bar)), 0.0, 2.0)
            if apex_ok and apex_bar is not None
            else clamp(float(age) / max(1.0, float(self.profile.max_consolidation_bars)), 0.0, 2.0)
        )
        top_above = upper_start > lower_start and upper_now > lower_now
        converging = (
            start_width > self.config.min_tick
            and current_width > self.config.min_tick
            and contraction is not None
            and contraction >= self.profile.min_contraction
            and slope_gap_norm < -MIN_SLOPE_NORM_TOL
        )
        parallel_like = abs(slope_gap_norm) <= self.profile.parallel_slope_norm_tol

        upper_touches, upper_avg_distance, upper_first, upper_last = self.store.touch_stats(
            side="high",
            x1=hb1,
            y1=hp1,
            x2=hb2,
            y2=hp2,
            start_bar=start_bar,
            end_bar=self.current_bar,
            tolerance=touch_tolerance,
        )
        lower_touches, lower_avg_distance, lower_first, lower_last = self.store.touch_stats(
            side="low",
            x1=lb1,
            y1=lp1,
            x2=lb2,
            y2=lp2,
            start_bar=start_bar,
            end_bar=self.current_bar,
            tolerance=touch_tolerance,
        )
        total_touches = upper_touches + lower_touches
        touch_distribution = (
            total_touches >= 4
            and upper_first is not None
            and lower_first is not None
            and min(upper_last if upper_last is not None else start_bar, lower_last if lower_last is not None else start_bar) - start_bar
            >= max(self.profile.min_age / 2.0, self.profile.min_touch_gap * 2)
        )
        touch_basics = (
            chronological
            and self.current_bar >= known_bar
            and top_above
            and age >= max(5.0, self.profile.min_age / 2.0)
            and upper_touches >= 2
            and lower_touches >= 2
            and touch_distribution
        )

        violation_up = max(0.0, self.highs[self.current_bar] - upper_now) / max(touch_tolerance, self.config.min_tick)
        violation_down = max(0.0, lower_now - self.lows[self.current_bar]) / max(touch_tolerance, self.config.min_tick)
        violation = max(violation_up, violation_down)
        highs_lower = pivot_trend_ok(hp1, hp2, rising=False, tolerance=touch_tolerance)
        lows_higher = pivot_trend_ok(lp1, lp2, rising=True, tolerance=touch_tolerance)

        generic_type = classify_generic_pattern(
            touch_basics=touch_basics,
            converging=converging,
            apex_ok=apex_ok,
            parallel_like=parallel_like,
            upper_slope_norm=upper_slope_norm,
            lower_slope_norm=lower_slope_norm,
            flat_slope_norm_tol=self.profile.flat_slope_norm_tol,
            highs_lower=highs_lower,
            lows_higher=lows_higher,
        )
        generic_supported = generic_type != PATTERN_NONE
        parallel_supported = touch_basics and parallel_like and not converging and start_width > self.config.min_tick and current_width > self.config.min_tick
        pre_geometry_score = clamp(
            (22.0 if chronological else 0.0)
            + (18.0 if top_above else 0.0)
            + (24.0 if upper_touches >= 2 and lower_touches >= 2 else 0.0)
            + (14.0 if touch_distribution else 0.0)
            + (22.0 if generic_supported or parallel_supported else 0.0),
            0.0,
            100.0,
        )
        violation_scan_eligible = touch_basics and (generic_supported or parallel_supported) and pre_geometry_score >= 55.0
        violation_scan_end_bar = max(start_bar, candidate_end_bar)
        historical = ViolationStats()
        if violation_scan_eligible:
            historical = self.boundary_violation_stats_range(
                upper_x1=hb1,
                upper_y1=hp1,
                upper_x2=hb2,
                upper_y2=hp2,
                lower_x1=lb1,
                lower_y1=lp1,
                lower_x2=lb2,
                lower_y2=lp2,
                geometry_start_bar=start_bar,
                requested_start_bar=start_bar,
                requested_end_bar=violation_scan_end_bar,
                apply_maximum_window=True,
            )

        historical_acceptable = (
            violation_scan_eligible
            and historical.close_count < 2
            and historical.max_violation <= self.maximum_accepted_historical_violation
            and historical.penalty < 62.0
        )

        post_start = candidate_end_bar + 1
        post_end = max(candidate_end_bar, self.current_bar - 1)
        post = ViolationStats()
        post_survival = True
        combined = historical
        if historical_acceptable and post_end >= post_start:
            post = self.boundary_violation_stats_range(
                upper_x1=hb1,
                upper_y1=hp1,
                upper_x2=hb2,
                upper_y2=hp2,
                lower_x1=lb1,
                lower_y1=lp1,
                lower_x2=lb2,
                lower_y2=lp2,
                geometry_start_bar=start_bar,
                requested_start_bar=post_start,
                requested_end_bar=post_end,
                apply_maximum_window=False,
            )
            post_survival = (
                post.close_count == 0
                and post.max_violation <= self.maximum_accepted_historical_violation
                and post.penalty < 62.0
            )
            combined = self._merge_violation_stats(historical, post, profile=self.config.profile)

        historical_acceptable = historical_acceptable and post_survival and combined.penalty < 62.0

        contraction_score = 0.0 if generic_type == PATTERN_NONE else contraction_quality(contraction, self.profile.min_contraction)
        flat_tol = self.profile.flat_slope_norm_tol
        upper_flat_quality = inverse_smoothstep(flat_tol * 0.55, flat_tol * 1.65, abs(upper_slope_norm)) * 100.0
        lower_flat_quality = inverse_smoothstep(flat_tol * 0.55, flat_tol * 1.65, abs(lower_slope_norm)) * 100.0
        upper_down_quality = smoothstep(flat_tol * 0.70, flat_tol * 4.50, -upper_slope_norm) * 100.0
        upper_up_quality = smoothstep(flat_tol * 0.70, flat_tol * 4.50, upper_slope_norm) * 100.0
        lower_up_quality = smoothstep(flat_tol * 0.70, flat_tol * 4.50, lower_slope_norm) * 100.0
        lower_down_quality = smoothstep(flat_tol * 0.70, flat_tol * 4.50, -lower_slope_norm) * 100.0

        if generic_type == PATTERN_ASCENDING_TRIANGLE:
            slope_shape_quality = upper_flat_quality * 0.52 + lower_up_quality * 0.48
        elif generic_type == PATTERN_DESCENDING_TRIANGLE:
            slope_shape_quality = lower_flat_quality * 0.52 + upper_down_quality * 0.48
        elif generic_type == PATTERN_SYMMETRICAL_TRIANGLE:
            slope_shape_quality = upper_down_quality * 0.50 + lower_up_quality * 0.50
        elif generic_type == PATTERN_RISING_WEDGE:
            slope_shape_quality = (
                upper_up_quality * 0.42
                + lower_up_quality * 0.42
                + smoothstep(MIN_SLOPE_NORM_TOL, flat_tol * 2.5, lower_slope_norm - upper_slope_norm) * 16.0
            )
        elif generic_type == PATTERN_FALLING_WEDGE:
            slope_shape_quality = (
                upper_down_quality * 0.42
                + lower_down_quality * 0.42
                + smoothstep(MIN_SLOPE_NORM_TOL, flat_tol * 2.5, upper_slope_norm - lower_slope_norm) * 16.0
            )
        else:
            slope_shape_quality = 0.0

        geometry_score = 0.0 if generic_type == PATTERN_NONE else clamp(slope_shape_quality * 0.65 + contraction_score * 0.35, 0.0, 100.0)
        average_touch_distance = (upper_avg_distance + lower_avg_distance) * 0.5
        touch_precision_quality = inverse_smoothstep(0.15, 1.10, average_touch_distance) * 100.0
        touch_count_quality = smoothstep(4.0, 7.0, float(total_touches)) * 100.0
        weakest_last_touch = min(upper_last if upper_last is not None else start_bar, lower_last if lower_last is not None else start_bar)
        touch_span_ratio = float(weakest_last_touch - start_bar) / max(1.0, float(age))
        touch_span_quality = smoothstep(0.35, 0.78, touch_span_ratio) * 100.0
        touch_score = (
            clamp(touch_precision_quality * 0.42 + touch_count_quality * 0.33 + touch_span_quality * 0.25, 0.0, 100.0)
            if touch_basics
            else 0.0
        )
        maturity_score = (
            0.0
            if generic_type == PATTERN_NONE
            else clamp(age_quality(age, self.profile.min_age) * 0.58 + progress_quality(progress) * 0.42, 0.0, 100.0)
        )
        cleanliness_score = cleanliness_quality(combined.penalty)
        generic_raw = (
            0.0
            if generic_type == PATTERN_NONE or not historical_acceptable
            else clamp(geometry_score * 0.38 + touch_score * 0.32 + maturity_score * 0.18 + cleanliness_score * 0.12, 0.0, 100.0)
        )
        accepted = generic_type != PATTERN_NONE and historical_acceptable and post_survival and generic_raw >= self.profile.min_raw_quality

        candidate = PatternCandidate(
            valid=accepted,
            pattern_type=generic_type,
            family=("Kama" if generic_type in {PATTERN_RISING_WEDGE, PATTERN_FALLING_WEDGE} else "Üçgen" if generic_type != PATTERN_NONE else PATTERN_NONE),
            classic_dir=classic_direction(generic_type),
            raw_quality=generic_raw,
            geometry_score=geometry_score,
            geometry_atr=geometry_atr,
            slope_shape_score=slope_shape_quality,
            touch_score=touch_score,
            contraction_score=contraction_score,
            maturity_score=maturity_score,
            violation=violation,
            historical_upper_close_violations=combined.upper_close,
            historical_lower_close_violations=combined.lower_close,
            historical_upper_wick_violations=combined.upper_wick,
            historical_lower_wick_violations=combined.lower_wick,
            historical_close_violations=combined.close_count,
            historical_wick_violations=combined.wick_count,
            max_historical_violation=combined.max_violation,
            historical_violation_penalty=combined.penalty,
            historical_scanned_bars=combined.scanned_bars,
            violation_history_truncated=combined.truncated,
            last_violation_processed_bar=(max(violation_scan_end_bar, post_end) if violation_scan_eligible else None),
            violation_geometry_key=f"{hb1}:{hb2}:{lb1}:{lb2}",
            violation_scan_mode=("Tam+Survival" if post.scanned_bars > 0 else "Tam") if violation_scan_eligible else "Atlandı",
            upper_touches=upper_touches,
            lower_touches=lower_touches,
            start_bar=start_bar,
            end_bar=end_bar,
            known_bar=known_bar,
            apex_bar=apex_bar,
            progress=progress,
            hb1=hb1,
            hp1=hp1,
            hb2=hb2,
            hp2=hp2,
            lb1=lb1,
            lp1=lp1,
            lb2=lb2,
            lp2=lp2,
            upper_slope=upper_slope,
            lower_slope=lower_slope,
            start_width=start_width,
            current_width=current_width,
            contraction=contraction,
            upper_now=upper_now,
            lower_now=lower_now,
        )
        return PatternGeometryAnalysis(
            candidate=candidate,
            chronological=chronological,
            touch_basics=touch_basics,
            touch_distribution=touch_distribution,
            generic_type=generic_type,
            generic_geometry_supported=generic_supported,
            parallel_geometry_supported=parallel_supported,
            historical_geometry_acceptable=historical_acceptable,
            post_pivot_survival_passed=post_survival,
            pre_geometry_score=pre_geometry_score,
            cleanliness_score=cleanliness_score,
            upper_slope_norm=upper_slope_norm,
            lower_slope_norm=lower_slope_norm,
            slope_gap_norm=slope_gap_norm,
            parallel_like=parallel_like,
            converging=converging,
            formed_duration=formed_duration,
        )
