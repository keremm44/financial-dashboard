from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from financial_dashboard.decision.gate_authority import GateAuthority
from financial_dashboard.decision.gate_registry import (
    GATE_REGISTRY,
    GateSemantic,
    gate_definition,
    unregistered_gate_tokens,
)


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "tools" / "architecture_audit.py"


def _audit_module():
    spec = importlib.util.spec_from_file_location("gate_registry_architecture_audit", AUDIT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_source_blocker_and_wait_token_has_one_registry_owner():
    audit = _audit_module()
    tokens = audit.scan_gate_tokens()

    assert unregistered_gate_tokens(tokens) == ()
    for token in tokens:
        definition = gate_definition(token)
        assert definition is not None
        assert isinstance(definition.owner, GateAuthority)
        assert isinstance(definition.semantic, GateSemantic)
        assert definition.evidence_family


def test_registry_patterns_are_unique_and_templates_are_unambiguous():
    patterns = [item.token_pattern for item in GATE_REGISTRY]
    assert len(patterns) == len(set(patterns))

    examples = {
        "STRUCTURE_DATA_UNAVAILABLE": GateAuthority.STRUCTURE,
        "STRUCTURAL_THESIS_INVALIDATED": GateAuthority.STRUCTURE,
        "30m:SETUP_TRIGGER_DATA": GateAuthority.TIMING,
        "30m:EXECUTION_TRIGGER_DATA": GateAuthority.EXECUTION,
        "ACTION_SIDE_NOT_PERMITTED:SHORT": GateAuthority.ACTION_CAPABILITY,
        "30m:LONG_EXIT_EXECUTION_DATA": GateAuthority.EXECUTION,
        "CRITICAL_COVERAGE:STABIL": GateAuthority.COVERAGE,
    }
    for token, owner in examples.items():
        definition = gate_definition(token)
        assert definition is not None
        assert definition.owner is owner


def test_structural_transition_wait_has_one_source_owner():
    eligibility = (ROOT / "src" / "financial_dashboard" / "decision" / "eligibility.py").read_text(encoding="utf-8")
    scenario = (ROOT / "src" / "financial_dashboard" / "decision" / "scenario.py").read_text(encoding="utf-8")

    assert 'waiting.append("STRUCTURAL_TRANSITION_TO_RESOLVE")' in eligibility
    assert 'waiting.append("STRUCTURAL_TRANSITION_TO_RESOLVE")' not in scenario
    assert gate_definition("STRUCTURAL_TRANSITION_TO_RESOLVE").owner is GateAuthority.STRUCTURE


def test_known_final_action_and_exit_wait_contract_is_registered():
    tokens = (
        "SCENARIO_TO_QUALIFY",
        "FRESH_EXECUTION_EVENT",
        "NEW_EXECUTION_EVENT",
        "30m:EXECUTION_TRIGGER_DATA",
        "FRESH_LONG_EXIT_EXECUTION_EVENT",
        "NEW_LONG_EXIT_EXECUTION_EVENT",
        "30m:LONG_EXIT_EXECUTION_DATA",
        "POSITION_ENTRY_METADATA_TO_RECOVER",
        "LT_STRUCTURE_AUTHORITY_TO_RECOVER",
        "ST_STRUCTURE_AUTHORITY_TO_RECOVER",
        "CROSS_HORIZON_STRUCTURE_TO_RECONCILE",
    )

    assert unregistered_gate_tokens(tokens) == ()
