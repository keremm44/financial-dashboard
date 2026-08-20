from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pandas as pd

from .models import Direction, EngineResult
from .volume_participation_engine import (
    EffortResultClass,
    ParticipationExport,
    VolumeParticipationConfig,
    VolumeParticipationEngine as _CoreVolumeParticipationEngine,
    VolumeParticipationMetrics,
    _rma,
    _safe_div,
)


class LifecycleStage(StrEnum):
    NONE = "NONE"
    DEVELOPING = "DEVELOPING"
    CONFIRMED = "CONFIRMED"
    PROTECTED = "PROTECTED"
    WEAKENING = "WEAKENING"
    CLOSED = "CLOSED"
    INVALIDATED = "INVALIDATED"


class AbsorptionSide(StrEnum):
    NONE = "NONE"
    UPPER = "UPPER"
    LOWER = "LOWER"


class AbsorptionStage(StrEnum):
    NONE = "NONE"
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


class BreakStage(StrEnum):
    NONE = "NONE"
    DEVELOPING = "DEVELOPING"
    SUPPORTED = "SUPPORTED"
    PROTECTED = "PROTECTED"
    UNSUPPORTED = "UNSUPPORTED"
    RECLAIMED = "RECLAIMED"


@dataclass(frozen=True, slots=True)
class ParticipationLifecycleConfig:
    recent_context_length: int = 8
    pivot_length: int = 4
    minimum_pivot_range_atr: float = 1.35
    minimum_pivot_bar_distance: int = 4
    recent_level_lookback: int = 20
    important_level_proximity_atr: float = 0.25
    break_buffer_atr: float = 0.08
    breakout_confirmation_bars: int = 2
    breakout_context_memory_bars: int = 8
    absorption_minimum_wick_ratio: float = 0.36
    absorption_minimum_evidence: int = 4
    absorption_confirmation_window: int = 4
    weakening_rvol_drop_factor: float = 0.85
    weakening_rtv_drop_factor: float = 0.85
    weakening_minimum_evidence: int = 4


@dataclass(frozen=True, slots=True)
class ConfirmedParticipationPivot:
    side: str
    price: float
    origin_index: int
    known_index: int
    atr_at_source: float


@dataclass(frozen=True, slots=True)
class AbsorptionEvent:
    side: AbsorptionSide = AbsorptionSide.NONE
    stage: AbsorptionStage = AbsorptionStage.NONE
    candidate_index: int | None = None
    reference_level: float | None = None
    reference_source: str | None = None
    candidate_high: float | None = None
    candidate_low: float | None = None
    candidate_mid: float | None = None
    frozen_atr: float | None = None
    frozen_buffer: float | None = None
    evidence_count: int = 0


@dataclass(frozen=True, slots=True)
class BreakParticipationEvent:
    direction: int = 0
    stage: BreakStage = BreakStage.NONE
    start_index: int | None = None
    level: float | None = None
    reference_source: str | None = None
    frozen_atr: float | None = None
    frozen_buffer: float | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ParticipationLifecycleExport:
    participation_direction: int = 0
    participation_stage: str = LifecycleStage.NONE.value
    controlled_pullback: bool = False
    controlled_reaction: bool = False
    absorption_side: str = AbsorptionSide.NONE.value
    absorption_stage: str = AbsorptionStage.NONE.value
    absorption_reference_level: float | None = None
    absorption_reference_source: str | None = None
    absorption_frozen_atr: float | None = None
    absorption_frozen_buffer: float | None = None
    break_direction: int = 0
    break_stage: str = BreakStage.NONE.value
    break_level: float | None = None
    break_reference_source: str | None = None
    break_frozen_atr: float | None = None
    break_frozen_buffer: float | None = None
    last_pivot_high: float | None = None
    last_pivot_high_known_index: int | None = None
    last_pivot_low: float | None = None
    last_pivot_low_known_index: int | None = None


