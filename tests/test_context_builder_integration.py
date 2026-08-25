from __future__ import annotations

import financial_dashboard.market_workspace as workspace_module
from financial_dashboard.analysis_config import ANALYSIS_TIMEFRAMES
from financial_dashboard.decision_input import build_decision_input_snapshot
from financial_dashboard.market_workspace import MarketAnalysisWorkspaceRunner, WorkspaceDomainStatus
from financial_dashboard.volatility_mtf_replay import VOLATILITY_TIMEFRAMES
from financial_dashboard.ham_mtf_replay import HAM_EVIDENCE_TIMEFRAMES
from _ui_test_data import make_ui_store


def test_workspace_exposes_cross_domain_shadow_result_without_action_authority(tmp_path) -> None:
    store = make_ui_store(tmp_path)
    workspace = MarketAnalysisWorkspaceRunner(store).run(symbol="THYAO", timeframes=ANALYSIS_TIMEFRAMES)

    assert workspace.cross_domain.status is WorkspaceDomainStatus.READY
    result = workspace.cross_domain_result
    assert result is not None
    assert workspace.targeting_result is not None
    assert result.context.as_of == workspace.targeting_result.as_of
    assert result.context.symbol == workspace.symbol
    assert result.context.anchor_timeframe == "4h"
    assert all(ref.available_at <= result.context.as_of for ref in result.context.source_refs)
    assert result.permission.is_actionable_signal is False

    # Target-bounded native/read-model inputs are exposed for the later decision
    # layer without changing context semantics or introducing action authority.
    assert result.structural is not None
    assert result.support_resistance is not None
    assert result.liquidity is not None
    assert result.liquidity_landscape is not None
    assert result.reaction is not None
    assert result.order_block_behavior is not None
    assert result.zones is not None

    # Rich behavior projections remain additive read models. They participate in the
    # same knowledge boundary but do not replace the established context axes.
    assert result.fvg_engulfing_lifecycle is not None
    assert result.participation_behavior is not None
    assert result.volatility_environment is not None
    assert result.pattern_behavior is not None
    source_ids = {ref.native_id for ref in result.context.source_refs}
    enriched_refs = (
        *result.support_resistance.refs,
        *result.liquidity_landscape.refs,
        *result.order_block_behavior.refs,
        *result.fvg_engulfing_lifecycle.refs,
        *result.participation_behavior.refs,
        *result.volatility_environment.refs,
        *result.pattern_behavior.refs,
    )
    assert all(ref.available_at <= result.context.as_of for ref in enriched_refs)
    assert all(ref.native_id in source_ids for ref in enriched_refs)

    decision_input = build_decision_input_snapshot(workspace)
    assert decision_input.symbol == workspace.symbol
    assert decision_input.as_of == result.context.as_of
    assert decision_input.structure is result.structural
    assert decision_input.support_resistance is result.support_resistance
    assert decision_input.liquidity_landscape is result.liquidity_landscape
    assert decision_input.order_block_behavior is result.order_block_behavior
    assert decision_input.permission.is_actionable_signal is False
    assert decision_input.derived_summaries == ("CONTEXT", "PERMISSION")
    assert all(ref.available_at <= decision_input.as_of for ref in decision_input.source_refs)


def test_optional_ham_failure_is_reported_but_does_not_break_shadow_context(tmp_path, monkeypatch) -> None:
    store = make_ui_store(tmp_path)

    def fail_ham(self, symbol, *, timeframes=HAM_EVIDENCE_TIMEFRAMES, input_snapshot=None):
        raise RuntimeError("synthetic Ham failure")

    monkeypatch.setattr(workspace_module.HamMTFEvidenceReplayRunner, "replay", fail_ham)
    workspace = MarketAnalysisWorkspaceRunner(store).run(symbol="THYAO", timeframes=ANALYSIS_TIMEFRAMES)

    assert workspace.ham.status is WorkspaceDomainStatus.ERROR
    assert workspace.cross_domain.status is WorkspaceDomainStatus.READY
    result = workspace.cross_domain_result
    assert result is not None
    assert "HAM_ERROR" in result.context.knowledge_boundary.unsupported_contexts


def test_optional_volatility_failure_is_reported_but_does_not_break_shadow_context(tmp_path, monkeypatch) -> None:
    store = make_ui_store(tmp_path)

    def fail_volatility(self, symbol, *, input_snapshot=None, timeframes=VOLATILITY_TIMEFRAMES, profile="Dengeli"):
        raise RuntimeError("synthetic volatility failure")

    monkeypatch.setattr(workspace_module.VolatilityMTFReplayRunner, "replay", fail_volatility)
    workspace = MarketAnalysisWorkspaceRunner(store).run(symbol="THYAO", timeframes=ANALYSIS_TIMEFRAMES)

    assert workspace.volatility.status is WorkspaceDomainStatus.ERROR
    assert workspace.cross_domain.status is WorkspaceDomainStatus.READY
    result = workspace.cross_domain_result
    assert result is not None
    assert "VOLATILITY_ERROR" in result.context.knowledge_boundary.unsupported_contexts
