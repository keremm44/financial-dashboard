from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src" / "financial_dashboard"
BASELINE_PATH = ROOT / "tools" / "architecture_audit_baseline.json"

FORBIDDEN_TRADE_ACTION_TOKENS = frozenset({"BUY", "SELL", "HOLD", "EXIT_WATCH"})
SYNTHESIS_ROOTS = ("domains", "engines", "context")
DECISION_COMPATIBILITY_NAMESPACES = ("buy", "sell", "shared", "trade_lifecycle")

# Positional fields that are part of the public blocker/wait contract. This keeps the
# audit source-based: adding a new literal blocker/wait to these dataclasses is
# visible even when the constructor uses positional arguments instead of keywords.
_GATE_POSITIONAL_FIELDS: dict[str, dict[str, int]] = {
    "PermissionEnvelope": {"blocking_reasons": 4, "waiting_for": 5},
    "EligibilityAssessment": {"blockers": 2, "waiting_for": 3},
    "TimingAssessment": {"waiting_for": 4},
    "EntryQualificationAssessment": {"target_path_waiting_for": 3},
    "EntryScenarioAssessment": {"presence_waiting_for": 12},
    "EntryScenarioArbitration": {"waiting_for": 8},
    "EntryDecision": {"blockers": 7, "waiting_for": 8},
    "FinalDecision": {"blockers": 7, "waiting_for": 8},
    "LongExitAssessment": {"waiting_for": 3},
    "LongExitExecutionAssessment": {"waiting_for": 2},
    "PositionExitDecision": {"waiting_for": 9},
}
_GATE_APPEND_COLLECTIONS = frozenset({
    "blockers",
    "waiting",
    "waiting_for",
    "target_waiting",
})
_GATE_KEYWORD_FIELDS = frozenset({
    "blockers",
    "blocking_reasons",
    "waiting_for",
    "presence_waiting_for",
    "target_path_waiting_for",
})


@dataclass(frozen=True, slots=True)
class ArchitectureAuditReport:
    reexport_shells: tuple[str, ...]
    empty_packages: tuple[str, ...]
    gate_tokens: tuple[str, ...]
    trade_action_leaks: tuple[str, ...]
    legacy_policy_paths: tuple[str, ...]

    @property
    def shell_count(self) -> int:
        return len(self.reexport_shells)

    @property
    def gate_token_count(self) -> int:
        return len(self.gate_tokens)

    @property
    def duplicate_gate_owner_count(self) -> int:
        # Gate-registry semantic validation is kept in a Decision-layer test so this
        # source audit never imports market policy while scanning architecture.
        return 0

    @property
    def legacy_policy_path_count(self) -> int:
        return len(self.legacy_policy_paths)

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload.update(
            shell_count=self.shell_count,
            gate_token_count=self.gate_token_count,
            duplicate_gate_owner_count=self.duplicate_gate_owner_count,
            legacy_policy_path_count=self.legacy_policy_path_count,
        )
        return payload


def _module_body(path: Path) -> list[ast.stmt]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            body = body[1:]
    return [
        node
        for node in body
        if not (
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
        )
    ]


def _is_dunder_all_assignment(node: ast.stmt) -> bool:
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        return False
    targets: list[ast.expr]
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    else:
        targets = [node.target]
    return any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets)


def classify_module(path: Path) -> str:
    """Return implementation, star, explicit, or empty for one Python module."""

    body = _module_body(path)
    if not body:
        return "empty"
    meaningful = [node for node in body if not _is_dunder_all_assignment(node)]
    if not meaningful:
        return "empty"
    if all(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "*" for alias in node.names)
        for node in meaningful
    ):
        return "star"
    if all(isinstance(node, (ast.Import, ast.ImportFrom)) for node in meaningful):
        return "explicit"
    return "implementation"


def _relative(path: Path) -> str:
    return path.relative_to(SOURCE_ROOT).as_posix()


def scan_reexport_shells() -> tuple[str, ...]:
    values = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if path.name == "__init__.py" or "__pycache__" in path.parts:
            continue
        if classify_module(path) in {"star", "explicit"}:
            values.append(_relative(path))
    return tuple(sorted(values))


def scan_empty_packages() -> tuple[str, ...]:
    values = []
    for path in SOURCE_ROOT.rglob("__init__.py"):
        if "__pycache__" in path.parts:
            continue
        if classify_module(path) == "empty":
            values.append(_relative(path))
    return tuple(sorted(values))


