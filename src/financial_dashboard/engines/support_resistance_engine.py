from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from statistics import median
from typing import Any

import pandas as pd

from .base import BaseEngine
from .models import Direction, EngineResult


class RangeState(StrEnum):
    INSUFFICIENT = "RANGE_INSUFFICIENT"
    CANDIDATE = "RANGE_CANDIDATE"
    GEOMETRY = "RANGE_GEOMETRY"
    DEFINED = "RANGE_DEFINED"
    BREAK_UP = "RANGE_BREAK_UP"
    BREAK_DOWN = "RANGE_BREAK_DOWN"


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
    # Pine geometry identity weights renormalized after removing Wyckoff event linkage.
    return upper_overlap * 0.316 + lower_overlap * 0.316 + mid_similarity * 0.263 + time_overlap * 0.105


def _boundary_blend_for_state(state: RangeState) -> float:
    if state in {RangeState.CANDIDATE, RangeState.GEOMETRY}:
        return 0.35
    if state == RangeState.DEFINED:
        return 0.08
    return 0.0


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
        self.export_contract = SupportResistanceExport()

    def _reset(self) -> None:
        self._rows = []
        self._high_pivots = []
        self._low_pivots = []
        self._snapshot = None
        self._range = RangeSnapshot()
        self._next_range_identity = 1
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
        floor = max(0, now - self.config.max_range_scan)
        values = [p for p in pivots if p.origin_index >= floor and p.known_index <= now]
        return values[-self.config.search_pivots :]

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

    def _build_range(self) -> RangeSnapshot:
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
        raw_start = max(min(p.origin_index for p in highs), min(p.origin_index for p in lows))
        raw_start = max(raw_start, now - self.config.max_range_scan)
        previous = self._range
        identity_score = _identity_score(previous, raw_upper, raw_lower, half, raw_start, now, self.config.min_tick)
        same_identity = previous.valid and identity_score >= self.config.range_identity_min_score

        if same_identity and previous.upper_center is not None and previous.lower_center is not None:
            blend = _boundary_blend_for_state(previous.state)
            upper = previous.upper_center * (1.0 - blend) + raw_upper * blend
            lower = previous.lower_center * (1.0 - blend) + raw_lower * blend
            start = previous.start_index if previous.start_index is not None else raw_start
            identity = previous.identity
        else:
            upper = raw_upper
            lower = raw_lower
            start = raw_start
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
            touch_q * 0.22
            + distribution_q * 0.11
            + duration_q * 0.11
            + overlap * 0.17
            + progress_q * 0.17
            + stability_q * 0.13
            + height_q * 0.09
        )

        basic = height_atr >= self.config.min_range_height_atr * 0.45 and height_atr <= self.config.max_range_height_atr * 1.50 and internal >= 1
        hard = height_atr >= self.config.min_range_height_atr * 0.70 and height_atr <= self.config.max_range_height_atr * 1.25 and internal >= 2
        defined = (
            duration >= self.config.min_range_age
            and upper_touches >= self.config.min_upper_touches
            and lower_touches >= self.config.min_lower_touches
            and self.config.min_range_height_atr <= height_atr <= self.config.max_range_height_atr
            and progress <= self.config.max_net_progress_ratio
            and overlap >= 28.0
            and internal >= 4
            and upper_viol + lower_viol <= 2
            and quality >= self.config.min_range_quality
        )

        state = RangeState.CANDIDATE if not hard else RangeState.DEFINED if defined else RangeState.GEOMETRY
        last_close = float(self._rows[-1]["close"])
        buffer = max(atr * self.config.break_buffer_atr, self.config.min_tick * 2.0)
        if defined and last_close > upper_top + buffer:
            state = RangeState.BREAK_UP
        elif defined and last_close < lower_bottom - buffer:
            state = RangeState.BREAK_DOWN

        return RangeSnapshot(
            valid=basic,
            identity=identity,
            state=state,
            known_index=now,
            start_index=start,
            upper_center=upper,
            upper_top=upper_top,
            upper_bottom=upper_bottom,
            lower_center=lower,
            lower_top=lower_top,
            lower_bottom=lower_bottom,
            mid_price=(upper + lower) * 0.5,
            height=height,
            height_atr=height_atr,
            upper_touches=upper_touches,
            lower_touches=lower_touches,
            internal_swings=internal,
            overlap_score=overlap,
            net_progress_ratio=progress,
            upper_close_violations=upper_viol,
            lower_close_violations=lower_viol,
            boundary_stability=stability_q,
            identity_score=identity_score,
            quality=quality,
        )

    def _publish(self, timestamp: Any) -> EngineResult:
        r = self._range
        direction = Direction.NEUTRAL
        if r.state == RangeState.BREAK_UP:
            direction = Direction.UP
        elif r.state == RangeState.BREAK_DOWN:
            direction = Direction.DOWN
        levels: dict[str, float] = {}
        for key in ("upper_center", "upper_top", "upper_bottom", "lower_center", "lower_top", "lower_bottom", "mid_price"):
            value = getattr(r, key)
            if value is not None:
                levels[key] = float(value)
        reasons = (
            f"range_identity={r.identity}",
            f"identity_score={r.identity_score:.3f}",
            f"touches={r.upper_touches}/{r.lower_touches}",
            f"overlap={r.overlap_score:.2f}",
            f"progress={r.net_progress_ratio:.3f}",
            f"violations={r.upper_close_violations}/{r.lower_close_violations}",
        ) if r.valid else ("insufficient_confirmed_range_geometry",)
        result = EngineResult(
            engine="support_resistance_range",
            state=r.state.value,
            timestamp=timestamp,
            direction=direction,
            score=r.quality if r.valid else None,
            quality=r.quality if r.valid else None,
            levels=levels,
            events=(r.state.value,) if r.valid else (),
            reasons=reasons,
            is_confirmed=True,
        )
        self.export_contract = SupportResistanceExport(
            state=r.state.value if r.valid else None,
            range_identity=r.identity if r.valid else None,
            upper_center=r.upper_center,
            upper_top=r.upper_top,
            upper_bottom=r.upper_bottom,
            lower_center=r.lower_center,
            lower_top=r.lower_top,
            lower_bottom=r.lower_bottom,
            mid_price=r.mid_price,
            quality=r.quality if r.valid else None,
            boundary_stability=r.boundary_stability if r.valid else None,
            identity_score=r.identity_score if r.valid else None,
            upper_touches=r.upper_touches,
            lower_touches=r.lower_touches,
            upper_close_violations=r.upper_close_violations,
            lower_close_violations=r.lower_close_violations,
        )
        self._snapshot = result
        return result

    def update(self, bar: pd.Series | dict[str, Any]) -> EngineResult | None:
        row = dict(bar) if isinstance(bar, dict) else bar.to_dict()
        if not bool(row.get("is_closed", True)) or not bool(row.get("is_complete", True)):
            return self._snapshot
        self._rows.append(row)
        self._confirm_new_pivot()
        self._range = self._build_range()
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
