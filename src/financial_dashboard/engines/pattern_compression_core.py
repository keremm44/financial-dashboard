from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


PROFILE_SENSITIVE = "Hassas"
PROFILE_BALANCED = "Dengeli"
PROFILE_SELECTIVE = "Seçici"
PROFILE_VALUES = (PROFILE_SENSITIVE, PROFILE_BALANCED, PROFILE_SELECTIVE)

ST_NONE = "FORMASYON_YOK"
ST_CANDIDATE = "ADAY_OLUSUYOR"
ST_GEOMETRY = "GEOMETRI_ADAYI"
ST_DEFINED = "FORMASYON_TANIMLANDI"
ST_MATURING = "OLGUNLASIYOR"
ST_COMPRESSING = "SIKISMA_GUCLENIYOR"
ST_PREP = "KIRILIM_HAZIRLIGI"
ST_BREAK_ATTEMPT = "KIRILIM_DENEMESI"
ST_BREAK_CANDIDATE = "KIRILIM_ADAYI"
ST_BREAK_CONFIRMED = "KIRILIM_TEYITLI"
ST_RETEST_WAIT = "RETEST_BEKLENIYOR"
ST_RETESTING = "RETEST_EDILIYOR"
ST_RETEST_OK = "RETEST_BASARILI"
ST_BREAK_TIMEOUT = "KIRILIM_TEYIT_ALAMADI"
ST_BREAK_FAILED = "BASARISIZ_KIRILIM"
ST_WEAK = "FORMASYON_ZAYIFLADI"
ST_INVALID = "FORMASYON_GECERSIZ"
ST_COMPLETED = "FORMASYON_TAMAMLANDI"

PATTERN_ASCENDING_TRIANGLE = "Yükselen Üçgen"
PATTERN_DESCENDING_TRIANGLE = "Alçalan Üçgen"
PATTERN_SYMMETRICAL_TRIANGLE = "Simetrik Üçgen"
PATTERN_RISING_WEDGE = "Yükselen Kama"
PATTERN_FALLING_WEDGE = "Alçalan Kama"
PATTERN_BULL_FLAG = "Boğa Bayrağı"
PATTERN_BEAR_FLAG = "Ayı Bayrağı"
PATTERN_BULL_PENNANT = "Boğa Flaması"
PATTERN_BEAR_PENNANT = "Ayı Flaması"
PATTERN_NONE = "Yok"

PATTERN_EXPORT_TITLES = (
    "ARGENT | PATTERN | STATE",
    "ARGENT | PATTERN | TYPE",
    "ARGENT | PATTERN | QUALITY",
    "ARGENT | PATTERN | CLASSIC_DIRECTION",
    "ARGENT | PATTERN | BREAK_STATE",
    "ARGENT | PATTERN | BREAK_LEVEL",
    "ARGENT | PATTERN | BREAK_STRENGTH",
    "ARGENT | PATTERN | RETEST_STATE",
    "ARGENT | PATTERN | RETEST_TOLERANCE",
    "ARGENT | PATTERN | IDENTITY",
)

MAX_PIVOTS = 24
SEARCH_PIVOTS = 6
MAX_PATH_SAMPLE = 120
MAX_VIOLATION_SCAN = 220
MAX_VIOLATION_CACHE = 100
MAX_HISTORY_OFFSET = 900
MIN_SLOPE_NORM_TOL = 0.006
ATR_LENGTH = 14


@dataclass(frozen=True, slots=True)
class PatternCompressionProfile:
    name: str
    pivot_len: int
    min_age: int
    min_touch_gap: int
    touch_atr_mult: float
    min_contraction: float
    break_atr: float
    confirm_window: int
    min_pole_atr: float
    min_pole_efficiency: float
    max_pole_bars: int
    max_consolidation_bars: int
    min_raw_quality: float
    min_specialized_quality: float
    min_pole_quality: float
    min_break_strength: float
    retest_window: int
    retest_hold_window: int
    flat_slope_norm_tol: float

    @property
    def parallel_slope_norm_tol(self) -> float:
        return self.flat_slope_norm_tol * 0.75


