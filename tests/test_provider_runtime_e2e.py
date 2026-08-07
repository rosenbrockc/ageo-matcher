"""Cold-environment E2E for remote discovery and opt-in provider installation."""

from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import site
import subprocess
import textwrap
from threading import Thread
import venv
import zipfile

from sciona.api.models import CatalogEntry, ProviderInstallInfo
from sciona.provider_runtime import PostgresSemanticIndex, ProviderInstaller


def _build_namespace_wheel(
    root: Path,
    *,
    distribution: str,
    version: str,
    discipline: str,
    body: str,
) -> Path:
    wheel_stem = distribution.replace("-", "_")
    wheel_path = root / f"{wheel_stem}-{version}-py3-none-any.whl"
    dist_info = f"{wheel_stem}-{version}.dist-info"
    files = {
        f"sciona/atoms/{discipline}/ops.py": textwrap.dedent(body).lstrip(),
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\n"
            f"Name: {distribution}\n"
            f"Version: {version}\n"
        ),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: sciona-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ),
    }
    record_path = f"{dist_info}/RECORD"
    files[record_path] = "".join(f"{name},,\n" for name in (*files, record_path))
    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return wheel_path


def _catalog_row(
    *,
    fqdn: str,
    discipline: str,
    distribution: str,
    wheel: Path,
) -> dict:
    return {
        "fqdn": fqdn,
        "description": f"Fixture atom for {discipline}",
        "domain_tags": [discipline],
        "overall_verdict": "trusted",
        "provider": {
            "provider_id": distribution,
            "distribution_name": distribution,
            "distribution_version": "1.0.0",
            "install_requirement": f"{distribution}==1.0.0",
            "import_module": f"sciona.atoms.{discipline}.ops",
            "import_symbol": fqdn.rsplit(".", 1)[-1],
            "wheel_url": wheel.resolve().as_uri(),
            "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        },
    }


def test_provider_installer_rejects_unpinned_requirement() -> None:
    candidate = CatalogEntry(
        fqdn="sciona.atoms.signal.ops.scale",
        description="Scale a signal",
        provider=ProviderInstallInfo(
            provider_id="signal",
            distribution_name="sciona-atoms-signal",
            distribution_version="1.0.0",
            install_requirement="sciona-atoms-signal>=1.0.0",
            import_module="sciona.atoms.signal.ops",
            import_symbol="scale",
        ),
    )

    try:
        ProviderInstaller().install(candidate.provider)
    except ValueError as exc:
        assert "pin one exact version" in str(exc)
    else:
        raise AssertionError("installer accepted an unpinned provider requirement")


def test_provider_installer_caps_artifact_size(tmp_path: Path, monkeypatch) -> None:
    wheel = tmp_path / "provider.whl"
    wheel.write_bytes(b"oversized-wheel")
    monkeypatch.setenv("SCIONA_PROVIDER_MAX_WHEEL_BYTES", "4")
    provider = ProviderInstallInfo(
        provider_id="demo",
        distribution_name="sciona-atoms-demo",
        distribution_version="1.0.0",
        install_requirement="sciona-atoms-demo==1.0.0",
        import_module="sciona.atoms.demo.ops",
        import_symbol="run",
        wheel_url=wheel.resolve().as_uri(),
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
    )

    try:
        ProviderInstaller(allow_local_artifacts=True)._download_verified_wheel(provider)
    except ValueError as exc:
        assert "size limit" in str(exc)
    else:
        raise AssertionError("installer accepted an oversized provider wheel")


def test_provider_installer_sets_bounded_pip_timeout(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setenv("SCIONA_PROVIDER_INSTALL_TIMEOUT_SECONDS", "17")
    monkeypatch.setattr(
        "sciona.provider_runtime.subprocess.run",
        lambda *args, **kwargs: calls.append(kwargs),
    )
    provider = ProviderInstallInfo(
        provider_id="demo",
        distribution_name="sciona-atoms-demo",
        distribution_version="1.0.0",
        install_requirement="sciona-atoms-demo==1.0.0",
        import_module="sciona.atoms.demo.ops",
        import_symbol="run",
    )

    ProviderInstaller().install(provider)

    assert calls[0]["timeout"] == 17.0


def test_postgres_semantic_index_preserves_install_metadata(monkeypatch) -> None:
    row = {
        "fqdn": "sciona.atoms.physics_fixture.ops.kinetic_energy",
        "description": "Compute kinetic energy",
        "domain_tags": ["physics"],
        "score": 0.82,
        "provider": {
            "provider_id": "sciona-atoms-physics",
            "distribution_name": "sciona-atoms-physics",
            "distribution_version": "1.0.0",
            "install_requirement": "sciona-atoms-physics==1.0.0",
            "import_module": "sciona.atoms.physics_fixture.ops",
            "import_symbol": "kinetic_energy",
        },
    }

    class Response:
        def raise_for_status(self) -> None:
            return

        def json(self) -> list[dict]:
            return [row]

    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: Response())

    index = PostgresSemanticIndex("https://catalog.example")
    results = index.search_by_embedding("energy from mass and velocity", k=5)

    declaration, score = results[0]
    assert score == 1.0
    assert declaration.prover.value == "python"
    assert declaration.install_requirement == "sciona-atoms-physics==1.0.0"
    assert declaration.import_module == "sciona.atoms.physics_fixture.ops"
    assert index.get_declaration(declaration.name) == declaration


def test_remote_search_installs_only_selected_multidisciplinary_provider(
    tmp_path: Path,
) -> None:
    signal_wheel = _build_namespace_wheel(
        tmp_path,
        distribution="sciona-atoms-signal-fixture",
        version="1.0.0",
        discipline="signal_fixture",
        body="""
        def scale_signal(value):
            return value * 2
        """,
    )
    physics_wheel = _build_namespace_wheel(
        tmp_path,
        distribution="sciona-atoms-physics-fixture",
        version="1.0.0",
        discipline="physics_fixture",
        body="""
        def kinetic_energy(mass, velocity):
            return 0.5 * mass * velocity ** 2
        """,
    )
    fintech_wheel = _build_namespace_wheel(
        tmp_path,
        distribution="sciona-atoms-fintech-fixture",
        version="1.0.0",
        discipline="fintech_fixture",
        body="""
        def compound_value(principal, rate):
            return principal * (1 + rate)
        """,
    )
    rows = [
        _catalog_row(
            fqdn="sciona.atoms.signal_fixture.ops.scale_signal",
            discipline="signal_fixture",
            distribution="sciona-atoms-signal-fixture",
            wheel=signal_wheel,
        ),
        _catalog_row(
            fqdn="sciona.atoms.physics_fixture.ops.kinetic_energy",
            discipline="physics_fixture",
            distribution="sciona-atoms-physics-fixture",
            wheel=physics_wheel,
        ),
        _catalog_row(
            fqdn="sciona.atoms.fintech_fixture.ops.compound_value",
            discipline="fintech_fixture",
            distribution="sciona-atoms-fintech-fixture",
            wheel=fintech_wheel,
        ),
    ]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            payload = json.dumps(rows).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cold_venv = tmp_path / "cold-venv"
        venv.EnvBuilder(with_pip=True).create(cold_venv)
        python = cold_venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        matcher_root = Path(__file__).resolve().parents[1]
        site_packages = Path(site.getsitepackages()[0])
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join((str(matcher_root), str(site_packages)))
        script = textwrap.dedent(
            f"""
            import asyncio
            import importlib.util
            from sciona.provider_runtime import ProviderInstaller, RemoteCatalogClient

            async def main():
                client = RemoteCatalogClient("http://127.0.0.1:{server.server_port}")
                candidates = await client.search("scientific transformation")
                assert {{row.provider.provider_id for row in candidates}} == {{
                    "sciona-atoms-signal-fixture",
                    "sciona-atoms-physics-fixture",
                    "sciona-atoms-fintech-fixture",
                }}
                assert not ProviderInstaller._module_available("sciona.atoms.signal_fixture.ops")
                assert not ProviderInstaller._module_available("sciona.atoms.physics_fixture.ops")
                assert not ProviderInstaller._module_available("sciona.atoms.fintech_fixture.ops")
                selected = next(row for row in candidates if "signal_fixture" in row.fqdn)
                function = ProviderInstaller(allow_local_artifacts=True).materialize(selected)
                assert function(7) == 14
                assert ProviderInstaller._module_available("sciona.atoms.signal_fixture.ops")
                assert not ProviderInstaller._module_available("sciona.atoms.physics_fixture.ops")
                assert not ProviderInstaller._module_available("sciona.atoms.fintech_fixture.ops")

            asyncio.run(main())
            """
        )
        result = subprocess.run(
            [str(python), "-c", script],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.shutdown()
        thread.join(timeout=5)
