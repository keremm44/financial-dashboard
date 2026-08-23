from __future__ import annotations

import ast
import inspect
from dataclasses import fields
from pathlib import Path

from financial_dashboard.context import FactRef, LineageGroup
import financial_dashboard.context.envelope as envelope_module
import financial_dashboard.context.lineage as lineage_module


FORBIDDEN_ACTION_FIELDS = {
    "buy",
    "sell",
    "entry",
    "exit",
    "stop",
    "stop_loss",
    "take_profit",
    "target",
    "position_size",
    "probability",
    "confidence",
}


def _imports(module: object) -> tuple[str, ...]:
    source = inspect.getsource(module)
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    return tuple(names)


def test_foundation_contracts_are_frozen_and_slotted() -> None:
    assert FactRef.__dataclass_params__.frozen is True
    assert LineageGroup.__dataclass_params__.frozen is True
    assert hasattr(FactRef, "__slots__")
    assert hasattr(LineageGroup, "__slots__")


def test_fact_ref_contains_no_action_or_probability_authority() -> None:
    names = {field.name for field in fields(FactRef)}
    assert names.isdisjoint(FORBIDDEN_ACTION_FIELDS)


def test_envelope_has_no_financial_dashboard_runtime_dependency() -> None:
    assert all(not name.startswith("financial_dashboard") for name in _imports(envelope_module))


def test_lineage_does_not_import_targeting_or_replay_runtime() -> None:
    imports = _imports(lineage_module)
    assert all("targeting" not in name for name in imports)
    assert all("replay" not in name for name in imports)
    assert all("market_workspace" not in name for name in imports)


def test_context_foundation_does_not_use_random_or_uuid_identity() -> None:
    package_dir = Path(inspect.getfile(envelope_module)).parent
    for name in ("envelope.py", "lineage.py"):
        source = (package_dir / name).read_text(encoding="utf-8")
        assert "import uuid" not in source
        assert "from uuid" not in source
        assert "import random" not in source
        assert "from random" not in source
