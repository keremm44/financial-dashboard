import ast
import inspect

import financial_dashboard.decision.composer as composer_module
import financial_dashboard.decision.eligibility as eligibility_module
import financial_dashboard.decision.engine as engine_module
import financial_dashboard.decision.execution as execution_module
import financial_dashboard.decision.timing as timing_module


MODULES = (
    timing_module,
    execution_module,
    eligibility_module,
    composer_module,
    engine_module,
)


def test_tur4_modules_do_not_import_native_engines_ui_or_workspace():
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


def test_execution_assessment_requires_explicit_event_input_instead_of_domain_guessing():
    parameters = set(inspect.signature(execution_module.assess_execution_trigger).parameters)
    assert parameters == {"side", "as_of", "timeframe", "data_quality", "event"}


def test_final_composer_consumes_frozen_eligibility_not_raw_domain_votes():
    parameters = set(inspect.signature(composer_module.compose_final_decision).parameters)
    assert parameters == {
        "structural",
        "eligibility",
        "execution",
        "policy",
        "additional_lineage",
    }


def test_eligibility_has_small_typed_input_surface_without_raw_domain_projections():
    parameters = set(inspect.signature(eligibility_module.assess_eligibility).parameters)
    # 'reaction' (T4 tolerance): typed ReactionAssessment only — eligibility reads
    # its confirmation flag for the at-primary-zone room discount. Still a small,
    # fully typed decision-assessment surface; no raw domain projections.
    assert parameters == {
        "structural",
        "permission",
        "timing",
        "opportunity",
        "conflict",
        "environment",
        "coverage",
        "reaction",
    }


def test_engine_has_no_hidden_opportunity_calibration_default_value():
    config = engine_module.DecisionEngineConfig()
    assert config.opportunity_calibration is None
