from __future__ import annotations

from dataclasses import dataclass

from .history_incremental import IncrementalHistoricalDecisionInputReplayRunner
from .history_single_pass import SinglePassHistoricalDecisionInputReplay
from .history_source import HistoricalDecisionInputConfig
from .persistent_state import (
    PersistentCacheIdentity,
    PersistentCheckpointIdentity,
    PersistentCheckpointRecord,
    PersistentObjectStore,
    build_prefix_fingerprints,
    validate_append_only_prefix,
)


_DECISION_APPEND_REFERENCE_SEMANTIC_VERSION = "decision-input-append-reference-v2"


@dataclass(frozen=True, slots=True)
class DecisionTimelineReference:
    """Small append checkpoint payload pointing at the frozen decision read model."""

    exact_identity: PersistentCacheIdentity


class PersistentHistoricalDecisionInputReplayRunner(
    IncrementalHistoricalDecisionInputReplayRunner
):
    """Canonical persistent runner without duplicating the full decision timeline.

    The exact frozen DecisionInput timeline is written once. The append checkpoint keeps
    only prefix fingerprints plus a reference to that exact object. Native/supporting
    engine checkpoints remain the continuation state used to process future closed bars.
    """

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
            persistent.save(exact_identity, result)
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
    "DecisionTimelineReference",
    "PersistentHistoricalDecisionInputReplayRunner",
]