@dataclass(frozen=True, slots=True)
class PatternCompressionConfig:
    profile: str = PROFILE_BALANCED
    use_manual: bool = False
    manual_pivot_len: int = 5
    manual_min_age: int = 16
    manual_min_touch_gap: int = 5
    manual_touch_atr: float = 0.15
    manual_min_contraction_pct: float = 25.0
    manual_min_break_strength: float = 50.0
    min_tick: float = 0.01

    def resolve(self) -> PatternCompressionProfile:
        if self.profile not in PROFILE_VALUES:
            raise ValueError(f"unsupported Pattern/Compression profile: {self.profile}")
        if self.min_tick <= 0:
            raise ValueError("min_tick must be positive")

        if self.profile == PROFILE_SENSITIVE:
            values = dict(
                pivot_len=3,
                min_age=10,
                min_touch_gap=3,
                touch_atr_mult=0.20,
                min_contraction=0.15,
                break_atr=0.04,
                confirm_window=2,
                min_pole_atr=2.0,
                min_pole_efficiency=0.55,
                max_pole_bars=16,
                max_consolidation_bars=30,
                min_raw_quality=38.0,
                min_specialized_quality=42.0,
                min_pole_quality=40.0,
                min_break_strength=42.0,
                retest_window=5,
                retest_hold_window=2,
                flat_slope_norm_tol=0.030,
            )
        elif self.profile == PROFILE_SELECTIVE:
            values = dict(
                pivot_len=7,
                min_age=24,
                min_touch_gap=7,
                touch_atr_mult=0.12,
                min_contraction=0.35,
                break_atr=0.08,
                confirm_window=3,
                min_pole_atr=3.6,
                min_pole_efficiency=0.70,
                max_pole_bars=26,
                max_consolidation_bars=50,
                min_raw_quality=55.0,
                min_specialized_quality=58.0,
                min_pole_quality=58.0,
                min_break_strength=58.0,
                retest_window=7,
                retest_hold_window=4,
                flat_slope_norm_tol=0.018,
            )
        else:
            values = dict(
                pivot_len=5,
                min_age=16,
                min_touch_gap=5,
                touch_atr_mult=0.15,
                min_contraction=0.25,
                break_atr=0.06,
                confirm_window=2,
                min_pole_atr=2.8,
                min_pole_efficiency=0.62,
                max_pole_bars=20,
                max_consolidation_bars=40,
                min_raw_quality=46.0,
                min_specialized_quality=50.0,
                min_pole_quality=49.0,
                min_break_strength=50.0,
                retest_window=5,
                retest_hold_window=3,
                flat_slope_norm_tol=0.024,
            )

        if self.use_manual:
            if self.manual_pivot_len < 2:
                raise ValueError("manual_pivot_len must be at least 2")
            if self.manual_min_age < 1:
                raise ValueError("manual_min_age must be positive")
            if self.manual_min_touch_gap < 1:
                raise ValueError("manual_min_touch_gap must be positive")
            if self.manual_touch_atr <= 0:
                raise ValueError("manual_touch_atr must be positive")
            if self.manual_min_contraction_pct < 0:
                raise ValueError("manual_min_contraction_pct must be non-negative")
            values.update(
                pivot_len=self.manual_pivot_len,
                min_age=self.manual_min_age,
                min_touch_gap=self.manual_min_touch_gap,
                touch_atr_mult=self.manual_touch_atr,
                min_contraction=self.manual_min_contraction_pct / 100.0,
                min_break_strength=self.manual_min_break_strength,
            )

        return PatternCompressionProfile(name=self.profile, **values)


@dataclass(slots=True)
class PoleInfo:
    valid: bool = False
    direction: int = 0
    start_bar: int | None = None
    start_price: float | None = None
    end_bar: int | None = None
    end_price: float | None = None
    duration: int = 0
    magnitude: float | None = None
    efficiency: float | None = None
    quality: float = 0.0