class VolumeParticipationEngine(_CoreVolumeParticipationEngine):
    """Tur-2 stateful lifecycle layer over the deterministic participation core."""

    def __init__(
        self,
        config: VolumeParticipationConfig | None = None,
        lifecycle_config: ParticipationLifecycleConfig | None = None,
    ) -> None:
        super().__init__(config)
        self.lifecycle_config = lifecycle_config or ParticipationLifecycleConfig()
        self.lifecycle_export = ParticipationLifecycleExport()
        self._active_participation_direction = 0
        self._active_participation_stage = LifecycleStage.NONE
        self._participation_confirmed_index: int | None = None
        self._absorption = AbsorptionEvent()
        self._absorption_hold_until = -1
        self._break = BreakParticipationEvent()
        self._accepted_pivot: ConfirmedParticipationPivot | None = None
        self._last_high_pivot: ConfirmedParticipationPivot | None = None
        self._last_low_pivot: ConfirmedParticipationPivot | None = None
        self._lifecycle_snapshot: EngineResult | None = None

    def _reset(self) -> None:
        super()._reset()
        self.lifecycle_export = ParticipationLifecycleExport()
        self._active_participation_direction = 0
        self._active_participation_stage = LifecycleStage.NONE
        self._participation_confirmed_index = None
        self._absorption = AbsorptionEvent()
        self._absorption_hold_until = -1
        self._break = BreakParticipationEvent()
        self._accepted_pivot = None
        self._last_high_pivot = None
        self._last_low_pivot = None
        self._lifecycle_snapshot = None

    def _atr_series(self) -> list[float | None]:
        if not self._rows:
            return []
        highs = [float(r["high"]) for r in self._rows]
        lows = [float(r["low"]) for r in self._rows]
        closes = [float(r["close"]) for r in self._rows]
        tr: list[float] = []
        for i in range(len(self._rows)):
            if i == 0:
                tr.append(highs[i] - lows[i])
            else:
                tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
        return _rma(tr, self.config.atr_length)

    def _update_pivot(self, index: int) -> None:
        p = self.lifecycle_config.pivot_length
        origin = index - p
        if origin < p or index < 2 * p:
            return
        highs = [float(r["high"]) for r in self._rows]
        lows = [float(r["low"]) for r in self._rows]
        high_window = highs[origin - p : origin + p + 1]
        low_window = lows[origin - p : origin + p + 1]
        is_high = highs[origin] == max(high_window) and high_window.count(highs[origin]) == 1
        is_low = lows[origin] == min(low_window) and low_window.count(lows[origin]) == 1
        if is_high == is_low:
            return
        atr_series = self._atr_series()
        atr = atr_series[origin]
        if atr is None or atr <= 0:
            return
        candidate = ConfirmedParticipationPivot(
            side="HIGH" if is_high else "LOW",
            price=highs[origin] if is_high else lows[origin],
            origin_index=origin,
            known_index=index,
            atr_at_source=float(atr),
        )
        accepted = self._accepted_pivot
        if accepted is None:
            self._accept_pivot(candidate)
            return
        if candidate.side == accepted.side:
            more_extreme = candidate.price > accepted.price if candidate.side == "HIGH" else candidate.price < accepted.price
            if more_extreme:
                self._accept_pivot(candidate)
            return
        distance_bars = candidate.origin_index - accepted.origin_index
        distance_atr = _safe_div(abs(candidate.price - accepted.price), candidate.atr_at_source)
        if distance_bars >= self.lifecycle_config.minimum_pivot_bar_distance and distance_atr >= self.lifecycle_config.minimum_pivot_range_atr:
            self._accept_pivot(candidate)

    def _accept_pivot(self, pivot: ConfirmedParticipationPivot) -> None:
        self._accepted_pivot = pivot
        if pivot.side == "HIGH":
            self._last_high_pivot = pivot
        else:
            self._last_low_pivot = pivot

    def _nearest_reference(self, *, upper: bool, index: int) -> tuple[float | None, float | None, str | None]:
        row = self._rows[index]
        test_price = float(row["high"] if upper else row["low"])
        pivot = self._last_high_pivot if upper else self._last_low_pivot
        atr_series = self._atr_series()
        current_atr = atr_series[index]
        if current_atr is None:
            return None, None, None
        candidates: list[tuple[float, float, str]] = []
        if pivot is not None:
            candidates.append((pivot.price, pivot.atr_at_source, "PIVOT"))
        lookback = self.lifecycle_config.recent_level_lookback
        start = max(0, index - lookback)
        if upper:
            recent = max(float(r["high"]) for r in self._rows[start:index]) if index > start else None
        else:
            recent = min(float(r["low"]) for r in self._rows[start:index]) if index > start else None
        if recent is not None:
            candidates.append((recent, float(current_atr), "RECENT_20"))
        if not candidates:
            return None, None, None
        candidates.sort(key=lambda item: abs(test_price - item[0]))
        level, atr, source = candidates[0]
        if abs(test_price - level) <= max(atr, 1e-12) * self.lifecycle_config.important_level_proximity_atr:
            return level, atr, source
        return None, None, None

    def _flow_shares_5(self) -> tuple[float, float, float, float]:
        n = len(self._rows)
        length = self.config.flow_short_length
        if n < length:
            return 0.5, 0.5, 0.5, 0.5
        rows = self._rows[-length:]
        start_index = n - length
        up_v = down_v = up_c = down_c = total_v = total_c = 0.0
        for j, row in enumerate(rows):
            i = start_index + j
            volume = float(row["volume"])
            tv = volume * ((float(row["high"]) + float(row["low"]) + float(row["close"])) / 3.0)
            total_v += volume
            total_c += tv
            if i > 0 and float(row["close"]) > float(self._rows[i - 1]["close"]):
                up_v += volume
                up_c += tv
            elif i > 0 and float(row["close"]) < float(self._rows[i - 1]["close"]):
                down_v += volume
                down_c += tv
        return (
            _safe_div(up_v, total_v, 0.5),
            _safe_div(down_v, total_v, 0.5),
            _safe_div(up_c, total_c, 0.5),
            _safe_div(down_c, total_c, 0.5),
        )

    def _update_participation_lifecycle(self, index: int, metrics: VolumeParticipationMetrics) -> tuple[bool, bool]:
        if not metrics.data_ready:
            return False, False
        if metrics.up_confirmed:
            self._active_participation_direction = 1
            self._active_participation_stage = LifecycleStage.CONFIRMED
            self._participation_confirmed_index = index
        elif metrics.down_confirmed:
            self._active_participation_direction = -1
            self._active_participation_stage = LifecycleStage.CONFIRMED
            self._participation_confirmed_index = index

        controlled_up = controlled_down = False
        if self._participation_confirmed_index is None:
            return controlled_up, controlled_down
        age = index - self._participation_confirmed_index
        if age > self.lifecycle_config.recent_context_length:
            self._active_participation_stage = LifecycleStage.CLOSED
            self._active_participation_direction = 0
            return controlled_up, controlled_down

        up_v5, down_v5, up_c5, down_c5 = self._flow_shares_5()
        close = float(self._rows[index]["close"])
        prev_close = float(self._rows[index - 1]["close"]) if index > 0 else close
        rvol = metrics.rvol or 0.0
        rtv = metrics.rtv or 0.0
        pressure = metrics.directional_value_pressure_5 or 0.0
        if self._active_participation_direction == 1 and close < prev_close:
            controlled_up = (
                metrics.down_evidence_count < self.config.participation_minimum_evidence
                and down_v5 < self.config.minimum_directional_share
                and down_c5 < self.config.minimum_directional_share
                and rvol <= 1.05 and rtv <= 1.05
                and pressure > -self.config.minimum_capital_pressure
            )
            if controlled_up:
                self._active_participation_stage = LifecycleStage.PROTECTED
        elif self._active_participation_direction == -1 and close > prev_close:
            controlled_down = (
                metrics.up_evidence_count < self.config.participation_minimum_evidence
                and up_v5 < self.config.minimum_directional_share
                and up_c5 < self.config.minimum_directional_share
                and rvol <= 1.05 and rtv <= 1.05
                and pressure < self.config.minimum_capital_pressure
            )
            if controlled_down:
                self._active_participation_stage = LifecycleStage.PROTECTED

        if index > 0 and self._active_participation_direction != 0:
            prev = self._metrics_history[-2] if len(self._metrics_history) >= 2 else None
            if prev and prev.data_ready:
                prev_rvol = prev.rvol or rvol
                prev_rtv = prev.rtv or rtv
                prev_progress = prev.net_progress_atr or 0.0
                prev_eff = prev.directional_efficiency or 0.0
                evidence = 0
                evidence += int(rvol < prev_rvol * self.lifecycle_config.weakening_rvol_drop_factor and (metrics.volume_slope or 0.0) < 0.0)
                evidence += int(rtv < prev_rtv * self.lifecycle_config.weakening_rtv_drop_factor and (metrics.capital_slope or 0.0) < 0.0)
                if self._active_participation_direction == 1:
                    evidence += int((metrics.net_progress_atr or 0.0) < prev_progress and (metrics.net_progress_atr or 0.0) < self.config.minimum_progress_atr * 0.60)
                    evidence += int(metrics.down_evidence_count >= self.config.participation_minimum_evidence - 1)
                else:
                    evidence += int((metrics.net_progress_atr or 0.0) > prev_progress and (metrics.net_progress_atr or 0.0) > -self.config.minimum_progress_atr * 0.60)
                    evidence += int(metrics.up_evidence_count >= self.config.participation_minimum_evidence - 1)
                evidence += int((metrics.directional_efficiency or 0.0) < prev_eff and (metrics.directional_efficiency or 0.0) < self.config.minimum_efficiency)
                if evidence >= self.lifecycle_config.weakening_minimum_evidence:
                    self._active_participation_stage = LifecycleStage.WEAKENING
        return controlled_up, controlled_down

    def _update_absorption(self, index: int, metrics: VolumeParticipationMetrics) -> None:
        if not metrics.data_ready:
            return
        cfg = self.lifecycle_config
        row = self._rows[index]
        close = float(row["close"])
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        total_range = high - low
        close_loc = _safe_div(close - low, total_range, 0.5)
        upper_wick = _safe_div(high - max(open_, close), total_range)
        lower_wick = _safe_div(min(open_, close) - low, total_range)
        prev_metrics = self._metrics_history[-2] if len(self._metrics_history) >= 2 else None
        prev_progress = prev_metrics.net_progress_atr if prev_metrics and prev_metrics.net_progress_atr is not None else metrics.net_progress_atr or 0.0
        prev_eff = prev_metrics.directional_efficiency if prev_metrics and prev_metrics.directional_efficiency is not None else metrics.directional_efficiency or 0.0
        upper_deteriorating = (metrics.net_progress_atr or 0.0) < prev_progress or (metrics.directional_efficiency or 0.0) < prev_eff
        lower_deteriorating = (metrics.net_progress_atr or 0.0) > prev_progress or (metrics.directional_efficiency or 0.0) < prev_eff
        volume_change = 0.0
        capital_change = 0.0
        if index > 0:
            prev = self._rows[index - 1]
            vavg = sum(float(r["volume"]) for r in self._rows[max(0, index - self.config.volume_average_length + 1): index + 1]) / min(index + 1, self.config.volume_average_length)
            volume_change = _safe_div(float(row["volume"]) - float(prev["volume"]), vavg)
            tv = float(row["volume"]) * ((high + low + close) / 3.0)
            ptv = float(prev["volume"]) * ((float(prev["high"]) + float(prev["low"]) + float(prev["close"])) / 3.0)
            capital_change = _safe_div(tv - ptv, max(abs(ptv), 1e-12))

        upper_ref, upper_atr, upper_source = self._nearest_reference(upper=True, index=index)
        lower_ref, lower_atr, lower_source = self._nearest_reference(upper=False, index=index)
        effort_mismatch = metrics.effort_result_class in {EffortResultClass.HIGH_EFFORT_WEAK_RESULT, EffortResultClass.VERY_HIGH_EFFORT_WEAK_RESULT}
        upper_evidence = sum((
            upper_wick >= cfg.absorption_minimum_wick_ratio,
            close_loc < 0.60,
            (metrics.directional_efficiency or 0.0) <= self.config.weak_result_efficiency_limit,
            abs(metrics.net_progress_atr or 0.0) <= self.config.weak_result_progress_limit,
            upper_deteriorating,
            volume_change > 0.0,
            capital_change > 0.0,
            (metrics.directional_value_pressure_5 or 0.0) < self.config.minimum_capital_pressure,
        ))
        lower_evidence = sum((
            lower_wick >= cfg.absorption_minimum_wick_ratio,
            close_loc > 0.40,
            (metrics.directional_efficiency or 0.0) <= self.config.weak_result_efficiency_limit,
            abs(metrics.net_progress_atr or 0.0) <= self.config.weak_result_progress_limit,
            lower_deteriorating,
            volume_change > 0.0,
            capital_change > 0.0,
            (metrics.directional_value_pressure_5 or 0.0) > -self.config.minimum_capital_pressure,
        ))
        upper_signal = effort_mismatch and upper_ref is not None and high >= float(self._rows[index - 1]["high"]) and (close > open_ or (metrics.net_progress_atr or 0.0) >= 0.0) and (upper_wick >= cfg.absorption_minimum_wick_ratio or close_loc < 0.60) and upper_evidence >= cfg.absorption_minimum_evidence
        lower_signal = effort_mismatch and lower_ref is not None and low <= float(self._rows[index - 1]["low"]) and (close < open_ or (metrics.net_progress_atr or 0.0) <= 0.0) and (lower_wick >= cfg.absorption_minimum_wick_ratio or close_loc > 0.40) and lower_evidence >= cfg.absorption_minimum_evidence
        if upper_signal and lower_signal:
            if upper_wick > lower_wick and close_loc < 0.50:
                lower_signal = False
            elif lower_wick > upper_wick and close_loc > 0.50:
                upper_signal = False
            else:
                upper_signal = lower_signal = False

        active = self._absorption
        if active.stage == AbsorptionStage.CANDIDATE and active.candidate_index is not None:
            age = index - active.candidate_index
            if active.side == AbsorptionSide.UPPER:
                invalid = close > float(active.candidate_high) + float(active.frozen_buffer) and metrics.up_evidence_count >= self.config.participation_minimum_evidence and (metrics.directional_value_pressure_5 or 0.0) >= self.config.minimum_capital_pressure
                confirm = index > active.candidate_index and age <= cfg.absorption_confirmation_window and high <= float(active.candidate_high) + float(active.frozen_buffer) and (close <= float(active.candidate_mid) or ((metrics.net_progress_atr or 0.0) <= 0.0 and metrics.down_evidence_count >= self.config.participation_minimum_evidence - 1 and (metrics.directional_value_pressure_5 or 0.0) <= 0.0))
            else:
                invalid = close < float(active.candidate_low) - float(active.frozen_buffer) and metrics.down_evidence_count >= self.config.participation_minimum_evidence and (metrics.directional_value_pressure_5 or 0.0) <= -self.config.minimum_capital_pressure
                confirm = index > active.candidate_index and age <= cfg.absorption_confirmation_window and low >= float(active.candidate_low) - float(active.frozen_buffer) and (close >= float(active.candidate_mid) or ((metrics.net_progress_atr or 0.0) >= 0.0 and metrics.up_evidence_count >= self.config.participation_minimum_evidence - 1 and (metrics.directional_value_pressure_5 or 0.0) >= 0.0))
            if invalid:
                self._absorption = AbsorptionEvent(side=active.side, stage=AbsorptionStage.INVALIDATED, candidate_index=active.candidate_index, reference_level=active.reference_level, reference_source=active.reference_source, candidate_high=active.candidate_high, candidate_low=active.candidate_low, candidate_mid=active.candidate_mid, frozen_atr=active.frozen_atr, frozen_buffer=active.frozen_buffer, evidence_count=active.evidence_count)
                return
            if confirm:
                self._absorption = AbsorptionEvent(side=active.side, stage=AbsorptionStage.CONFIRMED, candidate_index=active.candidate_index, reference_level=active.reference_level, reference_source=active.reference_source, candidate_high=active.candidate_high, candidate_low=active.candidate_low, candidate_mid=active.candidate_mid, frozen_atr=active.frozen_atr, frozen_buffer=active.frozen_buffer, evidence_count=active.evidence_count)
                self._absorption_hold_until = index + 2
                return
            if age > cfg.absorption_confirmation_window:
                self._absorption = AbsorptionEvent(side=active.side, stage=AbsorptionStage.EXPIRED, candidate_index=active.candidate_index, reference_level=active.reference_level, reference_source=active.reference_source, candidate_high=active.candidate_high, candidate_low=active.candidate_low, candidate_mid=active.candidate_mid, frozen_atr=active.frozen_atr, frozen_buffer=active.frozen_buffer, evidence_count=active.evidence_count)
                return

        if self._absorption.stage == AbsorptionStage.CONFIRMED and index <= self._absorption_hold_until:
            return
        if index <= self._absorption_hold_until:
            return
        if upper_signal:
            assert upper_ref is not None and upper_atr is not None
            self._absorption = AbsorptionEvent(side=AbsorptionSide.UPPER, stage=AbsorptionStage.CANDIDATE, candidate_index=index, reference_level=upper_ref, reference_source=upper_source, candidate_high=high, candidate_low=low, candidate_mid=(high + low) * 0.5, frozen_atr=upper_atr, frozen_buffer=upper_atr * cfg.break_buffer_atr, evidence_count=upper_evidence)
        elif lower_signal:
            assert lower_ref is not None and lower_atr is not None
            self._absorption = AbsorptionEvent(side=AbsorptionSide.LOWER, stage=AbsorptionStage.CANDIDATE, candidate_index=index, reference_level=lower_ref, reference_source=lower_source, candidate_high=high, candidate_low=low, candidate_mid=(high + low) * 0.5, frozen_atr=lower_atr, frozen_buffer=lower_atr * cfg.break_buffer_atr, evidence_count=lower_evidence)

    def _crossed_break_reference(self, index: int) -> tuple[int, float | None, float | None, str | None]:
        if index <= 0:
            return 0, None, None, None
        close = float(self._rows[index]["close"])
        prev_close = float(self._rows[index - 1]["close"])
        candidates: list[tuple[int, float, float, str]] = []
        if self._last_high_pivot is not None and prev_close <= self._last_high_pivot.price < close:
            candidates.append((1, self._last_high_pivot.price, self._last_high_pivot.atr_at_source, "PIVOT"))
        if self._last_low_pivot is not None and prev_close >= self._last_low_pivot.price > close:
            candidates.append((-1, self._last_low_pivot.price, self._last_low_pivot.atr_at_source, "PIVOT"))
        lookback = self.lifecycle_config.recent_level_lookback
        start = max(0, index - lookback)
        if index > start:
            recent_high = max(float(r["high"]) for r in self._rows[start:index])
            recent_low = min(float(r["low"]) for r in self._rows[start:index])
            atr = self._atr_series()[index]
            if atr is not None:
                if prev_close <= recent_high < close:
                    candidates.append((1, recent_high, float(atr), "RECENT_20"))
                if prev_close >= recent_low > close:
                    candidates.append((-1, recent_low, float(atr), "RECENT_20"))
        if not candidates:
            return 0, None, None, None
        candidates.sort(key=lambda x: abs(close - x[1]))
        return candidates[0]

    def _update_break(self, index: int, metrics: VolumeParticipationMetrics) -> None:
        if not metrics.data_ready:
            return
        cfg = self.lifecycle_config
        close = float(self._rows[index]["close"])
        if self._break.stage in {BreakStage.UNSUPPORTED, BreakStage.RECLAIMED}:
            self._break = BreakParticipationEvent()
        if self._break.stage == BreakStage.NONE:
            direction, level, atr, source = self._crossed_break_reference(index)
            if direction and level is not None and atr is not None:
                close_loc = _safe_div(close - float(self._rows[index]["low"]), float(self._rows[index]["high"]) - float(self._rows[index]["low"]), 0.5)
                valid_attempt = (direction == 1 and close_loc >= 0.55 and (metrics.net_progress_atr or 0.0) > 0.0) or (direction == -1 and close_loc <= 0.45 and (metrics.net_progress_atr or 0.0) < 0.0)
                if valid_attempt:
                    self._break = BreakParticipationEvent(direction=direction, stage=BreakStage.DEVELOPING, start_index=index, level=level, reference_source=source, frozen_atr=atr, frozen_buffer=atr * cfg.break_buffer_atr)
            return

        active = self._break
        if active.start_index is None or active.level is None or active.frozen_buffer is None:
            return
        if active.direction == 1 and close < active.level:
            self._break = BreakParticipationEvent(**{**active.__dict__, "stage": BreakStage.RECLAIMED, "reason": "level reclaimed"})
            return
        if active.direction == -1 and close > active.level:
            self._break = BreakParticipationEvent(**{**active.__dict__, "stage": BreakStage.RECLAIMED, "reason": "level reclaimed"})
            return
        age = index - active.start_index
        rvol_support = (metrics.rvol or 0.0) >= self.config.rising_rvol
        rtv_support = (metrics.rtv or 0.0) >= self.config.rising_rtv
        result_support = metrics.up_evidence_count >= self.config.participation_minimum_evidence if active.direction == 1 else metrics.down_evidence_count >= self.config.participation_minimum_evidence
        accepted = close >= active.level + active.frozen_buffer if active.direction == 1 else close <= active.level - active.frozen_buffer
        if active.stage == BreakStage.DEVELOPING and index > active.start_index and age <= cfg.breakout_confirmation_bars and accepted and rvol_support and rtv_support and result_support:
            self._break = BreakParticipationEvent(**{**active.__dict__, "stage": BreakStage.SUPPORTED})
            return
        if active.stage == BreakStage.DEVELOPING and age > cfg.breakout_confirmation_bars:
            self._break = BreakParticipationEvent(**{**active.__dict__, "stage": BreakStage.UNSUPPORTED, "reason": "participation support incomplete"})
            return
        if active.stage == BreakStage.SUPPORTED:
            row = self._rows[index]
            low_participation = (metrics.rvol or 0.0) <= 1.05 and (metrics.rtv or 0.0) <= 1.05
            retest = float(row["low"]) <= active.level + active.frozen_buffer if active.direction == 1 else float(row["high"]) >= active.level - active.frozen_buffer
            still_accepted = close >= active.level if active.direction == 1 else close <= active.level
            if retest and still_accepted and low_participation:
                self._break = BreakParticipationEvent(**{**active.__dict__, "stage": BreakStage.PROTECTED})

    def _sync_export(self, controlled_up: bool, controlled_down: bool) -> None:
        self.lifecycle_export = ParticipationLifecycleExport(
            participation_direction=self._active_participation_direction,
            participation_stage=self._active_participation_stage.value,
            controlled_pullback=controlled_up,
            controlled_reaction=controlled_down,
            absorption_side=self._absorption.side.value,
            absorption_stage=self._absorption.stage.value,
            absorption_reference_level=self._absorption.reference_level,
            absorption_reference_source=self._absorption.reference_source,
            absorption_frozen_atr=self._absorption.frozen_atr,
            absorption_frozen_buffer=self._absorption.frozen_buffer,
            break_direction=self._break.direction,
            break_stage=self._break.stage.value,
            break_level=self._break.level,
            break_reference_source=self._break.reference_source,
            break_frozen_atr=self._break.frozen_atr,
            break_frozen_buffer=self._break.frozen_buffer,
            last_pivot_high=self._last_high_pivot.price if self._last_high_pivot else None,
            last_pivot_high_known_index=self._last_high_pivot.known_index if self._last_high_pivot else None,
            last_pivot_low=self._last_low_pivot.price if self._last_low_pivot else None,
            last_pivot_low_known_index=self._last_low_pivot.known_index if self._last_low_pivot else None,
        )

    def update(self, bar: Any) -> EngineResult | None:
        row = dict(bar) if not isinstance(bar, dict) else bar.copy()
        if row.get("is_closed") is False or row.get("is_complete") is False:
            return self._lifecycle_snapshot or self._snapshot
        result = super().update(row)
        if result is None:
            return None
        index = len(self._rows) - 1
        metrics = self._metrics_history[-1]
        self._update_pivot(index)
        controlled_up, controlled_down = self._update_participation_lifecycle(index, metrics)
        self._update_absorption(index, metrics)
        self._update_break(index, metrics)
        self._sync_export(controlled_up, controlled_down)
        events = list(result.events)
        if controlled_up:
            events.append("CONTROLLED_UP_PULLBACK")
        if controlled_down:
            events.append("CONTROLLED_DOWN_REACTION")
        if self._absorption.stage != AbsorptionStage.NONE:
            events.append(f"ABSORPTION_{self._absorption.side.value}_{self._absorption.stage.value}")
        if self._break.stage != BreakStage.NONE:
            events.append(f"BREAK_{self._break.direction}_{self._break.stage.value}")
        enriched = EngineResult(
            engine=result.engine,
            state=result.state,
            timestamp=result.timestamp,
            direction=result.direction,
            score=result.score,
            quality=result.quality,
            levels=result.levels,
            events=tuple(events),
            reasons=result.reasons + (
                f"participation_stage={self._active_participation_stage.value}",
                f"absorption={self._absorption.side.value}/{self._absorption.stage.value}",
                f"break={self._break.direction}/{self._break.stage.value}",
            ),
            is_confirmed=True,
        )
        self._lifecycle_snapshot = enriched
        self._snapshot = enriched
        return enriched
