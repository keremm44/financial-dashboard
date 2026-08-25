import ast
import inspect

import financial_dashboard.decision.conflict as conflict_module
import financial_dashboard.decision.durability as durability_module
import financial_dashboard.decision.environment as environment_module
import financial_dashboard.decision.opportunity as opportunity_module
import financial_dashboard.decision.participation as participation_module
import financial_dashboard.decision.reaction as reaction_module


MODULES = (
    durability_module,
    reaction_module,
    participation_module,
    environment_module,
    opportunity_module,
    conflict_module,
)


def test_supporting_modules_do_not_import_native_engines_or_action_layers():
    for module in MODULES:
        tree = ast.parse(inspect.getsource(module))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        lowered = tuple(name.lower() for name in imports)
        assert all("financial_dashboard.engines" not in name for name in lowered)
        assert all("market_workspace" not in name for name in lowered)
        assert all("ui" not in name for name in lowered)


def test_supporting_modules_contain_no_buy_sell_action_authority():
    for module in MODULES:
        source = inspect.getsource(module)
        for token in ("BUY", "SELL", "position_size", "stop_loss", "take_profit"):
            assert token not in source


def test_conflict_accepts_independent_assessments_not_context_or_permission():
    parameters = set(inspect.signature(conflict_module.assess_conflict).parameters)
    assert parameters == {"side", "reaction", "participation", "environment"}


def test_opportunity_has_no_embedded_default_calibration():
    signature = inspect.signature(opportunity_module.assess_opportunity)
    assert signature.parameters["calibration"].default is None
