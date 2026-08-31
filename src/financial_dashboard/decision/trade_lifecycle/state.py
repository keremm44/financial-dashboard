"""Trade-lifecycle compatibility facade for canonical lifecycle ownership."""

from ..lifecycle import (
    ExitStage,
    PositionState,
    TradeLifecycleState,
    TradeLifecycleTransition,
    transition_entry_lifecycle,
    transition_trade_lifecycle,
)

__all__ = [
    "ExitStage",
    "PositionState",
    "TradeLifecycleState",
    "TradeLifecycleTransition",
    "transition_entry_lifecycle",
    "transition_trade_lifecycle",
]
