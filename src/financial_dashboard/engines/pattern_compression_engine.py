from __future__ import annotations

import copy
from dataclasses import dataclass
from itertools import combinations
from typing import Any

import pandas as pd

from .base import BaseEngine
from .models import Direction, EngineResult
from .pattern_compression_active import (
    evaluate_normal_state,
    is_break_lifecycle,
    is_terminal,
    refresh_active_candidate,
    reset_quality_snapshot,
)
from .pattern_compression_core import (
    ATR_LENGTH,
    SEARCH_PIVOTS,
    ST_BREAK_ATTEMPT,
    ST_BREAK_CANDIDATE,
    ST_BREAK_CONFIRMED,
    ST_BREAK_FAILED,
    ST_BREAK_TIMEOUT,
    ST_CANDIDATE,
    ST_COMPLETED,
    ST_INVALID,
    ST_NONE,
    ST_RETEST_OK,
    PatternCandidate,
    PatternCompressionConfig,
    PivotStore,
    export_break_state,
    export_pattern_state,
    export_pattern_type,
    export_retest_state,
    line_price,
)
from .pattern_compression_geometry import PatternGeometryEvaluator
from .pattern_compression_runtime import LifecycleBar, PatternLifecycleConfig, PatternLifecycleRuntime
from .pattern_compression_selection import (
    CompletedPatternReference,
    candidate_preferred,
    continuity_score,
    effective_raw_quality,
    identity_compatible,
    same_completed_structure,
    selection_score,
    should_replace_active,
)
from .pattern_compression_specialized import PatternPoleEvaluator, SpecializedPatternEvaluator


@dataclass(frozen=True, slots=True)
class PatternExport:
    state: int | None = None
    pattern_type: int | None = None
    quality: float | None = None
    classic_direction: int | None = None
    break_state: int | None = None
    break_level: float | None = None
    break_strength: float | None = None
    retest_state: int | None = None
    retest_tolerance: float | None = None
    identity: float | None = None


