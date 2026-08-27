from pathlib import Path


def test_entry_reason_profile_is_frozen_cache_only_and_counts_gate_reasons():
    source = Path("scripts/entry_reason_profile.py").read_text(encoding="utf-8")
    assert "load_frozen_decision_timeline(" in source
    assert "assess_entry_scenario(" in source
    assert "assess_entry_arbitration(" in source
    assert "assess_entry_decision(" in source
    assert "FROZEN_CACHE_FILE_MB" in source
    assert "FROZEN_TIMELINE_LOAD_SECONDS" in source
    assert "DOMAIN_REPLAY_SECONDS\\t0.000" in source
    assert "ENTRY REASONS" in source
    assert "ENTRY BLOCKERS" in source
    assert "ENTRY WAITING" in source
    assert "HistoricalDecisionInputReplayRunner(store).replay" not in source
    assert ".replay(" not in source


def test_entry_reason_profile_supports_both_horizons_and_arbiter_counts():
    source = Path("scripts/entry_reason_profile.py").read_text(encoding="utf-8")
    assert "DecisionHorizon.LONG_TERM" in source
    assert "DecisionHorizon.SHORT_TERM" in source
    assert "LT presence" in source
    assert "ST presence" in source
    assert "ARBITER STATE" in source
    assert "ARBITER SELECTION" in source
    assert "ENTRY ACTION" in source
