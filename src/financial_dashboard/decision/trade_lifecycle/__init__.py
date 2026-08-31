"""Trade lifecycle compatibility surface over canonical lifecycle ownership."""

from .state import (
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
