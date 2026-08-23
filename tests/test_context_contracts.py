from __future__ import annotations

import ast
import inspect
from dataclasses import fields
from pathlib import Path

from financial_dashboard.context import (
    CrossDomainContextSnapshot,
    FactRef,
    LineageGroup,
    PermissionEnvelope,
    QualifiedZone,
)
import financial_dashboard.context.axes as axes_module
import financial_dashboard.context.envelope as envelope_module
import financial_dashboard.context.lineage as lineage_module
import financial_dashboard.context.permissions as permissions_module
import financial_dashboard.context.projections as projections_module
import financial_dashboard.context.snapshot as snapshot_module
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
    for contract in (
        FactRef,
        LineageGroup,
        QualifiedZone,
        CrossDomainContextSnapshot,
        PermissionEnvelope,
    ):
        assert contract.__dataclass_params__.frozen is True
        assert hasattr(contract, "__slots__")


def test_fact_zone_snapshot_and_permission_contracts_contain_no_action_or_probability_fields() -> None:
    for contract in (FactRef, QualifiedZone, CrossDomainContextSnapshot, PermissionEnvelope):
        assert {field.name for field in fields(contract)}.isdisjoint(FORBIDDEN_ACTION_FIELDS)


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


def test_context_axes_snapshot_and_permissions_do_not_import_native_engines_or_workspace() -> None:
    for module in (axes_module, snapshot_module, permissions_module):
        imports = _imports(module)
        assert all("engines" not in name for name in imports)
        assert all("market_workspace" not in name for name in imports)
        assert all("replay" not in name for name in imports)
        assert all(".ui" not in name and not name.endswith("ui") for name in imports)


def test_context_axes_have_no_weighted_vote_or_numeric_composite_score() -> None:
    source = inspect.getsource(axes_module)
    forbidden = (
        "bullish_score",
        "bearish_score",
        "weighted_score",
        "confirmation_count",
        "domain_count",
    )
    assert all(token not in source for token in forbidden)


def test_permission_resolver_has_no_native_engine_import_and_no_position_sizing_policy() -> None:
    source = inspect.getsource(permissions_module)
    assert "position_size" not in source
    assert "reduced_size" not in source.lower()
    assert all("engines" not in name for name in _imports(permissions_module))


def test_context_foundation_does_not_use_random_or_uuid_identity() -> None:
    package_dir = Path(inspect.getfile(envelope_module)).parent
    for name in (
        "envelope.py",
        "lineage.py",
        "projections.py",
        "zones.py",
        "zone_interaction.py",
        "axes.py",
        "snapshot.py",
        "permissions.py",
    ):
        source = (package_dir / name).read_text(encoding="utf-8")
        assert "import uuid" not in source
        assert "from uuid" not in source
        assert "import random" not in source
        assert "from random" not in source
