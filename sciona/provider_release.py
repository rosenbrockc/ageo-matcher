"""Artifact-level release contract for PEP 420 atom provider distributions."""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import venv
import zipfile


_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[+.-][A-Za-z0-9.-]+)?$")
_DECORATORS = {"register_atom", "symbolic_atom"}


@dataclass(frozen=True)
class ProviderReleaseIssue:
    code: str
    message: str


@dataclass(frozen=True)
class ProviderReleaseReport:
    repo: str
    distribution_name: str
    distribution_version: str
    atom_count: int
    import_module_count: int
    wheel: str
    issues: tuple[ProviderReleaseIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def as_dict(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "distribution_name": self.distribution_name,
            "distribution_version": self.distribution_version,
            "atom_count": self.atom_count,
            "import_module_count": self.import_module_count,
            "wheel": self.wheel,
            "ok": self.ok,
            "issues": [asdict(issue) for issue in self.issues],
        }


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return ""


def _module_for_path(path: Path, source_root: Path) -> str:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _excluded_prefixes(payload: dict[str, object]) -> tuple[str, ...]:
    tool = payload.get("tool") or {}
    sciona = tool.get("sciona") or {}
    provider = sciona.get("provider") or {}
    values = provider.get("excluded-import-prefixes") or []
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("tool.sciona.provider.excluded-import-prefixes must be strings")
    return tuple(value.strip().rstrip(".") for value in values if value.strip())


def _is_excluded(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)


def _build_wheel(repo: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    clean_source = output_dir.parent / "source"
    shutil.copytree(
        repo,
        clean_source,
        ignore=shutil.ignore_patterns(
            ".git", "build", "dist", "*.egg-info", "__pycache__", "*.pyc"
        ),
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(output_dir),
            str(clean_source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = sorted(output_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one wheel, found {wheels}")
    return wheels[0]


def _cold_wheel_smoke(
    wheel: Path,
    modules: tuple[str, ...],
    *,
    distribution_name: str,
    distribution_version: str,
    work_dir: Path,
) -> str | None:
    environment = work_dir / "cold-venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    install = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        capture_output=True,
        text=True,
    )
    if install.returncode:
        return (install.stderr or install.stdout).strip()
    code = (
        "import importlib.metadata, importlib.util, json\n"
        f"modules = {list(modules)!r}\n"
        f"dist = importlib.metadata.distribution({distribution_name!r})\n"
        "files = {str(path) for path in (dist.files or [])}\n"
        "missing = [name for name in modules if "
        "name.replace('.', '/') + '.py' not in files and "
        "name.replace('.', '/') + '/__init__.py' not in files]\n"
        "namespaces = [name for name in ('sciona', 'sciona.atoms') "
        "if importlib.util.find_spec(name) is None]\n"
        "print(json.dumps({'version': dist.version, 'missing': missing, "
        "'missing_namespaces': namespaces}))\n"
    )
    result = subprocess.run(
        [str(python), "-c", code], capture_output=True, text=True
    )
    if result.returncode:
        return (result.stderr or result.stdout).strip()
    payload = json.loads(result.stdout)
    if payload["version"] != distribution_version:
        return (
            f"Installed version is {payload['version']!r}, expected "
            f"{distribution_version!r}"
        )
    if payload["missing"]:
        return f"Installed wheel is missing import modules: {payload['missing']}"
    if payload["missing_namespaces"]:
        return f"Installed wheel is missing namespaces: {payload['missing_namespaces']}"
    return None


def validate_provider_release(
    repo_root: str | Path,
    *,
    build: bool = True,
) -> ProviderReleaseReport:
    repo = Path(repo_root).resolve()
    issues: list[ProviderReleaseIssue] = []
    pyproject_path = repo / "pyproject.toml"
    if not pyproject_path.is_file():
        return ProviderReleaseReport(
            str(repo), "", "", 0, 0, "",
            (ProviderReleaseIssue("missing-pyproject", "pyproject.toml is required"),),
        )
    with pyproject_path.open("rb") as handle:
        payload = tomllib.load(handle)
    project = payload.get("project") or {}
    name = str(project.get("name") or "")
    version = str(project.get("version") or "")
    if name != repo.name:
        issues.append(ProviderReleaseIssue(
            "distribution-name", f"project.name must match provider repo name {repo.name!r}"
        ))
    if not _VERSION_RE.fullmatch(version):
        issues.append(ProviderReleaseIssue("distribution-version", "project.version must be SemVer-compatible"))
    setuptools = ((payload.get("tool") or {}).get("setuptools") or {})
    packages = setuptools.get("packages") or {}
    if packages.get("find"):
        packages = packages["find"]
    if packages.get("namespaces") is not True:
        issues.append(ProviderReleaseIssue("pep420-disabled", "setuptools namespace discovery must be enabled"))
    source_root = repo / "src"
    for namespace_init in (
        source_root / "sciona" / "__init__.py",
        source_root / "sciona" / "atoms" / "__init__.py",
        source_root / "sciona" / "probes" / "__init__.py",
    ):
        if namespace_init.exists():
            issues.append(ProviderReleaseIssue(
                "namespace-initializer",
                f"PEP 420 namespace segment must not contain {namespace_init.relative_to(repo)}",
            ))
    try:
        excluded = _excluded_prefixes(payload)
    except ValueError as exc:
        excluded = ()
        issues.append(ProviderReleaseIssue("excluded-prefixes", str(exc)))
    targets: list[tuple[str, str]] = []
    for path in sorted(source_root.rglob("*.py")) if source_root.exists() else []:
        module = _module_for_path(path, source_root)
        if not module or _is_excluded(module, excluded):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            issues.append(ProviderReleaseIssue("syntax-error", f"{path.relative_to(repo)}: {exc}"))
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(
                _decorator_name(decorator) in _DECORATORS
                for decorator in node.decorator_list
            ):
                targets.append((module, node.name))
    if not targets:
        issues.append(ProviderReleaseIssue("empty-provider", "provider must contain at least one decorated atom"))
    if len(set(targets)) != len(targets):
        issues.append(ProviderReleaseIssue("duplicate-import-target", "decorated module/symbol targets must be unique"))

    wheel_path = ""
    if build and not issues:
        with tempfile.TemporaryDirectory(prefix="sciona-provider-release-") as temp:
            work_dir = Path(temp)
            try:
                wheel = _build_wheel(repo, work_dir / "dist")
                wheel_path = wheel.name
                with zipfile.ZipFile(wheel) as archive:
                    names = set(archive.namelist())
                for forbidden in ("sciona/__init__.py", "sciona/atoms/__init__.py", "sciona/probes/__init__.py"):
                    if forbidden in names:
                        issues.append(ProviderReleaseIssue("wheel-namespace-initializer", f"wheel contains {forbidden}"))
                missing = sorted(
                    module for module in {module for module, _symbol in targets}
                    if f"{module.replace('.', '/')}.py" not in names
                    and f"{module.replace('.', '/')}/__init__.py" not in names
                )
                if missing:
                    issues.append(ProviderReleaseIssue("wheel-missing-module", f"wheel omits catalog import modules: {missing}"))
                leaked = sorted(
                    name for name in names
                    if name.endswith(".py") and _is_excluded(name[:-3].replace("/", "."), excluded)
                )
                if leaked:
                    issues.append(ProviderReleaseIssue("excluded-prefix-leak", f"wheel contains excluded modules: {leaked[:20]}"))
                if not issues:
                    smoke_error = _cold_wheel_smoke(
                        wheel,
                        tuple(sorted({module for module, _symbol in targets})),
                        distribution_name=name,
                        distribution_version=version,
                        work_dir=work_dir,
                    )
                    if smoke_error:
                        issues.append(ProviderReleaseIssue("cold-install-smoke", smoke_error))
            except (OSError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
                issues.append(ProviderReleaseIssue("wheel-build", str(exc)))
    return ProviderReleaseReport(
        str(repo), name, version, len(targets), len({module for module, _ in targets}),
        wheel_path, tuple(issues),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", nargs="?", default=".")
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args(argv)
    report = validate_provider_release(args.repo, build=not args.no_build)
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
