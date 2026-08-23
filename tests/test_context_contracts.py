from __future__ import annotations

import ast
import inspect
from dataclasses import fields
from pathlib import Path

from financial_dashboard.context import FactRef, LineageGroup, QualifiedZone
import financial_dashboard.context.envelope as envelope_module
import financial_dashboard.context.lineage as lineage_module
import financial_dashboard.context.projections as projections_module
import financial_dashboard.context.zones as zones_module
import financial_dashboard.context.zone_interaction as zone_interaction_module


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
    assert QualifiedZone.__dataclass_params__.frozen is True
    assert hasattr(FactRef, "__slots__")
    assert hasattr(LineageGroup, "__slots__")
    assert hasattr(QualifiedZone, "__slots__")


def test_fact_and_zone_contracts_contain_no_action_or_probability_authority() -> None:
    assert {field.name for field in fields(FactRef)}.isdisjoint(FORBIDDEN_ACTION_FIELDS)
    assert {field.name for field in fields(QualifiedZone)}.isdisjoint(FORBIDDEN_ACTION_FIELDS)


def test_envelope_has_no_financial_dashboard_runtime_dependency() -> None:
    assert all(not name.startswith("financial_dashboard") for name in _imports(envelope_module))


def test_lineage_does_not_import_targeting_or_replay_runtime() -> None:
    imports = _imports(lineage_module)
    assert all("targeting" not in name for name in imports)
    assert all("replay" not in name for name in imports)
    assert all("market_workspace" not in name for name in imports)


def test_projection_layer_does_not_import_native_engines_workspace_or_ui() -> None:
    imports = _imports(projections_module)
    assert all("engines" not in name for name in imports)
    assert all("market_workspace" not in name for name in imports)
    assert all(".ui" not in name and not name.endswith("ui") for name in imports)


def test_projection_layer_does_not_read_generic_engine_result_authority() -> None:
    source = inspect.getsource(projections_module)
    assert "EngineResult" not in source
    assert ".result.direction" not in source
    assert ".result.score" not in source
    assert ".result.quality" not in source
    assert ".result.is_confirmed" not in source


def test_zone_intelligence_does_not_import_native_engines_workspace_or_ui() -> None:
    for module in (zones_module, zone_interaction_module):
        imports = _imports(module)
        assert all("engines" not in name for name in imports)
        assert all("market_workspace" not in name for name in imports)
        assert all(".ui" not in name and not name.endswith("ui") for name in imports)


def test_zone_intelligence_has_no_weighted_vote_or_action_tokens() -> None:
    source = inspect.getsource(zones_module)
    assert "bullish_score" not in source
    assert "bearish_score" not in source
    assert "BUY" not in source
    assert "SELL" not in source


def test_context_foundation_does_not_use_random_or_uuid_identity() -> None:
    package_dir = Path(inspect.getfile(envelope_module)).parent
    for name in ("envelope.py", "lineage.py", "projections.py", "zones.py", "zone_interaction.py"):
        source = (package_dir / name).read_text(encoding="utf-8")
        assert "import uuid" not in source
        assert "from uuid" not in source
        assert "import random" not in source
        assert "from random" not in source
