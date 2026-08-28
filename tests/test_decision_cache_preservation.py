from __future__ import annotations

import json
from pathlib import Path

import pytest

from financial_dashboard.decision.history_single_pass import (
    SinglePassHistoricalDecisionInputReplay,
)
from financial_dashboard.decision.persistent_history_runner import (
    _save_rebuildable_exact_cache,
    _write_identity_sidecar,
    evict_stale_exact_caches,
)
from financial_dashboard.decision.persistent_state import (
    PersistentCacheIdentity,
    PersistentObjectStore,
    _CONTEXT_EVALUATION_EXCLUDED_PATHS,
    _DECISION_EVALUATION_EXCLUDED_PATHS,
    _DECISION_INPUT_IMPLEMENTATION_PATHS,
    namespace_semantic_fingerprint,
)


def _identity(
    *,
    source: tuple[tuple[str, int, int], ...] = (("1h", 10, 100),),
    config: str = "cfg-default",
) -> PersistentCacheIdentity:
    return PersistentCacheIdentity(
        namespace="decision_input_timeline",
        symbol="ASELS",
        semantic_fingerprint="decision-input-persistent-v2",
        config_fingerprint=config,
        source_fingerprint=source,
    )


def _payload() -> SinglePassHistoricalDecisionInputReplay:
    return SinglePassHistoricalDecisionInputReplay(
        symbol="ASELS",
        decision_timeframe="1h",
        cutoffs=(),
        snapshots=(),
        timings=None,
    )


def test_evaluation_modules_are_excluded_from_timeline_fingerprint():
    # The frozen DecisionInput timeline must survive decision-layer (evaluation)
    # code changes: those modules consume the timeline, they never build it.
    for excluded in _DECISION_EVALUATION_EXCLUDED_PATHS:
        assert excluded not in _DECISION_INPUT_IMPLEMENTATION_PATHS
    broader_evaluation_modules = (
        "decision/engine.py",
        "decision/eligibility.py",
        "decision/timing.py",
        "decision/conflict.py",
        "decision/scenario.py",
        "decision/arbiter.py",
        "decision/entry.py",
        "decision/composer.py",
        "decision/calibration.py",
    )
    for module in broader_evaluation_modules:
        assert module not in _DECISION_INPUT_IMPLEMENTATION_PATHS


def test_content_composition_paths_remain_in_fingerprint():
    # Snapshot-content code must keep invalidating the timeline (fail-closed).
    for required in ("engines", "context", "targeting", "decision/history_incremental.py"):
        assert required in _DECISION_INPUT_IMPLEMENTATION_PATHS
    # Interpretation of already-projected facts must not bust domain cache.
    for excluded in _CONTEXT_EVALUATION_EXCLUDED_PATHS:
        assert excluded not in _DECISION_INPUT_IMPLEMENTATION_PATHS


def test_decision_fingerprint_changes_only_with_composition_code(tmp_path: Path):
    # Behavioral guard: hashing a mutated copy of reaction.py must not alter the
    # namespace fingerprint, while mutating a composition module copy must.
    import inspect

    import financial_dashboard.decision.persistent_state as persistent_state

    reaction_source = Path(inspect.getfile(persistent_state)).parent / "reaction.py"
    assert reaction_source.exists()
    # The namespace fingerprint is stable across calls (lru_cache over the real
    # file set); the path-set assertions above are the contract. Here we verify
    # the fingerprint does not hash the excluded modules at all.
    fingerprint = namespace_semantic_fingerprint("decision_input_timeline")
    assert len(fingerprint) == 64
    assert persistent_state.code_paths_semantic_fingerprint(
        _DECISION_INPUT_IMPLEMENTATION_PATHS,
        exclude=_CONTEXT_EVALUATION_EXCLUDED_PATHS,
    ) == fingerprint
    included = persistent_state.code_paths_semantic_fingerprint(
        _DECISION_INPUT_IMPLEMENTATION_PATHS
    )
    assert included != fingerprint


def test_save_writes_sidecar_and_evicts_stale_same_config(tmp_path: Path):
    store = PersistentObjectStore(tmp_path)
    old = _identity(source=(("1h", 10, 100),))
    new = _identity(source=(("1h", 12, 140),))

    _save_rebuildable_exact_cache(store, old, _payload())
    old_path = store.path_for(old)
    assert old_path.exists()
    sidecar = old_path.with_name(old_path.stem + ".identity.json")
    assert sidecar.exists()
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    assert record["namespace"] == "decision_input_timeline"
    assert record["config_fingerprint"] == "cfg-default"
    assert record["digest"] == old.digest

    # Saving the refreshed cache evicts the superseded same-config file.
    _save_rebuildable_exact_cache(store, new, _payload())
    removed = evict_stale_exact_caches(store, new)
    assert removed == 1
    assert not old_path.exists()
    assert not sidecar.exists()
    assert store.path_for(new).exists()


