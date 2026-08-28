from __future__ import annotations

import runpy
from pathlib import Path

from financial_dashboard.engines.market_structure_engine import MarketStructureEngine


def _builder_module():
    return runpy.run_path(str(Path("scripts/build_decision_timeline_cache.py")))


def test_live_domain_timing_scope_is_builder_local_and_restores_engine_methods(capsys):
    module = _builder_module()
    scope = module["_live_domain_timing_scope"]
    original = MarketStructureEngine.update

    with scope():
        assert MarketStructureEngine.update is not original

    assert MarketStructureEngine.update is original
    assert "DOMAIN_TIMING\tLIVE" in capsys.readouterr().out


def test_builder_exposes_live_start_done_timeframe_domain_contract():
    source = Path("scripts/build_decision_timeline_cache.py").read_text(encoding="utf-8")
    assert "DOMAIN_START\\t" in source
    assert "DOMAIN_DONE\\t" in source
    assert "meta['timeframe']" in source
    assert "meta['domain']" in source
    assert "meta['seconds']" in source
    assert "flush=True" in source
    assert "LegacyHistoricalDecisionInputReplayRunner" not in source


def test_cold_bootstrap_uses_fresh_checkpoint_identity_and_restores_versions():
    import financial_dashboard.decision.history_incremental as incremental_history
    import financial_dashboard.decision.history_native_timeline as native_history
    from financial_dashboard.decision.timeline_build import cold_domain_checkpoint_scope as scope

    native_original = native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION
    supporting_original = incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION

    with scope():
        native_first = native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION
        supporting_first = incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION
        assert native_first != native_original
        assert supporting_first != supporting_original

    assert native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION == native_original
    assert incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION == supporting_original

    with scope():
        native_second = native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION
        supporting_second = incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION

    assert native_second != native_first
    assert supporting_second != supporting_first
    assert native_history._NATIVE_PERSISTENCE_SEMANTIC_VERSION == native_original
    assert incremental_history._SUPPORTING_PERSISTENCE_SEMANTIC_VERSION == supporting_original