@dataclass(slots=True)
class PatternCandidate:
    valid: bool = False
    identity: int = 0
    pattern_type: str = PATTERN_NONE
    family: str = PATTERN_NONE
    classic_dir: int = 0
    raw_quality: float = 0.0
    selection_score: float = 0.0
    geometry_score: float = 0.0
    geometry_atr: float | None = None
    slope_shape_score: float = 0.0
    touch_score: float = 0.0
    contraction_score: float = 0.0
    maturity_score: float = 0.0
    violation: float = 0.0
    historical_upper_close_violations: int = 0
    historical_lower_close_violations: int = 0
    historical_upper_wick_violations: int = 0
    historical_lower_wick_violations: int = 0
    historical_close_violations: int = 0
    historical_wick_violations: int = 0
    max_historical_violation: float = 0.0
    historical_violation_penalty: float = 0.0
    historical_scanned_bars: int = 0
    violation_history_truncated: bool = False
    last_violation_processed_bar: int | None = None
    violation_geometry_key: str = ""
    violation_scan_mode: str = "Bekliyor"
    upper_touches: int = 0
    lower_touches: int = 0
    start_bar: int | None = None
    end_bar: int | None = None
    known_bar: int | None = None
    apex_bar: int | None = None
    progress: float | None = None
    hb1: int | None = None
    hp1: float | None = None
    hb2: int | None = None
    hp2: float | None = None
    lb1: int | None = None
    lp1: float | None = None
    lb2: int | None = None
    lp2: float | None = None
    upper_slope: float | None = None
    lower_slope: float | None = None
    start_width: float | None = None
    current_width: float | None = None
    contraction: float | None = None
    upper_now: float | None = None
    lower_now: float | None = None
    has_pole: bool = False
    pole_dir: int = 0
    pole_start_bar: int | None = None
    pole_start_price: float | None = None
    pole_end_bar: int | None = None
    pole_end_price: float | None = None
    pole_duration: int = 0
    pole_magnitude: float | None = None
    pole_efficiency: float | None = None
    pole_quality: float = 0.0
    correction_depth: float | None = None
    duration_ratio: float | None = None
    consolidation_efficiency: float | None = None
    consolidation_height_ratio: float | None = None
    quality_frozen: bool = False
    frozen_raw_quality: float | None = None
    frozen_upper_boundary_at_break: float | None = None
    frozen_lower_boundary_at_break: float | None = None
    frozen_break_buffer: float | None = None
    frozen_retest_tolerance: float | None = None
    frozen_atr_at_break: float | None = None
    frozen_classic_dir: int = 0
    frozen_pattern_type: str = PATTERN_NONE
    break_snapshot_bar: int | None = None
    break_snapshot_price: float | None = None
    break_snapshot_direction: int = 0
    break_snapshot_quality: float | None = None
    break_strength: float | None = None
    break_body_score: float | None = None
    break_close_score: float | None = None
    break_penetration_score: float | None = None
    break_expansion_score: float | None = None
    break_volume_score: float | None = None
    break_confirmation_strength: float | None = None


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge1 == edge0:
        normalized = 1.0 if value >= edge1 else 0.0
    else:
        normalized = clamp((value - edge0) / (edge1 - edge0), 0.0, 1.0)
    return normalized * normalized * (3.0 - 2.0 * normalized)


def inverse_smoothstep(edge0: float, edge1: float, value: float) -> float:
    return 1.0 - smoothstep(edge0, edge1, value)


def band_quality(value: float, hard_low: float, optimal_low: float, optimal_high: float, hard_high: float) -> float:
    lower_quality = smoothstep(hard_low, optimal_low, value)
    upper_quality = inverse_smoothstep(optimal_high, hard_high, value)
    return clamp(min(lower_quality, upper_quality) * 100.0, 0.0, 100.0)


def progress_quality(progress: float | None) -> float:
    if progress is None:
        return 0.0
    return clamp(smoothstep(0.15, 0.42, progress) * inverse_smoothstep(0.82, 1.03, progress) * 100.0, 0.0, 100.0)


def age_quality(age: int, target_age: int) -> float:
    ratio = float(max(age, 0)) / max(1.0, float(target_age))
    return clamp(smoothstep(0.42, 1.05, ratio) * 100.0, 0.0, 100.0)


def contraction_quality(contraction: float | None, min_contraction: float) -> float:
    if contraction is None:
        return 0.0
    strong_contraction = max(0.52, min_contraction * 2.20)
    below = smoothstep(min_contraction * 0.55, min_contraction, contraction) * 45.0
    above = 45.0 + smoothstep(min_contraction, strong_contraction, contraction) * 55.0
    return clamp(below if contraction < min_contraction else above, 0.0, 100.0)


def cleanliness_quality(violation_penalty: float) -> float:
    return clamp(100.0 - smoothstep(10.0, 62.0, violation_penalty) * 100.0, 0.0, 100.0)


def line_price(x1: int, y1: float, x2: int, y2: float, x: int) -> float:
    if x2 == x1:
        return y2
    return y1 + (y2 - y1) / float(x2 - x1) * float(x - x1)


