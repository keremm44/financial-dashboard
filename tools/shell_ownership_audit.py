from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src" / "financial_dashboard"
DEFAULT_SCOPE = ROOT / "tools" / "decision_shell_cleanup_scope.json"
SCAN_ROOTS = (
    ROOT / "src",
    ROOT / "tests",
    ROOT / "scripts",
    ROOT / "tools",
)


def _module_body(path: Path) -> list[ast.stmt]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            body = body[1:]
    return [
        node
        for node in body
        if not (isinstance(node, ast.ImportFrom) and node.module == "__future__")
    ]


def _is_dunder_all_assignment(node: ast.stmt) -> bool:
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return False
    return any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets)


def classify_module(path: Path) -> str:
    body = _module_body(path)
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


def _relative_source(path: Path) -> str:
    return path.relative_to(SOURCE_ROOT).as_posix()


def module_name_for_source(path: Path) -> str:
    relative = path.relative_to(SOURCE_ROOT)
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("financial_dashboard", *parts))


def resolve_import_from(current_module: str | None, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    if current_module is None:
        return None
    package = current_module.split(".")[:-1]
    remove = node.level - 1
    if remove > len(package):
        return None
    base = package[: len(package) - remove] if remove else package
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def declared_exports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            continue
        value = node.value
        if isinstance(value, (ast.List, ast.Tuple)) and all(
            isinstance(item, ast.Constant) and isinstance(item.value, str)
            for item in value.elts
        ):
            return tuple(item.value for item in value.elts)
    return ()


def reexport_targets(path: Path) -> tuple[str, ...]:
    current_module = module_name_for_source(path)
    targets: set[str] = set()
    for node in _module_body(path):
        if _is_dunder_all_assignment(node):
            continue
        if isinstance(node, ast.ImportFrom):
            resolved = resolve_import_from(current_module, node)
            if resolved:
                targets.add(resolved)
        elif isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
    return tuple(sorted(targets))


def module_source_path(module: str) -> Path | None:
    prefix = "financial_dashboard"
    if module == prefix:
        candidate = SOURCE_ROOT / "__init__.py"
        return candidate if candidate.exists() else None
    if not module.startswith(prefix + "."):
        return None
    parts = module.split(".")[1:]
    file_candidate = SOURCE_ROOT.joinpath(*parts).with_suffix(".py")
    if file_candidate.exists():
        return file_candidate
    package_candidate = SOURCE_ROOT.joinpath(*parts) / "__init__.py"
    if package_candidate.exists():
        return package_candidate
    return None


def scan_shell_paths() -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                path
                for path in SOURCE_ROOT.rglob("*.py")
                if path.name != "__init__.py" and classify_module(path) in {"star", "explicit"}
            ),
            key=lambda path: _relative_source(path),
        )
    )


def _candidate_modules_from_import(path: Path) -> set[str]:
    current_module = module_name_for_source(path) if path.is_relative_to(SOURCE_ROOT) else None
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = resolve_import_from(current_module, node)
            if not base:
                continue
            modules.add(base)
            for alias in node.names:
                if alias.name != "*":
                    modules.add(f"{base}.{alias.name}")
    return modules


def _python_files() -> Iterable[Path]:
    seen: set[Path] = set()
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts or path in seen:
                continue
            seen.add(path)
            yield path


def internal_importers(shell_module: str, shell_path: Path) -> tuple[str, ...]:
    values: list[str] = []
    for path in _python_files():
        if path == shell_path:
            continue
        if shell_module in _candidate_modules_from_import(path):
            values.append(path.relative_to(ROOT).as_posix())
    return tuple(sorted(values))


def build_inventory() -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    shell_paths = scan_shell_paths()
    shell_modules = {module_name_for_source(path) for path in shell_paths}
    for path in shell_paths:
        module = module_name_for_source(path)
        targets = reexport_targets(path)
        target_rows = []
        for target in targets:
            target_path = module_source_path(target)
            target_rows.append(
                {
                    "module": target,
                    "path": None if target_path is None else _relative_source(target_path),
                    "kind": None if target_path is None else classify_module(target_path),
                    "is_shell": target in shell_modules,
                }
            )
        rows.append(
            {
                "path": _relative_source(path),
                "module": module,
                "kind": classify_module(path),
                "declared_exports": list(declared_exports(path)),
                "targets": target_rows,
                "internal_importers": list(internal_importers(module, path)),
            }
        )
    return tuple(rows)


def load_scope(path: Path = DEFAULT_SCOPE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def scope_errors(inventory: tuple[dict[str, Any], ...], scope: dict[str, Any]) -> tuple[str, ...]:
    rows = {str(row["path"]): row for row in inventory}
    scoped = tuple(str(item) for item in scope.get("shells", ()))
    errors: list[str] = []
    if len(scoped) != len(set(scoped)):
        errors.append("decision shell cleanup scope contains duplicate paths")
    for path in scoped:
        row = rows.get(path)
        if row is None:
            errors.append(f"scoped shell not found in live inventory: {path}")
            continue
        targets = row["targets"]
        if not targets:
            errors.append(f"scoped shell has no resolved re-export target: {path}")
        for target in targets:
            if target["path"] is None:
                errors.append(f"scoped shell target is not a repository module: {path} -> {target['module']}")
            elif target["is_shell"]:
                errors.append(f"scoped shell points to another shell: {path} -> {target['path']}")
    return tuple(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report shell ownership targets and internal import dependencies")
    parser.add_argument("--scope", type=Path, default=DEFAULT_SCOPE)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    inventory = build_inventory()
    scoped_paths = set(str(item) for item in load_scope(args.scope).get("shells", ()))
    payload = {
        "shell_count": len(inventory),
        "scoped_shell_count": len(scoped_paths),
        "shells": [row for row in inventory if row["path"] in scoped_paths],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if args.check:
        errors = scope_errors(inventory, load_scope(args.scope))
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
