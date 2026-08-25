from pathlib import Path


def test_single_pass_history_does_not_call_legacy_per_domain_capture_builders():
    source = Path("src/financial_dashboard/decision/history_single_pass.py").read_text(encoding="utf-8")

    assert "_build_structure_pattern_captures" not in source
    assert "_build_liquidity_captures" not in source
    assert "_build_order_block_captures" not in source
    assert "_build_fvg_captures" not in source
    assert "def _single_native_capture_pass(" in source


def test_backtest_routes_through_single_pass_history_runner():
    source = Path("scripts/decision_backtest.py").read_text(encoding="utf-8")

    assert "SinglePassHistoricalDecisionInputReplayRunner" in source
    assert "= HistoricalDecisionInputReplayRunner(" not in source
    assert "HistoricalDecisionInputReplayRunner," not in source
    assert "NATIVE_REPLAY_SECONDS" in source
    assert "SNAPSHOT_ASSEMBLY_SECONDS" in source


def test_max_bars_is_only_a_decision_point_filter_in_single_pass_runner():
    source = Path("src/financial_dashboard/decision/history_single_pass.py").read_text(encoding="utf-8")

    selection = 'if cfg.max_bars is not None:\n            decision_frame = decision_frame.tail(cfg.max_bars)'
    assert selection in source
    assert "load_analysis_inputs(" in source
    # Native replay consumes the prepared full timeframe frames; max_bars does not
    # truncate AnalysisInputSnapshot or create a per-cutoff engine replay.
    assert "frame = batch.frame" in source
    assert "for index, row in enumerate(frame.to_dict(\"records\"))" in source