def slope(x1: int, y1: float, x2: int, y2: float) -> float:
    if x2 == x1:
        return 0.0
    return (y2 - y1) / float(x2 - x1)


def classic_direction(pattern_type: str) -> int:
    if pattern_type in {PATTERN_ASCENDING_TRIANGLE, PATTERN_FALLING_WEDGE, PATTERN_BULL_FLAG, PATTERN_BULL_PENNANT}:
        return 1
    if pattern_type in {PATTERN_DESCENDING_TRIANGLE, PATTERN_RISING_WEDGE, PATTERN_BEAR_FLAG, PATTERN_BEAR_PENNANT}:
        return -1
    return 0


def is_flag(pattern_type: str) -> bool:
    return pattern_type in {PATTERN_BULL_FLAG, PATTERN_BEAR_FLAG}


def is_pennant(pattern_type: str) -> bool:
    return pattern_type in {PATTERN_BULL_PENNANT, PATTERN_BEAR_PENNANT}


def export_pattern_state(state: str) -> int:
    return {
        ST_CANDIDATE: 1,
        ST_GEOMETRY: 2,
        ST_DEFINED: 3,
        ST_MATURING: 4,
        ST_COMPRESSING: 5,
        ST_PREP: 6,
        ST_BREAK_ATTEMPT: 7,
        ST_BREAK_CANDIDATE: 8,
        ST_BREAK_CONFIRMED: 9,
        ST_RETEST_WAIT: 10,
        ST_RETESTING: 11,
        ST_RETEST_OK: 12,
        ST_COMPLETED: 13,
        ST_BREAK_TIMEOUT: 14,
        ST_BREAK_FAILED: 15,
        ST_WEAK: 16,
        ST_INVALID: 17,
    }.get(state, 0)


def export_pattern_type(pattern_type: str) -> int:
    return {
        PATTERN_ASCENDING_TRIANGLE: 1,
        PATTERN_DESCENDING_TRIANGLE: 2,
        PATTERN_SYMMETRICAL_TRIANGLE: 3,
        PATTERN_RISING_WEDGE: 4,
        PATTERN_FALLING_WEDGE: 5,
        PATTERN_BULL_FLAG: 6,
        PATTERN_BEAR_FLAG: 7,
        PATTERN_BULL_PENNANT: 8,
        PATTERN_BEAR_PENNANT: 9,
    }.get(pattern_type, 0)


def export_break_state(state: str, break_direction: int) -> int:
    if break_direction == 0:
        return 0
    lifecycle = 0
    if state == ST_BREAK_ATTEMPT:
        lifecycle = 1
    elif state == ST_BREAK_CANDIDATE:
        lifecycle = 2
    elif state in {ST_BREAK_CONFIRMED, ST_RETEST_WAIT, ST_RETESTING, ST_RETEST_OK}:
        lifecycle = 3
    elif state == ST_COMPLETED:
        lifecycle = 4
    elif state == ST_BREAK_TIMEOUT:
        lifecycle = 5
    elif state == ST_BREAK_FAILED:
        lifecycle = 6
    return (1 if break_direction > 0 else -1) * lifecycle


def export_retest_state(state: str, confirmed_bar: int | None, successful_retest_bar: int | None) -> int:
    if state in {ST_BREAK_CONFIRMED, ST_RETEST_WAIT}:
        return 1
    if state == ST_RETESTING:
        return 2
    if state == ST_RETEST_OK:
        return 3
    if state == ST_COMPLETED and successful_retest_bar is not None:
        return 4
    if state == ST_BREAK_FAILED and confirmed_bar is not None:
        return -1
    return 0


def chronological_swings(hb1: int, hb2: int, lb1: int, lb2: int) -> bool:
    high_low_high_low = hb1 < lb1 < hb2 < lb2
    low_high_low_high = lb1 < hb1 < lb2 < hb2
    return high_low_high_low or low_high_low_high


PivotSide = Literal["high", "low"]


