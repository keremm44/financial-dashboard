from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

from financial_dashboard.data.identity import normalize_symbol
from financial_dashboard.data.parquet_store import ParquetOHLCVStore

from .history_incremental import _zero_timings
from .history_replay import HistoricalDecisionInputReplayRunner
from .history_single_pass import SinglePassHistoricalDecisionInputReplay
from .history_source import HistoricalDecisionInputConfig
from .persistent_history_runner import (
    _EXACT_CACHE_SUFFIX,
    _IDENTITY_SIDECAR_SUFFIX,
)
from .persistent_state import (
    PersistentCacheIdentity,
    PersistentCheckpointRecord,
    PersistentObjectStore,
)
from .timeline_cache import (
    DecisionTimelineCacheMiss,
    FrozenDecisionTimelineLoad,
    load_frozen_decision_timeline,
)

_ALIGNMENT_ERROR = "native checkpoint delta is not aligned with the persisted decision prefix"
_BOOTSTRAP_NATIVE_VERSION_PREFIX = "-decision-bootstrap-full-v2-"
_BOOTSTRAP_SUPPORTING_VERSION_PREFIX = "-decision-bootstrap-full-v2-"

ProgressCallback = Callable[[str], None]
RunHook = Callable[[HistoricalDecisionInputReplayRunner, str, HistoricalDecisionInputConfig], Any]


@contextmanager
def cold_domain_checkpoint_scope():
    """Force one truly fresh canonical domain run without deleting production checkpoints.

    Bootstrap checkpoint identities are unique per invocation. A prior interrupted or
    completed bootstrap therefore can never be mistaken for the start of a new rebuild,
    which would otherwise return only a native delta while the DecisionInput prefix is
    empty. Production native/supporting checkpoint identities are restored afterwards.
    """

    import financial_dashboard.decision.history_incremental as incremental_history
    import financial_dashboard.decision.history_native_timeline as native_history

    native_version = native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION
    supporting_version = incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION
    nonce = uuid4().hex
    native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION = (
        native_version + _BOOTSTRAP_NATIVE_VERSION_PREFIX + nonce
    )
    incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION = (
        supporting_version + _BOOTSTRAP_SUPPORTING_VERSION_PREFIX + nonce
    )
    try:
        yield
    finally:
        native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION = native_version
        incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION = supporting_version


def _default_progress(message: str) -> None:
    return None


def decision_prefix_exists(
    store: ParquetOHLCVStore,
    *,
    symbol: str,
    config: HistoricalDecisionInputConfig,
    runner: HistoricalDecisionInputReplayRunner | None = None,
) -> bool:
    clean_symbol = normalize_symbol(symbol)
    effective_runner = runner or HistoricalDecisionInputReplayRunner(store)
    persistent = PersistentObjectStore(store.root)
    identity = effective_runner._decision_checkpoint_identity(symbol=clean_symbol, config=config)
    return persistent.load_checkpoint(identity) is not None


def seed_production_checkpoints(
    store: ParquetOHLCVStore,
    symbol: str,
    *,
    progress: ProgressCallback = _default_progress,
) -> int:
    """Copy cold-bootstrap checkpoints to their production identities.

    The cold bootstrap intentionally writes native/supporting checkpoints under
    one-shot nonce identities so an interrupted rebuild can never be mistaken
    for a resume point. The side effect was that production identities stayed
    empty, forcing the NEXT source refresh into another full cold native replay.
    After a successful cold build the nonce state is re-saved under production
    identities, so the next refresh resumes incrementally. Prefix fingerprints
    are source-row based and identity independent, so they remain valid.
    """

    persistent = PersistentObjectStore(store.root)
    clean_symbol = normalize_symbol(symbol)
    # Same-package private helpers keep this cheap without a public surface change.
    symbol_dir = persistent._symbol_directory(clean_symbol)
    seeded = 0
    try:
        candidates = sorted(symbol_dir.glob("*__checkpoint__*.pkl"))
    except OSError:
        return 0
    for path in candidates:
        envelope = PersistentObjectStore._load_envelope(path)
        if envelope is None:
            continue
        record = envelope.get("record")
        if not isinstance(record, PersistentCheckpointRecord):
            continue
        semantic = str(record.identity.semantic_fingerprint)
        marker = semantic.find(_BOOTSTRAP_NATIVE_VERSION_PREFIX)
        if marker < 0:
            continue
        production = replace(record.identity, semantic_fingerprint=semantic[:marker])
        try:
            persistent.save_checkpoint(replace(record, identity=production))
        except Exception:
            continue
        seeded += 1
    if seeded:
        progress(f"CHECKPOINTS_SEEDED\t{seeded}")
    return seeded


def _sidecar_digest_matches(
    persistent: PersistentObjectStore,
    identity: PersistentCacheIdentity,
) -> bool:
    """Cheap exact-cache verification without unpickling the payload.

    Reads the tiny ``.identity.json`` sidecar written next to the cache file and
    compares its digest. A missing or mismatched sidecar returns False and the
    caller falls back to a full reload.
    """

    cache_path = persistent.path_for(identity)
    if not cache_path.exists():
        return False
    sidecar = cache_path.with_name(
        cache_path.name[: -len(_EXACT_CACHE_SUFFIX)] + _IDENTITY_SIDECAR_SUFFIX
    )
    try:
        record = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return record.get("digest") == identity.digest


