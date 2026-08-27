from __future__ import annotations

from dataclasses import dataclass
import os
import pickle

from financial_dashboard.data.identity import normalize_symbol

from .history_incremental import IncrementalHistoricalDecisionInputReplayRunner, _zero_timings
from .history_single_pass import SinglePassHistoricalDecisionInputReplay
from .history_source import HistoricalDecisionInputConfig
from .persistent_state import (
    PERSISTENT_STATE_SCHEMA_VERSION,
    PersistentCacheIdentity,
    PersistentCheckpointIdentity,
    PersistentCheckpointRecord,
    PersistentObjectStore,
    build_prefix_fingerprints,
    validate_append_only_prefix,
)


_DECISION_APPEND_REFERENCE_SEMANTIC_VERSION = "decision-input-append-reference-v2"


class DecisionTimelineCacheMiss(RuntimeError):
    """Raised when a cache-only consumer cannot find the exact frozen timeline."""


@dataclass(frozen=True, slots=True)
class DecisionTimelineReference:
    """Small append checkpoint payload pointing at the frozen decision read model."""

    exact_identity: PersistentCacheIdentity


def _save_rebuildable_exact_cache(
    persistent: PersistentObjectStore,
    identity: PersistentCacheIdentity,
    payload: SinglePassHistoricalDecisionInputReplay,
) -> None:
    """Atomically write the large rebuildable read model without a durability fsync.

    Engine continuation checkpoints remain durable through PersistentObjectStore.  The
    exact DecisionInput timeline is a derived cache: a crash during this write may lose
    the cache, but cannot corrupt market state because the temporary file is replaced
    atomically and the loader fails closed. Avoiding fsync prevents a multi-hundred-MB
    cache write from blocking on a full disk flush after an otherwise completed cold
    replay.
    """

    path = persistent.path_for(identity)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.cache.tmp")
    try:
        with temporary.open("wb") as handle:
            pickle.dump(
                {
                    "schema_version": PERSISTENT_STATE_SCHEMA_VERSION,
                    "identity": identity,
                    "payload": payload,
                },
                handle,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
            handle.flush()
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class PersistentHistoricalDecisionInputReplayRunner(
    IncrementalHistoricalDecisionInputReplayRunner
):
    """Canonical persistent runner without duplicating the full decision timeline.

    The exact frozen DecisionInput timeline is written once. The append checkpoint keeps
    only prefix fingerprints plus a reference to that exact object. Native/supporting
    engine checkpoints remain the continuation state used to process future closed bars.
    """

    def load_cached(
        self,
        symbol: str,
        *,
        config: HistoricalDecisionInputConfig | None = None,
    ) -> SinglePassHistoricalDecisionInputReplay:
        """Load only the exact frozen DecisionInput timeline; never replay domains.

        This is the BUY/SELL/backtest fast path. A missing or stale exact cache is an
        explicit error instead of silently falling back to native/supporting replay.
        Call ``replay`` separately when the timeline must be built or refreshed.
        """

        cfg = config or HistoricalDecisionInputConfig()
        clean_symbol = normalize_symbol(symbol)
        self.last_assembly_breakdown = {}
        self.last_persistent_cache_status = "MISS_CACHE_ONLY"
        self.last_native_checkpoint_status = "NOT_TOUCHED"
        self.last_supporting_checkpoint_status = "NOT_TOUCHED"
        self.last_decision_append_status = "NOT_TOUCHED"

        persistent = PersistentObjectStore(self.store.root)
        exact_identity = self._cache_identity(symbol=clean_symbol, config=cfg)
        cached = persistent.load(exact_identity)
        if not isinstance(cached, SinglePassHistoricalDecisionInputReplay):
            raise DecisionTimelineCacheMiss(
                "exact frozen DecisionInput timeline is unavailable for the current "
                f"symbol/config/source identity: {clean_symbol}"
            )

        self.last_persistent_cache_status = "HIT_EXACT_CACHE_ONLY"
        self.last_assembly_breakdown = {
            "views": 0.0,
            "evidence": 0.0,
            "dedup": 0.0,
            "targeting": 0.0,
            "semantic_targeting": 0.0,
            "cross_domain": 0.0,
            "decision_input": 0.0,
        }
        return SinglePassHistoricalDecisionInputReplay(
            symbol=cached.symbol,
            decision_timeframe=cached.decision_timeframe,
            cutoffs=cached.cutoffs,
            snapshots=cached.snapshots,
            timings=_zero_timings(),
        )

    def _decision_checkpoint_identity(
        self,
        *,
        symbol: str,
        config: HistoricalDecisionInputConfig,
    ) -> PersistentCheckpointIdentity:
        base = super()._decision_checkpoint_identity(symbol=symbol, config=config)
        return PersistentCheckpointIdentity(
            namespace=base.namespace,
            symbol=base.symbol,
            semantic_fingerprint=_DECISION_APPEND_REFERENCE_SEMANTIC_VERSION,
            config_fingerprint=base.config_fingerprint,
            implementation_fingerprint=base.implementation_fingerprint,
        )

    def _cached_prefix_result(
        self,
        record: PersistentCheckpointRecord | None,
        *,
        inputs,
    ) -> SinglePassHistoricalDecisionInputReplay | None:
        if record is None or not validate_append_only_prefix(inputs, record.prefixes):
            return None

        payload = record.payload
        if isinstance(payload, SinglePassHistoricalDecisionInputReplay):
            # Backward-compatible read of the old oversized checkpoint. New writes never
            # use this shape.
            return payload
        if not isinstance(payload, DecisionTimelineReference):
            return None

        cached = PersistentObjectStore(self.store.root).load(payload.exact_identity)
        return cached if isinstance(cached, SinglePassHistoricalDecisionInputReplay) else None

    def _save_decision_checkpoints(
        self,
        *,
        persistent: PersistentObjectStore,
        exact_identity: PersistentCacheIdentity,
        append_identity: PersistentCheckpointIdentity,
        inputs,
        result: SinglePassHistoricalDecisionInputReplay,
    ) -> None:
        exact_saved = False
        append_saved = False
        try:
            _save_rebuildable_exact_cache(persistent, exact_identity, result)
            exact_saved = True
        except Exception:
            pass

        if exact_saved:
            try:
                watermarks = {
                    timeframe: len(inputs.for_timeframe(timeframe).input_batch.frame) - 1
                    for timeframe in inputs.timeframes
                }
                persistent.save_checkpoint(
                    PersistentCheckpointRecord(
                        identity=append_identity,
                        prefixes=build_prefix_fingerprints(inputs, watermarks=watermarks),
                        cursor=None,
                        payload=DecisionTimelineReference(exact_identity=exact_identity),
                    )
                )
                append_saved = True
            except Exception:
                pass

        self.last_persistent_cache_status = "SAVED" if exact_saved else "SAVE_FAILED"
        self.last_decision_append_status = "SAVED" if append_saved else "SAVE_FAILED"


__all__ = [
    "DecisionTimelineCacheMiss",
    "DecisionTimelineReference",
    "PersistentHistoricalDecisionInputReplayRunner",
]
