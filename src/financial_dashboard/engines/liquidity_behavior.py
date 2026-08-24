from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .liquidity_models import LiquidityPool, LiquidityPoolState, LiquiditySide


class LiquidityPoolMaturity(StrEnum):
    FORMING = "FORMING"
    ESTABLISHED = "ESTABLISHED"
    MATURE = "MATURE"
    STALE = "STALE"


class LiquidityPriceRelation(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    APPROACHING = "APPROACHING"
    AT_POOL = "AT_POOL"
    LEFT_BEHIND = "LEFT_BEHIND"


class LiquidityRemovalState(StrEnum):
    UNTOUCHED = "UNTOUCHED"
    TESTING = "TESTING"
    SWEEP_REJECTING = "SWEEP_REJECTING"
    SWEEP_RECLAIMED = "SWEEP_RECLAIMED"
    ACCEPTED_BEYOND = "ACCEPTED_BEYOND"
    CONSUMED = "CONSUMED"
    INVALIDATED = "INVALIDATED"


class LiquidityLandscapeState(StrEnum):
    NO_NEARBY_OBJECTIVE = "NO_NEARBY_OBJECTIVE"
    ONE_SIDED_OBJECTIVE = "ONE_SIDED_OBJECTIVE"
    COMPETING_OBJECTIVES = "COMPETING_OBJECTIVES"


@dataclass(frozen=True, slots=True)
class LiquidityBehaviorConfig:
    near_atr: float = 0.75
    mature_touches: int = 3
    stale_bars: int = 20
    acceptance_bars: int = 2
    landscape_near_atr: float = 2.0

    def __post_init__(self) -> None:
        if self.near_atr <= 0:
            raise ValueError("near_atr must be positive")
        if self.mature_touches < 2:
            raise ValueError("mature_touches must be >= 2")
        if self.stale_bars < 1:
            raise ValueError("stale_bars must be >= 1")
        if self.acceptance_bars < 1:
            raise ValueError("acceptance_bars must be >= 1")
        if self.landscape_near_atr <= 0:
            raise ValueError("landscape_near_atr must be positive")


@dataclass(frozen=True, slots=True)
class LiquidityPoolBehaviorSnapshot:
    identity: str
    side: LiquiditySide
    level: float
    maturity: LiquidityPoolMaturity
    relation: LiquidityPriceRelation
    removal: LiquidityRemovalState
    age_bars: int
    bars_since_touch: int
    touch_count: int
    distance_atr: float | None
    distance_delta_atr: float | None


@dataclass(frozen=True, slots=True)
class LiquidityBehaviorSnapshot:
    as_of: object | None
    landscape: LiquidityLandscapeState
    pools: tuple[LiquidityPoolBehaviorSnapshot, ...]

    def for_pool(self, identity: str) -> LiquidityPoolBehaviorSnapshot:
        for pool in self.pools:
            if pool.identity == identity:
                return pool
        raise KeyError(f"liquidity behavior pool not found: {identity}")


class LiquidityBehaviorTracker:
    """Incremental descriptive behavior over canonical liquidity pools.

    The canonical pool lifecycle remains authoritative. This tracker only interprets
    maturity, price relation, sweep aftermath and the nearby objective landscape
    from closed-bar facts already owned by the Liquidity engine.
    """

    def __init__(self, config: LiquidityBehaviorConfig | None = None) -> None:
        self.config = config or LiquidityBehaviorConfig()
        self.reset()

    def reset(self) -> None:
        self._previous_abs_distance_atr: dict[str, float] = {}
        self._terminal_bars: dict[str, int] = {}
        self._snapshot = LiquidityBehaviorSnapshot(
            as_of=None,
            landscape=LiquidityLandscapeState.NO_NEARBY_OBJECTIVE,
            pools=(),
        )

    @property
    def snapshot(self) -> LiquidityBehaviorSnapshot:
        return self._snapshot

    def update(
        self,
        pools: tuple[LiquidityPool, ...],
        *,
        bar_index: int,
        timestamp: object,
        high: float,
        low: float,
        close: float,
        atr: float | None,
    ) -> LiquidityBehaviorSnapshot:
        safe_atr = None if atr is None or abs(float(atr)) <= 1e-12 else abs(float(atr))
        rows: list[LiquidityPoolBehaviorSnapshot] = []

        for pool in pools:
            first_touch_index = int(pool.touches[0].bar_index)
            last_touch_index = int(pool.touches[-1].bar_index)
            age_bars = max(0, int(bar_index) - first_touch_index)
            bars_since_touch = max(0, int(bar_index) - last_touch_index)

            if pool.state is LiquidityPoolState.FORMING:
                maturity = LiquidityPoolMaturity.FORMING
            elif bars_since_touch >= self.config.stale_bars:
                maturity = LiquidityPoolMaturity.STALE
            elif pool.touch_count >= self.config.mature_touches:
                maturity = LiquidityPoolMaturity.MATURE
            else:
                maturity = LiquidityPoolMaturity.ESTABLISHED

            distance_atr: float | None = None
            distance_delta_atr: float | None = None
            relation = LiquidityPriceRelation.UNAVAILABLE
            if safe_atr is not None:
                signed_distance_atr = (float(pool.level) - float(close)) / safe_atr
                distance_atr = abs(signed_distance_atr)
                previous = self._previous_abs_distance_atr.get(pool.identity)
                if previous is not None:
                    distance_delta_atr = distance_atr - previous
                self._previous_abs_distance_atr[pool.identity] = distance_atr

                touched_now = float(low) <= float(pool.level) <= float(high)
                if touched_now or distance_atr <= self.config.near_atr * 0.25:
                    relation = LiquidityPriceRelation.AT_POOL
                elif distance_atr <= self.config.near_atr and distance_delta_atr is not None and distance_delta_atr < 0:
                    relation = LiquidityPriceRelation.APPROACHING
                else:
                    relation = LiquidityPriceRelation.LEFT_BEHIND

            if pool.state is LiquidityPoolState.TESTED:
                removal = LiquidityRemovalState.TESTING
            elif pool.state is LiquidityPoolState.SWEPT:
                removal = LiquidityRemovalState.SWEEP_REJECTING
            elif pool.state is LiquidityPoolState.RECLAIMED:
                removal = LiquidityRemovalState.SWEEP_RECLAIMED
            elif pool.state is LiquidityPoolState.INVALIDATED:
                removal = LiquidityRemovalState.INVALIDATED
            elif pool.state is LiquidityPoolState.CONSUMED:
                terminal_bars = self._terminal_bars.get(pool.identity, 0) + 1
                self._terminal_bars[pool.identity] = terminal_bars
                removal = (
                    LiquidityRemovalState.ACCEPTED_BEYOND
                    if terminal_bars < self.config.acceptance_bars
                    else LiquidityRemovalState.CONSUMED
                )
            else:
                self._terminal_bars.pop(pool.identity, None)
                removal = LiquidityRemovalState.UNTOUCHED

            rows.append(
                LiquidityPoolBehaviorSnapshot(
                    identity=pool.identity,
                    side=pool.side,
                    level=float(pool.level),
                    maturity=maturity,
                    relation=relation,
                    removal=removal,
                    age_bars=age_bars,
                    bars_since_touch=bars_since_touch,
                    touch_count=int(pool.touch_count),
                    distance_atr=distance_atr,
                    distance_delta_atr=distance_delta_atr,
                )
            )

        landscape = self._landscape(rows)
        self._snapshot = LiquidityBehaviorSnapshot(
            as_of=timestamp,
            landscape=landscape,
            pools=tuple(sorted(rows, key=lambda item: (item.side.value, item.level, item.identity))),
        )
        return self._snapshot

    def _landscape(
        self,
        rows: list[LiquidityPoolBehaviorSnapshot],
    ) -> LiquidityLandscapeState:
        nearby_sides = {
            row.side
            for row in rows
            if row.distance_atr is not None
            and row.distance_atr <= self.config.landscape_near_atr
            and row.removal not in {
                LiquidityRemovalState.CONSUMED,
                LiquidityRemovalState.INVALIDATED,
            }
            and row.maturity is not LiquidityPoolMaturity.FORMING
        }
        if len(nearby_sides) >= 2:
            return LiquidityLandscapeState.COMPETING_OBJECTIVES
        if len(nearby_sides) == 1:
            return LiquidityLandscapeState.ONE_SIDED_OBJECTIVE
        return LiquidityLandscapeState.NO_NEARBY_OBJECTIVE


__all__ = [
    "LiquidityBehaviorConfig",
    "LiquidityBehaviorSnapshot",
    "LiquidityBehaviorTracker",
    "LiquidityLandscapeState",
    "LiquidityPoolBehaviorSnapshot",
    "LiquidityPoolMaturity",
    "LiquidityPriceRelation",
    "LiquidityRemovalState",
]
