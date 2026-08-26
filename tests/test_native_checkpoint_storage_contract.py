from pathlib import Path

from financial_dashboard.decision.persistent_state import (
    PersistentCacheIdentity,
    PersistentCheckpointIdentity,
)


def test_native_checkpoint_does_not_embed_full_historical_replay():
    source = Path("src/financial_dashboard/decision/history_native_timeline.py").read_text(
        encoding="utf-8"
    )

    payload_start = source.index("class _NativeTimelineCheckpointPayload")
    payload_end = source.index("\ndef _config_fingerprint", payload_start)
    payload_block = source[payload_start:payload_end]

    assert "runtime: NativeRuntimeCheckpoint" in payload_block
    assert "cutoffs:" in payload_block
    assert "full_state:" in payload_block
    assert "fingerprint:" in payload_block
    assert "replay:" not in payload_block
    assert "native-causal-runtime-checkpoint-v2" in source


def test_decision_assembly_consumes_only_new_native_points_after_restore():
    source = Path("src/financial_dashboard/decision/history_incremental.py").read_text(
        encoding="utf-8"
    )

    assert "native.state_store_start_position == start_position" in source
    assert "points_to_assemble = native.state_store.domains" in source
    assert "native checkpoint delta is not aligned" in source


def test_decision_append_checkpoint_keeps_only_a_reference_to_frozen_timeline():
    source = Path("src/financial_dashboard/decision/persistent_history_runner.py").read_text(
        encoding="utf-8"
    )

    assert "class DecisionTimelineReference" in source
    assert "payload=DecisionTimelineReference(exact_identity=exact_identity)" in source
    assert "payload=result" not in source
    assert "decision-input-append-reference-v2" in source


def test_large_exact_decision_cache_is_atomic_but_not_fsynced_twice():
    source = Path("src/financial_dashboard/decision/persistent_history_runner.py").read_text(
        encoding="utf-8"
    )

    helper_start = source.index("def _save_rebuildable_exact_cache")
    helper_end = source.index("\n\nclass PersistentHistoricalDecisionInputReplayRunner", helper_start)
    helper = source[helper_start:helper_end]

    assert "temporary.replace(path)" in helper
    assert "handle.flush()" in helper
    assert "os.fsync" not in helper


def test_canonical_history_runner_uses_compact_persistent_runner():
    source = Path("src/financial_dashboard/decision/history_replay.py").read_text(
        encoding="utf-8"
    )

    assert "PersistentHistoricalDecisionInputReplayRunner" in source
    assert "class HistoricalDecisionInputReplayRunner(PersistentHistoricalDecisionInputReplayRunner)" in source


def test_persistent_identity_accepts_scoped_implementation_fingerprint():
    exact = PersistentCacheIdentity(
        namespace="decision_input_timeline",
        symbol="ASELS",
        semantic_fingerprint="decision-v1",
        config_fingerprint="default",
        source_fingerprint=(("30m", 100, 123),),
        implementation_fingerprint="impl-v1",
    )
    checkpoint = PersistentCheckpointIdentity(
        namespace="native_timeline",
        symbol="ASELS",
        semantic_fingerprint="native-v1",
        config_fingerprint="default",
        implementation_fingerprint="impl-v1",
    )

    assert exact.implementation_fingerprint == "impl-v1"
    assert checkpoint.implementation_fingerprint == "impl-v1"
    assert exact.digest
    assert checkpoint.digest
