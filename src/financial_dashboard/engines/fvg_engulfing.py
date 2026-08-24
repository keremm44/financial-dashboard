from __future__ import annotations

from dataclasses import replace

from .fvg_engulfing_final import (
    ATR_LENGTH,
    CONFLICT_PROGRESS_FACTOR,
    FLOW_LENGTH,
    RETENTION_CLOSE_FLOOR,
    RETENTION_EFFICIENCY_FACTOR,
    SHOCK_CLOSE_LOWER,
    SHOCK_CLOSE_UPPER,
    SHOCK_DIRECTIONLESS_BODY_SHARE,
    SHOCK_MIDDLE_HIGH,
    SHOCK_MIDDLE_LOW,
    FvgEngulfingEngine as _FinalFvgEngulfingEngine,
    _LifecycleMetrics,
    _rma,
    _safe_div,
)
from .fvg_engulfing_models import FvgDirection


class FvgEngulfingEngine(_FinalFvgEngulfingEngine):
    """Final public facade with exact detector-result reuse for Tur-2 lifecycle."""

    def reset(self) -> None:
        super().reset()
        self._runtime_series_cache_length = -1
        self._runtime_series_cache = None

    def _calculate_series(self):
        length = len(self._rows)
        if (
            getattr(self, "_runtime_series_cache_length", -1) == length
            and getattr(self, "_runtime_series_cache", None) is not None
        ):
            return self._runtime_series_cache
        series = super()._calculate_series()
        self._runtime_series_cache_length = length
        self._runtime_series_cache = series
        return series

    def _lifecycle_metrics(self, i: int) -> _LifecycleMetrics:
        rows = self._rows
        t = self._thresholds
        tick = self.config.minimum_tick
        r = rows[i]
        o, h, l, c = r["open"], r["high"], r["low"], r["close"]
        candle_range = max(h - l, 0.0)
        body = abs(c - o)
        bull, bear = c > o, c < o
        close_loc = 0.5 if candle_range <= 1e-10 else _safe_div(c - l, candle_range, 0.5, tick)

        tr: list[float | None] = []
        for j, bar in enumerate(rows):
            if not self._valid[j]:
                tr.append(None)
                continue
            prev = rows[j - 1]["close"] if j > 0 and self._valid[j - 1] else None
            value = bar["high"] - bar["low"]
            if prev is not None:
                value = max(value, abs(bar["high"] - prev), abs(bar["low"] - prev))
            tr.append(max(value, 0.0))
        atr = _rma(tr, ATR_LENGTH)
        prior_atr = atr[i - 1] if i > 0 and atr[i - 1] is not None else tick
        safe_prior_atr = max(prior_atr, tick)
        body_to_atr = _safe_div(body, safe_prior_atr, 0.0, tick)

        net_progress = c - rows[i - FLOW_LENGTH]["close"]
        net_progress_atr = _safe_div(net_progress, atr[i] or tick, 0.0, tick)
        path = sum(abs(rows[j]["close"] - rows[j - 1]["close"]) for j in range(i - 3, i + 1))
        efficiency = _safe_div(abs(net_progress), path, 0.0, tick)
        higher_share = sum(rows[j]["close"] > rows[j - 1]["close"] for j in range(i - 3, i + 1)) / 4.0
        lower_share = sum(rows[j]["close"] < rows[j - 1]["close"] for j in range(i - 3, i + 1)) / 4.0

        # Detector update has already computed this exact prefix. Reuse it instead
        # of running the full historical detector a second time for Tur-2.
        current_series = self._calculate_series()
        m = current_series[i]
        bull_engulf = bool(m["bullish_engulfing"])
        bear_engulf = bool(m["bearish_engulfing"])

        upper_wick = max(h - max(o, c), 0.0)
        lower_wick = max(min(o, c) - l, 0.0)
        safe_body = max(body, tick)
        upper_wick_body = _safe_div(upper_wick, safe_body, 0.0, tick)
        lower_wick_body = _safe_div(lower_wick, safe_body, 0.0, tick)
        upper_wick_atr = _safe_div(upper_wick, safe_prior_atr, 0.0, tick)
        lower_wick_atr = _safe_div(lower_wick, safe_prior_atr, 0.0, tick)
        prev_high = max(rows[j]["high"] for j in range(i - 4, i))
        prev_low = min(rows[j]["low"] for j in range(i - 4, i))
        primitive_lower = lower_wick_body >= t.minimum_rejection_wick_body and lower_wick_atr >= t.minimum_rejection_wick_atr and close_loc >= t.rejection_close_location and lower_wick >= upper_wick * t.minimum_wick_dominance and l <= prev_low
        primitive_upper = upper_wick_body >= t.minimum_rejection_wick_body and upper_wick_atr >= t.minimum_rejection_wick_atr and close_loc <= 1.0 - t.rejection_close_location and upper_wick >= lower_wick * t.minimum_wick_dominance and h >= prev_high
        strong_buy = bull and body_to_atr >= t.minimum_continuation_body_atr and close_loc >= t.minimum_continuation_close_location and net_progress_atr > 0
        strong_sell = bear and body_to_atr >= t.minimum_continuation_body_atr and close_loc <= 1.0 - t.minimum_continuation_close_location and net_progress_atr < 0
        prev = rows[i - 1]
        prev_range = max(prev["high"] - prev["low"], 0.0)
        body_share = _safe_div(body, candle_range, 0.0, tick)
        green_share = sum(rows[j]["close"] > rows[j]["open"] for j in range(i - 3, i + 1)) / 4.0
        red_share = sum(rows[j]["close"] < rows[j]["open"] for j in range(i - 3, i + 1)) / 4.0
        buy_e = [bull, body_to_atr >= t.minimum_continuation_body_atr, body_share >= t.minimum_continuation_body_share, close_loc >= t.minimum_continuation_close_location, upper_wick_body <= t.maximum_opposing_wick_body, net_progress_atr >= t.minimum_continuation_progress_atr, efficiency >= t.minimum_continuation_efficiency, higher_share >= .5, green_share >= .5, c > prev["close"], c >= prev["high"] or c >= prev["low"] + prev_range * .70]
        sell_e = [bear, body_to_atr >= t.minimum_continuation_body_atr, body_share >= t.minimum_continuation_body_share, close_loc <= 1.0 - t.minimum_continuation_close_location, lower_wick_body <= t.maximum_opposing_wick_body, net_progress_atr <= -t.minimum_continuation_progress_atr, efficiency >= t.minimum_continuation_efficiency, lower_share >= .5, red_share >= .5, c < prev["close"], c <= prev["low"] or c <= prev["low"] + prev_range * .30]
        buy_base = bull and (buy_e[1] or buy_e[2]) and (buy_e[3] or (close_loc >= RETENTION_CLOSE_FLOOR and buy_e[4])) and sum(buy_e[5:]) >= 2 and sum(buy_e) >= t.continuation_evidence_minimum and not (primitive_upper or strong_sell)
        sell_base = bear and (sell_e[1] or sell_e[2]) and (sell_e[3] or (close_loc <= 1.0 - RETENTION_CLOSE_FLOOR and sell_e[4])) and sum(sell_e[5:]) >= 2 and sum(sell_e) >= t.continuation_evidence_minimum and not (primitive_lower or strong_buy)
        if buy_base and sell_base:
            threshold = t.minimum_continuation_progress_atr * CONFLICT_PROGRESS_FACTOR
            if net_progress_atr > threshold:
                sell_base = False
            elif net_progress_atr < -threshold:
                buy_base = False
            else:
                buy_base = sell_base = False
        extreme = (candle_range / safe_prior_atr >= t.shock_range_atr) or (body_to_atr >= t.shock_body_atr)
        one_bar_shock = extreme and (
            (bull and close_loc >= SHOCK_CLOSE_UPPER and net_progress_atr > 0)
            or (bear and close_loc <= SHOCK_CLOSE_LOWER and net_progress_atr < 0)
            or (
                body_share <= SHOCK_DIRECTIONLESS_BODY_SHARE
                and SHOCK_MIDDLE_LOW <= close_loc <= SHOCK_MIDDLE_HIGH
            )
        )
        buy_candidate = buy_base and not one_bar_shock
        sell_candidate = sell_base and not one_bar_shock
        buy_confirmed = buy_candidate and close_loc >= max(RETENTION_CLOSE_FLOOR, t.minimum_continuation_close_location - .12) and efficiency >= t.minimum_continuation_efficiency * RETENTION_EFFICIENCY_FACTOR and net_progress_atr > 0
        sell_confirmed = sell_candidate and close_loc <= 1.0 - max(RETENTION_CLOSE_FLOOR, t.minimum_continuation_close_location - .12) and efficiency >= t.minimum_continuation_efficiency * RETENTION_EFFICIENCY_FACTOR and net_progress_atr < 0

        return _LifecycleMetrics(
            c,
            l,
            h,
            bull,
            bear,
            close_loc,
            body_to_atr,
            net_progress_atr,
            efficiency,
            higher_share,
            lower_share,
            buy_candidate,
            sell_candidate,
            buy_confirmed,
            sell_confirmed,
            primitive_lower,
            primitive_upper,
            bull_engulf,
            bear_engulf,
        )

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