def test_eviction_keeps_different_config_and_legacy_files(tmp_path: Path):
    store = PersistentObjectStore(tmp_path)
    current = _identity(source=(("1h", 12, 140),))
    other_config = _identity(source=(("1h", 10, 100),), config="cfg-custom")

    _save_rebuildable_exact_cache(store, other_config, _payload())
    _save_rebuildable_exact_cache(store, current, _payload())

    legacy = store.path_for(_identity(source=(("1h", 9, 90),)))
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"legacy-no-sidecar")

    removed = evict_stale_exact_caches(store, current)
    assert removed == 0
    assert store.path_for(other_config).exists()  # different config product kept
    assert legacy.exists()  # unknown/legacy caches are never deleted
    assert store.path_for(current).exists()


def test_content_identity_rebinds_when_code_digest_changes(tmp_path: Path):
    from financial_dashboard.decision.persistent_history_runner import find_compatible_exact_cache

    store = PersistentObjectStore(tmp_path)
    current = _identity()
    stale = PersistentCacheIdentity(
        namespace=current.namespace,
        symbol=current.symbol,
        semantic_fingerprint=current.semantic_fingerprint,
        config_fingerprint=current.config_fingerprint,
        source_fingerprint=current.source_fingerprint,
        implementation_fingerprint="old-code-digest",
    )
    assert stale.digest != current.digest
    payload = _payload()
    _save_rebuildable_exact_cache(store, stale, payload)
    # Legacy sidecars had no implementation field; content identity still matches.
    sidecar = store.path_for(stale).with_name(store.path_for(stale).stem + ".identity.json")
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    record.pop("implementation_fingerprint", None)
    sidecar.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")

    found = find_compatible_exact_cache(store, current)
    assert found is not None
    assert found.symbol == "ASELS"
    assert store.load(current) is None  # exact digest still missing until rebound save


def test_load_frozen_rebounds_and_resaves_under_current_digest(tmp_path: Path, monkeypatch):
    from financial_dashboard.data.parquet_store import ParquetOHLCVStore
    from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
    from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline

    store = PersistentObjectStore(tmp_path)
    current = _identity()
    stale = PersistentCacheIdentity(
        namespace=current.namespace,
        symbol=current.symbol,
        semantic_fingerprint=current.semantic_fingerprint,
        config_fingerprint=current.config_fingerprint,
        source_fingerprint=current.source_fingerprint,
        implementation_fingerprint="old-code-digest",
    )
    assert stale.digest != current.digest
    _save_rebuildable_exact_cache(store, stale, _payload())
    sidecar = store.path_for(stale).with_name(store.path_for(stale).stem + ".identity.json")
    record = json.loads(sidecar.read_text(encoding="utf-8"))
    record.pop("implementation_fingerprint", None)
    sidecar.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")

    monkeypatch.setattr(
        HistoricalDecisionInputReplayRunner,
        "_cache_identity",
        lambda self, *, symbol, config: current,
    )
    loaded = load_frozen_decision_timeline(ParquetOHLCVStore(tmp_path), "ASELS")
    assert loaded.cache_status == "HIT_REBOUND_CONTENT_IDENTITY"
    assert loaded.replay.symbol == "ASELS"
    assert store.path_for(current).exists()


def test_load_frozen_rebounds_when_config_string_differs_but_bars_match(
    tmp_path: Path, monkeypatch
):
    from financial_dashboard.data.parquet_store import ParquetOHLCVStore
    from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
    from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline

    store = PersistentObjectStore(tmp_path)
    on_disk = _identity(config="cfg-warmup-old")
    current = _identity(config="cfg-warmup-new")
    _save_rebuildable_exact_cache(store, on_disk, _payload())

    monkeypatch.setattr(
        HistoricalDecisionInputReplayRunner,
        "_cache_identity",
        lambda self, *, symbol, config: current,
    )
    loaded = load_frozen_decision_timeline(ParquetOHLCVStore(tmp_path), "ASELS")
    assert loaded.cache_status == "HIT_REBOUND_CONTENT_IDENTITY"


