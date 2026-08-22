from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from statistics import median
from typing import Any

import pandas as pd

from .base import BaseEngine
from .models import Direction, EngineResult
from .support_resistance_zones import (
    SupportResistanceZone,
    SupportResistanceZoneLedger,
    ZoneLifecycleEvent,
)


class RangeState(StrEnum):
    INSUFFICIENT = "RANGE_INSUFFICIENT"
    CANDIDATE = "RANGE_CANDIDATE"
    GEOMETRY = "RANGE_GEOMETRY"
    DEFINED = "RANGE_DEFINED"
    STABILIZING = "RANGE_STABILIZING"
    ACTIVE = "RANGE_ACTIVE"
    WEAK = "RANGE_WEAK"
    BREAK_ATTEMPT = "RANGE_BREAK_ATTEMPT"
    BREAK_CANDIDATE = "RANGE_BREAK_CANDIDATE"
    BREAK_CONFIRMED = "RANGE_BREAK_CONFIRMED"
    BREAK_FAILED = "RANGE_BREAK_FAILED"
    INVALID = "RANGE_INVALID"


@dataclass(frozen=True, slots=True)
class SupportResistanceConfig:
    pivot_span: int = 2
    search_pivots: int = 8
    max_range_scan: int = 120
    min_touch_gap: int = 3
    zone_tolerance_atr: float = 0.16
    min_range_age: int = 18
    min_upper_touches: int = 2
    min_lower_touches: int = 2
    min_range_height_atr: float = 1.60
    max_range_height_atr: float = 8.0
    max_net_progress_ratio: float = 0.35
    min_range_quality: float = 50.0
    range_identity_min_score: float = 0.58
    break_buffer_atr: float = 0.07
    breakout_confirm_window: int = 2
    min_tick: float = 0.01


@dataclass(frozen=True, slots=True)
class ConfirmedPivot:
    side: str
    price: float
    origin_index: int
    known_index: int
    timestamp: Any


@dataclass(frozen=True, slots=True)
class RangeSnapshot:
    valid: bool = False
    identity: int = 0
    state: RangeState = RangeState.INSUFFICIENT
    known_index: int | None = None
    start_index: int | None = None
    last_state_change_index: int | None = None
    upper_center: float | None = None
    upper_top: float | None = None
    upper_bottom: float | None = None
    lower_center: float | None = None
    lower_top: float | None = None
    lower_bottom: float | None = None
    mid_price: float | None = None
    height: float | None = None
    height_atr: float | None = None
    upper_touches: int = 0
    lower_touches: int = 0
    internal_swings: int = 0
    overlap_score: float = 0.0
    net_progress_ratio: float = 0.0
    upper_close_violations: int = 0
    lower_close_violations: int = 0
    boundary_stability: float = 0.0
    identity_score: float = 0.0
    quality: float = 0.0
    break_direction: int = 0
    break_candidate_index: int | None = None
    break_confirmed_index: int | None = None
    break_boundary: float | None = None
    frozen_upper_top: float | None = None
    frozen_lower_bottom: float | None = None
    break_reference_atr: float | None = None
    break_frozen_buffer: float | None = None
    break_return_state: RangeState | None = None


@dataclass(frozen=True, slots=True)
class SupportResistanceExport:
    state: str | None = None
    range_identity: int | None = None
    upper_center: float | None = None
    upper_top: float | None = None
    upper_bottom: float | None = None
    lower_center: float | None = None
    lower_top: float | None = None
    lower_bottom: float | None = None
    mid_price: float | None = None
    quality: float | None = None
    boundary_stability: float | None = None
    identity_score: float | None = None
    upper_touches: int = 0
    lower_touches: int = 0
    upper_close_violations: int = 0
    lower_close_violations: int = 0
    break_direction: int = 0
    break_candidate_index: int | None = None
    break_confirmed_index: int | None = None
    break_boundary: float | None = None
    break_buffer: float | None = None
    price_location: str | None = None
    nearest_support_low: float | None = None
    nearest_support_high: float | None = None
    nearest_resistance_low: float | None = None
    nearest_resistance_high: float | None = None
    role_reversal_support_low: float | None = None
    role_reversal_support_high: float | None = None
    role_reversal_resistance_low: float | None = None
    role_reversal_resistance_high: float | None = None
    contract_version: int = 2
    reference_atr: float | None = None
    zones: tuple[SupportResistanceZone, ...] = ()
    zone_lifecycle_events: tuple[ZoneLifecycleEvent, ...] = ()


