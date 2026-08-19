from __future__ import annotations

import copy
from collections.abc import Sequence

from .pattern_compression_core import (
    MAX_HISTORY_OFFSET,
    MAX_PATH_SAMPLE,
    PATTERN_ASCENDING_TRIANGLE,
    PATTERN_BEAR_FLAG,
    PATTERN_BEAR_PENNANT,
    PATTERN_BULL_FLAG,
    PATTERN_BULL_PENNANT,
    PATTERN_DESCENDING_TRIANGLE,
    PATTERN_NONE,
    PATTERN_SYMMETRICAL_TRIANGLE,
    PatternCandidate,
    PatternCompressionConfig,
    PivotStore,
    PoleInfo,
    band_quality,
    clamp,
    inverse_smoothstep,
    is_flag,
    is_pennant,
    line_price,
)
from .pattern_compression_geometry import PatternGeometryAnalysis


def depth_quality(depth: float | None) -> float:
    return 0.0 if depth is None else band_quality(depth, 0.03, 0.16, 0.45, 0.82)


def duration_quality(duration_ratio: float | None) -> float:
    return 0.0 if duration_ratio is None else band_quality(duration_ratio, 0.12, 0.35, 1.55, 3.60)


def is_specialized(pattern_type: str) -> bool:
    return is_flag(pattern_type) or is_pennant(pattern_type)


class PatternPoleEvaluator:
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
        if not (len(highs) == len(lows) == len(closes) == len(atrs)):
            raise ValueError("OHLC/ATR series must have equal lengths")
        self.store = store
        self.config = store.config
        self.profile = self.config.resolve()
        self.highs = highs
        self.lows = lows
        self.closes = closes
        self.atrs = atrs
        self.current_bar = current_bar
        fallback = atrs[current_bar] if 0 <= current_bar < len(atrs) else None
        self.safe_atr = max(
            float(safe_atr if safe_atr is not None else fallback if fallback is not None else self.config.min_tick * 10.0),
            self.config.min_tick * 10.0,
        )

    def _atr_at(self, bar: int) -> float:
        value = self.atrs[bar] if 0 <= bar < len(self.atrs) else None
        return max(float(value if value is not None else self.safe_atr), self.config.min_tick * 10.0)

    def path_stats(self, *, start_bar: int, end_bar: int, start_price: float, end_price: float) -> tuple[float, float, float, float]:
        duration = max(0, end_bar - start_bar)
        sample_bars = min(duration, MAX_PATH_SAMPLE)
        total_path = 0.0
        maximum_range = 0.0
        if sample_bars > 0 and end_bar <= self.current_bar:
            for step in range(sample_bars):
                absolute_bar = end_bar - step
                if absolute_bar >= 1:
                    total_path += abs(self.closes[absolute_bar] - self.closes[absolute_bar - 1])
                    maximum_range = max(maximum_range, self.highs[absolute_bar] - self.lows[absolute_bar])
        net_move = abs(end_price - start_price)
        efficiency = clamp(net_move / max(total_path, net_move, self.config.min_tick), 0.0, 1.0)
        return net_move, total_path, efficiency, maximum_range

    def local_extreme_break(self, *, start_bar: int, end_price: float, direction: int, reference_tolerance: float) -> bool:
        found_reference = False
        if direction == 1:
            previous_high: float | None = None
            for pivot_bar, pivot_price in zip(self.store.high_bars, self.store.high_prices, strict=True):
                if pivot_bar < start_bar and pivot_bar >= start_bar - self.profile.max_pole_bars * 3:
                    previous_high = pivot_price if previous_high is None else max(previous_high, pivot_price)
                    found_reference = True
            return bool(found_reference and previous_high is not None and end_price > previous_high + reference_tolerance)

        previous_low: float | None = None
        for pivot_bar, pivot_price in zip(self.store.low_bars, self.store.low_prices, strict=True):
            if pivot_bar < start_bar and pivot_bar >= start_bar - self.profile.max_pole_bars * 3:
                previous_low = pivot_price if previous_low is None else min(previous_low, pivot_price)
                found_reference = True
        return bool(found_reference and previous_low is not None and end_price < previous_low - reference_tolerance)

    def find_pole(self, *, end_bar: int, end_price: float, direction: int) -> PoleInfo:
        best = PoleInfo()
        prices = self.store.low_prices if direction == 1 else self.store.high_prices
        bars = self.store.low_bars if direction == 1 else self.store.high_bars
        history_available = end_bar <= self.current_bar and self.current_bar - end_bar <= MAX_HISTORY_OFFSET - self.profile.max_pole_bars
        if not history_available:
            return best

        for start_bar, start_price in zip(bars, prices, strict=True):
            duration = end_bar - start_bar
            if not (start_bar < end_bar and 1 <= duration <= self.profile.max_pole_bars):
                continue
            magnitude = abs(end_price - start_price)
            _, _, efficiency, maximum_range = self.path_stats(
                start_bar=start_bar,
                end_bar=end_bar,
                start_price=start_price,
                end_price=end_price,
            )
            pole_atr = self._atr_at(end_bar)
            atr_units = magnitude / pole_atr
            speed = atr_units / duration if duration > 0 else 0.0
            directional = end_price > start_price if direction == 1 else end_price < start_price
            local_break_tolerance = max(self.config.min_tick * 2.0, pole_atr * self.profile.touch_atr_mult)
            local_break = directional and self.local_extreme_break(
                start_bar=start_bar,
                end_price=end_price,
                direction=direction,
                reference_tolerance=local_break_tolerance,
            )
            single_shock = duration <= 1 or maximum_range >= magnitude * 0.72
            magnitude_score = clamp(atr_units / max(self.profile.min_pole_atr, 0.1) * 65.0, 0.0, 100.0)
            efficiency_score = clamp((efficiency - 0.30) / 0.70 * 100.0, 0.0, 100.0)
            duration_score = 100.0 if duration >= 2 else 35.0
            speed_score = clamp(speed / 0.22 * 100.0, 0.0, 100.0)
            uncapped_quality = clamp(
                magnitude_score * 0.30
                + efficiency_score * 0.30
                + duration_score * 0.16
                + speed_score * 0.12
                + (12.0 if local_break else 0.0)
                - (24.0 if single_shock else 0.0),
                0.0,
                100.0,
            )
            quality = min(uncapped_quality, 46.0) if single_shock else uncapped_quality
            valid = (
                directional
                and atr_units >= self.profile.min_pole_atr
                and efficiency >= self.profile.min_pole_efficiency
                and quality >= self.profile.min_pole_quality
            )
            outranks = (valid and not best.valid) or (valid == best.valid and quality > best.quality)
            if outranks:
                best = PoleInfo(
                    valid=valid,
                    direction=direction,
                    start_bar=start_bar,
                    start_price=start_price,
                    end_bar=end_bar,
                    end_price=end_price,
                    duration=duration,
                    magnitude=magnitude,
                    efficiency=efficiency,
                    quality=quality,
                )
        return best


