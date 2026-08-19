from __future__ import annotations

import copy
from dataclasses import dataclass

from .pattern_compression_core import (
    ST_BREAK_ATTEMPT,
    ST_BREAK_CANDIDATE,
    ST_BREAK_CONFIRMED,
    ST_BREAK_FAILED,
    ST_BREAK_TIMEOUT,
    ST_COMPLETED,
    ST_RETESTING,
    ST_RETEST_OK,
    ST_RETEST_WAIT,
    PatternCandidate,
    PatternCompressionConfig,
    clamp,
    line_price,
    smoothstep,
)


BREAK_LIFECYCLE_STATES = frozenset(
    {
        ST_BREAK_ATTEMPT,
        ST_BREAK_CANDIDATE,
        ST_BREAK_CONFIRMED,
        ST_RETEST_WAIT,
        ST_RETESTING,
        ST_RETEST_OK,
        ST_BREAK_FAILED,
        ST_BREAK_TIMEOUT,
        ST_COMPLETED,
    }
)
TERMINAL_STATES = frozenset({ST_BREAK_FAILED, ST_COMPLETED})


@dataclass(frozen=True, slots=True)
class BreakoutStrength:
    strength: float
    body_score: float
    close_score: float
    penetration_score: float
    expansion_score: float
    volume_score: float


@dataclass(frozen=True, slots=True)
class LifecycleBar:
    bar_index: int
    open: float
    high: float
    low: float
    close: float
    atr: float
    volume: float | None = None
    volume_sma: float | None = None


@dataclass(frozen=True, slots=True)
class PatternLifecycleSnapshot:
    state: str
    candidate: PatternCandidate
    break_direction: int = 0
    break_candidate_bar: int | None = None
    break_confirmed_bar: int | None = None
    retest_success_bar: int | None = None
    invalid_reason: str = "Yok"
    break_line_x1: int | None = None
    break_line_y1: float | None = None
    break_line_x2: int | None = None
    break_line_y2: float | None = None


@dataclass(frozen=True, slots=True)
class PatternLifecycleConfig:
    pattern: PatternCompressionConfig = PatternCompressionConfig()
    use_breakout_quality_filter: bool = True


def breakout_strength(
    *,
    direction: int,
    boundary: float,
    bar: LifecycleBar,
    base_break_atr: float,
    min_tick: float,
) -> BreakoutStrength:
    if direction not in {-1, 1}:
        raise ValueError("direction must be -1 or 1")
    candle_range = max(bar.high - bar.low, min_tick)
    directional_body_ratio = (bar.close - bar.open) / candle_range if direction == 1 else (bar.open - bar.close) / candle_range
    close_location = (bar.close - bar.low) / candle_range if direction == 1 else (bar.high - bar.close) / candle_range
    safe_atr = max(bar.atr, min_tick)
    penetration_atr = (bar.close - boundary) / safe_atr if direction == 1 else (boundary - bar.close) / safe_atr
    expansion_atr = candle_range / safe_atr
    volume_available = (
        bar.volume is not None
        and bar.volume > 0
        and bar.volume_sma is not None
        and bar.volume_sma > 0
    )
    volume_ratio = bar.volume / bar.volume_sma if volume_available and bar.volume_sma is not None else 1.0
    body_score = smoothstep(0.10, 0.65, directional_body_ratio) * 100.0
    close_score = smoothstep(0.56, 0.88, close_location) * 100.0
    penetration_score = smoothstep(base_break_atr * 0.75, max(0.24, base_break_atr * 4.0), penetration_atr) * 100.0
    expansion_score = smoothstep(0.65, 1.55, expansion_atr) * 100.0
    volume_score = smoothstep(0.90, 1.55, volume_ratio) * 100.0 if volume_available else 50.0
    strength = clamp(
        body_score * 0.24
        + close_score * 0.28
        + penetration_score * 0.25
        + expansion_score * 0.15
        + volume_score * 0.08,
        0.0,
        100.0,
    )
    return BreakoutStrength(
        strength=strength,
        body_score=body_score,
        close_score=close_score,
        penetration_score=penetration_score,
        expansion_score=expansion_score,
        volume_score=volume_score,
    )