def build_timeline_once(
    store: ParquetOHLCVStore,
    *,
    symbol: str,
    config: HistoricalDecisionInputConfig,
    run_with: RunHook | None = None,
    progress: ProgressCallback = _default_progress,
):
    """Run canonical domains once and persist the exact DecisionInput read-model."""

    clean_symbol = normalize_symbol(symbol)
    runner = HistoricalDecisionInputReplayRunner(store)
    execute = run_with or (lambda r, s, c: r.replay(s, config=c))

    if not decision_prefix_exists(
        store,
        symbol=clean_symbol,
        config=config,
        runner=runner,
    ):
        progress("BUILD_MODE\tCANONICAL_COLD_DOMAIN_ONCE")
        with cold_domain_checkpoint_scope():
            built = execute(runner, clean_symbol, config)
        if store is not None:
            try:
                seed_production_checkpoints(store, clean_symbol, progress=progress)
            except Exception:
                pass
        return runner, built

    progress("BUILD_MODE\tCANONICAL_INCREMENTAL_OR_EXACT")
    try:
        built = execute(runner, clean_symbol, config)
    except RuntimeError as exc:
        if _ALIGNMENT_ERROR not in str(exc):
            raise
        progress("BUILD_RECOVERY\tCANONICAL_COLD_DOMAIN_ONCE")
        runner = HistoricalDecisionInputReplayRunner(store)
        with cold_domain_checkpoint_scope():
            built = execute(runner, clean_symbol, config)
        if store is not None:
            try:
                seed_production_checkpoints(store, clean_symbol, progress=progress)
            except Exception:
                pass
    return runner, built


@dataclass(frozen=True, slots=True)
class EnsuredDecisionTimeline:
    """Result of loading the frozen timeline, building it first when missing."""

    load: FrozenDecisionTimelineLoad
    built: bool
    load_seconds: float
    build_seconds: float
    runner: Any = None
    snapshots_built: int = 0


def ensure_frozen_decision_timeline(
    store: ParquetOHLCVStore,
    symbol: str,
    *,
    config: HistoricalDecisionInputConfig | None = None,
    run_with: RunHook | None = None,
    progress: ProgressCallback = _default_progress,
    verify_reload: bool = False,
) -> EnsuredDecisionTimeline:
    """Load the exact frozen DecisionInput timeline, building it on an explicit miss.

    Read-only consumers (BUY/SELL backtests, profilers) call this instead of
    :func:`load_frozen_decision_timeline` when a missing cache should be prepared
    in place instead of raising. The build path is the same canonical domain
    replay used by the explicit ``build_decision_timeline_cache`` preparation step.

    After a successful build the exact cache is verified through its tiny
    ``.identity.json`` sidecar (digest match) instead of unpickling the
    multi-hundred-MB payload again — the in-memory build result is reused
    directly. Pass ``verify_reload=True`` to force the original full reload.
    """

    cfg = config or HistoricalDecisionInputConfig()
    clean_symbol = normalize_symbol(symbol)

    started = perf_counter()
    try:
        load = load_frozen_decision_timeline(store, clean_symbol, config=cfg)
    except DecisionTimelineCacheMiss as exc:
        progress("CACHE_STATUS\tMISS_BUILDING")
        progress(f"CACHE_MISS_REASON\t{exc}")
        build_started = perf_counter()
        runner, built = build_timeline_once(
            store,
            symbol=clean_symbol,
            config=cfg,
            run_with=run_with,
            progress=progress,
        )
        build_seconds = perf_counter() - build_started
        progress(f"BUILD_SECONDS\t{build_seconds:.3f}")

        load_started = perf_counter()
        sidecar_ok = False
        if not verify_reload:
            try:
                identity = HistoricalDecisionInputReplayRunner(store)._cache_identity(
                    symbol=clean_symbol, config=cfg
                )
                sidecar_ok = _sidecar_digest_matches(
                    PersistentObjectStore(store.root), identity
                )
            except Exception:
                sidecar_ok = False
        if sidecar_ok:
            verify_seconds = perf_counter() - load_started
            progress("VERIFY_MODE\tSIDECAR_DIGEST")
            progress(f"VERIFY_SECONDS\t{verify_seconds:.3f}")
            load = FrozenDecisionTimelineLoad(
                replay=SinglePassHistoricalDecisionInputReplay(
                    symbol=built.symbol,
                    decision_timeframe=built.decision_timeframe,
                    cutoffs=built.cutoffs,
                    snapshots=built.snapshots,
                    timings=_zero_timings(),
                ),
                cache_status="HIT_BUILT_SIDECAR_VERIFIED",
            )
        else:
            progress("VERIFY_MODE\tFULL_RELOAD")
            try:
                load = load_frozen_decision_timeline(store, clean_symbol, config=cfg)
            except DecisionTimelineCacheMiss as exc:
                raise RuntimeError(
                    "DecisionInput timeline was computed but exact cache verification "
                    "failed; do not run BUY/SELL backtest yet."
                ) from exc
        load_seconds = perf_counter() - load_started
        progress(f"VERIFY_LOAD_SECONDS\t{load_seconds:.3f}")
        progress(f"VERIFY_STATUS\t{load.cache_status}")
        return EnsuredDecisionTimeline(
            load=load,
            built=True,
            load_seconds=load_seconds,
            build_seconds=build_seconds,
            runner=runner,
            snapshots_built=len(built.snapshots),
        )

    load_seconds = perf_counter() - started
    return EnsuredDecisionTimeline(
        load=load,
        built=False,
        load_seconds=load_seconds,
        build_seconds=0.0,
    )


__all__ = [
    "EnsuredDecisionTimeline",
    "build_timeline_once",
    "cold_domain_checkpoint_scope",
    "decision_prefix_exists",
    "ensure_frozen_decision_timeline",
    "seed_production_checkpoints",
]
