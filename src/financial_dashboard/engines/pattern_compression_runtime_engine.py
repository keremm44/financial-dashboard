from __future__ import annotations

from collections import OrderedDict
from typing import Any

import pandas as pd

from .pattern_compression_active import is_break_lifecycle, refresh_active_candidate
from .pattern_compression_core import (
    MAX_HISTORY_OFFSET,
    MAX_VIOLATION_CACHE,
    MAX_VIOLATION_SCAN,
    line_price,
)
from .pattern_compression_engine import PatternCompressionEngine
from .pattern_compression_geometry import (
    PatternGeometryEvaluator,
    ViolationStats,
    violation_penalty_from_stats,
)


_GeometryKey = tuple[int, float, int, float, int, float, int, float]
_ViolationCache = OrderedDict[_GeometryKey, dict[int, ViolationStats]]


class RuntimePatternGeometryEvaluator(PatternGeometryEvaluator):
    """Exact Pattern geometry evaluator with immutable per-bar violation caching."""

    def __init__(self, *, violation_cache: _ViolationCache, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._violation_cache = violation_cache

    def _geometry_cache(
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
    ) -> dict[int, ViolationStats]:
        key: _GeometryKey = (
            upper_x1,
            upper_y1,
            upper_x2,
            upper_y2,
            lower_x1,
            lower_y1,
            lower_x2,
            lower_y2,
        )
        cached = self._violation_cache.get(key)
        if cached is not None:
            self._violation_cache.move_to_end(key)
            return cached
        cached = {}
        self._violation_cache[key] = cached
        while len(self._violation_cache) > MAX_VIOLATION_CACHE:
            self._violation_cache.popitem(last=False)
        return cached

    def _one_bar_violation(
        self,
        bar: int,
        *,
        upper_x1: int,
        upper_y1: float,
        upper_x2: int,
        upper_y2: float,
        lower_x1: int,
        lower_y1: float,
        lower_x2: int,
        lower_y2: float,
        cache: dict[int, ViolationStats],
    ) -> ViolationStats:
        # During ATR warmup the canonical evaluator falls back to current safe_atr,
        # which can change on a future call. Do not cache those early contributions.
        historical_value = self.atrs[bar] if 0 <= bar < len(self.atrs) else None
        if historical_value is not None and bar in cache:
            return cache[bar]

        historical_atr = self._atr_at(bar)
        close_mult = 0.05 if self.config.profile == "Hassas" else 0.07 if self.config.profile == "Seçici" else 0.06
        wick_mult = 0.13 if self.config.profile == "Hassas" else 0.18 if self.config.profile == "Seçici" else 0.15
        close_buffer = max(self.config.min_tick * 2.0, historical_atr * close_mult)
        wick_buffer = max(self.config.min_tick * 2.0, historical_atr * wick_mult)
        upper_boundary = line_price(upper_x1, upper_y1, upper_x2, upper_y2, bar)
        lower_boundary = line_price(lower_x1, lower_y1, lower_x2, lower_y2, bar)

        upper_close_broken = self.closes[bar] > upper_boundary + close_buffer
        lower_close_broken = self.closes[bar] < lower_boundary - close_buffer
        upper_wick_broken = not upper_close_broken and self.highs[bar] > upper_boundary + wick_buffer
        lower_wick_broken = not lower_close_broken and self.lows[bar] < lower_boundary - wick_buffer
        upper_excess = (
            (self.closes[bar] - upper_boundary - close_buffer) / historical_atr
            if upper_close_broken
            else (self.highs[bar] - upper_boundary - wick_buffer) / historical_atr
            if upper_wick_broken
            else 0.0
        )
        lower_excess = (
            (lower_boundary - self.closes[bar] - close_buffer) / historical_atr
            if lower_close_broken
            else (lower_boundary - self.lows[bar] - wick_buffer) / historical_atr
            if lower_wick_broken
            else 0.0
        )
        result = ViolationStats(
            upper_close=int(upper_close_broken),
            lower_close=int(lower_close_broken),
            upper_wick=int(upper_wick_broken),
            lower_wick=int(lower_wick_broken),
            max_upper=max(0.0, upper_excess),
            max_lower=max(0.0, lower_excess),
            scanned_bars=1,
        )
        if historical_value is not None:
            cache[bar] = result
        return result

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
        available_start_bar = max(
            requested_start_bar,
            max(0, self.current_bar - MAX_HISTORY_OFFSET),
        )
        scan_start_bar = (
            max(available_start_bar, safe_end_bar - MAX_VIOLATION_SCAN + 1)
            if apply_maximum_window
            else available_start_bar
        )
        truncated = apply_maximum_window and scan_start_bar > geometry_start_bar
        if safe_end_bar < scan_start_bar:
            return ViolationStats(truncated=truncated)

        cache = self._geometry_cache(
            upper_x1=upper_x1,
            upper_y1=upper_y1,
            upper_x2=upper_x2,
            upper_y2=upper_y2,
            lower_x1=lower_x1,
            lower_y1=lower_y1,
            lower_x2=lower_x2,
            lower_y2=lower_y2,
        )
        upper_close = lower_close = upper_wick = lower_wick = 0
        max_upper = max_lower = 0.0
        scanned = 0
        for bar in range(scan_start_bar, safe_end_bar + 1):
            item = self._one_bar_violation(
                bar,
                upper_x1=upper_x1,
                upper_y1=upper_y1,
                upper_x2=upper_x2,
                upper_y2=upper_y2,
                lower_x1=lower_x1,
                lower_y1=lower_y1,
                lower_x2=lower_x2,
                lower_y2=lower_y2,
                cache=cache,
            )
            upper_close += item.upper_close
            lower_close += item.lower_close
            upper_wick += item.upper_wick
            lower_wick += item.lower_wick
            max_upper = max(max_upper, item.max_upper)
            max_lower = max(max_lower, item.max_lower)
            scanned += item.scanned_bars

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


class RuntimePatternCompressionEngine(PatternCompressionEngine):
    """Pattern engine with append-only replay arrays and exact geometry caches."""

    def reset(self) -> None:
        super().reset()
        self._runtime_highs: list[float] = []
        self._runtime_lows: list[float] = []
        self._runtime_closes: list[float] = []
        self._runtime_violation_cache: _ViolationCache = OrderedDict()

    def update(self, bar: pd.Series | dict[str, Any]):
        row = dict(bar) if isinstance(bar, dict) else bar.to_dict()
        if not bool(row.get("is_closed", True)):
            return super().update(bar)

        required = ("timestamp", "open", "high", "low", "close")
        if any(key not in row or pd.isna(row[key]) for key in required):
            return super().update(bar)

        self._runtime_highs.append(float(row["high"]))
        self._runtime_lows.append(float(row["low"]))
        self._runtime_closes.append(float(row["close"]))
        try:
            return super().update(bar)
        except Exception:
            self._runtime_highs.pop()
            self._runtime_lows.pop()
            self._runtime_closes.pop()
            raise

    def _geometry_evaluator(self, bar_index: int, safe_atr: float) -> PatternGeometryEvaluator:
        return RuntimePatternGeometryEvaluator(
            violation_cache=self._runtime_violation_cache,
            store=self._store,
            highs=self._runtime_highs,
            lows=self._runtime_lows,
            closes=self._runtime_closes,
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
            highs=self._runtime_highs,
            lows=self._runtime_lows,
            closes=self._runtime_closes,
            violation_end_bar=violation_end,
        )


__all__ = ["RuntimePatternCompressionEngine", "RuntimePatternGeometryEvaluator"]
