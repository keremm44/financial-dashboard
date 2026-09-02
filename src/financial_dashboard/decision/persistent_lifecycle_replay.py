from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from .engine import DecisionEngineConfig
from .execution import ExecutionTriggerEvent
from .lifecycle import TradeLifecycleState
from .lifecycle_persistence import (
    LifecycleCheckpointStatus,
    PersistentTradeLifecycleStore,
    TradeLifecycleCheckpoint,
    causal_prefix_digest,
    decision_config_digest,
)
from .lifecycle_replay import (
    CanonicalLifecycleReplayResult,
    ReplayAuditMarkerState,
    replay_canonical_trade_lifecycle,
)

if TYPE_CHECKING:
    from financial_dashboard.decision_input import DecisionInputSnapshot


@dataclass(frozen=True, slots=True)
class PersistentLifecycleReplayResult:
    replay: CanonicalLifecycleReplayResult
    resumed: bool
    processed_count: int
    checkpoint: TradeLifecycleCheckpoint


class PersistentCanonicalLifecycleReplayRunner:
    """Resume only the unconsumed tail of a full causal DecisionInput stream.

    A loaded checkpoint is trusted only if the complete previously-consumed prefix,
    same-bar entry/exit execution inputs, and decision config still match. Trading
    ownership and downstream audit progression are both restored from that verified
    checkpoint. Mismatch or corrupt state fails closed instead of resetting to FLAT.
    """

    def __init__(self, root: str | Path) -> None:
        self.store = PersistentTradeLifecycleStore(root)

    def run(
        self,
        symbol: str,
        snapshots: Iterable["DecisionInputSnapshot"],
        *,
        config: DecisionEngineConfig | None = None,
        entry_execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
        exit_execution_events: Mapping[Any, ExecutionTriggerEvent] | None = None,
    ) -> PersistentLifecycleReplayResult:
        if not symbol.strip():
            raise ValueError("persistent lifecycle replay symbol must be non-empty")

        values = tuple(snapshots)
        entry_events = entry_execution_events or {}
        exit_events = exit_execution_events or {}
        cfg = config or DecisionEngineConfig()
        cfg_digest = decision_config_digest(cfg)

        previous_as_of: Any | None = None
        for snapshot in values:
            if str(snapshot.symbol) != symbol:
                raise ValueError("persistent lifecycle replay snapshots must match symbol")
            if previous_as_of is not None and snapshot.as_of <= previous_as_of:
                raise ValueError("persistent lifecycle replay snapshots must be strictly increasing")
            previous_as_of = snapshot.as_of

        loaded = self.store.load(symbol)
        if loaded.status is LifecycleCheckpointStatus.INVALID:
            raise RuntimeError("persisted lifecycle checkpoint is invalid; explicit cold replay is required")

        resumed = loaded.status is LifecycleCheckpointStatus.LOADED
        if resumed:
            assert loaded.checkpoint is not None
            checkpoint = loaded.checkpoint
            if checkpoint.decision_config_digest != cfg_digest:
                raise ValueError("persisted lifecycle decision config changed; cold replay is required")
            if checkpoint.prefix_count > len(values):
                raise ValueError("persisted lifecycle prefix is longer than supplied snapshots")
            consumed_prefix = values[: checkpoint.prefix_count]
            current_digest = causal_prefix_digest(
                consumed_prefix,
                entry_execution_events=entry_events,
                exit_execution_events=exit_events,
            )
            if current_digest != checkpoint.causal_prefix_digest:
                raise ValueError("persisted lifecycle causal prefix changed; cold replay is required")
            if checkpoint.prefix_count and consumed_prefix[-1].as_of != checkpoint.last_as_of:
                raise ValueError("persisted lifecycle last_as_of no longer matches causal prefix")
            initial_state = checkpoint.state
            initial_audit_markers = checkpoint.audit_markers
            tail = values[checkpoint.prefix_count :]
        else:
            initial_state = TradeLifecycleState()
            initial_audit_markers = ReplayAuditMarkerState()
            tail = values

        replay = replay_canonical_trade_lifecycle(
            tail,
            # ``cfg`` is always used for the checkpoint digest. Only an explicitly
            # supplied config is forwarded as an override to policy calls; omitting
            # config preserves the canonical default call surface while hashing the
            # exact same default semantics.
            config=config,
            entry_execution_events=entry_events,
            exit_execution_events=exit_events,
            initial_state=initial_state,
            initial_audit_markers=initial_audit_markers,
        )

        checkpoint = TradeLifecycleCheckpoint(
            symbol=symbol,
            state=replay.final_state,
            prefix_count=len(values),
            last_as_of=None if not values else values[-1].as_of,
            causal_prefix_digest=causal_prefix_digest(
                values,
                entry_execution_events=entry_events,
                exit_execution_events=exit_events,
            ),
            decision_config_digest=cfg_digest,
            audit_markers=replay.final_audit_markers,
        )
        if not resumed or tail:
            self.store.save(checkpoint)

        return PersistentLifecycleReplayResult(
            replay=replay,
            resumed=resumed,
            processed_count=len(tail),
            checkpoint=checkpoint,
        )

    def clear(self, symbol: str) -> None:
        """Explicit operator action required before a cold replay after invalidation."""

        self.store.clear(symbol)


__all__ = [
    "PersistentCanonicalLifecycleReplayRunner",
    "PersistentLifecycleReplayResult",
]
