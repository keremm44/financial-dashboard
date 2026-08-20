from __future__ import annotations

from dataclasses import replace

from .fvg_engulfing_final import FvgEngulfingEngine as _FinalFvgEngulfingEngine, _LifecycleMetrics
from .fvg_engulfing_models import FvgDirection


class FvgEngulfingEngine(_FinalFvgEngulfingEngine):
    """Final public facade with the v0.3.8 continuation-candidate alignment edge closed.

    Pine assigns BUY/SELL_CONTINUATION candle state to both confirmed and
    candidate continuation stages (subject to the state-priority chain). FVG
    embedded candle contribution therefore must not treat confirmed-only as the
    whole continuation family, nor double-count a candidate when another
    alignment branch already supplied the same 5-point contribution.
    """

    def _repair_candidate_alignment(self, idx: int, metrics: _LifecycleMetrics) -> None:
        candidates = [formation for formation in self._fvg_formations if formation.formation_index == idx]
        if not candidates:
            return

        for formation in candidates:
            if formation.direction is FvgDirection.BULLISH:
                aligned = (
                    metrics.buy_continuation_confirmed
                    or metrics.buy_continuation_candidate
                    or metrics.bullish_engulfing
                    or metrics.lower_rejection
                    or (
                        metrics.candle_bullish
                        and metrics.close_location >= self._thresholds.minimum_continuation_close_location
                    )
                )
                counter_absent = (
                    not metrics.sell_continuation_confirmed
                    and not metrics.bearish_engulfing
                    and not metrics.upper_rejection
                )
            else:
                aligned = (
                    metrics.sell_continuation_confirmed
                    or metrics.sell_continuation_candidate
                    or metrics.bearish_engulfing
                    or metrics.upper_rejection
                    or (
                        metrics.candle_bearish
                        and metrics.close_location <= 1.0 - self._thresholds.minimum_continuation_close_location
                    )
                )
                counter_absent = (
                    not metrics.buy_continuation_confirmed
                    and not metrics.bullish_engulfing
                    and not metrics.lower_rejection
                )

            expected_embedded = (5.0 if aligned else 0.0) + (5.0 if counter_absent else 0.0)
            delta = expected_embedded - formation.embedded_candle_contribution
            if abs(delta) <= 1e-12:
                continue

            position = self._fvg_formations.index(formation)
            self._fvg_formations[position] = replace(
                formation,
                quality=max(0.0, min(100.0, formation.quality + delta)),
                embedded_candle_contribution=expected_embedded,
            )
