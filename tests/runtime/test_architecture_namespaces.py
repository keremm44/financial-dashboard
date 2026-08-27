from __future__ import annotations

import importlib


def test_runtime_replay_workspace_and_decision_namespaces_import() -> None:
    modules = (
        "financial_dashboard.runtime.native.domain_runtime",
        "financial_dashboard.runtime.supporting.replay_runtime",
        "financial_dashboard.runtime.timeline.causal_reducer",
        "financial_dashboard.runtime.timeline.state_timeline",
        "financial_dashboard.runtime.timeline.historical_stream",
        "financial_dashboard.runtime.timeline.native_timeline",
        "financial_dashboard.runtime.timeline.incremental_history",
        "financial_dashboard.runtime.persistence.state",
        "financial_dashboard.runtime.contracts",
        "financial_dashboard.replay.cross_domain",
        "financial_dashboard.replay.structure",
        "financial_dashboard.replay.ham",
        "financial_dashboard.replay.volume",
        "financial_dashboard.replay.volatility",
        "financial_dashboard.replay.targeting",
        "financial_dashboard.replay.stabil",
        "financial_dashboard.replay.three_domain",
        "financial_dashboard.replay.market_structure",
        "financial_dashboard.workspace.market",
        "financial_dashboard.decision.buy",
        "financial_dashboard.decision.sell",
        "financial_dashboard.decision.shared.composer",
        "financial_dashboard.decision.trade_lifecycle",
        "financial_dashboard.decision.models",
    )
    for module in modules:
        assert importlib.import_module(module) is not None
