from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


class LiquiditySide(StrEnum):
    BSL = "BSL"
    SSL = "SSL"


class LiquidityPoolState(StrEnum):
    FORMING = "FORMING"
    ACTIVE = "ACTIVE"
    TESTED = "TESTED"
    SWEPT = "SWEPT"
    RECLAIMED = "RECLAIMED"
    CONSUMED = "CONSUMED"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True, slots=True)
class LiquidityConfig:
    atr_tolerance: float = 0.15
    min_tick: float = 0.01
    min_touches_active: int = 2
    test_tolerance_factor: float = 1.0

    def __post_init__(self) -> None:
        if self.atr_tolerance <= 0:
            raise ValueError("atr_tolerance must be > 0")
        if self.min_tick <= 0:
            raise ValueError("min_tick must be > 0")
        if self.min_touches_active < 2:
            raise ValueError("min_touches_active must be >= 2")
        if self.test_tolerance_factor <= 0:
            raise ValueError("test_tolerance_factor must be > 0")


@dataclass(frozen=True, slots=True)
class LiquidityTouch:
    timestamp: Any
    price: float
    bar_index: int


@dataclass(frozen=True, slots=True)
class LiquidityPool:
    identity: str
    side: LiquiditySide
    level: float
    state: LiquidityPoolState
    touches: tuple[LiquidityTouch, ...]
    created_at: Any
    updated_at: Any
    last_event: str | None = None

    @property
    def touch_count(self) -> int:
        return len(self.touches)

    def with_touch(self, touch: LiquidityTouch) -> "LiquidityPool":
        prices = [t.price for t in self.touches] + [touch.price]
        return replace(
            self,
            level=sum(prices) / len(prices),
            touches=self.touches + (touch,),
            updated_at=touch.timestamp,
        )
