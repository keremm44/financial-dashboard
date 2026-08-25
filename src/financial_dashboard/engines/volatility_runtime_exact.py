from __future__ import annotations

from typing import Any

from .volatility_bands_fib import VolatilityBandsFibEngine


class ExactRuntimeVolatilityBandsFibEngine(VolatilityBandsFibEngine):
    """Compatibility facade over the canonical prefix-incremental Volatility core.

    The canonical core now owns the exact causal series and bounded-history helpers
    (`_hist_confirm`, `_hist_share`, `_hist_recent_prior`, etc.).  The older runtime
    implementation duplicated the decision loop and rebuilt full boolean histories
    on every bar, making a long 1d replay quadratic.  Reusing the canonical engine
    removes that duplicate slow path while preserving every state, threshold and
    final export bit-for-bit.

    The `_runtime_*` properties are retained for the existing parity/diagnostic
    contract; they are aliases to the canonical engine's immutable causal caches.
    """

    @property
    def runtime_closes(self) -> list[float]:
        return self._s["closes"]

    @property
    def runtime_atr(self) -> list[float | None]:
        return self._s["atr"]

    @property
    def _runtime_atr_avg(self) -> list[float | None]:
        return self._s["atr_avg"]

    @property
    def _runtime_basis(self) -> list[float | None]:
        return self._s["basis"]

    @property
    def _runtime_stdev(self) -> list[float | None]:
        return self._s["stdev"]

    @property
    def _runtime_calc(self) -> list[dict[str, Any]]:
        return self._calc


__all__ = ["ExactRuntimeVolatilityBandsFibEngine"]
