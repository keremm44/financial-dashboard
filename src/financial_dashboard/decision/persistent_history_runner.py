from __future__ import annotations

from dataclasses import dataclass
import json
import os
import pickle

from .history_incremental import IncrementalHistoricalDecisionInputReplayRunner
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
_EXACT_CACHE_SUFFIX = ".pkl"
_IDENTITY_SIDECAR_SUFFIX = ".identity.json"


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
    _write_identity_sidecar(path, identity)


def _write_identity_sidecar(cache_path, identity: PersistentCacheIdentity) -> None:
    """Persist a tiny JSON identity record next to one exact cache file.

    The sidecar lets stale-cache eviction inspect an identity without
    unpickling a multi-hundred-MB payload. Best-effort: a failed sidecar
    write only leaves a legacy-style cache file that eviction will skip.
    """

    sidecar = cache_path.with_name(
        cache_path.name[: -len(_EXACT_CACHE_SUFFIX)] + _IDENTITY_SIDECAR_SUFFIX
    )
    try:
        sidecar.write_text(
            json.dumps(
                {
                    "namespace": identity.namespace,
                    "symbol": identity.symbol,
                    "semantic_fingerprint": identity.semantic_fingerprint,
                    "config_fingerprint": identity.config_fingerprint,
                    "source_fingerprint": [list(row) for row in identity.source_fingerprint],
                    "digest": identity.digest,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def _safe_namespace_prefix(namespace: str) -> str:
    from .persistent_state import _safe_part

    return f"{_safe_part(namespace)}__"


def evict_stale_exact_caches(
    persistent: PersistentObjectStore,
    identity: PersistentCacheIdentity,
) -> int:
    """Delete superseded exact caches for the same symbol/config; return count.

    Each source-data refresh produces a new exact DecisionInput cache under a
    new digest while the previous file remained on disk, growing storage by
    ~one full timeline per refresh. After a successful save, stale files whose
    sidecar reports the SAME namespace and config fingerprint are removed:
    they can no longer be referenced by the append checkpoint (which now points
    at the freshly saved cache). Different-config caches and legacy files
    without a sidecar are never touched, and checkpoint files are excluded by
    name pattern.
    """

    kept_path = persistent.path_for(identity)
    symbol_dir = kept_path.parent
    removed = 0
    try:
        candidates = sorted(
            symbol_dir.glob(f"{_safe_namespace_prefix(identity.namespace)}*{_EXACT_CACHE_SUFFIX}")
        )
    except OSError:
        return 0
    for candidate in candidates:
        if candidate == kept_path or "__checkpoint__" in candidate.name:
            continue
        sidecar = candidate.with_name(
            candidate.name[: -len(_EXACT_CACHE_SUFFIX)] + _IDENTITY_SIDECAR_SUFFIX
        )
        try:
            record = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # legacy or unreadable: never delete unknown caches
        if record.get("namespace") != identity.namespace:
            continue
        if record.get("config_fingerprint") != identity.config_fingerprint:
            continue  # a different config product owns this cache
        try:
            candidate.unlink(missing_ok=True)
            sidecar.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


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
            _save_rebuildable_exact_cache(persistent, exact_identity, result)
            exact_saved = True
        except Exception:
            pass

        if exact_saved:
            try:
                evict_stale_exact_caches(persistent, exact_identity)
            except Exception:
                pass
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
    "evict_stale_exact_caches",
]
