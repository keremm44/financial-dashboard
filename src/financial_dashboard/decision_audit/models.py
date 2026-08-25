from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class DecisionAction(StrEnum):
    WAIT = "WAIT"
    READY = "READY"
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    NO_TRADE = "NO_TRADE"


class DecisionSide(StrEnum):
    NONE = "NONE"
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True, slots=True)
class DecisionEvent:
    """One causal decision emitted from closed-bar information only.

    ``snapshot`` is intentionally opaque to the audit engine. It is persisted so a
    bad historical decision can later be explained with the exact LT/ST/domain state
    that was available when the decision was made.
    """

    timestamp: Any
    action: DecisionAction
    side: DecisionSide = DecisionSide.NONE
    price: float | None = None
    atr: float | None = None
    reasons: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    waiting_for: tuple[str, ...] = ()
    source_lineage: tuple[str, ...] = ()
    snapshot: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DecisionAuditConfig:
    """Hindsight-only benchmark settings.

    These values never feed the live decision engine. They only define the future /
    surrounding windows used to grade historical decisions after the causal replay
    has already produced them.
    """

    extrema_lookback_bars: int = 10
    extrema_lookahead_bars: int = 10
    opportunity_horizon_bars: int = 20
    swing_radius_bars: int = 3
    meaningful_move_atr: float | None = None
    capture_entry_window_bars: int = 5
    atr_length: int = 14
    allow_short_entries: bool = False

    def __post_init__(self) -> None:
        positive_ints = {
            "extrema_lookback_bars": self.extrema_lookback_bars,
            "extrema_lookahead_bars": self.extrema_lookahead_bars,
            "opportunity_horizon_bars": self.opportunity_horizon_bars,
            "swing_radius_bars": self.swing_radius_bars,
            "capture_entry_window_bars": self.capture_entry_window_bars,
            "atr_length": self.atr_length,
        }
        for name, value in positive_ints.items():
            if value < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.meaningful_move_atr is not None and self.meaningful_move_atr <= 0:
            raise ValueError("meaningful_move_atr must be positive when provided")


@dataclass(frozen=True, slots=True)
class TradeAudit:
    symbol: str
    side: DecisionSide
    entry_time: Any
    exit_time: Any
    entry_price: float
    exit_price: float
    bars_held: int

    return_pct: float
    mfe_pct: float
    mae_pct: float
    move_capture_ratio: float | None

    entry_local_low: float | None
    entry_local_low_miss_pct: float | None
    entry_local_low_miss_atr: float | None
    entry_early_bars: int
    entry_late_bars: int
    post_entry_additional_downside_pct: float | None
    post_entry_additional_downside_atr: float | None

    exit_local_high: float | None
    exit_peak_miss_pct: float | None
    exit_peak_miss_atr: float | None
    exit_early_bars: int
    exit_late_bars: int
    post_exit_missed_upside_pct: float | None
    post_exit_missed_upside_atr: float | None
    profit_giveback_pct: float | None
    profit_giveback_atr: float | None

    entry_reasons: tuple[str, ...] = ()
    entry_blockers: tuple[str, ...] = ()
    entry_waiting_for: tuple[str, ...] = ()
    entry_source_lineage: tuple[str, ...] = ()
    entry_snapshot: Mapping[str, Any] = field(default_factory=dict)

    exit_reasons: tuple[str, ...] = ()
    exit_blockers: tuple[str, ...] = ()
    exit_waiting_for: tuple[str, ...] = ()
    exit_source_lineage: tuple[str, ...] = ()
    exit_snapshot: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SignalStabilityAudit:
    action_counts: Mapping[str, int]
    ready_to_wait_reversals: int
    wait_episode_count: int
    ready_episode_count: int
    average_wait_duration_bars: float | None
    average_ready_duration_bars: float | None
    average_ready_to_buy_delay_bars: float | None


@dataclass(frozen=True, slots=True)
class MissedOpportunity:
    side: DecisionSide
    start_time: Any
    extreme_time: Any
    end_time: Any
    start_price: float
    extreme_price: float
    move_pct: float
    move_atr: float
    captured: bool
    nearest_decision_action: str | None
    nearest_decision_time: Any | None


@dataclass(frozen=True, slots=True)
class AggregateTradeMetrics:
    completed_trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate_pct: float | None
    average_return_pct: float | None
    median_return_pct: float | None
    compounded_return_pct: float | None
    average_winner_pct: float | None
    average_loser_pct: float | None
    best_trade_pct: float | None
    worst_trade_pct: float | None
    average_mfe_pct: float | None
    average_mae_pct: float | None
    average_move_capture_ratio_pct: float | None

    average_entry_local_low_miss_pct: float | None
    average_entry_local_low_miss_atr: float | None
    early_entry_cases: int
    late_entry_cases: int
    average_entry_early_bars: float | None
    average_entry_late_bars: float | None
    average_post_entry_additional_downside_pct: float | None
    worst_post_entry_additional_downside_pct: float | None

    average_exit_peak_miss_pct: float | None
    average_exit_peak_miss_atr: float | None
    early_exit_cases: int
    late_exit_cases: int
    average_exit_early_bars: float | None
    average_exit_late_bars: float | None
    average_post_exit_missed_upside_pct: float | None
    worst_post_exit_missed_upside_pct: float | None
    average_profit_giveback_pct: float | None


@dataclass(frozen=True, slots=True)
class DecisionAuditReport:
    symbol: str
    timeframe: str
    start_time: Any | None
    end_time: Any | None
    metrics: AggregateTradeMetrics
    signal_stability: SignalStabilityAudit
    trades: tuple[TradeAudit, ...]
    missed_opportunities: tuple[MissedOpportunity, ...] = ()
    unmatched_buy_events: int = 0
    unmatched_sell_events: int = 0


__all__ = [
    "AggregateTradeMetrics",
    "DecisionAction",
    "DecisionAuditConfig",
    "DecisionAuditReport",
    "DecisionEvent",
    "DecisionSide",
    "MissedOpportunity",
    "SignalStabilityAudit",
    "TradeAudit",
]
