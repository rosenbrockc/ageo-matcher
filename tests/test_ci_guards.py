"""CI guards: prevent forbidden provider-namespace imports in matcher tests.

These tests parse every test file with ``ast`` and flag any import statement
(at any nesting depth) that references a concrete provider-owned namespace.
String literals that happen to contain these patterns (e.g. code-emission
assertions) are intentionally ignored.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
_LEGACY_NAMESPACE_LABEL = "age" + "oa"
_LEGACY_PROVIDER_LABEL = "ageo" + "-atoms"

FORBIDDEN_PREFIXES = (
    "sciona.atoms.",
    "sciona.probes.",
    "sciona.expansion_atoms.",
)


def _is_forbidden(module_name: str) -> bool:
    """Return True if *module_name* is a forbidden provider-namespace import.

    The bare ``sciona.principal.expansion_rules`` (without a trailing
    submodule) is allowed — only concrete family submodules are forbidden.
    """
    for prefix in FORBIDDEN_PREFIXES:
        if module_name.startswith(prefix):
            return True
    return False


def _collect_violations(
    root: Path,
    *,
    exclude_dirs: frozenset[str] = frozenset(),
    exclude_files: frozenset[str] = frozenset(),
) -> list[tuple[str, int, str]]:
    """Walk ``root/**/*.py`` and return ``(file, line, module)`` violations."""
    violations: list[tuple[str, int, str]] = []
    for py_file in sorted(root.rglob("*.py")):
        rel = py_file.relative_to(root)
        if any(part in exclude_dirs for part in rel.parts):
            continue
        if py_file.name in exclude_files:
            continue

        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_forbidden(alias.name):
                        violations.append(
                            (str(rel), node.lineno, alias.name)
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if _is_forbidden(node.module):
                    violations.append(
                        (str(rel), node.lineno, node.module)
                    )
    return violations


def test_no_forbidden_namespace_imports_in_matcher_tests() -> None:
    """Matcher tests must not import concrete provider-owned namespaces."""
    violations = _collect_violations(
        TESTS_DIR,
        exclude_dirs=frozenset({"synthetic_family", "test_symbolic_funnel"}),
        exclude_files=frozenset({"test_ci_guards.py"}),
    )
    msg_lines = ["Forbidden provider-namespace imports found in matcher tests:"]
    for file, line, module in violations:
        msg_lines.append(f"  {file}:{line}  ->  {module}")
    assert not violations, "\n".join(msg_lines)


def test_synthetic_family_fixture_has_no_forbidden_imports() -> None:
    """The synthetic_family fixture must be fully self-contained."""
    fixture_dir = TESTS_DIR / "fixtures" / "synthetic_family"
    if not fixture_dir.is_dir():
        return  # nothing to check
    violations = _collect_violations(fixture_dir)
    msg_lines = [
        "Forbidden provider-namespace imports found in synthetic_family fixture:"
    ]
    for file, line, module in violations:
        msg_lines.append(f"  {file}:{line}  ->  {module}")
    assert not violations, "\n".join(msg_lines)


def test_active_matcher_paths_have_no_legacy_namespace_strings() -> None:
    active_paths = (
        REPO_ROOT / "sciona",
        REPO_ROOT / "scripts",
        REPO_ROOT / "tests",
        REPO_ROOT / "README.md",
        REPO_ROOT / "ARCH.md",
        REPO_ROOT / "sources.yml",
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "docs" / "REFINE_INGEST_STATUS.md",
        REPO_ROOT / "docs" / "supabase" / "local-reset-and-reseed.md",
        REPO_ROOT / "docs" / "supabase" / "schema-ownership-consolidation.md",
    )
    violations: list[str] = []
    for target in active_paths:
        if not target.exists():
            continue
        candidates = [target] if target.is_file() else sorted(target.rglob("*"))
        for candidate in candidates:
            if candidate.is_dir():
                continue
            if "__pycache__" in candidate.parts or candidate.suffix == ".pyc":
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if _LEGACY_NAMESPACE_LABEL in text or _LEGACY_PROVIDER_LABEL in text:
                violations.append(str(candidate.relative_to(REPO_ROOT)))
    assert not violations, "Legacy namespace strings remain in active matcher paths:\n" + "\n".join(violations)