def _clamp100(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _atr(rows: list[dict[str, Any]], length: int = 14, min_tick: float = 0.01) -> float:
    if not rows:
        return min_tick
    trs: list[float] = []
    start = max(0, len(rows) - length)
    for i in range(start, len(rows)):
        row = rows[i]
        tr = float(row["high"] - row["low"])
        if i > 0:
            prev = float(rows[i - 1]["close"])
            tr = max(tr, abs(float(row["high"]) - prev), abs(float(row["low"]) - prev))
        trs.append(tr)
    return max(sum(trs) / max(len(trs), 1), min_tick)


def _is_unique_high(rows: list[dict[str, Any]], index: int, span: int) -> bool:
    value = float(rows[index]["high"])
    window = [float(rows[i]["high"]) for i in range(index - span, index + span + 1)]
    return value == max(window) and window.count(value) == 1


def _is_unique_low(rows: list[dict[str, Any]], index: int, span: int) -> bool:
    value = float(rows[index]["low"])
    window = [float(rows[i]["low"]) for i in range(index - span, index + span + 1)]
    return value == min(window) and window.count(value) == 1


def _pair_overlap_score(a: dict[str, Any], b: dict[str, Any], min_tick: float) -> float:
    ah, al = float(a["high"]), float(a["low"])
    bh, bl = float(b["high"]), float(b["low"])
    overlap = max(0.0, min(ah, bh) - max(al, bl))
    union = max(max(ah, bh) - min(al, bl), min_tick)
    abo, abc = float(a["open"]), float(a["close"])
    bbo, bbc = float(b["open"]), float(b["close"])
    a_body_hi, a_body_lo = max(abo, abc), min(abo, abc)
    b_body_hi, b_body_lo = max(bbo, bbc), min(bbo, bbc)
    body_overlap = max(0.0, min(a_body_hi, b_body_hi) - max(a_body_lo, b_body_lo))
    body_union = max(max(a_body_hi, b_body_hi) - min(a_body_lo, b_body_lo), min_tick)
    return _clamp100((overlap / union) * 70.0 + (body_overlap / body_union) * 30.0)


def _interval_overlap_ratio(low_a: float, high_a: float, low_b: float, high_b: float, min_tick: float) -> float:
    intersection = max(0.0, min(high_a, high_b) - max(low_a, low_b))
    union = max(max(high_a, high_b) - min(low_a, low_b), min_tick)
    return max(0.0, min(1.0, intersection / union))


def _identity_score(previous: RangeSnapshot, upper: float, lower: float, half: float, start: int, now: int, min_tick: float) -> float:
    if not previous.valid or previous.upper_top is None or previous.upper_bottom is None or previous.lower_top is None or previous.lower_bottom is None:
        return 0.0
    upper_overlap = _interval_overlap_ratio(previous.upper_bottom, previous.upper_top, upper - half, upper + half, min_tick)
    lower_overlap = _interval_overlap_ratio(previous.lower_bottom, previous.lower_top, lower - half, lower + half, min_tick)
    candidate_mid = (upper + lower) * 0.5
    previous_mid = previous.mid_price if previous.mid_price is not None else candidate_mid
    height_ref = max(abs(upper - lower), previous.height or 0.0, min_tick)
    mid_similarity = max(0.0, min(1.0, 1.0 - abs(previous_mid - candidate_mid) / height_ref))
    time_overlap = 1.0 if previous.start_index is not None and start <= now and previous.start_index <= now else 0.0
    return upper_overlap * 0.316 + lower_overlap * 0.316 + mid_similarity * 0.263 + time_overlap * 0.105


def _boundary_blend_for_state(state: RangeState) -> float:
    if state in {RangeState.CANDIDATE, RangeState.GEOMETRY}:
        return 0.35
    if state == RangeState.DEFINED:
        return 0.08
    if state == RangeState.STABILIZING:
        return 0.04
    return 0.0


_NORMAL_MATURE_STATES = {RangeState.DEFINED, RangeState.STABILIZING, RangeState.ACTIVE, RangeState.WEAK, RangeState.BREAK_ATTEMPT}


class SupportResistanceRangeEngine(BaseEngine):
    """Wyckoff-derived range geometry only; no phase/climax/story semantics."""

    def __init__(self, config: SupportResistanceConfig | None = None) -> None:
        self.config = config or SupportResistanceConfig()
        self._rows: list[dict[str, Any]] = []
        self._high_pivots: list[ConfirmedPivot] = []
        self._low_pivots: list[ConfirmedPivot] = []
        self._snapshot: EngineResult | None = None
        self._range = RangeSnapshot()
        self._next_range_identity = 1
        self._range_epoch_start = 0
        self._role_support: tuple[float, float] | None = None
        self._role_resistance: tuple[float, float] | None = None
        self._role_support_identity: int | None = None
        self._role_resistance_identity: int | None = None
        self._zone_ledger = SupportResistanceZoneLedger(
            role_break_confirm_bars=max(2, self.config.breakout_confirm_window),
            min_tick=self.config.min_tick,
        )
        self._zones: tuple[SupportResistanceZone, ...] = ()
        self.export_contract = SupportResistanceExport()

    def _reset(self) -> None:
        self._rows = []
        self._high_pivots = []
        self._low_pivots = []
        self._snapshot = None
        self._range = RangeSnapshot()
        self._next_range_identity = 1
        self._range_epoch_start = 0
        self._role_support = None
        self._role_resistance = None
        self._role_support_identity = None
        self._role_resistance_identity = None
        self._zone_ledger.reset()
        self._zones = ()
        self.export_contract = SupportResistanceExport()

    def _confirm_new_pivot(self) -> None:
        span = self.config.pivot_span
        if len(self._rows) < span * 2 + 1:
            return
        known_index = len(self._rows) - 1
        origin = known_index - span
        if origin < span:
            return
        row = self._rows[origin]
        if _is_unique_high(self._rows, origin, span):
            self._high_pivots.append(ConfirmedPivot("HIGH", float(row["high"]), origin, known_index, row.get("timestamp")))
        if _is_unique_low(self._rows, origin, span):
            self._low_pivots.append(ConfirmedPivot("LOW", float(row["low"]), origin, known_index, row.get("timestamp")))

    def _recent(self, pivots: list[ConfirmedPivot]) -> list[ConfirmedPivot]:
        if not self._rows:
            return []
        now = len(self._rows) - 1
        floor = max(self._range_epoch_start, now - self.config.max_range_scan)
        return [p for p in pivots if p.origin_index >= floor and p.known_index <= now][-self.config.search_pivots :]

    def _touch_count(self, pivots: list[ConfirmedPivot], center: float, half_width: float, start: int) -> tuple[int, int | None, int | None]:
        count = 0
        first: int | None = None
        last: int | None = None
        for pivot in pivots:
            if pivot.origin_index < start or abs(pivot.price - center) > half_width:
                continue
            if last is None or pivot.origin_index - last >= self.config.min_touch_gap:
                count += 1
                first = pivot.origin_index if first is None else first
                last = pivot.origin_index
        return count, first, last

    def _build_geometry(self) -> RangeSnapshot:
        highs = self._recent(self._high_pivots)
        lows = self._recent(self._low_pivots)
        if len(highs) < 2 or len(lows) < 2:
            return RangeSnapshot()

        raw_upper = float(median([p.price for p in highs]))
        raw_lower = float(median([p.price for p in lows]))
        if raw_upper <= raw_lower:
            return RangeSnapshot()

        now = len(self._rows) - 1
        atr = _atr(self._rows, min_tick=self.config.min_tick)
        half = max(self.config.min_tick * 3.0, atr * self.config.zone_tolerance_atr)
        raw_start = max(min(p.origin_index for p in highs), min(p.origin_index for p in lows), self._range_epoch_start)
        raw_start = max(raw_start, now - self.config.max_range_scan)
        previous = self._range
        identity_score = _identity_score(previous, raw_upper, raw_lower, half, raw_start, now, self.config.min_tick)
        terminal_previous = previous.state in {RangeState.BREAK_CONFIRMED, RangeState.INVALID}
        same_identity = previous.valid and not terminal_previous and identity_score >= self.config.range_identity_min_score

        if same_identity and previous.upper_center is not None and previous.lower_center is not None:
            blend = _boundary_blend_for_state(previous.state)
            upper = previous.upper_center * (1.0 - blend) + raw_upper * blend
            lower = previous.lower_center * (1.0 - blend) + raw_lower * blend
            start = previous.start_index if previous.start_index is not None else raw_start
            identity = previous.identity
        else:
            upper, lower, start = raw_upper, raw_lower, raw_start
            identity = self._next_range_identity
            self._next_range_identity += 1

        upper_top, upper_bottom = upper + half, upper - half
        lower_top, lower_bottom = lower + half, lower - half
        duration = now - start
        height = upper - lower
        height_atr = height / max(atr, self.config.min_tick)
        upper_touches, first_up, last_up = self._touch_count(highs, upper, half, start)
        lower_touches, first_dn, last_dn = self._touch_count(lows, lower, half, start)
        internal = sum(1 for p in self._high_pivots + self._low_pivots if start <= p.origin_index <= now and lower_bottom <= p.price <= upper_top)

        scan = self._rows[start:]
        overlaps = [_pair_overlap_score(scan[i - 1], scan[i], self.config.min_tick) for i in range(1, len(scan))]
        overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0
        upper_viol = sum(1 for r in scan if float(r["close"]) > upper_top)
        lower_viol = sum(1 for r in scan if float(r["close"]) < lower_bottom)
        start_close = float(scan[0]["close"]) if scan else float(self._rows[-1]["close"])
        progress = abs(float(self._rows[-1]["close"]) - start_close) / max(height, self.config.min_tick)
        progress_q = _clamp100((1.0 - progress / max(self.config.max_net_progress_ratio, 0.05)) * 100.0)
        pivot_balance = min(upper_touches, lower_touches) / max(upper_touches, lower_touches, 1)
        swing_balance = 100.0 if internal >= 4 else internal * 25.0
        balance_q = _clamp100(pivot_balance * 70.0 + swing_balance * 0.30)
        touch_q = _clamp100((min(upper_touches, self.config.min_upper_touches + 1) + min(lower_touches, self.config.min_lower_touches + 1)) / float(self.config.min_upper_touches + self.config.min_lower_touches + 2) * 100.0)
        first_touch_values = [v for v in (first_up, first_dn) if v is not None]
        last_touch_values = [v for v in (last_up, last_dn) if v is not None]
        touch_span = ((max(last_touch_values) - min(first_touch_values)) / duration) if first_touch_values and last_touch_values and duration > 0 else 0.0
        distribution_q = _clamp100(touch_span * 100.0)
        duration_q = _clamp100(duration / max(self.config.min_range_age * 1.8, 1.0) * 100.0)
        if self.config.min_range_height_atr <= height_atr <= self.config.max_range_height_atr:
            height_q = 100.0
        elif height_atr < self.config.min_range_height_atr:
            height_q = _clamp100(height_atr / self.config.min_range_height_atr * 100.0)
        else:
            height_q = _clamp100((self.config.max_range_height_atr * 1.5 - height_atr) / (self.config.max_range_height_atr * 0.5) * 100.0)
        if same_identity and previous.upper_center is not None and previous.lower_center is not None:
            raw_shift_atr = (abs(previous.upper_center - raw_upper) + abs(previous.lower_center - raw_lower)) / max(atr, self.config.min_tick)
            stability_q = _clamp100(100.0 - raw_shift_atr * 42.0 - (upper_viol + lower_viol) * 5.0)
        else:
            stability_q = _clamp100(70.0 - (upper_viol + lower_viol) * 5.0)
        quality = _clamp100(
            touch_q * 0.20 + distribution_q * 0.105 + duration_q * 0.105 + overlap * 0.16
            + progress_q * 0.16 + stability_q * 0.13 + height_q * 0.085 + balance_q * 0.055
        )
        basic = height_atr >= self.config.min_range_height_atr * 0.45 and height_atr <= self.config.max_range_height_atr * 1.50 and internal >= 1
        hard = height_atr >= self.config.min_range_height_atr * 0.70 and height_atr <= self.config.max_range_height_atr * 1.25 and internal >= 2
        defined = (
            duration >= self.config.min_range_age and upper_touches >= self.config.min_upper_touches
            and lower_touches >= self.config.min_lower_touches
            and self.config.min_range_height_atr <= height_atr <= self.config.max_range_height_atr
            and progress <= self.config.max_net_progress_ratio and overlap >= 28.0 and internal >= 4
            and upper_viol + lower_viol <= 2 and quality >= self.config.min_range_quality
        )
        state = RangeState.CANDIDATE if not hard else RangeState.DEFINED if defined else RangeState.GEOMETRY
        last_change = previous.last_state_change_index if same_identity else now
        if same_identity and previous.state in {RangeState.DEFINED, RangeState.STABILIZING, RangeState.ACTIVE, RangeState.WEAK, RangeState.BREAK_ATTEMPT, RangeState.BREAK_FAILED}:
            state = previous.state
        return RangeSnapshot(
            valid=basic, identity=identity, state=state, known_index=now, start_index=start,
            last_state_change_index=last_change, upper_center=upper, upper_top=upper_top, upper_bottom=upper_bottom,
            lower_center=lower, lower_top=lower_top, lower_bottom=lower_bottom, mid_price=(upper + lower) * 0.5,
            height=height, height_atr=height_atr, upper_touches=upper_touches, lower_touches=lower_touches,
            internal_swings=internal, overlap_score=overlap, net_progress_ratio=progress,
            upper_close_violations=upper_viol, lower_close_violations=lower_viol,
            boundary_stability=stability_q, identity_score=identity_score, quality=quality,
            break_direction=previous.break_direction if same_identity else 0,
            break_candidate_index=previous.break_candidate_index if same_identity else None,
            break_confirmed_index=previous.break_confirmed_index if same_identity else None,
            break_boundary=previous.break_boundary if same_identity else None,
            frozen_upper_top=previous.frozen_upper_top if same_identity else None,
            frozen_lower_bottom=previous.frozen_lower_bottom if same_identity else None,
            break_reference_atr=previous.break_reference_atr if same_identity else None,
            break_frozen_buffer=previous.break_frozen_buffer if same_identity else None,
            break_return_state=previous.break_return_state if same_identity else None,
        )

    def _advance_lifecycle(self, geometry: RangeSnapshot) -> RangeSnapshot:
        if not geometry.valid:
            return geometry
        now = len(self._rows) - 1
        prev = self._range
        same = prev.valid and prev.identity == geometry.identity
        state = geometry.state
        last_change = geometry.last_state_change_index if geometry.last_state_change_index is not None else now

        if same and prev.state == RangeState.BREAK_CONFIRMED:
            return prev

        if same and prev.state == RangeState.BREAK_FAILED:
            geometry_ok = geometry.height_atr is not None and self.config.min_range_height_atr * 0.45 <= geometry.height_atr <= self.config.max_range_height_atr * 1.50
            return replace(
                geometry,
                state=prev.break_return_state if geometry_ok and prev.break_return_state in _NORMAL_MATURE_STATES else RangeState.INVALID,
                last_state_change_index=now,
                break_direction=0,
                break_candidate_index=None,
                break_confirmed_index=None,
                break_boundary=None,
                frozen_upper_top=None,
                frozen_lower_bottom=None,
                break_reference_atr=None,
                break_frozen_buffer=None,
                break_return_state=None,
            )

        if same and prev.state == RangeState.BREAK_CANDIDATE:
            frozen_upper = prev.frozen_upper_top if prev.frozen_upper_top is not None else prev.break_boundary
            frozen_lower = prev.frozen_lower_bottom if prev.frozen_lower_bottom is not None else prev.break_boundary
            buffer = prev.break_frozen_buffer or self.config.min_tick
            age = now - (prev.break_candidate_index if prev.break_candidate_index is not None else now)
            close = float(self._rows[-1]["close"])
            accepted = (prev.break_direction == 1 and frozen_upper is not None and close > frozen_upper + buffer) or (prev.break_direction == -1 and frozen_lower is not None and close < frozen_lower - buffer)
            returned_inside = age >= 1 and ((prev.break_direction == 1 and frozen_upper is not None and close <= frozen_upper) or (prev.break_direction == -1 and frozen_lower is not None and close >= frozen_lower))
            mid = prev.mid_price if prev.mid_price is not None else geometry.mid_price
            strong_opposite = age >= 1 and mid is not None and ((prev.break_direction == 1 and close < mid - buffer) or (prev.break_direction == -1 and close > mid + buffer))
            if age >= 1 and age <= self.config.breakout_confirm_window and accepted:
                if prev.break_direction == 1 and prev.upper_bottom is not None and prev.frozen_upper_top is not None:
                    self._role_support = (prev.upper_bottom, prev.frozen_upper_top)
                    self._role_support_identity = prev.identity
                elif prev.break_direction == -1 and prev.frozen_lower_bottom is not None and prev.lower_top is not None:
                    self._role_resistance = (prev.frozen_lower_bottom, prev.lower_top)
                    self._role_resistance_identity = prev.identity
                self._range_epoch_start = now
                return replace(prev, state=RangeState.BREAK_CONFIRMED, known_index=now, break_confirmed_index=now, last_state_change_index=now)
            if returned_inside or strong_opposite or age > self.config.breakout_confirm_window:
                return replace(prev, state=RangeState.BREAK_FAILED, known_index=now, last_state_change_index=now)
            return replace(prev, known_index=now)

        if state == RangeState.DEFINED and same:
            age = now - (prev.last_state_change_index if prev.last_state_change_index is not None else now)
            permanent_violation = geometry.upper_close_violations + geometry.lower_close_violations > 2
            if prev.state == RangeState.DEFINED and age >= self.config.min_touch_gap and geometry.boundary_stability >= 52 and not permanent_violation:
                state, last_change = RangeState.STABILIZING, now
            elif prev.state == RangeState.STABILIZING and geometry.boundary_stability >= 58 and geometry.quality >= self.config.min_range_quality and geometry.upper_touches >= self.config.min_upper_touches and geometry.lower_touches >= self.config.min_lower_touches and geometry.net_progress_ratio <= self.config.max_net_progress_ratio and not permanent_violation:
                state, last_change = RangeState.ACTIVE, now
            elif prev.state in {RangeState.ACTIVE, RangeState.WEAK, RangeState.BREAK_ATTEMPT}:
                state = prev.state

        if state in _NORMAL_MATURE_STATES:
            close = float(self._rows[-1]["close"])
            high = float(self._rows[-1]["high"])
            low = float(self._rows[-1]["low"])
            upper = geometry.upper_top
            lower = geometry.lower_bottom
            if upper is not None and lower is not None:
                atr = _atr(self._rows, min_tick=self.config.min_tick)
                buffer = max(atr * self.config.break_buffer_atr, self.config.min_tick)
                close_inside = lower <= close <= upper
                upward_wick = high > upper and close <= upper
                downward_wick = low < lower and close >= lower
                upward_break = close > upper + buffer
                downward_break = close < lower - buffer
                if upward_break or downward_break:
                    direction = 1 if upward_break and not downward_break else -1 if downward_break and not upward_break else (1 if geometry.mid_price is not None and close >= geometry.mid_price else -1)
                    return replace(
                        geometry,
                        state=RangeState.BREAK_CANDIDATE,
                        last_state_change_index=now,
                        break_direction=direction,
                        break_candidate_index=now,
                        break_confirmed_index=None,
                        break_boundary=upper if direction == 1 else lower,
                        frozen_upper_top=upper,
                        frozen_lower_bottom=lower,
                        break_reference_atr=atr,
                        break_frozen_buffer=buffer,
                        break_return_state=prev.state if same and prev.state in _NORMAL_MATURE_STATES else state,
                    )
                if upward_wick or downward_wick:
                    direction = 1 if upward_wick and not downward_wick else -1 if downward_wick and not upward_wick else (1 if geometry.mid_price is not None and close >= geometry.mid_price else -1)
                    return replace(geometry, state=RangeState.BREAK_ATTEMPT, last_state_change_index=now, break_direction=direction, break_return_state=state)
                if same and prev.state == RangeState.BREAK_ATTEMPT and close_inside:
                    state = prev.break_return_state if prev.break_return_state in _NORMAL_MATURE_STATES else RangeState.WEAK
                    last_change = now

            weak_evidence = 0
            weak_evidence += int(geometry.boundary_stability < 48)
            weak_evidence += int(geometry.quality < self.config.min_range_quality)
            weak_evidence += int(geometry.net_progress_ratio > self.config.max_net_progress_ratio)
            weak_evidence += int(geometry.upper_close_violations + geometry.lower_close_violations >= 2)
            weak_evidence += int(geometry.overlap_score < 24)
            if state == RangeState.ACTIVE and weak_evidence >= 2:
                state, last_change = RangeState.WEAK, now
            elif state == RangeState.WEAK:
                recovered = geometry.quality >= self.config.min_range_quality and geometry.boundary_stability >= 58 and geometry.net_progress_ratio <= self.config.max_net_progress_ratio and geometry.overlap_score >= 28 and geometry.lower_bottom is not None and geometry.upper_top is not None and geometry.lower_bottom <= float(self._rows[-1]["close"]) <= geometry.upper_top
                if recovered:
                    state, last_change = RangeState.ACTIVE, now

        return replace(geometry, state=state, last_state_change_index=last_change)

    def _level_context(self, close: float) -> tuple[str, tuple[float, float] | None, tuple[float, float] | None]:
        r = self._range
        location = "NO_ACTIVE_RANGE"
        if r.valid and r.upper_top is not None and r.upper_bottom is not None and r.lower_top is not None and r.lower_bottom is not None:
            if close > r.upper_top:
                location = "ABOVE_RANGE"
            elif close >= r.upper_bottom:
                location = "UPPER_ZONE"
            elif close > r.lower_top:
                location = "INSIDE_RANGE"
            elif close >= r.lower_bottom:
                location = "LOWER_ZONE"
            else:
                location = "BELOW_RANGE"

        supports: list[tuple[float, float]] = []
        resistances: list[tuple[float, float]] = []
        if r.valid and r.lower_bottom is not None and r.lower_top is not None and close >= r.lower_bottom:
            supports.append((r.lower_bottom, r.lower_top))
        if r.valid and r.upper_bottom is not None and r.upper_top is not None and close <= r.upper_top:
            resistances.append((r.upper_bottom, r.upper_top))
        if self._role_support is not None and close >= self._role_support[0]:
            supports.append(self._role_support)
        if self._role_resistance is not None and close <= self._role_resistance[1]:
            resistances.append(self._role_resistance)

        def distance(zone: tuple[float, float]) -> float:
            low, high = zone
            if low <= close <= high:
                return 0.0
            return min(abs(close - low), abs(close - high))

        nearest_support = min(supports, key=distance) if supports else None
        nearest_resistance = min(resistances, key=distance) if resistances else None
        return location, nearest_support, nearest_resistance

    def _publish(self, timestamp: Any) -> EngineResult:
        r = self._range
        direction = Direction.NEUTRAL
        if r.state == RangeState.BREAK_CONFIRMED:
            direction = Direction.UP if r.break_direction == 1 else Direction.DOWN if r.break_direction == -1 else Direction.NEUTRAL
        close = float(self._rows[-1]["close"]) if self._rows else 0.0
        location, nearest_support, nearest_resistance = self._level_context(close)
        levels: dict[str, float] = {}
        for key in ("upper_center", "upper_top", "upper_bottom", "lower_center", "lower_top", "lower_bottom", "mid_price", "break_boundary"):
            value = getattr(r, key)
            if value is not None:
                levels[key] = float(value)
        if nearest_support is not None:
            levels["nearest_support_low"], levels["nearest_support_high"] = nearest_support
        if nearest_resistance is not None:
            levels["nearest_resistance_low"], levels["nearest_resistance_high"] = nearest_resistance
        if self._role_support is not None:
            levels["role_reversal_support_low"], levels["role_reversal_support_high"] = self._role_support
        if self._role_resistance is not None:
            levels["role_reversal_resistance_low"], levels["role_reversal_resistance_high"] = self._role_resistance
        reasons = (
            f"range_identity={r.identity}", f"identity_score={r.identity_score:.3f}",
            f"touches={r.upper_touches}/{r.lower_touches}", f"overlap={r.overlap_score:.2f}",
            f"progress={r.net_progress_ratio:.3f}", f"violations={r.upper_close_violations}/{r.lower_close_violations}",
            f"price_location={location}",
        ) if r.valid else ("insufficient_confirmed_range_geometry", f"price_location={location}")
        events: tuple[str, ...] = ()
        if r.valid:
            events = (r.state.value,)
            if r.break_direction:
                events += (("BREAK_UP" if r.break_direction == 1 else "BREAK_DOWN"),)
        result = EngineResult(
            engine="support_resistance_range", state=r.state.value, timestamp=timestamp,
            direction=direction, score=r.quality if r.valid else None, quality=r.quality if r.valid else None,
            levels=levels, events=events, reasons=reasons, is_confirmed=True,
        )
        self.export_contract = SupportResistanceExport(
            state=r.state.value if r.valid else None, range_identity=r.identity if r.valid else None,
            upper_center=r.upper_center, upper_top=r.upper_top, upper_bottom=r.upper_bottom,
            lower_center=r.lower_center, lower_top=r.lower_top, lower_bottom=r.lower_bottom,
            mid_price=r.mid_price, quality=r.quality if r.valid else None,
            boundary_stability=r.boundary_stability if r.valid else None,
            identity_score=r.identity_score if r.valid else None,
            upper_touches=r.upper_touches, lower_touches=r.lower_touches,
            upper_close_violations=r.upper_close_violations, lower_close_violations=r.lower_close_violations,
            break_direction=r.break_direction, break_candidate_index=r.break_candidate_index,
            break_confirmed_index=r.break_confirmed_index, break_boundary=r.break_boundary,
            break_buffer=r.break_frozen_buffer, price_location=location,
            nearest_support_low=nearest_support[0] if nearest_support else None,
            nearest_support_high=nearest_support[1] if nearest_support else None,
            nearest_resistance_low=nearest_resistance[0] if nearest_resistance else None,
            nearest_resistance_high=nearest_resistance[1] if nearest_resistance else None,
            role_reversal_support_low=self._role_support[0] if self._role_support else None,
            role_reversal_support_high=self._role_support[1] if self._role_support else None,
            role_reversal_resistance_low=self._role_resistance[0] if self._role_resistance else None,
            role_reversal_resistance_high=self._role_resistance[1] if self._role_resistance else None,
            reference_atr=_atr(self._rows, min_tick=self.config.min_tick) if self._rows else None,
            zones=self._zones,
            zone_lifecycle_events=self._zone_ledger.events,
        )
        self._snapshot = result
        return result

    def update(self, bar: pd.Series | dict[str, Any]) -> EngineResult | None:
        row = dict(bar) if isinstance(bar, dict) else bar.to_dict()
        if not bool(row.get("is_closed", True)) or not bool(row.get("is_complete", True)):
            return self._snapshot
        self._rows.append(row)
        self._confirm_new_pivot()
        geometry = self._build_geometry()
        self._range = self._advance_lifecycle(geometry)
        reference_atr = _atr(self._rows, min_tick=self.config.min_tick)
        self._zones = self._zone_ledger.observe(
            self._range,
            role_support=self._role_support,
            role_support_identity=self._role_support_identity,
            role_resistance=self._role_resistance,
            role_resistance_identity=self._role_resistance_identity,
            bar_index=len(self._rows) - 1,
            timestamp=row.get("timestamp"),
            close=float(row["close"]),
            reference_atr=reference_atr,
        )
        return self._publish(row.get("timestamp"))

    def replay(self, frame: pd.DataFrame) -> list[EngineResult]:
        self._reset()
        results: list[EngineResult] = []
        for _, bar in frame.iterrows():
            result = self.update(bar)
            if result is not None:
                results.append(result)
        return results

    def snapshot(self) -> EngineResult | None:
        return self._snapshot

    @property
    def confirmed_pivots(self) -> tuple[ConfirmedPivot, ...]:
        return tuple(sorted(self._high_pivots + self._low_pivots, key=lambda p: (p.known_index, p.origin_index, p.side)))

    @property
    def zones(self) -> tuple[SupportResistanceZone, ...]:
        return self._zones

    @property
    def active_zones(self) -> tuple[SupportResistanceZone, ...]:
        return self._zone_ledger.active()

    @property
    def zone_lifecycle_events(self) -> tuple[ZoneLifecycleEvent, ...]:
        return self._zone_ledger.events