class PatternLifecycleRuntime:
    """Closed-bar breakout/retest state machine for one active pattern identity.

    Candidate discovery/refresh is intentionally outside this class. The runtime owns
    only breakout snapshot/freeze semantics and subsequent lifecycle chronology.
    """

    def __init__(
        self,
        candidate: PatternCandidate,
        *,
        state: str,
        config: PatternLifecycleConfig | None = None,
    ) -> None:
        self.config = config or PatternLifecycleConfig()
        self.profile = self.config.pattern.resolve()
        self.candidate = copy.deepcopy(candidate)
        self.state = state
        self.break_direction = 0
        self.break_candidate_bar: int | None = None
        self.break_confirmed_bar: int | None = None
        self.retest_success_bar: int | None = None
        self.invalid_reason = "Yok"
        self.break_line_x1: int | None = None
        self.break_line_y1: float | None = None
        self.break_line_x2: int | None = None
        self.break_line_y2: float | None = None

    @property
    def min_tick(self) -> float:
        return self.config.pattern.min_tick

    def _safe_atr(self, atr: float) -> float:
        return max(float(atr), self.min_tick * 10.0)

    def _live_buffers(self, atr: float) -> tuple[float, float]:
        safe_atr = self._safe_atr(atr)
        tolerance = max(self.min_tick * 2.0, safe_atr * self.profile.touch_atr_mult)
        break_buffer = max(self.min_tick * 2.0, safe_atr * self.profile.break_atr)
        return break_buffer, tolerance

    def _effective_break_buffer(self, atr: float) -> float:
        live_break, _ = self._live_buffers(atr)
        return (
            float(self.candidate.frozen_break_buffer)
            if self.candidate.quality_frozen and self.candidate.frozen_break_buffer is not None
            else live_break
        )

    def _effective_retest_tolerance(self, atr: float) -> float:
        _, live_tol = self._live_buffers(atr)
        return (
            float(self.candidate.frozen_retest_tolerance)
            if self.candidate.quality_frozen and self.candidate.frozen_retest_tolerance is not None
            else live_tol
        )

    def _project_boundaries(self, bar_index: int) -> tuple[float, float]:
        required = (
            self.candidate.hb1,
            self.candidate.hp1,
            self.candidate.hb2,
            self.candidate.hp2,
            self.candidate.lb1,
            self.candidate.lp1,
            self.candidate.lb2,
            self.candidate.lp2,
        )
        if any(value is None for value in required):
            raise ValueError("pattern candidate is missing boundary geometry")
        upper = line_price(
            int(self.candidate.hb1),
            float(self.candidate.hp1),
            int(self.candidate.hb2),
            float(self.candidate.hp2),
            bar_index,
        )
        lower = line_price(
            int(self.candidate.lb1),
            float(self.candidate.lp1),
            int(self.candidate.lb2),
            float(self.candidate.lp2),
            bar_index,
        )
        self.candidate.upper_now = upper
        self.candidate.lower_now = lower
        return upper, lower

    def _break_boundary(self, bar_index: int) -> float:
        if None in (self.break_line_x1, self.break_line_y1, self.break_line_x2, self.break_line_y2):
            raise ValueError("break boundary has not been frozen")
        return line_price(
            int(self.break_line_x1),
            float(self.break_line_y1),
            int(self.break_line_x2),
            float(self.break_line_y2),
            bar_index,
        )

    def _freeze_quality(
        self,
        *,
        bar: LifecycleBar,
        direction: int,
        break_buffer: float,
        tolerance: float,
        upper: float,
        lower: float,
    ) -> None:
        if not self.candidate.valid or self.candidate.quality_frozen:
            return
        self.candidate.quality_frozen = True
        self.candidate.frozen_raw_quality = self.candidate.raw_quality
        self.candidate.frozen_upper_boundary_at_break = upper
        self.candidate.frozen_lower_boundary_at_break = lower
        self.candidate.frozen_break_buffer = break_buffer
        self.candidate.frozen_retest_tolerance = tolerance
        self.candidate.frozen_atr_at_break = self._safe_atr(bar.atr)
        self.candidate.frozen_classic_dir = self.candidate.classic_dir
        self.candidate.frozen_pattern_type = self.candidate.pattern_type
        self.candidate.break_snapshot_bar = bar.bar_index
        self.candidate.break_snapshot_direction = direction
        self.candidate.break_snapshot_price = upper if direction == 1 else lower
        self.candidate.break_snapshot_quality = self.candidate.raw_quality
        self.candidate.violation_scan_mode = "Dondurulmuş"

    def _start_break(
        self,
        *,
        bar: LifecycleBar,
        direction: int,
        strength: BreakoutStrength,
        strong: bool,
        upper: float,
        lower: float,
    ) -> None:
        break_buffer, tolerance = self._live_buffers(bar.atr)
        self.state = ST_BREAK_CANDIDATE if strong else ST_BREAK_ATTEMPT
        self.break_direction = direction
        self.break_candidate_bar = bar.bar_index
        self.break_confirmed_bar = None
        self.retest_success_bar = None
        if direction == 1:
            self.break_line_x1, self.break_line_y1 = self.candidate.hb1, self.candidate.hp1
            self.break_line_x2, self.break_line_y2 = self.candidate.hb2, self.candidate.hp2
        else:
            self.break_line_x1, self.break_line_y1 = self.candidate.lb1, self.candidate.lp1
            self.break_line_x2, self.break_line_y2 = self.candidate.lb2, self.candidate.lp2
        self._freeze_quality(
            bar=bar,
            direction=direction,
            break_buffer=break_buffer,
            tolerance=tolerance,
            upper=upper,
            lower=lower,
        )
        self.candidate.break_strength = strength.strength
        self.candidate.break_body_score = strength.body_score
        self.candidate.break_close_score = strength.close_score
        self.candidate.break_penetration_score = strength.penetration_score
        self.candidate.break_expansion_score = strength.expansion_score
        self.candidate.break_volume_score = strength.volume_score
        self.invalid_reason = "Yok" if strong else "Sınır dışı kapanış var; kırılım gücü teyit bekliyor"

    def snapshot(self) -> PatternLifecycleSnapshot:
        return PatternLifecycleSnapshot(
            state=self.state,
            candidate=copy.deepcopy(self.candidate),
            break_direction=self.break_direction,
            break_candidate_bar=self.break_candidate_bar,
            break_confirmed_bar=self.break_confirmed_bar,
            retest_success_bar=self.retest_success_bar,
            invalid_reason=self.invalid_reason,
            break_line_x1=self.break_line_x1,
            break_line_y1=self.break_line_y1,
            break_line_x2=self.break_line_x2,
            break_line_y2=self.break_line_y2,
        )

    def preview_state(self, bar: LifecycleBar) -> str:
        if not self.candidate.valid or self.state in BREAK_LIFECYCLE_STATES:
            return self.state
        upper, lower = self._project_boundaries(bar.bar_index)
        break_buffer, _ = self._live_buffers(bar.atr)
        live_up = bar.high > upper + break_buffer and bar.close <= upper + break_buffer
        live_down = bar.low < lower - break_buffer and bar.close >= lower - break_buffer
        return ST_BREAK_ATTEMPT if live_up or live_down else self.state

    def update_closed(self, bar: LifecycleBar, *, hard_geometry_invalid: bool = False) -> PatternLifecycleSnapshot:
        if not self.candidate.valid:
            return self.snapshot()
        upper, lower = self._project_boundaries(bar.bar_index)

        if self.state in TERMINAL_STATES:
            return self.snapshot()

        if self.state in {ST_BREAK_ATTEMPT, ST_BREAK_CANDIDATE}:
            if self.break_candidate_bar is None or self.break_direction == 0:
                raise ValueError("break lifecycle is missing candidate chronology")
            boundary = self._break_boundary(bar.bar_index)
            break_buffer = self._effective_break_buffer(bar.atr)
            retest_tolerance = self._effective_retest_tolerance(bar.atr)
            lifecycle_atr = (
                float(self.candidate.frozen_atr_at_break)
                if self.candidate.quality_frozen and self.candidate.frozen_atr_at_break is not None
                else self._safe_atr(bar.atr)
            )
            strength = breakout_strength(
                direction=self.break_direction,
                boundary=boundary,
                bar=LifecycleBar(
                    bar_index=bar.bar_index,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    atr=lifecycle_atr,
                    volume=bar.volume,
                    volume_sma=bar.volume_sma,
                ),
                base_break_atr=self.profile.break_atr,
                min_tick=self.min_tick,
            )
            same_side_close = bar.close > boundary + break_buffer if self.break_direction == 1 else bar.close < boundary - break_buffer
            confirmation_floor = max(25.0, self.profile.min_break_strength - 6.0)
            strong_same_side_close = same_side_close and (
                not self.config.use_breakout_quality_filter or strength.strength >= confirmation_floor
            )
            hold_buffer = max(self.min_tick, retest_tolerance * 0.12)
            attempt_retest = (
                bar.low <= boundary + retest_tolerance and bar.close > boundary + hold_buffer
                if self.break_direction == 1
                else bar.high >= boundary - retest_tolerance and bar.close < boundary - hold_buffer
            )
            back_inside = (
                bar.close < upper - hold_buffer
                if self.break_direction == 1
                else bar.close > lower + hold_buffer
            )
            age = bar.bar_index - self.break_candidate_bar
            if back_inside and bar.bar_index > self.break_candidate_bar:
                self.state = ST_BREAK_FAILED
                self.invalid_reason = (
                    "Kırılım denemesi formasyon içine döndü"
                    if self.state == ST_BREAK_ATTEMPT
                    else "Teyitsiz kırılım formasyon içine döndü"
                )
            elif bar.bar_index > self.break_candidate_bar and age <= self.profile.confirm_window and (strong_same_side_close or attempt_retest):
                self.state = ST_BREAK_CONFIRMED
                self.break_confirmed_bar = bar.bar_index
                self.candidate.break_confirmation_strength = strength.strength
                self.invalid_reason = "Yok"
            elif age > self.profile.confirm_window:
                was_attempt = self.state == ST_BREAK_ATTEMPT
                self.state = ST_BREAK_TIMEOUT
                self.invalid_reason = "Kırılım denemesi güçlenmedi" if was_attempt else "Kırılım teyit alamadı"
            return self.snapshot()

        if self.state in {ST_BREAK_CONFIRMED, ST_RETEST_WAIT, ST_RETESTING}:
            if self.break_confirmed_bar is None or self.break_direction == 0:
                raise ValueError("confirmed lifecycle is missing chronology")
            boundary = self._break_boundary(bar.bar_index)
            tolerance = self._effective_retest_tolerance(bar.atr)
            hold_buffer = max(self.min_tick, tolerance * 0.12)
            returned_inside = bar.close < upper - hold_buffer if self.break_direction == 1 else bar.close > lower + hold_buffer
            retest_touch = (
                bar.low <= boundary + tolerance and bar.high >= boundary - tolerance
                if self.break_direction == 1
                else bar.high >= boundary - tolerance and bar.low <= boundary + tolerance
            )
            retest_held = (
                retest_touch and bar.close > boundary + hold_buffer
                if self.break_direction == 1
                else retest_touch and bar.close < boundary - hold_buffer
            )
            confirmed_age = bar.bar_index - self.break_confirmed_bar
            if returned_inside:
                self.state = ST_BREAK_FAILED
                self.invalid_reason = "Kırılım sonrası formasyon alanına dönüldü"
            elif retest_held:
                self.state = ST_RETEST_OK
                self.retest_success_bar = bar.bar_index
                self.invalid_reason = "Yok"
            elif retest_touch:
                self.state = ST_RETESTING
            elif confirmed_age > self.profile.retest_window:
                self.state = ST_COMPLETED
            else:
                self.state = ST_RETEST_WAIT
            return self.snapshot()

        if self.state == ST_RETEST_OK:
            if self.retest_success_bar is None or self.break_direction == 0:
                raise ValueError("retest-ok lifecycle is missing chronology")
            boundary = self._break_boundary(bar.bar_index)
            tolerance = self._effective_retest_tolerance(bar.atr)
            hold_buffer = max(self.min_tick, tolerance * 0.12)
            retained = bar.close > boundary + hold_buffer if self.break_direction == 1 else bar.close < boundary - hold_buffer
            returned_inside = bar.close < upper - hold_buffer if self.break_direction == 1 else bar.close > lower + hold_buffer
            hold_age = bar.bar_index - self.retest_success_bar
            if returned_inside:
                self.state = ST_BREAK_FAILED
                self.invalid_reason = "Başarılı retest sonrası yapı içine dönüldü"
            elif retained and hold_age >= self.profile.retest_hold_window:
                self.state = ST_COMPLETED
            elif not retained:
                self.state = ST_RETESTING
                self.invalid_reason = "Retest sınır çevresinde yeniden izleniyor"
            else:
                self.state = ST_RETEST_OK
                self.invalid_reason = "Retest korunumu bekleniyor"
            return self.snapshot()

        break_buffer, _ = self._live_buffers(bar.atr)
        up_strength = breakout_strength(
            direction=1,
            boundary=upper,
            bar=bar,
            base_break_atr=self.profile.break_atr,
            min_tick=self.min_tick,
        )
        down_strength = breakout_strength(
            direction=-1,
            boundary=lower,
            bar=bar,
            base_break_atr=self.profile.break_atr,
            min_tick=self.min_tick,
        )
        close_up_raw = bar.close > upper + break_buffer
        close_down_raw = bar.close < lower - break_buffer
        close_up = close_up_raw and (
            not self.config.use_breakout_quality_filter or up_strength.strength >= self.profile.min_break_strength
        )
        close_down = close_down_raw and (
            not self.config.use_breakout_quality_filter or down_strength.strength >= self.profile.min_break_strength
        )
        weak_up = close_up_raw and not close_up
        weak_down = close_down_raw and not close_down
        closed_boundary_break = close_up_raw or close_down_raw

        if closed_boundary_break and not hard_geometry_invalid:
            direction = 1 if close_up_raw else -1
            strong = close_up or close_down
            self._start_break(
                bar=bar,
                direction=direction,
                strength=up_strength if direction == 1 else down_strength,
                strong=strong,
                upper=upper,
                lower=lower,
            )
        return self.snapshot()
