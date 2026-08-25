from __future__ import annotations

import ast
import inspect

import financial_dashboard.decision.structural as structural_module


def _imports(module: object) -> tuple[str, ...]:
    tree = ast.parse(inspect.getsource(module))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return tuple(names)


def test_structural_decision_layer_only_imports_structure_read_models() -> None:
    imports = _imports(structural_module)
    forbidden = (
        "stabil",
        "support_resistance",
        "liquidity",
        "order_block",
        "fvg",
        "engulfing",
        "participation",
        "volume",
        "volatility",
        "pattern",
        "ham",
        "targeting",
        "permission",
        "zones",
        "axes",
        "engines",
        "market_workspace",
        "ui",
    )
    lowered = tuple(name.lower() for name in imports)
    assert all(all(token not in name for token in forbidden) for name in lowered)


def test_structural_decision_layer_contains_no_action_authority() -> None:
    source = inspect.getsource(structural_module)
    for token in ("BUY", "SELL", "stop_loss", "take_profit", "position_size"):
        assert token not in source


def test_structural_function_signatures_cannot_accept_supporting_domains() -> None:
    supporting_tokens = {
        "stabil",
        "liquidity",
        "order_block",
        "fvg",
        "engulfing",
        "participation",
        "volume",
        "volatility",
        "pattern",
        "ham",
        "targeting",
        "permission",
        "context",
    }
    for function in (
        structural_module.assess_long_term_structure,
        structural_module.assess_short_term_structure,
        structural_module.build_horizon_structural_snapshot,
    ):
        parameter_names = {name.lower() for name in inspect.signature(function).parameters}
        assert parameter_names.isdisjoint(supporting_tokens)
