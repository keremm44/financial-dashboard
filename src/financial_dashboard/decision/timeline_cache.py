from __future__ import annotations

from dataclasses import dataclass

from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore

from .history_incremental import _zero_timings
from .history_replay import HistoricalDecisionInputReplayRunner
from .history_single_pass import SinglePassHistoricalDecisionInputReplay
from .history_source import HistoricalDecisionInputConfig
from .persistent_history_runner import (
    _save_rebuildable_exact_cache,
    find_compatible_exact_cache,
)
from .persistent_state import PersistentObjectStore


class DecisionTimelineCacheMiss(RuntimeError):
    """Raised when the exact frozen DecisionInput timeline is unavailable."""


@dataclass(frozen=True, slots=True)
class FrozenDecisionTimelineLoad:
    replay: SinglePassHistoricalDecisionInputReplay
    cache_status: str


def load_frozen_decision_timeline(
    store: ParquetOHLCVStore,
    symbol: str,
    *,
    config: HistoricalDecisionInputConfig | None = None,
) -> FrozenDecisionTimelineLoad:
    """Load the exact frozen DecisionInput timeline without touching domain replay.

    This module is intentionally downstream of the persisted DecisionInput semantics, so
    adding/changing BUY/SELL backtest plumbing does not invalidate an otherwise compatible
    DecisionInput cache. A miss is explicit; callers must invoke the separate builder if
    they want to create or refresh the timeline.
    """

    cfg = config or HistoricalDecisionInputConfig()
    clean_symbol = normalize_symbol(symbol)
    identity_runner = HistoricalDecisionInputReplayRunner(store)
    exact_identity = identity_runner._cache_identity(symbol=clean_symbol, config=cfg)
    persistent = PersistentObjectStore(store.root)
    cached = persistent.load(exact_identity)
    cache_status = "HIT_EXACT_CACHE_ONLY"
    if not isinstance(cached, SinglePassHistoricalDecisionInputReplay):
        cached = find_compatible_exact_cache(persistent, exact_identity)
        if not isinstance(cached, SinglePassHistoricalDecisionInputReplay):
            raise DecisionTimelineCacheMiss(
                "exact frozen DecisionInput timeline is unavailable for the current "
                f"symbol/config/source identity: {clean_symbol}"
            )
        cache_status = "HIT_REBOUND_CONTENT_IDENTITY"
        try:
            _save_rebuildable_exact_cache(persistent, exact_identity, cached)
        except Exception:
            pass

    replay = SinglePassHistoricalDecisionInputReplay(
        symbol=cached.symbol,
        decision_timeframe=cached.decision_timeframe,
        cutoffs=cached.cutoffs,
        snapshots=cached.snapshots,
        timings=_zero_timings(),
    )
    return FrozenDecisionTimelineLoad(
        replay=replay,
        cache_status=cache_status,
    )


__all__ = [
    "DecisionTimelineCacheMiss",
    "FrozenDecisionTimelineLoad",
    "load_frozen_decision_timeline",
]
