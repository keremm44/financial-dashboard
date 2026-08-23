from __future__ import annotations

import ast
import inspect

import financial_dashboard.context.builder as builder_module


def _imports(module: object) -> tuple[str, ...]:
    tree = ast.parse(inspect.getsource(module))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return tuple(names)


def test_builder_is_orchestration_only_and_does_not_import_workspace_or_native_engines() -> None:
    imports = _imports(builder_module)
    assert all("market_workspace" not in name for name in imports)
    assert all("engines" not in name for name in imports)
    assert all(".ui" not in name and not name.endswith("ui") for name in imports)


def test_builder_contains_no_action_layer_authority() -> None:
    source = inspect.getsource(builder_module)
    for token in ("BUY", "SELL", "stop_loss", "take_profit", "position_size"):
        assert token not in source