def _literal_tokens(node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        token = node.value.strip()
        if token:
            yield token
        return
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("{}")
        token = "".join(parts).strip()
        if token:
            yield token
        return
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        for item in node.elts:
            yield from _literal_tokens(item)
        return
    if isinstance(node, ast.BoolOp):
        for value in node.values:
            yield from _literal_tokens(value)
        return
    if isinstance(node, ast.IfExp):
        yield from _literal_tokens(node.body)
        yield from _literal_tokens(node.orelse)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def scan_gate_tokens() -> tuple[str, ...]:
    """Collect source-level blocker/wait tokens without importing policy code."""

    values: set[str] = set()
    decision_root = SOURCE_ROOT / "decision"
    context_permission = SOURCE_ROOT / "context" / "permissions.py"
    paths = [*decision_root.rglob("*.py"), context_permission]
    for path in paths:
        if not path.exists() or path.name == "gate_registry.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"append", "extend"}:
                owner = node.func.value
                if isinstance(owner, ast.Name) and owner.id in _GATE_APPEND_COLLECTIONS:
                    for arg in node.args:
                        values.update(_literal_tokens(arg))
            for keyword in node.keywords:
                if keyword.arg in _GATE_KEYWORD_FIELDS:
                    values.update(_literal_tokens(keyword.value))
            name = _call_name(node)
            positions = _GATE_POSITIONAL_FIELDS.get(name or "", {})
            for index in positions.values():
                if index < len(node.args):
                    values.update(_literal_tokens(node.args[index]))
    return tuple(sorted(values))


def _is_decision_import(node: ast.ImportFrom) -> bool:
    module = node.module or ""
    if module.startswith("financial_dashboard.decision"):
        return True
    # AST represents ``from ..decision import`` as module="decision", level=2.
    return bool(node.level and (module == "decision" or module.startswith("decision.")))


def scan_trade_action_leaks() -> tuple[str, ...]:
    leaks: set[str] = set()
    for root_name in SYNTHESIS_ROOTS:
        root = SOURCE_ROOT / root_name
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value.strip().upper() in FORBIDDEN_TRADE_ACTION_TOKENS:
                        leaks.add(f"{_relative(path)}:{node.lineno}:{node.value.strip().upper()}")
                elif isinstance(node, ast.ImportFrom) and _is_decision_import(node):
                    leaks.add(f"{_relative(path)}:{node.lineno}:DECISION_IMPORT")
    return tuple(sorted(leaks))


def scan_legacy_policy_paths(shells: tuple[str, ...]) -> tuple[str, ...]:
    prefixes = tuple(f"decision/{name}/" for name in DECISION_COMPATIBILITY_NAMESPACES)
    return tuple(path for path in shells if path.startswith(prefixes))


def build_report() -> ArchitectureAuditReport:
    shells = scan_reexport_shells()
    return ArchitectureAuditReport(
        reexport_shells=shells,
        empty_packages=scan_empty_packages(),
        gate_tokens=scan_gate_tokens(),
        trade_action_leaks=scan_trade_action_leaks(),
        legacy_policy_paths=scan_legacy_policy_paths(shells),
    )


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def baseline_errors(report: ArchitectureAuditReport, baseline: dict[str, object]) -> tuple[str, ...]:
    errors: list[str] = []
    allowed_shells = set(str(item) for item in baseline.get("reexport_shells", []))
    new_shells = set(report.reexport_shells) - allowed_shells
    if new_shells:
        errors.append("new re-export shells: " + ", ".join(sorted(new_shells)))
    if len(report.reexport_shells) > len(allowed_shells):
        errors.append(f"shell_count grew: {len(report.reexport_shells)} > {len(allowed_shells)}")

    allowed_empty = set(str(item) for item in baseline.get("empty_packages", []))
    new_empty = set(report.empty_packages) - allowed_empty
    if new_empty:
        errors.append("new empty packages: " + ", ".join(sorted(new_empty)))

    allowed_leaks = set(str(item) for item in baseline.get("trade_action_leaks", []))
    new_leaks = set(report.trade_action_leaks) - allowed_leaks
    if new_leaks:
        errors.append("trade-action leakage into analysis layers: " + ", ".join(sorted(new_leaks)))

    max_gate_tokens = int(baseline.get("max_gate_token_count", report.gate_token_count))
    if report.gate_token_count > max_gate_tokens:
        errors.append(f"gate token count grew: {report.gate_token_count} > {max_gate_tokens}")

    max_legacy = int(baseline.get("max_legacy_policy_path_count", report.legacy_policy_path_count))
    if report.legacy_policy_path_count > max_legacy:
        errors.append(
            f"legacy policy path count grew: {report.legacy_policy_path_count} > {max_legacy}"
        )
    return tuple(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit migration architecture invariants")
    parser.add_argument("--check", action="store_true", help="fail when the baseline expands")
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    args = parser.parse_args()

    report = build_report()
    print(json.dumps(report.to_payload(), indent=2, sort_keys=True, default=str))
    if args.check:
        errors = baseline_errors(report, load_baseline(args.baseline))
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
