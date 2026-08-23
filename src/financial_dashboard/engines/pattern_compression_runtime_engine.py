from __future__ import annotations

from typing import Any

import pandas as pd

from .pattern_compression_active import is_break_lifecycle, refresh_active_candidate
from .pattern_compression_engine import PatternCompressionEngine
from .pattern_compression_geometry import PatternGeometryEvaluator


class RuntimePatternCompressionEngine(PatternCompressionEngine):
    """Pattern engine with append-only replay arrays.

    Canonical Pattern selection and lifecycle logic stays in ``PatternCompressionEngine``.
    This runtime facade only removes repeated O(n) materialization of the same high,
    low and close history while an active pattern is refreshed on every bar.
    """

    def reset(self) -> None:
        super().reset()
        self._runtime_highs: list[float] = []
        self._runtime_lows: list[float] = []
        self._runtime_closes: list[float] = []

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
        return PatternGeometryEvaluator(
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


__all__ = ["RuntimePatternCompressionEngine"]
