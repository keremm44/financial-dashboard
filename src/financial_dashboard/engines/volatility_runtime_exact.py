from __future__ import annotations

from typing import Any

from .volatility_bands_fib_engine import _safe_div
from .volatility_runtime_engine import RuntimeVolatilityBandsFibEngine


class ExactRuntimeVolatilityBandsFibEngine(RuntimeVolatilityBandsFibEngine):
    """Runtime core whose RMA arithmetic follows the canonical operation order."""

    def _append_runtime_row(self, row: dict[str, Any]) -> None:
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        prior_close = self._runtime_closes[-1] if self._runtime_closes else None

        self._runtime_opens.append(open_)
        self._runtime_highs.append(high)
        self._runtime_lows.append(low)
        self._runtime_closes.append(close)

        true_range = high - low
        if prior_close is not None:
            true_range = max(true_range, abs(high - prior_close), abs(low - prior_close))
        self._runtime_tr.append(true_range)

        count = len(self._runtime_tr)
        if count < self.ATR_LENGTH:
            atr = None
        elif count == self.ATR_LENGTH:
            atr = sum(self._runtime_tr[: self.ATR_LENGTH]) / self.ATR_LENGTH
        else:
            prior_atr = self._runtime_atr[-1]
            if prior_atr is None:
                atr = sum(self._runtime_tr[-self.ATR_LENGTH :]) / self.ATR_LENGTH
            else:
                alpha = 1.0 / self.ATR_LENGTH
                atr = alpha * true_range + (1.0 - alpha) * float(prior_atr)
        self._runtime_atr.append(atr)
        self._runtime_atr_avg.append(
            self._last_optional_sma(self._runtime_atr, self.ATR_AVERAGE_LENGTH)
        )

        basis = self._last_sma(self._runtime_closes, self.BOLLINGER_LENGTH)
        stdev = self._last_population_std(self._runtime_closes, self.BOLLINGER_LENGTH)
        self._runtime_basis.append(basis)
        self._runtime_stdev.append(stdev)
        if basis is None or stdev is None:
            upper = lower = norm_width = None
        else:
            dev = stdev * self.BOLLINGER_MULTIPLIER
            upper = basis + dev
            lower = basis - dev
            norm_width = _safe_div(
                upper - lower,
                max(abs(basis), self.config.minimum_tick),
                0.0,
            )
        self._runtime_upper.append(upper)
        self._runtime_lower.append(lower)
        self._runtime_norm_width.append(norm_width)
        self._runtime_avg_width.append(
            self._last_optional_sma(self._runtime_norm_width, self.BOLLINGER_LENGTH)
        )


__all__ = ["ExactRuntimeVolatilityBandsFibEngine"]