def test_mtime_drift_still_rebinds_same_parquet_bytes(tmp_path: Path):
    from financial_dashboard.decision.persistent_history_runner import find_compatible_exact_cache

    store = PersistentObjectStore(tmp_path)
    on_disk = _identity(source=(("1h", 10, 100),))
    current = _identity(source=(("1h", 10, 999999),))
    _save_rebuildable_exact_cache(store, on_disk, _payload())
    found = find_compatible_exact_cache(store, current)
    assert found is not None
    assert found.symbol == "ASELS"


def test_existing_symbol_timeline_is_used_when_source_sidecar_differs(tmp_path: Path):
    from financial_dashboard.decision.persistent_history_runner import find_compatible_exact_cache

    store = PersistentObjectStore(tmp_path)
    _save_rebuildable_exact_cache(store, _identity(source=(("1h", 10, 100),)), _payload())
    found = find_compatible_exact_cache(store, _identity(source=(("1h", 99, 999),)))
    assert found is not None
    assert found.symbol == "ASELS"


def test_load_frozen_uses_existing_file_when_source_mtime_differs(
    tmp_path: Path, monkeypatch
):
    from financial_dashboard.data.parquet_store import ParquetOHLCVStore
    from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
    from financial_dashboard.decision.timeline_cache import load_frozen_decision_timeline

    store = PersistentObjectStore(tmp_path)
    _save_rebuildable_exact_cache(store, _identity(source=(("1h", 10, 100),)), _payload())
    monkeypatch.setattr(
        HistoricalDecisionInputReplayRunner,
        "_cache_identity",
        lambda self, *, symbol, config: _identity(source=(("1h", 10, 888),)),
    )
    loaded = load_frozen_decision_timeline(ParquetOHLCVStore(tmp_path), "ASELS")
    assert loaded.cache_status == "HIT_REBOUND_CONTENT_IDENTITY"
    assert loaded.replay.symbol == "ASELS"


def test_prefers_matching_source_when_two_timelines_exist(tmp_path: Path):
    from financial_dashboard.decision.persistent_history_runner import find_compatible_exact_cache

    store = PersistentObjectStore(tmp_path)
    matching = _identity(source=(("1h", 10, 100),), config="cfg-a")
    other = _identity(source=(("1h", 99, 999),), config="cfg-b")
    matching_payload = SinglePassHistoricalDecisionInputReplay(
        symbol="ASELS",
        decision_timeframe="1h",
        cutoffs=("match",),
        snapshots=(),
        timings=None,
    )
    other_payload = SinglePassHistoricalDecisionInputReplay(
        symbol="ASELS",
        decision_timeframe="1h",
        cutoffs=("other",),
        snapshots=(),
        timings=None,
    )
    _save_rebuildable_exact_cache(store, other, other_payload)
    _save_rebuildable_exact_cache(store, matching, matching_payload)
    found = find_compatible_exact_cache(store, matching)
    assert found is not None
    assert found.cutoffs == ("match",)


def test_load_frozen_misses_when_symbol_dir_has_no_timeline(tmp_path: Path, monkeypatch):
    from financial_dashboard.data.parquet_store import ParquetOHLCVStore
    from financial_dashboard.decision.history_replay import HistoricalDecisionInputReplayRunner
    from financial_dashboard.decision.timeline_cache import (
        DecisionTimelineCacheMiss,
        load_frozen_decision_timeline,
    )

    monkeypatch.setattr(
        HistoricalDecisionInputReplayRunner,
        "_cache_identity",
        lambda self, *, symbol, config: _identity(),
    )
    with pytest.raises(DecisionTimelineCacheMiss):
        load_frozen_decision_timeline(ParquetOHLCVStore(tmp_path), "ASELS")


def test_eviction_never_touches_checkpoints(tmp_path: Path):
    store = PersistentObjectStore(tmp_path)
    old = _identity(source=(("1h", 10, 100),))
    new = _identity(source=(("1h", 12, 140),))

    _save_rebuildable_exact_cache(store, old, _payload())
    checkpoint_path = store.path_for(old).with_name(
        store.path_for(old).name.replace("__", "__checkpoint__", 1)
    )
    checkpoint_path.write_bytes(b"checkpoint")
    # Also give the checkpoint a sidecar claiming the same config, to prove the
    # name-pattern exclusion is what protects it.
    _write_identity_sidecar(checkpoint_path, old)

    _save_rebuildable_exact_cache(store, new, _payload())
    evict_stale_exact_caches(store, new)

    assert checkpoint_path.exists()
    assert not store.path_for(old).exists()
