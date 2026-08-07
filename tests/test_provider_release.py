from __future__ import annotations

from pathlib import Path

from sciona.provider_release import validate_provider_release


def _write_provider(root: Path, *, namespace_initializer: bool = False) -> None:
    (root / "src/sciona/atoms/demo").mkdir(parents=True)
    (root / "README.md").write_text("# demo\n")
    (root / "pyproject.toml").write_text(
        """[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "sciona-atoms-demo"
version = "1.2.3"
readme = "README.md"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
namespaces = true
"""
    )
    (root / "src/sciona/atoms/demo/ops.py").write_text(
        "@register_atom(object())\ndef calculate(value):\n    return value\n"
    )
    if namespace_initializer:
        (root / "src/sciona/__init__.py").write_text("# incompatible\n")


def test_provider_release_builds_and_cold_installs_wheel(tmp_path: Path) -> None:
    repo = tmp_path / "sciona-atoms-demo"
    _write_provider(repo)

    report = validate_provider_release(repo)

    assert report.ok
    assert report.atom_count == 1
    assert report.import_module_count == 1
    assert report.wheel.startswith("sciona_atoms_demo-1.2.3-")


def test_provider_release_rejects_namespace_initializer(tmp_path: Path) -> None:
    repo = tmp_path / "sciona-atoms-demo"
    _write_provider(repo, namespace_initializer=True)

    report = validate_provider_release(repo, build=False)

    assert not report.ok
    assert [issue.code for issue in report.issues] == ["namespace-initializer"]
