from __future__ import annotations

from typing import Any

import pandas as pd

from .market_structure import MarketStructureConfig, MarketStructureEngine as _SwingCoreEngine
from .market_structure_runtime import MarketStructureRuntime
from .market_structure_state import BreakConfig
from .models import Direction, EngineResult


class MarketStructureEngine(_SwingCoreEngine):
    """Integrated ARGENT Market Structure engine.

    The swing detector remains the validated Tur 1 implementation. The runtime
    layer adds Tur 2 BOS/CHoCH/transition/role/break lifecycle and evidence/export
    without mixing TradingView visual state into the math path.
    """

    def __init__(
        self,
        config: MarketStructureConfig | None = None,
        break_config: BreakConfig | None = None,
    ) -> None:
        self.break_config = break_config or BreakConfig(
            profile=(config.profile if config is not None else "Dengeli"),
            min_tick=(config.min_tick if config is not None else 0.01),
        )
        super().__init__(config)

    def reset(self) -> None:
        super().reset()
        self._runtime = MarketStructureRuntime(self.break_config)
        self._export = None

    def _candidate_update(self, candidate, incoming, locked_by_break: bool):
        runtime_lock = self._runtime.locks_candidate(incoming.scope, candidate) if hasattr(self, "_runtime") else False
        return super()._candidate_update(candidate, incoming, locked_by_break=locked_by_break or runtime_lock)

    def update(self, bar: pd.Series | dict[str, Any]) -> EngineResult | None:
        row = dict(bar) if isinstance(bar, dict) else bar.to_dict()
        if not bool(row.get("is_closed", True)):
            return self._snapshot

        base = super().update(row)
        if base is None:
            return None

        bar_index = len(self._rows) - 1
        clean = self._rows[-1]
        previous_atr = self._atr_values[-2] if len(self._atr_values) >= 2 else None
        current_atr = self._atr_values[-1] if self._atr_values else None
        safe_atr = max(float(previous_atr or current_atr or self._tr_values[-1]), self.config.min_tick)

        structure_events = []
        structure_events.extend(
            self._runtime.process_scope(
                scope="EXTERNAL",
                swings=self._external.swings,
                high_candidate=self._external.high_candidate,
                low_candidate=self._external.low_candidate,
                bar_index=bar_index,
                open_=clean["open"],
                high=clean["high"],
                low=clean["low"],
                close=clean["close"],
                safe_atr=safe_atr,
            )
        )
        structure_events.extend(
            self._runtime.process_scope(
                scope="INTERNAL",
                swings=self._internal.swings,
                high_candidate=self._internal.high_candidate,
                low_candidate=self._internal.low_candidate,
                bar_index=bar_index,
                open_=clean["open"],
                high=clean["high"],
                low=clean["low"],
                close=clean["close"],
                safe_atr=safe_atr,
            )
        )

        score = self._runtime.score(bar_index=bar_index)
        self._export = self._runtime.export(self._external.swings, self._internal.swings, bar_index=bar_index)
        external = self._runtime.external.context

        if external.direction > 0:
            direction = Direction.UP
        elif external.direction < 0:
            direction = Direction.DOWN
        else:
            direction = Direction.NEUTRAL

        levels = self._public_levels()
        export_levels = {
            "external_protected_low": self._export.external_protected_low,
            "external_protected_high": self._export.external_protected_high,
            "external_weak_low": self._export.external_weak_low,
            "external_weak_high": self._export.external_weak_high,
            "internal_protected_low": self._export.internal_protected_low,
            "internal_protected_high": self._export.internal_protected_high,
            "internal_weak_low": self._export.internal_weak_low,
            "internal_weak_high": self._export.internal_weak_high,
        }
        levels.update({key: float(value) for key, value in export_levels.items() if value is not None})

        event_names = list(base.events)
        event_names.extend(f"{event.scope}:{event.event_type}:{event.direction}:{event.identity}" for event in structure_events)
        reasons = [event.evidence_text for event in structure_events if event.evidence_text]
        if external.evidence_text:
            reasons.append(external.evidence_text)
        if external.conflict_text:
            reasons.append(external.conflict_text)

        self._snapshot = EngineResult(
            engine=self.name,
            state=external.state,
            timestamp=clean["timestamp"],
            direction=direction,
            score=score,
            quality=external.quality if external.quality else None,
            levels=levels,
            events=tuple(event_names),
            reasons=tuple(reasons),
            is_confirmed=True,
        )
        return self._snapshot

    @property
    def external_context(self):
        return self._runtime.external.context

    @property
    def internal_context(self):
        return self._runtime.internal.context

    @property
    def export_contract(self):
        return self._export

    @property
    def external_break_candidate(self):
        return self._runtime.external.candidate

    @property
    def internal_break_candidate(self):
        return self._runtime.internal.candidate
