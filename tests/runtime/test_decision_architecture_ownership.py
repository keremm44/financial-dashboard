from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = ROOT / "tools" / "architecture_audit.py"


def _audit_module():
    spec = importlib.util.spec_from_file_location("migration_architecture_audit", AUDIT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_repository_architecture_baseline_does_not_expand() -> None:
    audit = _audit_module()
    report = audit.build_report()
    baseline = audit.load_baseline()

    assert audit.baseline_errors(report, baseline) == ()
    assert report.shell_count <= len(baseline["reexport_shells"])
    assert report.gate_token_count <= baseline["max_gate_token_count"]
    assert report.legacy_policy_path_count <= baseline["max_legacy_policy_path_count"]
    assert report.trade_action_leaks == ()


def test_shell_classifier_catches_explicit_and_star_reexports(tmp_path) -> None:
    audit = _audit_module()

    explicit = tmp_path / "explicit.py"
    explicit.write_text("from example import thing\n__all__ = ['thing']\n", encoding="utf-8")
    star = tmp_path / "star.py"
    star.write_text("from example import *\n", encoding="utf-8")

    assert audit.classify_module(explicit) == "explicit"
    assert audit.classify_module(star) == "star"
