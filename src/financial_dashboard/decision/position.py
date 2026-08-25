from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .structural import StructuralAssessment, StructuralDirection, ThesisState


class PositionSide(StrEnum):
    """Current exposure, deliberately separate from the market thesis side."""

    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True, slots=True)
class PositionContext:
    """Minimal position state used by the action composer.

    Position sizing, stops, targets and portfolio risk remain outside the v1
    decision contract. ``opened_at`` and ``entry_price`` are optional metadata so a
    manually supplied UI position can still be represented without inventing fill
    information.
    """

    side: PositionSide = PositionSide.FLAT
    opened_at: Any | None = None
    entry_price: float | None = None

    def __post_init__(self) -> None:
        if self.side is PositionSide.FLAT and (
            self.opened_at is not None or self.entry_price is not None
        ):
            raise ValueError("flat position cannot carry entry metadata")
        if self.entry_price is not None and float(self.entry_price) <= 0.0:
            raise ValueError("position entry_price must be positive when supplied")

    @classmethod
    def flat(cls) -> "PositionContext":
        return cls()

    @classmethod
    def long(
        cls,
        *,
        opened_at: Any | None = None,
        entry_price: float | None = None,
    ) -> "PositionContext":
        return cls(PositionSide.LONG, opened_at=opened_at, entry_price=entry_price)

    @classmethod
    def short(
        cls,
        *,
        opened_at: Any | None = None,
        entry_price: float | None = None,
    ) -> "PositionContext":
        return cls(PositionSide.SHORT, opened_at=opened_at, entry_price=entry_price)


def opposing_structural_side(position: PositionContext) -> StructuralDirection | None:
    if position.side is PositionSide.LONG:
        return StructuralDirection.SHORT
    if position.side is PositionSide.SHORT:
        return StructuralDirection.LONG
    return None


def position_exit_candidate(
    structural: StructuralAssessment,
    position: PositionContext,
) -> StructuralDirection | None:
    """Return the Structure-owned side that may close the current exposure.

    This function never establishes a new market thesis. For an existing position,
    a canonical opposite Structure side, a canonical transition target, or an
    explicit structural invalidation is enough to *monitor* an exit. The actual
    position action still requires the configured fresh execution event (30m in v1).

    That distinction lets cash-equity ``SELL`` mean "close an existing LONG"
    without pretending the account is allowed to open a short position.
    """

    opposite = opposing_structural_side(position)
    if opposite is None:
        return None
    if structural.thesis_state is ThesisState.INVALIDATED:
        return opposite
    if structural.direction is opposite:
        return opposite
    if (
        structural.thesis_state is ThesisState.TRANSITIONING
        and structural.transition_target is opposite
    ):
        return opposite
    return None


__all__ = [
    "PositionContext",
    "PositionSide",
    "opposing_structural_side",
    "position_exit_candidate",
]