class SpecializedPatternEvaluator:
    def __init__(
        self,
        *,
        store: PivotStore,
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
    ) -> None:
        if not (len(highs) == len(lows) == len(closes)):
            raise ValueError("price series must have equal lengths")
        self.store = store
        self.config: PatternCompressionConfig = store.config
        self.profile = self.config.resolve()
        self.highs = highs
        self.lows = lows
        self.closes = closes

    def range_between(self, *, start_bar: int, end_bar: int, maximum_bars: int = MAX_PATH_SAMPLE) -> tuple[float, float]:
        bounded_end = min(end_bar, len(self.highs) - 1)
        available_bars = min(max(bounded_end - start_bar, 0), maximum_bars)
        range_high = self.highs[bounded_end]
        range_low = self.lows[bounded_end]
        if available_bars > 0:
            for step in range(available_bars):
                absolute_bar = bounded_end - step
                range_high = max(range_high, self.highs[absolute_bar])
                range_low = min(range_low, self.lows[absolute_bar])
        return range_high, range_low

    @staticmethod
    def _copy_pole(candidate: PatternCandidate, pole: PoleInfo) -> None:
        candidate.has_pole = pole.valid
        candidate.pole_dir = pole.direction
        candidate.pole_start_bar = pole.start_bar
        candidate.pole_start_price = pole.start_price
        candidate.pole_end_bar = pole.end_bar
        candidate.pole_end_price = pole.end_price
        candidate.pole_duration = pole.duration
        candidate.pole_magnitude = pole.magnitude
        candidate.pole_efficiency = pole.efficiency
        candidate.pole_quality = pole.quality

    def apply(
        self,
        *,
        analysis: PatternGeometryAnalysis,
        bull_pole: PoleInfo,
        bear_pole: PoleInfo,
    ) -> PatternCandidate:
        base = analysis.candidate
        candidate = copy.deepcopy(base)
        required = (base.hb1, base.hp1, base.hb2, base.hp2, base.lb1, base.lp1, base.lb2, base.lp2, base.start_bar, base.end_bar)
        if any(value is None for value in required):
            return candidate

        hb1, hp1, hb2, hp2 = int(base.hb1), float(base.hp1), int(base.hb2), float(base.hp2)
        lb1, lp1, lb2, lp2 = int(base.lb1), float(base.lp1), int(base.lb2), float(base.lp2)
        start_bar = int(base.start_bar)
        end_bar = int(base.end_bar)
        geometry_can_use_pole = analysis.touch_basics and analysis.historical_geometry_acceptable and (
            analysis.parallel_like
            or analysis.generic_type in {PATTERN_SYMMETRICAL_TRIANGLE, PATTERN_ASCENDING_TRIANGLE, PATTERN_DESCENDING_TRIANGLE}
        )
        bull_sequence_compatible = analysis.chronological and hb1 < lb1
        bear_sequence_compatible = analysis.chronological and lb1 < hb1
        max_pole_link_bars = max(self.profile.pivot_len * 2, self.profile.min_touch_gap + 2)
        bull_linked = (
            geometry_can_use_pole
            and bull_sequence_compatible
            and bull_pole.valid
            and bull_pole.end_bar is not None
            and abs(bull_pole.end_bar - start_bar) <= max_pole_link_bars
        )
        bear_linked = (
            geometry_can_use_pole
            and bear_sequence_compatible
            and bear_pole.valid
            and bear_pole.end_bar is not None
            and abs(bear_pole.end_bar - start_bar) <= max_pole_link_bars
        )

        bull_observed_high = max(hp1, hp2)
        bull_observed_low = min(lp1, lp2)
        bear_observed_high = bull_observed_high
        bear_observed_low = bull_observed_low
        if bull_linked and bull_pole.end_bar is not None:
            bull_observed_high, bull_observed_low = self.range_between(start_bar=bull_pole.end_bar, end_bar=end_bar)
        if bear_linked and bear_pole.end_bar is not None:
            bear_observed_high, bear_observed_low = self.range_between(start_bar=bear_pole.end_bar, end_bar=end_bar)

        base_low = min(lp1, lp2)
        base_high = max(hp1, hp2)
        bull_low = min(base_low, bull_observed_low)
        bull_high = max(base_high, bull_observed_high)
        bear_low = min(base_low, bear_observed_low)
        bear_high = max(base_high, bear_observed_high)
        base_height = max(base_high - base_low, self.config.min_tick)
        bull_height = max(bull_high - bull_low, self.config.min_tick)
        bear_height = max(bear_high - bear_low, self.config.min_tick)
        center_start = (line_price(hb1, hp1, hb2, hp2, start_bar) + line_price(lb1, lp1, lb2, lp2, start_bar)) * 0.5
        center_end = (line_price(hb1, hp1, hb2, hp2, end_bar) + line_price(lb1, lp1, lb2, lp2, end_bar)) * 0.5
        pivot_path = abs(hp2 - hp1) + abs(lp2 - lp1) + base_height
        consolidation_efficiency = clamp(abs(center_end - center_start) / max(pivot_path, self.config.min_tick), 0.0, 1.0)

        formed_duration = analysis.formed_duration
        bull_magnitude = max(float(bull_pole.magnitude or 0.0), self.config.min_tick)
        bear_magnitude = max(float(bear_pole.magnitude or 0.0), self.config.min_tick)
        bull_depth = clamp((float(bull_pole.end_price) - bull_low) / bull_magnitude, 0.0, 2.0) if bull_linked and bull_pole.end_price is not None else None
        bear_depth = clamp((bear_high - float(bear_pole.end_price)) / bear_magnitude, 0.0, 2.0) if bear_linked and bear_pole.end_price is not None else None
        bull_duration_ratio = float(formed_duration) / max(1.0, float(bull_pole.duration)) if bull_linked else None
        bear_duration_ratio = float(formed_duration) / max(1.0, float(bear_pole.duration)) if bear_linked else None
        bull_height_ratio = bull_height / bull_magnitude if bull_linked else None
        bear_height_ratio = bear_height / bear_magnitude if bear_linked else None

        average_slope_norm = (analysis.upper_slope_norm + analysis.lower_slope_norm) * 0.5
        parallel_quality = (
            inverse_smoothstep(
                self.profile.parallel_slope_norm_tol * 0.30,
                self.profile.parallel_slope_norm_tol * 1.05,
                abs(analysis.slope_gap_norm),
            )
            * 100.0
            if analysis.parallel_like
            else 0.0
        )
        bull_flag_slope = average_slope_norm <= self.profile.flat_slope_norm_tol * 0.35 and average_slope_norm >= -0.16
        bear_flag_slope = average_slope_norm >= -self.profile.flat_slope_norm_tol * 0.35 and average_slope_norm <= 0.16
        bull_flag_geometry = analysis.touch_basics and analysis.parallel_like and bull_flag_slope and not analysis.converging
        bear_flag_geometry = analysis.touch_basics and analysis.parallel_like and bear_flag_slope and not analysis.converging

        bull_eff = float(bull_pole.efficiency or 0.0)
        bear_eff = float(bear_pole.efficiency or 0.0)
        bull_flag_valid = bool(
            bull_flag_geometry
            and bull_linked
            and bull_depth is not None
            and bull_height_ratio is not None
            and bull_duration_ratio is not None
            and 0.08 <= bull_depth <= 0.80
            and bull_height_ratio <= 0.58
            and bull_duration_ratio <= 3.50
            and formed_duration <= self.profile.max_consolidation_bars
            and consolidation_efficiency < bull_eff + 0.10
        )
        bear_flag_valid = bool(
            bear_flag_geometry
            and bear_linked
            and bear_depth is not None
            and bear_height_ratio is not None
            and bear_duration_ratio is not None
            and 0.08 <= bear_depth <= 0.80
            and bear_height_ratio <= 0.58
            and bear_duration_ratio <= 3.50
            and formed_duration <= self.profile.max_consolidation_bars
            and consolidation_efficiency < bear_eff + 0.10
        )
        bull_calmness = 100.0 if bull_linked and consolidation_efficiency <= bull_eff else inverse_smoothstep(0.00, 0.28, consolidation_efficiency - bull_eff) * 100.0 if bull_linked else 0.0
        bear_calmness = 100.0 if bear_linked and consolidation_efficiency <= bear_eff else inverse_smoothstep(0.00, 0.28, consolidation_efficiency - bear_eff) * 100.0 if bear_linked else 0.0
        cleanliness = analysis.cleanliness_score
        bull_flag_quality = (
            clamp(
                bull_pole.quality * 0.28
                + depth_quality(bull_depth) * 0.20
                + duration_quality(bull_duration_ratio) * 0.12
                + parallel_quality * 0.16
                + base.touch_score * 0.12
                + bull_calmness * 0.07
                + cleanliness * 0.05,
                0.0,
                100.0,
            )
            if bull_flag_valid
            else 0.0
        )
        bear_flag_quality = (
            clamp(
                bear_pole.quality * 0.28
                + depth_quality(bear_depth) * 0.20
                + duration_quality(bear_duration_ratio) * 0.12
                + parallel_quality * 0.16
                + base.touch_score * 0.12
                + bear_calmness * 0.07
                + cleanliness * 0.05,
                0.0,
                100.0,
            )
            if bear_flag_valid
            else 0.0
        )

        standard_pennant = analysis.generic_type == PATTERN_SYMMETRICAL_TRIANGLE
        bull_inclined_pennant = analysis.generic_type == PATTERN_ASCENDING_TRIANGLE
        bear_inclined_pennant = analysis.generic_type == PATTERN_DESCENDING_TRIANGLE
        bull_standard_base = bool(
            standard_pennant and bull_linked and bull_depth is not None and bull_height_ratio is not None and bull_duration_ratio is not None
            and 0.06 <= bull_depth <= 0.70 and bull_height_ratio <= 0.46 and bull_duration_ratio <= 2.80
            and formed_duration <= self.profile.max_consolidation_bars and consolidation_efficiency < bull_eff + 0.08
        )
        bear_standard_base = bool(
            standard_pennant and bear_linked and bear_depth is not None and bear_height_ratio is not None and bear_duration_ratio is not None
            and 0.06 <= bear_depth <= 0.70 and bear_height_ratio <= 0.46 and bear_duration_ratio <= 2.80
            and formed_duration <= self.profile.max_consolidation_bars and consolidation_efficiency < bear_eff + 0.08
        )
        max_inclined_duration = int(round(float(self.profile.max_consolidation_bars) * 0.85))
        bull_inclined_base = bool(
            bull_inclined_pennant and bull_linked and bull_depth is not None and bull_height_ratio is not None and bull_duration_ratio is not None
            and bull_pole.quality >= self.profile.min_pole_quality + 10.0
            and 0.08 <= bull_depth <= 0.60 and bull_height_ratio <= 0.40 and bull_duration_ratio <= 2.40
            and formed_duration <= max_inclined_duration and consolidation_efficiency < bull_eff
        )
        bear_inclined_base = bool(
            bear_inclined_pennant and bear_linked and bear_depth is not None and bear_height_ratio is not None and bear_duration_ratio is not None
            and bear_pole.quality >= self.profile.min_pole_quality + 10.0
            and 0.08 <= bear_depth <= 0.60 and bear_height_ratio <= 0.40 and bear_duration_ratio <= 2.40
            and formed_duration <= max_inclined_duration and consolidation_efficiency < bear_eff
        )
        bull_pennant_base = bull_standard_base or bull_inclined_base
        bear_pennant_base = bear_standard_base or bear_inclined_base
        bull_pennant_pre = (
            clamp(
                bull_pole.quality * 0.26
                + depth_quality(bull_depth) * 0.18
                + duration_quality(bull_duration_ratio) * 0.12
                + base.geometry_score * 0.20
                + base.touch_score * 0.10
                + bull_calmness * 0.07
                + cleanliness * 0.07,
                0.0,
                100.0,
            )
            if bull_pennant_base
            else 0.0
        )
        bear_pennant_pre = (
            clamp(
                bear_pole.quality * 0.26
                + depth_quality(bear_depth) * 0.18
                + duration_quality(bear_duration_ratio) * 0.12
                + base.geometry_score * 0.20
                + base.touch_score * 0.10
                + bear_calmness * 0.07
                + cleanliness * 0.07,
                0.0,
                100.0,
            )
            if bear_pennant_base
            else 0.0
        )
        bull_pennant_valid = bull_pennant_pre >= (self.profile.min_specialized_quality if bull_standard_base else self.profile.min_specialized_quality + 8.0) if bull_pennant_base else False
        bear_pennant_valid = bear_pennant_pre >= (self.profile.min_specialized_quality if bear_standard_base else self.profile.min_specialized_quality + 8.0) if bear_pennant_base else False
        bull_pennant_quality = bull_pennant_pre if bull_pennant_valid else 0.0
        bear_pennant_quality = bear_pennant_pre if bear_pennant_valid else 0.0

        selected_type = base.pattern_type
        selected_family = base.family
        selected_raw = base.raw_quality
        selected_pole: PoleInfo | None = None
        selected_depth: float | None = None
        selected_duration: float | None = None
        selected_height: float | None = None
        best_specialized = max(bull_flag_quality, bear_flag_quality, bull_pennant_quality, bear_pennant_quality)
        if best_specialized >= self.profile.min_specialized_quality:
            if best_specialized == bull_flag_quality:
                selected_type, selected_family, selected_raw = PATTERN_BULL_FLAG, "Bayrak", bull_flag_quality
                selected_pole, selected_depth, selected_duration, selected_height = bull_pole, bull_depth, bull_duration_ratio, bull_height_ratio
                candidate.geometry_score = parallel_quality
                candidate.contraction_score = 0.0
            elif best_specialized == bear_flag_quality:
                selected_type, selected_family, selected_raw = PATTERN_BEAR_FLAG, "Bayrak", bear_flag_quality
                selected_pole, selected_depth, selected_duration, selected_height = bear_pole, bear_depth, bear_duration_ratio, bear_height_ratio
                candidate.geometry_score = parallel_quality
                candidate.contraction_score = 0.0
            elif best_specialized == bull_pennant_quality:
                selected_type, selected_family, selected_raw = PATTERN_BULL_PENNANT, "Flama", bull_pennant_quality
                selected_pole, selected_depth, selected_duration, selected_height = bull_pole, bull_depth, bull_duration_ratio, bull_height_ratio
            else:
                selected_type, selected_family, selected_raw = PATTERN_BEAR_PENNANT, "Flama", bear_pennant_quality
                selected_pole, selected_depth, selected_duration, selected_height = bear_pole, bear_depth, bear_duration_ratio, bear_height_ratio

        candidate.pattern_type = selected_type
        candidate.family = selected_family
        candidate.classic_dir = 1 if selected_type in {PATTERN_BULL_FLAG, PATTERN_BULL_PENNANT} else -1 if selected_type in {PATTERN_BEAR_FLAG, PATTERN_BEAR_PENNANT} else base.classic_dir
        candidate.raw_quality = selected_raw
        candidate.slope_shape_score = parallel_quality if is_flag(selected_type) else base.slope_shape_score
        candidate.correction_depth = selected_depth
        candidate.duration_ratio = selected_duration
        candidate.consolidation_efficiency = consolidation_efficiency
        candidate.consolidation_height_ratio = selected_height
        candidate.valid = (
            selected_type != PATTERN_NONE
            and analysis.historical_geometry_acceptable
            and analysis.post_pivot_survival_passed
            and selected_raw >= (self.profile.min_specialized_quality if is_specialized(selected_type) else self.profile.min_raw_quality)
        )
        if selected_pole is not None and is_specialized(selected_type):
            self._copy_pole(candidate, selected_pole)
        return candidate