@dataclass(slots=True)
class PivotStore:
    config: PatternCompressionConfig = field(default_factory=PatternCompressionConfig)
    high_prices: list[float] = field(default_factory=list)
    high_bars: list[int] = field(default_factory=list)
    high_confirm_bars: list[int] = field(default_factory=list)
    high_locked: list[bool] = field(default_factory=list)
    low_prices: list[float] = field(default_factory=list)
    low_bars: list[int] = field(default_factory=list)
    low_confirm_bars: list[int] = field(default_factory=list)
    low_locked: list[bool] = field(default_factory=list)
    last_accepted_pivot_type: int = 0

    @property
    def profile(self) -> PatternCompressionProfile:
        return self.config.resolve()

    def _arrays(self, side: PivotSide) -> tuple[list[float], list[int], list[int], list[bool]]:
        if side == "high":
            return self.high_prices, self.high_bars, self.high_confirm_bars, self.high_locked
        return self.low_prices, self.low_bars, self.low_confirm_bars, self.low_locked

    def last_price(self, side: PivotSide) -> float | None:
        prices, _, _, _ = self._arrays(side)
        return prices[-1] if prices else None

    def add_pivot(
        self,
        *,
        side: PivotSide,
        price: float,
        source_bar: int,
        confirm_bar: int,
        same_open_type: bool | None = None,
    ) -> tuple[bool, bool]:
        prices, bars, confirms, locks = self._arrays(side)
        candidate_type = 1 if side == "high" else -1
        if same_open_type is None:
            same_open_type = self.last_accepted_pivot_type == candidate_type

        accepted = False
        appended = False
        if prices:
            last_price = prices[-1]
            last_locked = locks[-1]
            stronger_extreme = price > last_price if side == "high" else price < last_price
            if same_open_type:
                if not last_locked and stronger_extreme:
                    prices[-1] = price
                    bars[-1] = source_bar
                    confirms[-1] = confirm_bar
                    accepted = True
                elif last_locked and stronger_extreme:
                    prices.append(price)
                    bars.append(source_bar)
                    confirms.append(confirm_bar)
                    locks.append(False)
                    accepted = True
                    appended = True
            else:
                prices.append(price)
                bars.append(source_bar)
                confirms.append(confirm_bar)
                locks.append(False)
                accepted = True
                appended = True
        else:
            prices.append(price)
            bars.append(source_bar)
            confirms.append(confirm_bar)
            locks.append(False)
            accepted = True
            appended = True

        while len(prices) > MAX_PIVOTS:
            prices.pop(0)
            bars.pop(0)
            confirms.pop(0)
            locks.pop(0)

        if accepted:
            self.last_accepted_pivot_type = candidate_type
        return accepted, appended

    def lock_pivot_by_bar(self, side: PivotSide, source_bar: int) -> int:
        _, bars, _, locks = self._arrays(side)
        newly_locked = 0
        for index, pivot_bar in enumerate(bars):
            if pivot_bar == source_bar and not locks[index]:
                locks[index] = True
                newly_locked += 1
        return newly_locked

    def lock_used_pivots(self, candidate: PatternCandidate) -> int:
        newly_locked = 0
        for side, source_bar in (
            ("high", candidate.hb1),
            ("high", candidate.hb2),
            ("low", candidate.lb1),
            ("low", candidate.lb2),
        ):
            if source_bar is not None:
                newly_locked += self.lock_pivot_by_bar(side, source_bar)
        return newly_locked

    def normalized_pivot_distance(self, candidate_price: float, reference_price: float | None, source_atr: float) -> float:
        if reference_price is None:
            return 0.0
        denominator = max(source_atr, self.config.min_tick * 10.0)
        return abs(candidate_price - reference_price) / denominator

    def same_bar_candidate_valid(self, *, side: PivotSide, candidate_price: float, source_atr: float) -> bool:
        candidate_type = 1 if side == "high" else -1
        prices, _, _, locks = self._arrays(side)
        valid = True
        if self.last_accepted_pivot_type == candidate_type and prices:
            last_price = prices[-1]
            last_locked = locks[-1]
            stronger_extreme = candidate_price > last_price if side == "high" else candidate_price < last_price
            minimum_move = (
                max(self.config.min_tick * 2.0, source_atr * 0.03)
                if last_locked
                else self.config.min_tick
            )
            valid = stronger_extreme and abs(candidate_price - last_price) >= minimum_move
        elif self.last_accepted_pivot_type == -candidate_type:
            reference_side: PivotSide = "low" if candidate_type == 1 else "high"
            reference_price = self.last_price(reference_side)
            minimum_move_atr = (
                0.08
                if self.config.profile == PROFILE_SENSITIVE
                else 0.16
                if self.config.profile == PROFILE_SELECTIVE
                else 0.12
            )
            minimum_move = max(self.config.min_tick * 2.0, source_atr * minimum_move_atr)
            valid = reference_price is not None and abs(candidate_price - reference_price) >= minimum_move
        return valid

    def choose_same_bar_pivot(
        self,
        *,
        high_candidate: float,
        low_candidate: float,
        source_atr: float,
        source_open: float,
        source_high: float,
        source_low: float,
        source_close: float,
    ) -> tuple[int, str, float, float]:
        high_valid = self.same_bar_candidate_valid(side="high", candidate_price=high_candidate, source_atr=source_atr)
        low_valid = self.same_bar_candidate_valid(side="low", candidate_price=low_candidate, source_atr=source_atr)

        if self.last_accepted_pivot_type == 1:
            reference_price = self.last_price("high")
        elif self.last_accepted_pivot_type == -1:
            reference_price = self.last_price("low")
        else:
            reference_price = (source_high + source_low) * 0.5

        high_distance_atr = self.normalized_pivot_distance(high_candidate, reference_price, source_atr)
        low_distance_atr = self.normalized_pivot_distance(low_candidate, reference_price, source_atr)
        selected_type = 0
        reason = "İki aday da kabul şartını sağlamadı"

        if high_valid and not low_valid:
            selected_type = 1
            reason = "Yalnız High adayı kabul edilebilir"
        elif low_valid and not high_valid:
            selected_type = -1
            reason = "Yalnız Low adayı kabul edilebilir"
        elif high_valid and low_valid:
            if self.last_accepted_pivot_type == 1:
                selected_type = -1
                reason = "Dönüşümlü swing önceliği: Low"
            elif self.last_accepted_pivot_type == -1:
                selected_type = 1
                reason = "Dönüşümlü swing önceliği: High"
            else:
                equality_tolerance = (
                    0.05
                    if self.config.profile == PROFILE_SENSITIVE
                    else 0.10
                    if self.config.profile == PROFILE_SELECTIVE
                    else 0.075
                )
                distance_difference = high_distance_atr - low_distance_atr
                if abs(distance_difference) > equality_tolerance:
                    selected_type = 1 if distance_difference > 0.0 else -1
                    reason = (
                        "Daha büyük ATR-normalize High hareketi"
                        if selected_type == 1
                        else "Daha büyük ATR-normalize Low hareketi"
                    )
                else:
                    candle_range = max(source_high - source_low, self.config.min_tick)
                    body_strength = abs(source_close - source_open) / candle_range
                    if body_strength >= 0.35 and source_close > source_open:
                        selected_type = 1
                        reason = "Yakın mesafe; güçlü yükseliş mumu"
                    elif body_strength >= 0.35 and source_close < source_open:
                        selected_type = -1
                        reason = "Yakın mesafe; güçlü düşüş mumu"
                    else:
                        high_close_distance = abs(high_candidate - source_close)
                        low_close_distance = abs(source_close - low_candidate)
                        selected_type = 1 if high_close_distance >= low_close_distance else -1
                        reason = (
                            "Nötr mum; deterministik High seçimi"
                            if selected_type == 1
                            else "Nötr mum; deterministik Low seçimi"
                        )

        return selected_type, reason, high_distance_atr, low_distance_atr

    def touch_stats(
        self,
        *,
        side: PivotSide,
        x1: int,
        y1: float,
        x2: int,
        y2: float,
        start_bar: int,
        end_bar: int,
        tolerance: float,
    ) -> tuple[int, float, int | None, int | None]:
        if tolerance <= 0:
            raise ValueError("tolerance must be positive")
        prices, bars, _, _ = self._arrays(side)
        touch_count = 0
        first_touch: int | None = None
        last_touch: int | None = None
        last_accepted: int | None = None
        distance_sum = 0.0
        for price, pivot_bar in zip(prices, bars, strict=True):
            if start_bar <= pivot_bar <= end_bar:
                expected = line_price(x1, y1, x2, y2, pivot_bar)
                distance = abs(price - expected)
                if distance <= tolerance and (
                    last_accepted is None or pivot_bar - last_accepted >= self.profile.min_touch_gap
                ):
                    touch_count += 1
                    distance_sum += distance / max(tolerance, self.config.min_tick)
                    first_touch = pivot_bar if first_touch is None else first_touch
                    last_touch = pivot_bar
                    last_accepted = pivot_bar
        average_distance = distance_sum / touch_count if touch_count > 0 else 10.0
        return touch_count, average_distance, first_touch, last_touch
