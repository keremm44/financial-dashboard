from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .base import BaseEngine
from .liquidity_behavior import LiquidityBehaviorSnapshot, LiquidityBehaviorTracker
from .liquidity_core import apply_bar_event, cluster_touch
from .liquidity_models import LiquidityConfig, LiquidityPool, LiquidityPoolState, LiquiditySide, LiquidityTouch
from .models import Direction, EngineResult


@dataclass(frozen=True, slots=True)
class LiquidityExport:
    nearest_bsl: float | None = None
    nearest_ssl: float | None = None
    active_bsl_count: int = 0
    active_ssl_count: int = 0
    latest_event_side: str | None = None
    latest_event_state: str | None = None
    latest_event_level: float | None = None
    latest_event_identity: str | None = None
    latest_event_direction: int = 0
    quality: float | None = None


class LiquidityEngine(BaseEngine):
    name = "liquidity"

    def __init__(self, config: LiquidityConfig | None = None) -> None:
        self.config = config or LiquidityConfig()
        self.reset()

    def reset(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self._tr_values: list[float] = []
        self._atr_values: list[float | None] = []
        self._pools: tuple[LiquidityPool, ...] = ()
        self._snapshot: EngineResult | None = None
        self._export: LiquidityExport | None = None
        self._behavior_tracker = LiquidityBehaviorTracker()

    def replay(self, frame: pd.DataFrame) -> list[EngineResult]:
        self.reset()
        out: list[EngineResult] = []
        for row in frame.to_dict("records"):
            result = self.update(row)
            if result is not None:
                out.append(result)
        return out

    def snapshot(self) -> EngineResult | None:
        return self._snapshot

    @property
    def export_contract(self) -> LiquidityExport | None:
        return self._export

    @property
    def pools(self) -> tuple[LiquidityPool, ...]:
        return self._pools

    @property
    def behavior_snapshot(self) -> LiquidityBehaviorSnapshot:
        return self._behavior_tracker.snapshot

    def update(self, bar: pd.Series | dict[str, Any]) -> EngineResult | None:
        row = dict(bar) if isinstance(bar, dict) else bar.to_dict()
        if not bool(row.get("is_closed", True)):
            return self._snapshot

        required = ("timestamp", "open", "high", "low", "close")
        missing = [key for key in required if key not in row or pd.isna(row[key])]
        if missing:
            raise ValueError(f"liquidity requires closed OHLC bars; missing {missing}")

        clean = {
            "timestamp": row["timestamp"],
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
        self._rows.append(clean)
        self._append_atr(clean)
        bar_index = len(self._rows) - 1
        safe_atr = self._safe_atr(bar_index)

        events: list[str] = []
        reasons: list[str] = []

        # Existing pools first observe the current confirmed bar.
        updated_pools: list[LiquidityPool] = []
        directional_events: list[tuple[Direction, LiquidityPool, str]] = []
        for pool in self._pools:
            before = pool.state
            updated = apply_bar_event(
                pool,
                high=clean["high"],
                low=clean["low"],
                close=clean["close"],
                timestamp=clean["timestamp"],
                atr=safe_atr,
                config=self.config,
            )
            updated_pools.append(updated)
            if updated.state is not before or updated.last_event != pool.last_event:
                event = updated.last_event or "STATE_CHANGE"
                events.append(f"{updated.side.value}:{event}:{updated.identity}")
                if event in {"SWEEP", "RECLAIM"}:
                    direction = Direction.DOWN if updated.side is LiquiditySide.BSL else Direction.UP
                    directional_events.append((direction, updated, event))
                    reasons.append(f"{updated.side.value} {event.lower()} at {updated.level:.6f}")
                elif event == "CONSUME":
                    reasons.append(f"{updated.side.value} liquidity consumed beyond {updated.level:.6f}")
        self._pools = tuple(updated_pools)

        # Confirm a centered pivot only after pivot_span right-hand bars exist.
        source_bar = bar_index - self.config.pivot_span
        if source_bar >= self.config.pivot_span:
            fallback_atr = self._safe_atr(source_bar)
            if self._is_pivot_high(source_bar):
                touch = LiquidityTouch(
                    timestamp=self._rows[source_bar]["timestamp"],
                    price=self._rows[source_bar]["high"],
                    bar_index=source_bar,
                )
                self._pools, chosen = cluster_touch(
                    self._pools,
                    side=LiquiditySide.BSL,
                    touch=touch,
                    atr=fallback_atr,
                    config=self.config,
                )
                events.append(f"BSL:PIVOT_CONFIRMED:{chosen.identity}")
            if self._is_pivot_low(source_bar):
                touch = LiquidityTouch(
                    timestamp=self._rows[source_bar]["timestamp"],
                    price=self._rows[source_bar]["low"],
                    bar_index=source_bar,
                )
                self._pools, chosen = cluster_touch(
                    self._pools,
                    side=LiquiditySide.SSL,
                    touch=touch,
                    atr=fallback_atr,
                    config=self.config,
                )
                events.append(f"SSL:PIVOT_CONFIRMED:{chosen.identity}")

        # Additive behavior interpretation. It observes the final canonical pool
        # snapshot for this closed bar and cannot mutate pool lifecycle state.
        self._behavior_tracker.update(
            self._pools,
            bar_index=bar_index,
            timestamp=clean["timestamp"],
            high=clean["high"],
            low=clean["low"],
            close=clean["close"],
            atr=safe_atr,
        )

        direction, state, score, quality, latest = self._interpret(directional_events)
        levels = self._public_levels(clean["close"])
        self._export = self._build_export(clean["close"], latest, quality)
        self._snapshot = EngineResult(
            engine=self.name,
            state=state,
            timestamp=clean["timestamp"],
            direction=direction,
            score=score,
            quality=quality,
            levels=levels,
            events=tuple(events),
            reasons=tuple(reasons),
            is_confirmed=True,
        )
        return self._snapshot

    def _append_atr(self, row: dict[str, Any]) -> None:
        idx = len(self._rows) - 1
        prev_close = self._rows[idx - 1]["close"] if idx > 0 else None
        tr = row["high"] - row["low"]
        if prev_close is not None:
            tr = max(tr, abs(row["high"] - prev_close), abs(row["low"] - prev_close))
        self._tr_values.append(float(tr))
        length = self.config.atr_length
        if len(self._tr_values) < length:
            atr = None
        elif len(self._tr_values) == length:
            atr = sum(self._tr_values[-length:]) / length
        else:
            prev_atr = self._atr_values[-1]
            atr = (
                sum(self._tr_values[-length:]) / length
                if prev_atr is None
                else (prev_atr * (length - 1) + tr) / length
            )
        self._atr_values.append(None if atr is None else float(atr))

    def _safe_atr(self, bar_index: int) -> float:
        value = self._atr_values[bar_index]
        fallback = self._tr_values[bar_index] if self._tr_values else self.config.min_tick
        return max(float(value if value is not None else fallback), self.config.min_tick)

    def _is_pivot_high(self, source_bar: int) -> bool:
        span = self.config.pivot_span
        value = self._rows[source_bar]["high"]
        window = [r["high"] for r in self._rows[source_bar - span : source_bar + span + 1]]
        return value == max(window) and window.count(value) == 1

    def _is_pivot_low(self, source_bar: int) -> bool:
        span = self.config.pivot_span
        value = self._rows[source_bar]["low"]
        window = [r["low"] for r in self._rows[source_bar - span : source_bar + span + 1]]
        return value == min(window) and window.count(value) == 1

    def _interpret(
        self,
        directional_events: list[tuple[Direction, LiquidityPool, str]],
    ) -> tuple[Direction, str, float, float, tuple[Direction, LiquidityPool, str] | None]:
        if not directional_events:
            active = [p for p in self._pools if p.state not in {LiquidityPoolState.CONSUMED, LiquidityPoolState.INVALIDATED}]
            quality = self._pool_quality(active)
            return Direction.NEUTRAL, "LIQUIDITY_NEUTRAL", 0.0, quality, None

        directions = {event[0] for event in directional_events}
        if len(directions) > 1:
            quality = max(self._event_quality(pool, event) for _, pool, event in directional_events)
            return Direction.NEUTRAL, "LIQUIDITY_CONFLICT", 0.0, quality, directional_events[-1]

        direction = directional_events[-1][0]
        latest = directional_events[-1]
        strongest = max(directional_events, key=lambda item: self._event_quality(item[1], item[2]))
        _, pool, event = strongest
        quality = self._event_quality(pool, event)
        score = quality if direction is Direction.UP else -quality
        state = f"{pool.side.value}_{event}"
        return direction, state, score, quality, latest

    def _event_quality(self, pool: LiquidityPool, event: str) -> float:
        touch_component = min(30.0, 15.0 * pool.touch_count)
        event_component = 55.0 if event == "RECLAIM" else 45.0
        return round(min(100.0, touch_component + event_component), 2)

    def _pool_quality(self, pools: list[LiquidityPool]) -> float:
        if not pools:
            return 0.0
        best = max(min(70.0, 20.0 + 15.0 * pool.touch_count) for pool in pools)
        return round(best, 2)

    def _public_levels(self, close: float) -> dict[str, float]:
        active = [p for p in self._pools if p.state not in {LiquidityPoolState.CONSUMED, LiquidityPoolState.INVALIDATED}]
        bsl = [p for p in active if p.side is LiquiditySide.BSL and p.level >= close]
        ssl = [p for p in active if p.side is LiquiditySide.SSL and p.level <= close]
        levels: dict[str, float] = {}
        if bsl:
            levels["nearest_bsl"] = min(p.level for p in bsl)
        if ssl:
            levels["nearest_ssl"] = max(p.level for p in ssl)
        return levels

    def _build_export(
        self,
        close: float,
        latest: tuple[Direction, LiquidityPool, str] | None,
        quality: float,
    ) -> LiquidityExport:
        active = [p for p in self._pools if p.state not in {LiquidityPoolState.CONSUMED, LiquidityPoolState.INVALIDATED}]
        bsl = [p for p in active if p.side is LiquiditySide.BSL]
        ssl = [p for p in active if p.side is LiquiditySide.SSL]
        nearest_bsl = min((p.level for p in bsl if p.level >= close), default=None)
        nearest_ssl = max((p.level for p in ssl if p.level <= close), default=None)
        if latest is None:
            return LiquidityExport(
                nearest_bsl=nearest_bsl,
                nearest_ssl=nearest_ssl,
                active_bsl_count=len(bsl),
                active_ssl_count=len(ssl),
                quality=quality,
            )
        direction, pool, event = latest
        return LiquidityExport(
            nearest_bsl=nearest_bsl,
            nearest_ssl=nearest_ssl,
            active_bsl_count=len(bsl),
            active_ssl_count=len(ssl),
            latest_event_side=pool.side.value,
            latest_event_state=event,
            latest_event_level=pool.level,
            latest_event_identity=pool.identity,
            latest_event_direction=int(direction),
            quality=quality,
        )
