from __future__ import annotations

from .volatility_bands_fib_engine import VolatilityBandsConfig
from .volatility_direction_transition import (
    EarlyDirectionEvidence,
    VolatilityDirectionSnapshot,
    VolatilityDirectionTransitionEngine,
)
from .volatility_runtime_exact import ExactRuntimeVolatilityBandsFibEngine


class RuntimeVolatilityDirectionTransitionEngine(VolatilityDirectionTransitionEngine):
    """Direction-transition lifecycle backed by the prefix-incremental core."""

    def __init__(self, config: VolatilityBandsConfig | None = None) -> None:
        super().__init__(config)
        self._core = ExactRuntimeVolatilityBandsFibEngine(self.config)
        self._snapshot = VolatilityDirectionSnapshot(
            timestamp=None,
            core_result=None,
            confirmed_export=self._core.final_export,
            early=EarlyDirectionEvidence(),
        )


__all__ = ["RuntimeVolatilityDirectionTransitionEngine"]
