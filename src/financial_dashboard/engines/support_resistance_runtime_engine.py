from __future__ import annotations

from bisect import bisect_left, bisect_right, insort
from statistics import median
from typing import Any

from .support_resistance_engine import (
    ConfirmedPivot,
    RangeSnapshot,
    RangeState,
    SupportResistanceConfig,
    SupportResistanceRangeEngine,
    _atr,
    _boundary_blend_for_state,
    _clamp100,
    _identity_score,
    _pair_overlap_score,
)


class RuntimeSupportResistanceRangeEngine(SupportResistanceRangeEngine):
    """Exact S/R state machine with indexed immutable-history queries.

    Long-lived range identity may deliberately preserve an old start_index. The
    canonical engine therefore rescans every historical close and recalculates every
    adjacent-bar overlap on each new candle. This facade keeps that semantic start
    intact while indexing threshold counts and caching immutable pair scores.
    """

    _BLOCK_SIZE = 64

    def __init__(self, config: SupportResistanceConfig | None = None) -> None:
        super().__init__(config)
        self._reset_runtime_index()

    def _reset_runtime_index(self) -> None:
        self._runtime_indexed_rows = 0
        self._runtime_closes: list[float] = []
        self._runtime_close_blocks: list[list[float]] = []
        self._runtime_sorted_close_blocks: list[list[float]] = []
        # score[i] is the overlap of rows i-1 and i; index zero has no pair.
        self._runtime_pair_scores: list[float] = []
        # start_index -> (last included pair index, canonical left-to-right sum).
        # Extending the previous sum by one immutable pair preserves exactly the
        # same floating-point addition order as sum(scores[start+1:now+1]).
        self._runtime_overlap_accumulators: dict[int, tuple[int, float]] = {}

    def _reset(self) -> None:
        super()._reset()
        self._reset_runtime_index()

    def _sync_runtime_index(self) -> None:
        if self._runtime_indexed_rows > len(self._rows):
            self._reset_runtime_index()
        while self._runtime_indexed_rows < len(self._rows):
            index = self._runtime_indexed_rows
            row = self._rows[index]
            close = float(row["close"])
            self._runtime_closes.append(close)

            block_index = index // self._BLOCK_SIZE
            if block_index == len(self._runtime_close_blocks):
                self._runtime_close_blocks.append([])
                self._runtime_sorted_close_blocks.append([])
            self._runtime_close_blocks[block_index].append(close)
            insort(self._runtime_sorted_close_blocks[block_index], close)

            if index == 0:
                self._runtime_pair_scores.append(0.0)
            else:
                self._runtime_pair_scores.append(
                    _pair_overlap_score(
                        self._rows[index - 1],
                        row,
                        self.config.min_tick,
                    )
                )
            self._runtime_indexed_rows += 1

    def _recent(self, pivots: list[ConfirmedPivot]) -> list[ConfirmedPivot]:
        if not self._rows:
            return []
        now = len(self._rows) - 1
        floor = max(self._range_epoch_start, now - self.config.max_range_scan)
        selected: list[ConfirmedPivot] = []
        for pivot in reversed(pivots):
            if pivot.known_index > now:
                continue
            if pivot.origin_index < floor:
                break
            selected.append(pivot)
            if len(selected) >= self.config.search_pivots:
                break
        selected.reverse()
        return selected

    def _count_closes(self, start: int, end: int, threshold: float, *, above: bool) -> int:
        self._sync_runtime_index()
        if start > end:
            return 0
        count = 0
        position = max(0, start)
        end = min(end, len(self._runtime_closes) - 1)
        while position <= end:
            block_index = position // self._BLOCK_SIZE
            block_start = block_index * self._BLOCK_SIZE
            block = self._runtime_close_blocks[block_index]
            actual_block_end = block_start + len(block) - 1
            if position == block_start and actual_block_end <= end:
                ordered = self._runtime_sorted_close_blocks[block_index]
                if above:
                    count += len(ordered) - bisect_right(ordered, threshold)
                else:
                    count += bisect_left(ordered, threshold)
                position = actual_block_end + 1
                continue
            value = self._runtime_closes[position]
            count += int(value > threshold if above else value < threshold)
            position += 1
        return count

    def _overlap_score(self, start: int, now: int) -> float:
        self._sync_runtime_index()
        if now <= start:
            return 0.0

        cached = self._runtime_overlap_accumulators.get(start)
        if cached is None:
            last_index = start
            total = 0.0
        else:
            last_index, total = cached

        # Engine replay is monotonic. Keep a defensive exact fallback in case an
        # alternate caller asks for an earlier endpoint after a later one.
        if last_index > now:
            values = self._runtime_pair_scores[start + 1 : now + 1]
            return sum(values) / len(values) if values else 0.0

        first_missing = max(start + 1, last_index + 1)
        for index in range(first_missing, now + 1):
            total += self._runtime_pair_scores[index]
        self._runtime_overlap_accumulators[start] = (now, total)
        return total / float(now - start)

    def _build_geometry(self) -> RangeSnapshot:
        self._sync_runtime_index()
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
        raw_start = max(
            min(p.origin_index for p in highs),
            min(p.origin_index for p in lows),
            self._range_epoch_start,
        )
        raw_start = max(raw_start, now - self.config.max_range_scan)
        previous = self._range
        identity_score = _identity_score(
            previous,
            raw_upper,
            raw_lower,
            half,
            raw_start,
            now,
            self.config.min_tick,
        )
        terminal_previous = previous.state in {RangeState.BREAK_CONFIRMED, RangeState.INVALID}
        same_identity = (
            previous.valid
            and not terminal_previous
            and identity_score >= self.config.range_identity_min_score
        )

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
        internal = sum(
            1
            for p in self._high_pivots + self._low_pivots
            if start <= p.origin_index <= now and lower_bottom <= p.price <= upper_top
        )

        overlap = self._overlap_score(start, now)
        upper_viol = self._count_closes(start, now, upper_top, above=True)
        lower_viol = self._count_closes(start, now, lower_bottom, above=False)
        start_close = float(self._rows[start]["close"]) if self._rows else float(self._rows[-1]["close"])
        progress = abs(float(self._rows[-1]["close"]) - start_close) / max(height, self.config.min_tick)
        progress_q = _clamp100(
            (1.0 - progress / max(self.config.max_net_progress_ratio, 0.05)) * 100.0
        )
        pivot_balance = min(upper_touches, lower_touches) / max(upper_touches, lower_touches, 1)
        swing_balance = 100.0 if internal >= 4 else internal * 25.0
        balance_q = _clamp100(pivot_balance * 70.0 + swing_balance * 0.30)
        touch_q = _clamp100(
            (
                min(upper_touches, self.config.min_upper_touches + 1)
                + min(lower_touches, self.config.min_lower_touches + 1)
            )
            / float(self.config.min_upper_touches + self.config.min_lower_touches + 2)
            * 100.0
        )
        first_touch_values = [v for v in (first_up, first_dn) if v is not None]
        last_touch_values = [v for v in (last_up, last_dn) if v is not None]
        touch_span = (
            (max(last_touch_values) - min(first_touch_values)) / duration
            if first_touch_values and last_touch_values and duration > 0
            else 0.0
        )
        distribution_q = _clamp100(touch_span * 100.0)
        duration_q = _clamp100(
            duration / max(self.config.min_range_age * 1.8, 1.0) * 100.0
        )
        if self.config.min_range_height_atr <= height_atr <= self.config.max_range_height_atr:
            height_q = 100.0
        elif height_atr < self.config.min_range_height_atr:
            height_q = _clamp100(height_atr / self.config.min_range_height_atr * 100.0)
        else:
            height_q = _clamp100(
                (self.config.max_range_height_atr * 1.5 - height_atr)
                / (self.config.max_range_height_atr * 0.5)
                * 100.0
            )
        if same_identity and previous.upper_center is not None and previous.lower_center is not None:
            raw_shift_atr = (
                abs(previous.upper_center - raw_upper)
                + abs(previous.lower_center - raw_lower)
            ) / max(atr, self.config.min_tick)
            stability_q = _clamp100(
                100.0 - raw_shift_atr * 42.0 - (upper_viol + lower_viol) * 5.0
            )
        else:
            stability_q = _clamp100(70.0 - (upper_viol + lower_viol) * 5.0)
        quality = _clamp100(
            touch_q * 0.20
            + distribution_q * 0.105
            + duration_q * 0.105
            + overlap * 0.16
            + progress_q * 0.16
            + stability_q * 0.13
            + height_q * 0.085
            + balance_q * 0.055
        )
        basic = (
            height_atr >= self.config.min_range_height_atr * 0.45
            and height_atr <= self.config.max_range_height_atr * 1.50
            and internal >= 1
        )
        hard = (
            height_atr >= self.config.min_range_height_atr * 0.70
            and height_atr <= self.config.max_range_height_atr * 1.25
            and internal >= 2
        )
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
        last_change = previous.last_state_change_index if same_identity else now
        if same_identity and previous.state in {
            RangeState.DEFINED,
            RangeState.STABILIZING,
            RangeState.ACTIVE,
            RangeState.WEAK,
            RangeState.BREAK_ATTEMPT,
            RangeState.BREAK_FAILED,
        }:
            state = previous.state
        return RangeSnapshot(
            valid=basic,
            identity=identity,
            state=state,
            known_index=now,
            start_index=start,
            last_state_change_index=last_change,
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


__all__ = ["RuntimeSupportResistanceRangeEngine"]
