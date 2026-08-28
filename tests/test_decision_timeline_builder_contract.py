from pathlib import Path


def _builder_source() -> str:
    return Path("scripts/build_decision_timeline_cache.py").read_text(encoding="utf-8")


def _buy_sell_source() -> str:
    return Path("scripts/buy_sell_backtest.py").read_text(encoding="utf-8")


def test_builder_uses_canonical_domains_and_never_legacy_replay() -> None:
    source = _builder_source()
    assert "HistoricalDecisionInputReplayRunner" in source
    assert "LegacyHistoricalDecisionInputReplayRunner" not in source
    assert "CANONICAL_COLD_DOMAIN_ONCE" in source
    assert "_cold_domain_checkpoint_scope" in source
    assert "runner.replay(" in source


def test_builder_does_not_delete_existing_domain_checkpoints() -> None:
    source = _builder_source()
    assert ".remove_checkpoint(" not in source
    assert ".remove(" not in source


def test_builder_restores_checkpoint_identity_versions_after_cold_run() -> None:
    source = _builder_source()
    assert "finally:" in source
    assert "native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION = native_version" in source
    assert (
        "incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION = supporting_version"
        in source
    )


def test_buy_sell_fast_path_remains_frozen_cache_only() -> None:
    source = _buy_sell_source()
    assert "load_frozen_decision_timeline(" in source
    assert "HistoricalDecisionInputReplayRunner" not in source
    assert "LegacyHistoricalDecisionInputReplayRunner" not in source
    assert "DOMAIN_REPLAY_AND_SNAPSHOT_SECONDS\\t0.00" in source
    assert "FROZEN_DECISION_TIMELINE_CACHE_MISS" in source