class PatternCompressionEngine(BaseEngine):
    """Integrated ARGENT Pattern/Compression v0.4.6 engine.

    The engine keeps Pine chronology explicit: only closed candles mutate state,
    centered pivots are accepted after their right-side confirmation delay, expensive
    candidate scans run only when a new pivot is accepted, and breakout/retest state
    is delegated to the validated lifecycle runtime.
    """

    name = "pattern_compression"

    def __init__(
        self,
        config: PatternCompressionConfig | None = None,
        *,
        use_breakout_quality_filter: bool = True,
    ) -> None:
        self.config = config or PatternCompressionConfig()
        self.profile = self.config.resolve()
        self.use_breakout_quality_filter = use_breakout_quality_filter
        self.reset()

    def reset(self) -> None:
        self.profile = self.config.resolve()
        self._rows: list[dict[str, Any]] = []
        self._tr_values: list[float] = []
        self._atr_values: list[float | None] = []
        self._volumes: list[float | None] = []
        self._store = PivotStore(self.config)
        self._active = PatternCandidate()
        self._pattern_state = ST_NONE
        self._invalid_reason = "Yeterli teyitli geometri yok"
        self._next_pattern_identity = 0
        self._completed: CompletedPatternReference | None = None
        self._lifecycle: PatternLifecycleRuntime | None = None
        self._snapshot: EngineResult | None = None
        self._export: PatternExport | None = None

    def replay(self, frame: pd.DataFrame) -> list[EngineResult]:
        self.reset()
        results: list[EngineResult] = []
        for _, row in frame.iterrows():
            result = self.update(row)
            if result is not None:
                results.append(result)
        return results

    def snapshot(self) -> EngineResult | None:
        return self._snapshot

    @property
    def active_candidate(self) -> PatternCandidate:
        return copy.deepcopy(self._active)

    @property
    def pivot_store(self) -> PivotStore:
        return copy.deepcopy(self._store)

    @property
    def pattern_state(self) -> str:
        return self._pattern_state

    @property
    def export_contract(self) -> PatternExport | None:
        return self._export

    @property
    def break_direction(self) -> int:
        return self._lifecycle.break_direction if self._lifecycle is not None else 0

    def update(self, bar: pd.Series | dict[str, Any]) -> EngineResult | None:
        row = dict(bar) if isinstance(bar, dict) else bar.to_dict()
        if not bool(row.get("is_closed", True)):
            return self._snapshot

        required = ("timestamp", "open", "high", "low", "close")
        missing = [key for key in required if key not in row or pd.isna(row[key])]
        if missing:
            raise ValueError(f"pattern compression requires closed OHLC bars; missing {missing}")

        clean = {
            "timestamp": row["timestamp"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": None if "volume" not in row or pd.isna(row["volume"]) else float(row["volume"]),
        }
        self._rows.append(clean)
        self._volumes.append(clean["volume"])
        self._append_atr(clean)
        bar_index = len(self._rows) - 1
        safe_atr = self._safe_atr(bar_index)
        previous_state = self._pattern_state
        previous_identity = self._active.identity if self._active.valid else 0
        previous_break_direction = self.break_direction
        events: list[str] = []

        new_pivot = self._process_confirmed_pivot(bar_index, safe_atr, events)
        if new_pivot:
            if self._active.valid and not is_terminal(self._pattern_state):
                self._refresh_active(bar_index, safe_atr)
                self._refresh_selection_score(bar_index, safe_atr)
            self._scan_candidates(bar_index, safe_atr, events)

        if self._active.valid:
            self._refresh_active(bar_index, safe_atr)
            self._refresh_selection_score(bar_index, safe_atr)
            self._advance_state(bar_index, safe_atr)
        else:
            self._pattern_state = ST_NONE
            self._invalid_reason = "Yeterli teyitli geometri yok"
            self._lifecycle = None

        if previous_identity != (self._active.identity if self._active.valid else 0):
            if self._active.valid:
                events.append(f"PATTERN_NEW:{self._active.pattern_type}:{self._active.identity}")
        if previous_state != self._pattern_state:
            events.append(f"STATE:{previous_state}->{self._pattern_state}")
        if previous_break_direction == 0 and self.break_direction != 0 and self._pattern_state in {ST_BREAK_ATTEMPT, ST_BREAK_CANDIDATE}:
            side = "UP" if self.break_direction > 0 else "DOWN"
            events.append(f"BREAK_START:{side}:{self._active.identity}")
        if self._pattern_state == ST_BREAK_CONFIRMED and previous_state != ST_BREAK_CONFIRMED:
            side = "UP" if self.break_direction > 0 else "DOWN"
            events.append(f"BREAK_CONFIRMED:{side}:{self._active.identity}")
        if self._pattern_state == ST_RETEST_OK and previous_state != ST_RETEST_OK:
            events.append(f"RETEST_OK:{self._active.identity}")
        if self._pattern_state == ST_COMPLETED and previous_state != ST_COMPLETED:
            events.append(f"PATTERN_COMPLETED:{self._active.identity}")
        if self._pattern_state == ST_INVALID and previous_state != ST_INVALID:
            events.append(f"PATTERN_INVALID:{self._active.identity}")
        if self._pattern_state == ST_BREAK_FAILED and previous_state != ST_BREAK_FAILED:
            events.append(f"BREAK_FAILED:{self._active.identity}")

        self._export = self._build_export(bar_index)
        self._snapshot = self._build_result(clean, events)
        return self._snapshot

    def _append_atr(self, row: dict[str, Any]) -> None:
        index = len(self._rows) - 1
        previous_close = self._rows[index - 1]["close"] if index > 0 else None
        true_range = row["high"] - row["low"]
        if previous_close is not None:
            true_range = max(
                true_range,
                abs(row["high"] - previous_close),
                abs(row["low"] - previous_close),
            )
        self._tr_values.append(float(true_range))
        length = ATR_LENGTH
        if len(self._tr_values) < length:
            atr = None
        elif len(self._tr_values) == length:
            atr = sum(self._tr_values[-length:]) / length
        else:
            previous_atr = self._atr_values[-1]
            atr = (
                sum(self._tr_values[-length:]) / length
                if previous_atr is None
                else (previous_atr * (length - 1) + true_range) / length
            )
        self._atr_values.append(None if atr is None else float(atr))

    def _safe_atr(self, bar_index: int) -> float:
        value = self._atr_values[bar_index]
        return max(float(value if value is not None else self.config.min_tick * 10.0), self.config.min_tick * 10.0)

    def _atr_at(self, source_bar: int, fallback: float) -> float:
        value = self._atr_values[source_bar] if 0 <= source_bar < len(self._atr_values) else None
        return max(float(value if value is not None else fallback), self.config.min_tick * 10.0)

    def _volume_sma(self, bar_index: int, length: int = 20) -> float | None:
        if bar_index + 1 < length:
            return None
        window = self._volumes[bar_index - length + 1 : bar_index + 1]
        if any(value is None for value in window):
            return None
        return sum(float(value) for value in window if value is not None) / length

    def _is_pivot_high(self, source_bar: int) -> bool:
        length = self.profile.pivot_len
        value = self._rows[source_bar]["high"]
        window = [row["high"] for row in self._rows[source_bar - length : source_bar + length + 1]]
        return value == max(window)

    def _is_pivot_low(self, source_bar: int) -> bool:
        length = self.profile.pivot_len
        value = self._rows[source_bar]["low"]
        window = [row["low"] for row in self._rows[source_bar - length : source_bar + length + 1]]
        return value == min(window)

    def _lock_previous_opposite(self, accepted_side: str) -> None:
        locks = self._store.low_locked if accepted_side == "high" else self._store.high_locked
        if locks:
            locks[-1] = True

    def _accept_pivot(self, *, side: str, price: float, source_bar: int, confirm_bar: int) -> bool:
        previous_type = self._store.last_accepted_pivot_type
        accepted, _ = self._store.add_pivot(
            side=side,
            price=price,
            source_bar=source_bar,
            confirm_bar=confirm_bar,
        )
        if accepted:
            new_type = 1 if side == "high" else -1
            if previous_type == -new_type:
                self._lock_previous_opposite(side)
        return accepted

    def _process_confirmed_pivot(self, bar_index: int, safe_atr: float, events: list[str]) -> bool:
        length = self.profile.pivot_len
        source_bar = bar_index - length
        if source_bar < length or bar_index < length * 2:
            return False
        high_pivot = self._is_pivot_high(source_bar)
        low_pivot = self._is_pivot_low(source_bar)
        if not high_pivot and not low_pivot:
            return False

        source = self._rows[source_bar]
        source_atr = self._atr_at(source_bar, safe_atr)
        accepted_high = False
        accepted_low = False
        if high_pivot and low_pivot:
            selected, reason, _, _ = self._store.choose_same_bar_pivot(
                high_candidate=float(source["high"]),
                low_candidate=float(source["low"]),
                source_atr=source_atr,
                source_open=float(source["open"]),
                source_high=float(source["high"]),
                source_low=float(source["low"]),
                source_close=float(source["close"]),
            )
            if selected == 1:
                accepted_high = self._accept_pivot(side="high", price=float(source["high"]), source_bar=source_bar, confirm_bar=bar_index)
            elif selected == -1:
                accepted_low = self._accept_pivot(side="low", price=float(source["low"]), source_bar=source_bar, confirm_bar=bar_index)
            if selected != 0:
                events.append(f"DOUBLE_PIVOT:{source_bar}:{'HIGH' if selected == 1 else 'LOW'}:{reason}")
        else:
            if high_pivot:
                accepted_high = self._accept_pivot(side="high", price=float(source["high"]), source_bar=source_bar, confirm_bar=bar_index)
            if low_pivot:
                accepted_low = self._accept_pivot(side="low", price=float(source["low"]), source_bar=source_bar, confirm_bar=bar_index)

        if accepted_high:
            events.append(f"PIVOT_HIGH:{source_bar}:{bar_index}")
        if accepted_low:
            events.append(f"PIVOT_LOW:{source_bar}:{bar_index}")
        return accepted_high or accepted_low

    def _geometry_evaluator(self, bar_index: int, safe_atr: float) -> PatternGeometryEvaluator:
        return PatternGeometryEvaluator(
            store=self._store,
            highs=[row["high"] for row in self._rows],
            lows=[row["low"] for row in self._rows],
            closes=[row["close"] for row in self._rows],
            atrs=self._atr_values,
            current_bar=bar_index,
            safe_atr=safe_atr,
        )

    def _refresh_active(self, bar_index: int, safe_atr: float) -> None:
        if not self._active.valid:
            return
        break_candidate_bar = self._lifecycle.break_candidate_bar if self._lifecycle is not None else None
        violation_end = (
            max(int(self._active.start_bar), int(break_candidate_bar) - 1)
            if is_break_lifecycle(self._pattern_state) and break_candidate_bar is not None
            else max(int(self._active.start_bar), bar_index - 1)
        )
        evaluator = self._geometry_evaluator(bar_index, safe_atr)
        self._active = refresh_active_candidate(
            self._active,
            evaluator=evaluator,
            highs=[row["high"] for row in self._rows],
            lows=[row["low"] for row in self._rows],
            closes=[row["close"] for row in self._rows],
            violation_end_bar=violation_end,
        )

    def _refresh_selection_score(self, bar_index: int, safe_atr: float) -> None:
        if not self._active.valid or is_terminal(self._pattern_state):
            return
        self._active.selection_score = selection_score(
            self._active,
            active=self._active,
            pattern_state=self._pattern_state,
            bar_index=bar_index,
            close=float(self._rows[bar_index]["close"]),
            safe_atr=safe_atr,
            config=self.config,
            terminal=False,
        )

    def _scan_candidates(self, bar_index: int, safe_atr: float, events: list[str]) -> None:
        high_count = len(self._store.high_prices)
        low_count = len(self._store.low_prices)
        if high_count < 2 or low_count < 2:
            return
        high_start = max(0, high_count - SEARCH_PIVOTS)
        low_start = max(0, low_count - SEARCH_PIVOTS)
        evaluator = self._geometry_evaluator(bar_index, safe_atr)
        highs = [row["high"] for row in self._rows]
        lows = [row["low"] for row in self._rows]
        closes = [row["close"] for row in self._rows]
        pole_evaluator = PatternPoleEvaluator(
            store=self._store,
            highs=highs,
            lows=lows,
            closes=closes,
            atrs=self._atr_values,
            current_bar=bar_index,
            safe_atr=safe_atr,
        )
        specialized = SpecializedPatternEvaluator(
            store=self._store,
            highs=highs,
            lows=lows,
            closes=closes,
        )
        bull_poles = {
            index: pole_evaluator.find_pole(
                end_bar=self._store.high_bars[index],
                end_price=self._store.high_prices[index],
                direction=1,
            )
            for index in range(high_start, high_count)
        }
        bear_poles = {
            index: pole_evaluator.find_pole(
                end_bar=self._store.low_bars[index],
                end_price=self._store.low_prices[index],
                direction=-1,
            )
            for index in range(low_start, low_count)
        }

        best = PatternCandidate()
        for high_a, high_b in combinations(range(high_start, high_count), 2):
            for low_a, low_b in combinations(range(low_start, low_count), 2):
                analysis = evaluator.analyze(high_a=high_a, high_b=high_b, low_a=low_a, low_b=low_b)
                candidate = specialized.apply(
                    analysis=analysis,
                    bull_pole=bull_poles[high_a],
                    bear_pole=bear_poles[low_a],
                )
                if not candidate.valid or same_completed_structure(candidate, self._completed, safe_atr=safe_atr, config=self.config):
                    continue
                candidate.selection_score = selection_score(
                    candidate,
                    active=self._active,
                    pattern_state=self._pattern_state,
                    bar_index=bar_index,
                    close=float(self._rows[bar_index]["close"]),
                    safe_atr=safe_atr,
                    config=self.config,
                    terminal=is_terminal(self._pattern_state),
                )
                if candidate_preferred(candidate, best, config=self.config):
                    best = candidate

        if not best.valid or same_completed_structure(best, self._completed, safe_atr=safe_atr, config=self.config):
            return
        lifecycle_can_update = not is_break_lifecycle(self._pattern_state)
        continuity = (
            continuity_score(self._active, best, bar_index=bar_index, safe_atr=safe_atr, config=self.config)
            if self._active.valid
            else 0.0
        )
        if self._active.valid and identity_compatible(self._active, best) and lifecycle_can_update and continuity >= 60.0:
            preserved_identity = self._active.identity
            best.identity = preserved_identity
            self._active = reset_quality_snapshot(best)
            self._store.lock_used_pivots(self._active)
            self._reset_lifecycle()
            events.append(f"PATTERN_CONTINUITY:{preserved_identity}:{continuity:.1f}")
            return

        replace, reason = should_replace_active(
            self._active,
            best,
            state=self._pattern_state,
            lifecycle_can_update=lifecycle_can_update,
            terminal=is_terminal(self._pattern_state),
            config=self.config,
        )
        if not replace:
            events.append(f"PATTERN_REPLACEMENT_BLOCKED:{reason}")
            return
        self._next_pattern_identity += 1
        best.identity = self._next_pattern_identity
        self._active = reset_quality_snapshot(best)
        self._store.lock_used_pivots(self._active)
        self._pattern_state = ST_CANDIDATE
        self._invalid_reason = "Yok"
        self._reset_lifecycle()

    def _reset_lifecycle(self) -> None:
        if not self._active.valid:
            self._lifecycle = None
            return
        self._lifecycle = PatternLifecycleRuntime(
            self._active,
            state=self._pattern_state,
            config=PatternLifecycleConfig(
                pattern=self.config,
                use_breakout_quality_filter=self.use_breakout_quality_filter,
            ),
        )

    def _advance_state(self, bar_index: int, safe_atr: float) -> None:
        row = self._rows[bar_index]
        normal = evaluate_normal_state(
            self._active,
            current_state=self._pattern_state,
            bar_index=bar_index,
            close=float(row["close"]),
            safe_atr=safe_atr,
            config=self.config,
        )

        if self._pattern_state == ST_BREAK_TIMEOUT:
            self._active = reset_quality_snapshot(self._active)
            self._pattern_state = normal.state
            self._invalid_reason = normal.invalid_reason
            self._reset_lifecycle()
            return
        if is_terminal(self._pattern_state):
            return

        if self._lifecycle is None:
            self._reset_lifecycle()
        assert self._lifecycle is not None
        self._lifecycle.candidate = copy.deepcopy(self._active)
        self._lifecycle.state = self._pattern_state
        lifecycle_before = self._pattern_state
        lifecycle_snapshot = self._lifecycle.update_closed(
            LifecycleBar(
                bar_index=bar_index,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                atr=safe_atr,
                volume=row["volume"],
                volume_sma=self._volume_sma(bar_index),
            ),
            hard_geometry_invalid=normal.hard_geometry_invalid,
        )
        self._active = lifecycle_snapshot.candidate
        if lifecycle_snapshot.state != lifecycle_before:
            self._pattern_state = lifecycle_snapshot.state
            self._invalid_reason = lifecycle_snapshot.invalid_reason
        elif is_break_lifecycle(lifecycle_before):
            self._pattern_state = lifecycle_snapshot.state
            self._invalid_reason = lifecycle_snapshot.invalid_reason
        else:
            self._pattern_state = normal.state
            self._invalid_reason = normal.invalid_reason
            self._lifecycle.state = self._pattern_state
            self._lifecycle.candidate = copy.deepcopy(self._active)

        if self._pattern_state == ST_COMPLETED and self._completed is None or (
            self._pattern_state == ST_COMPLETED
            and self._completed is not None
            and self._completed.start_bar != int(self._active.start_bar)
        ):
            self._completed = CompletedPatternReference.from_candidate(
                self._active,
                safe_atr=safe_atr,
                min_tick=self.config.min_tick,
            )

    def _break_level(self, bar_index: int) -> float | None:
        if self._lifecycle is None or self._lifecycle.break_direction == 0:
            return None
        values = (
            self._lifecycle.break_line_x1,
            self._lifecycle.break_line_y1,
            self._lifecycle.break_line_x2,
            self._lifecycle.break_line_y2,
        )
        if any(value is None for value in values):
            return None
        return line_price(
            int(self._lifecycle.break_line_x1),
            float(self._lifecycle.break_line_y1),
            int(self._lifecycle.break_line_x2),
            float(self._lifecycle.break_line_y2),
            bar_index,
        )

    def _build_export(self, bar_index: int) -> PatternExport:
        available = self._active.valid and self._pattern_state != ST_NONE
        if not available:
            return PatternExport()
        usable_for_break = self._pattern_state != ST_INVALID
        break_direction = self.break_direction
        break_lifecycle = is_break_lifecycle(self._pattern_state) and break_direction != 0
        break_confirmed_bar = self._lifecycle.break_confirmed_bar if self._lifecycle is not None else None
        retest_success_bar = self._lifecycle.retest_success_bar if self._lifecycle is not None else None
        return PatternExport(
            state=export_pattern_state(self._pattern_state),
            pattern_type=export_pattern_type(self._active.pattern_type),
            quality=effective_raw_quality(self._active),
            classic_direction=self._active.classic_dir,
            break_state=export_break_state(self._pattern_state, break_direction) if usable_for_break else None,
            break_level=self._break_level(bar_index) if break_lifecycle else None,
            break_strength=self._active.break_strength if break_lifecycle and self._active.quality_frozen else None,
            retest_state=(
                export_retest_state(self._pattern_state, break_confirmed_bar, retest_success_bar)
                if usable_for_break
                else None
            ),
            retest_tolerance=(
                self._active.frozen_retest_tolerance
                if break_lifecycle and self._active.quality_frozen
                else None
            ),
            identity=float(self._active.identity),
        )

    def _build_result(self, clean: dict[str, Any], events: list[str]) -> EngineResult:
        break_direction = self.break_direction
        semantic_direction = break_direction if break_direction != 0 else self._active.classic_dir if self._active.valid else 0
        direction = Direction.UP if semantic_direction > 0 else Direction.DOWN if semantic_direction < 0 else Direction.NEUTRAL
        levels: dict[str, float] = {}
        if self._active.valid:
            if self._active.upper_now is not None:
                levels["upper_boundary"] = float(self._active.upper_now)
            if self._active.lower_now is not None:
                levels["lower_boundary"] = float(self._active.lower_now)
        if self._export is not None and self._export.break_level is not None:
            levels["break_level"] = float(self._export.break_level)
        if self._export is not None and self._export.retest_tolerance is not None:
            levels["retest_tolerance"] = float(self._export.retest_tolerance)
        reasons: list[str] = []
        if self._active.valid:
            reasons.append(f"pattern={self._active.pattern_type}")
            reasons.append(f"identity={self._active.identity}")
        if self._invalid_reason and self._invalid_reason != "Yok":
            reasons.append(self._invalid_reason)
        return EngineResult(
            engine=self.name,
            state=self._pattern_state,
            timestamp=clean["timestamp"],
            direction=direction,
            score=effective_raw_quality(self._active) if self._active.valid else None,
            quality=effective_raw_quality(self._active) if self._active.valid else None,
            levels=levels,
            events=tuple(events),
            reasons=tuple(reasons),
            is_confirmed=True,
        )
