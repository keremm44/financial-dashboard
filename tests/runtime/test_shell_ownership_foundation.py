from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INITIAL_DECISION_SHELL_COUNT = 15


def _ownership_module():
    path = ROOT / "tools" / "shell_ownership_audit.py"
    spec = importlib.util.spec_from_file_location("shell_ownership_audit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_decision_shell_cleanup_scope_tracks_only_live_shells_and_may_shrink():
    audit = _ownership_module()
    inventory = audit.build_inventory()
    scope = audit.load_scope()

    assert audit.scope_errors(inventory, scope) == ()
    assert scope["policy"] == "behavior_preserving"
    assert scope["public_import_contract"] == "preserve"

    scoped = set(scope["shells"])
    live_decision_shells = {
        row["path"]
        for row in inventory
        if row["path"].startswith(
            (
                "decision/buy/",
                "decision/sell/",
                "decision/shared/",
                "decision/trade_lifecycle/",
            )
        )
    }
    assert scoped == live_decision_shells
    assert len(scoped) <= INITIAL_DECISION_SHELL_COUNT

    rows = {row["path"]: row for row in inventory}
    for path in scoped:
        row = rows[path]
        assert row["targets"]
        assert all(target["path"] is not None for target in row["targets"])
        assert all(target["is_shell"] is False for target in row["targets"])
