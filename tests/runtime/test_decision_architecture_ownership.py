from __future__ import annotations

import ast
from pathlib import Path


DECISION_ROOT = Path(__file__).resolve().parents[2] / "src" / "financial_dashboard" / "decision"
OWNERSHIP_NAMESPACES = ("buy", "sell", "shared", "trade_lifecycle")

# Baseline observed on arena/buy-sell-engine@34ffcfeb. This is intentionally an
# upper bound, not an allow-forever list: implementations/removals may reduce it,
# while a newly introduced re-export shell must fail the architecture guard.
BASELINE_REEXPORT_SHELLS = frozenset(
    {
        "buy/eligibility.py",
        "buy/engine.py",
        "buy/execution.py",
        "sell/exits.py",
        "shared/composer.py",
        "shared/conflict.py",
        "shared/coverage.py",
        "shared/durability.py",
        "shared/environment.py",
        "shared/opportunity.py",
        "shared/participation.py",
        "shared/reaction.py",
        "shared/structural.py",
        "shared/timing.py",
        "trade_lifecycle/state.py",
    }
)


def _is_star_reexport_shell(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            body = body[1:]

    body = [
        node
        for node in body
        if not (
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
        )
    ]
    if not body:
        return False
    return all(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "*" for alias in node.names)
        for node in body
    )


def _observed_reexport_shells() -> frozenset[str]:
    observed: set[str] = set()
    for namespace in OWNERSHIP_NAMESPACES:
        for path in sorted((DECISION_ROOT / namespace).glob("*.py")):
            if path.name == "__init__.py":
                continue
            if _is_star_reexport_shell(path):
                observed.add(path.relative_to(DECISION_ROOT).as_posix())
    return frozenset(observed)


def test_decision_ownership_reexport_shells_do_not_expand() -> None:
    observed = _observed_reexport_shells()
    new_shells = observed - BASELINE_REEXPORT_SHELLS

    assert not new_shells, (
        "new unapproved decision re-export shells detected: "
        + ", ".join(sorted(new_shells))
    )
    assert len(observed) <= len(BASELINE_REEXPORT_SHELLS)


def test_decision_ownership_shell_baseline_is_scoped() -> None:
    # Baseline entries may disappear as shells are implemented or removed. Only the
    # ownership namespace itself is fixed here so cleanup can monotonically reduce
    # shell_count without weakening the guard for newly introduced shells.
    assert all(
        relative.split("/", 1)[0] in OWNERSHIP_NAMESPACES
        for relative in BASELINE_REEXPORT_SHELLS
    )
