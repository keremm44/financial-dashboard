from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from financial_dashboard.data.parquet_store import ParquetOHLCVStore
from financial_dashboard.decision.history_source import HistoricalDecisionInputConfig
from financial_dashboard.decision.history_single_pass import (
    HistoricalReplayTimings,
    SinglePassHistoricalDecisionInputReplayRunner,
)
from financial_dashboard.decision_input import DecisionInputSnapshot

from .state_timeline import (
    CausalStateStore,
    DecisionStatePoint,
    DomainStatePoint,
    TimelineFingerprint,
    build_state_store,
)


@dataclass(frozen=True, slots=True)
class HistoricalDecisionStateStoreReplay:
    """Migration contract between the old producer and the new timeline consumer.

    The first migration step deliberately preserves the existing producer byte-for-
    byte: it wraps already-produced causal snapshots in the append-only state-store
    contract. The producer can then be replaced by the incremental reducer without
    changing BUY/SELL, lifecycle or audit consumers at the same time.
    """

    symbol: str
    decision_timeframe: str
    state_store: CausalStateStore[dict[str, int], DecisionInputSnapshot]
    timings: HistoricalReplayTimings

    @property
    def cutoffs(self) -> tuple[Any, ...]:
        return self.state_store.cutoffs

    @property
    def snapshots(self) -> tuple[DecisionInputSnapshot, ...]:
        return self.state_store.decision_states


class HistoricalDecisionStateStoreReplayRunner:
    def __init__(self, store: ParquetOHLCVStore) -> None:
        self.store = store

    def replay(
        self,
        symbol: str,
        *,
        config: HistoricalDecisionInputConfig | None = None,
    ) -> HistoricalDecisionStateStoreReplay:
        cfg = config or HistoricalDecisionInputConfig()
        legacy = SinglePassHistoricalDecisionInputReplayRunner(self.store).replay(
            symbol,
            config=cfg,
        )
        fingerprint = TimelineFingerprint(
            symbol=legacy.symbol,
            engine_config="historical-domain-state-v1",
            clock_version="CausalBarClock-v1",
            pattern_profile=cfg.pattern_profile,
        )
        domain_points = tuple(
            DomainStatePoint(
                as_of=cutoff,
                watermarks={},
                state={"legacy_snapshot_position": position},
            )
            for position, cutoff in enumerate(legacy.cutoffs)
        )
        decision_points = tuple(
            DecisionStatePoint(
                as_of=cutoff,
                domain_position=position,
                state=snapshot,
            )
            for position, (cutoff, snapshot) in enumerate(
                zip(legacy.cutoffs, legacy.snapshots, strict=True)
            )
        )
        state_store = build_state_store(
            fingerprint=fingerprint,
            domain_points=domain_points,
            decision_points=decision_points,
        )
        return HistoricalDecisionStateStoreReplay(
            symbol=legacy.symbol,
            decision_timeframe=legacy.decision_timeframe,
            state_store=state_store,
            timings=legacy.timings,
        )


__all__ = [
    "HistoricalDecisionStateStoreReplay",
    "HistoricalDecisionStateStoreReplayRunner",
]
