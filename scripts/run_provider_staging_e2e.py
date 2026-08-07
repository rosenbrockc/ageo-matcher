#!/usr/bin/env python3
"""Run the real catalog-to-provider execution path in disposable infrastructure."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
from threading import Thread
import time
from types import SimpleNamespace
from urllib.request import urlopen
import venv


PROVIDERS = (
    (
        "sciona-atoms-signal",
        "sciona.atoms.expansion.signal_transform.validate_parseval_energy",
    ),
    (
        "sciona-atoms-physics",
        "sciona.atoms.physics.jFOF.find_fof_clusters",
    ),
    (
        "sciona-atoms-fintech",
        "sciona.atoms.fintech.quant_engine.execute_vwap",
    ),
    (
        "sciona-atoms-ml",
        "sciona.atoms.ml.model_selection.diagnostics.compute_condition_number",
    ),
    (
        "sciona-atoms-cs",
        "sciona.atoms.combinatorial.graph.dijkstra_shortest_path.run_dijkstra_heap",
    ),
    (
        "sciona-atoms-geo",
        "sciona.atoms.geo.geospatial_sensors.lla_to_ecef",
    ),
    (
        "sciona-atoms-robotics",
        "sciona.atoms.robotics.pronto.flex_estimator.estimate_flex_deflection",
    ),
)


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("+", shlex.join(command), flush=True)
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=True,
            text=True,
            capture_output=capture,
        )
    except subprocess.CalledProcessError as exc:
        if capture:
            if exc.stdout:
                print(exc.stdout, file=sys.stderr)
            if exc.stderr:
                print(exc.stderr, file=sys.stderr)
        raise


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _configure_supabase(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination / "supabase")
    config_path = destination / "supabase" / "config.toml"
    config = config_path.read_text()
    config = re.sub(
        r'^project_id = ".*"$',
        f'project_id = "sciona-provider-e2e-{os.getpid()}"',
        config,
        flags=re.MULTILINE,
    )
    for old_port in (54320, 54321, 54322, 54323):
        config = config.replace(f"port = {old_port}", f"port = {_free_port()}")
    for section in ("realtime", "studio", "storage"):
        config = re.sub(
            rf"(\[{re.escape(section)}\]\s*\nenabled = )true",
            r"\1false",
            config,
        )
    config += "\n[inbucket]\nenabled = false\n"
    config += "\n[edge_runtime]\nenabled = false\n"
    config += "\n[analytics]\nenabled = false\n"
    config_path.write_text(config)


def _supabase_environment(root: Path) -> dict[str, str]:
    result = _run(
        ["supabase", "status", "--output", "env"],
        cwd=root,
        capture=True,
    )
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        values[name] = raw_value.strip().strip('"')
    return values


def _build_wheels(
    *, matcher_root: Path, workspace_root: Path, wheel_dir: Path
) -> dict[str, Path]:
    python = Path(sys.executable)
    for repo_name, _fqdn in PROVIDERS:
        _run(
            [
                str(python),
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(wheel_dir),
                str(workspace_root / repo_name),
            ],
            cwd=matcher_root,
        )
    _run(
        [
            str(python),
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
            str(matcher_root),
        ],
        cwd=matcher_root,
    )
    wheels: dict[str, Path] = {}
    for repo_name, _fqdn in PROVIDERS:
        stem = repo_name.replace("-", "_") + "-"
        matches = sorted(wheel_dir.glob(f"{stem}*.whl"))
        if len(matches) != 1:
            raise RuntimeError(f"Expected one wheel for {repo_name}, found {matches}")
        wheels[repo_name] = matches[0]
    return wheels


class _QuietFileHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def _start_artifact_server(
    wheel_dir: Path, work_dir: Path
) -> tuple[ThreadingHTTPServer, Thread, Path]:
    cert_path = work_dir / "artifact-cert.pem"
    key_path = work_dir / "artifact-key.pem"
    _run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        cwd=work_dir,
        capture=True,
    )
    handler = lambda *args, **kwargs: _QuietFileHandler(  # noqa: E731
        *args, directory=str(wheel_dir), **kwargs
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, cert_path


def _publish_catalog(
    *, matcher_root: Path, workspace_root: Path, supabase_env: dict[str, str]
) -> None:
    env = os.environ.copy()
    env.update(
        {
            "SUPABASE_URL": supabase_env["API_URL"],
            "SUPABASE_SERVICE_ROLE_KEY": supabase_env["SERVICE_ROLE_KEY"],
            "SUPABASE_ANON_KEY": supabase_env["ANON_KEY"],
        }
    )
    _run(
        [
            str(Path(sys.executable).with_name("sciona")),
            "catalog",
            "publish-providers",
            "--workspace-root",
            str(workspace_root),
            "--apply",
            "--ensure-owner",
            "--database-url",
            supabase_env["DB_URL"],
            "--skip-embeddings",
        ],
        cwd=matcher_root,
        env=env,
    )


def _attach_wheels(
    *,
    wheels: dict[str, Path],
    artifact_port: int,
    supabase_env: dict[str, str],
) -> None:
    from supabase import create_client

    client = create_client(supabase_env["API_URL"], supabase_env["SERVICE_ROLE_KEY"])
    for repo_name, wheel in wheels.items():
        payload = {
            "wheel_url": f"https://localhost:{artifact_port}/{wheel.name}",
            "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        }
        result = (
            client.table("atom_source_repositories")
            .update(payload)
            .eq("repo_name", repo_name)
            .execute()
        )
        if len(result.data or []) != 1:
            raise RuntimeError(f"Could not attach wheel metadata for {repo_name}")


def _assert_e2e_candidates_are_served(supabase_env: dict[str, str]) -> None:
    from supabase import create_client

    client = create_client(supabase_env["API_URL"], supabase_env["SERVICE_ROLE_KEY"])
    rows: list[dict[str, object]] = []
    page_size = 1000
    offset = 0
    while True:
        page = (
            client.table("catalog_atom_installations")
            .select("fqdn,provider_id")
            .order("fqdn")
            .range(offset, offset + page_size - 1)
            .execute()
            .data
            or []
        )
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    served = {str(row["fqdn"]) for row in rows}
    missing = [fqdn for _provider, fqdn in PROVIDERS if fqdn not in served]
    if not missing:
        return
    diagnostics: dict[str, object] = {}
    for fqdn in missing:
        atom_rows = (
            client.table("atoms")
            .select("atom_id,fqdn,is_publishable")
            .eq("fqdn", fqdn)
            .execute()
            .data
            or []
        )
        if not atom_rows:
            diagnostics[fqdn] = {"seeded": False}
            continue
        atom = atom_rows[0]
        atom_id = atom["atom_id"]
        component_tables = {
            "io_specs": "atom_io_specs",
            "parameters": "atom_parameters",
            "descriptions": "atom_descriptions",
            "audit_rollups": "atom_audit_rollups",
            "references": "atom_references",
        }
        diagnostics[fqdn] = {
            "seeded": True,
            "is_publishable": bool(atom.get("is_publishable")),
            **{
                name: (
                    client.table(table)
                    .select("*")
                    .eq("atom_id", atom_id)
                    .execute()
                    .data
                    or []
                )
                for name, table in component_tables.items()
            },
        }
    samples: dict[str, list[str]] = {}
    for provider, _fqdn in PROVIDERS:
        samples[provider] = sorted(
            str(row["fqdn"])
            for row in rows
            if row.get("provider_id") == provider
        )[:20]
    raise RuntimeError(
        "Staging candidates are not served after publication: "
        f"{missing}; diagnostics: {json.dumps(diagnostics, sort_keys=True, default=str)}; "
        f"installable provider samples: {json.dumps(samples, sort_keys=True)}"
    )


class _DeterministicEmbeddingEndpoint:
    def create(
        self, *, model: str, input: list[str], dimensions: int
    ) -> SimpleNamespace:
        items = []
        for index, text in enumerate(input):
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vector = [0.0] * dimensions
            for position, value in enumerate(digest):
                vector[position] = (value - 127.5) / 127.5
            items.append(SimpleNamespace(index=index, embedding=vector))
        return SimpleNamespace(data=items, model=f"{model}-staging-deterministic")


def _exercise_embedding_refresh(supabase_env: dict[str, str]) -> dict[str, object]:
    from supabase import create_client

    from sciona.catalog_embeddings import embedding_config_from_env
    from sciona.provider_publication import refresh_catalog_embeddings

    service_client = create_client(
        supabase_env["API_URL"], supabase_env["SERVICE_ROLE_KEY"]
    )
    openai_client = SimpleNamespace(embeddings=_DeterministicEmbeddingEndpoint())
    first = refresh_catalog_embeddings(
        service_client,
        openai_client=openai_client,
    )
    if int(first["embedded"]) <= 1000:
        raise RuntimeError(
            "Staging embedding refresh did not exercise multi-page catalog retrieval"
        )

    config = embedding_config_from_env()
    anon_client = create_client(supabase_env["API_URL"], supabase_env["ANON_KEY"])
    active_rows = (
        anon_client.rpc("get_active_embedding_configuration", {}).execute().data or []
    )
    if len(active_rows) != 1 or active_rows[0]["embedding_space_id"] != config.space_id:
        raise RuntimeError(f"Unexpected active embedding configuration: {active_rows}")
    visible = (
        anon_client.table("atom_embeddings")
        .select("atom_id,embedding_space_id")
        .limit(1)
        .execute()
        .data
        or []
    )
    if not visible or visible[0]["embedding_space_id"] != config.space_id:
        raise RuntimeError("Anonymous catalog search cannot see the active embedding space")

    second = refresh_catalog_embeddings(
        service_client,
        openai_client=openai_client,
    )
    if second["needed"] != 0 or second["embedded"] != 0:
        raise RuntimeError(f"Unchanged catalog embeddings were regenerated: {second}")
    return first


def _exercise_intent_retrieval(
    *, api_url: str, matcher_root: Path
) -> dict[str, object]:
    from sciona.catalog_quality import (
        evaluate_catalog_intents,
        load_catalog_intent_cases,
    )
    from sciona.provider_runtime import RemoteCatalogClient

    cases = load_catalog_intent_cases(
        matcher_root / "tests/fixtures/provider_intent_benchmark.json"
    )
    client = RemoteCatalogClient(api_url)

    async def search(query: str, top_k: int) -> list[object]:
        return await client.search(query, limit=top_k)

    report = asyncio.run(evaluate_catalog_intents(cases, search))
    if not report.ok:
        raise RuntimeError(
            "Multidisciplinary catalog intent quality gate failed: "
            f"{json.dumps(report.as_dict(), sort_keys=True)}"
        )
    return report.as_dict()


def _wait_for_url(url: str, *, cafile: Path | None = None, timeout: float = 45) -> None:
    context = ssl.create_default_context(cafile=str(cafile)) if cafile else None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2, context=context) as response:
                if response.status < 500:
                    return
        except Exception:
            time.sleep(0.25)
    raise TimeoutError(f"Service did not become ready: {url}")


def _start_api(
    *, matcher_root: Path, supabase_env: dict[str, str], api_port: int
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env.update(
        {
            "SCIONA_SUPABASE_URL": supabase_env["API_URL"],
            "SCIONA_SUPABASE_ANON_KEY": supabase_env["ANON_KEY"],
            "SCIONA_SUPABASE_SERVICE_ROLE_KEY": supabase_env["SERVICE_ROLE_KEY"],
            "TEMPORAL_ADDRESS": "",
        }
    )
    command = [
        str(sys.executable),
        "-m",
        "uvicorn",
        "sciona.api.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(api_port),
    ]
    print("+", shlex.join(command), flush=True)
    return subprocess.Popen(command, cwd=matcher_root, env=env, text=True)


def _create_cold_matcher_environment(
    *, matcher_root: Path, wheel_dir: Path, cold_venv: Path
) -> Path:
    venv.EnvBuilder(with_pip=True).create(cold_venv)
    python = cold_venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    matcher_wheels = sorted(wheel_dir.glob("sciona-*.whl"))
    matcher_wheels = [wheel for wheel in matcher_wheels if "sciona_atoms_" not in wheel.name]
    if len(matcher_wheels) != 1:
        raise RuntimeError(f"Expected one matcher wheel, found {matcher_wheels}")
    _run(
        [str(python), "-m", "pip", "install", str(matcher_wheels[0])],
        cwd=matcher_root,
    )
    return python


def _exercise_cold_runtime(
    *, python: Path, api_url: str, cert_path: Path, work_dir: Path
) -> dict[str, object]:
    fqdn_rows = json.dumps([fqdn for _provider, fqdn in PROVIDERS])
    provider_rows = json.dumps([provider for provider, _fqdn in PROVIDERS])
    script = f"""
import asyncio
import importlib.metadata
import json
import numpy as np
from sciona.provider_runtime import ProviderInstaller, RemoteCatalogClient

fqdns = {fqdn_rows}
providers = {provider_rows}

def installed(name):
    try:
        importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True

async def main():
    client = RemoteCatalogClient({api_url!r})
    candidates = [await client.find(fqdn) for fqdn in fqdns]
    assert not any(installed(name) for name in providers)
    results = {{}}
    for index, candidate in enumerate(candidates):
        assert [installed(name) for name in providers] == [i < index for i in range(len(providers))]
        function = ProviderInstaller().materialize(candidate)
        assert [installed(name) for name in providers] == [i <= index for i in range(len(providers))]
        if index == 0:
            values = np.array([1.0, 2.0, 3.0, 4.0])
            result = function(values, np.fft.fft(values))
            assert result == (0.0, True)
            results["signal"] = result
        elif index == 1:
            points = np.array([[0.0, 0.0], [0.05, 0.0], [1.0, 1.0]])
            result = function(points, b=0.1, L=10.0)
            assert result[0] == result[1] and result[2] != result[0]
            results["physics"] = result.tolist()
        elif index == 2:
            from sciona.atoms.fintech.quant_engine import LimitQueueState
            value, state = function(40, LimitQueueState(my_qty=9))
            assert value is None and state.my_qty == 5
            results["fintech"] = state.my_qty
        elif index == 3:
            result = function(np.eye(3))
            assert np.isclose(result, 1.0)
            results["ml"] = result
        elif index == 4:
            from scipy.sparse import csr_array
            graph = csr_array([[0.0, 1.0, 4.0], [0.0, 0.0, 2.0], [0.0, 0.0, 0.0]])
            result = function(graph, source=0, has_negative=False)
            assert np.allclose(result, [0.0, 1.0, 3.0])
            results["cs"] = result.tolist()
        elif index == 5:
            result = function(
                np.array([0.0]),
                np.array([0.0]),
                np.array([0.0]),
            )
            assert np.allclose(result[0], [6378137.0])
            assert np.allclose(result[1], [0.0]) and np.allclose(result[2], [0.0])
            results["geo"] = [part.tolist() for part in result]
        else:
            result = function(
                np.zeros((2, 3)),
                np.array([100.0, -50.0]),
                np.array([True, False]),
            )
            assert np.allclose(result, [0.1, 0.0])
            results["robotics"] = result.tolist()
    print(json.dumps(results, sort_keys=True))

asyncio.run(main())
"""
    env = os.environ.copy()
    env["SSL_CERT_FILE"] = str(cert_path)
    result = _run(
        [str(python), "-c", script],
        cwd=work_dir,
        env=env,
        capture=True,
    )
    output = result.stdout.strip().splitlines()
    if not output:
        raise RuntimeError("Cold runtime produced no result")
    return json.loads(output[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--keep-workdir", action="store_true")
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Seed, backfill, report catalog audit status, and stop",
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=None,
        help="Write the catalog audit JSON to this path",
    )
    args = parser.parse_args()

    workspace_root = args.workspace_root.resolve()
    matcher_root = Path(__file__).resolve().parents[1]
    temp_context = None
    if args.keep_workdir:
        work_dir = Path(tempfile.mkdtemp(prefix="sciona-provider-staging-"))
    else:
        temp_context = tempfile.TemporaryDirectory(prefix="sciona-provider-staging-")
        work_dir = Path(temp_context.name)
    supabase_root = work_dir / "infra"
    wheel_dir = work_dir / "wheels"
    wheel_dir.mkdir(parents=True)
    api_process: subprocess.Popen[str] | None = None
    artifact_server: ThreadingHTTPServer | None = None
    artifact_thread: Thread | None = None
    supabase_started = False
    try:
        _configure_supabase(workspace_root / "sciona-infra" / "supabase", supabase_root)
        _run(["supabase", "start"], cwd=supabase_root)
        supabase_started = True
        supabase_env = _supabase_environment(supabase_root)
        _publish_catalog(
            matcher_root=matcher_root,
            workspace_root=workspace_root,
            supabase_env=supabase_env,
        )
        from sciona.catalog_audit import audit_catalog

        audit_results = audit_catalog(supabase_env["DB_URL"])
        unsafe_served = audit_results["totals"][
            "published_without_audit_ready_fqdns"
        ]
        if unsafe_served:
            raise RuntimeError(
                "Catalog serves atoms that do not satisfy the audit policy: "
                f"{unsafe_served}"
            )
        if args.audit_output is not None:
            args.audit_output.parent.mkdir(parents=True, exist_ok=True)
            args.audit_output.write_text(
                json.dumps(audit_results, indent=2, sort_keys=True) + "\n"
            )
        if args.audit_only:
            print(json.dumps({"status": "audited", "audit": audit_results}, indent=2))
            return 0

        wheels = _build_wheels(
            matcher_root=matcher_root,
            workspace_root=workspace_root,
            wheel_dir=wheel_dir,
        )
        artifact_server, artifact_thread, cert_path = _start_artifact_server(
            wheel_dir, work_dir
        )
        _wait_for_url(
            f"https://localhost:{artifact_server.server_port}/",
            cafile=cert_path,
        )
        _attach_wheels(
            wheels=wheels,
            artifact_port=artifact_server.server_port,
            supabase_env=supabase_env,
        )
        _assert_e2e_candidates_are_served(supabase_env)
        embedding_results = _exercise_embedding_refresh(supabase_env)
        api_port = _free_port()
        api_process = _start_api(
            matcher_root=matcher_root,
            supabase_env=supabase_env,
            api_port=api_port,
        )
        api_url = f"http://127.0.0.1:{api_port}"
        _wait_for_url(f"{api_url}/openapi.json")
        retrieval_results = _exercise_intent_retrieval(
            api_url=api_url,
            matcher_root=matcher_root,
        )
        python = _create_cold_matcher_environment(
            matcher_root=matcher_root,
            wheel_dir=wheel_dir,
            cold_venv=work_dir / "cold-venv",
        )
        results = _exercise_cold_runtime(
            python=python,
            api_url=api_url,
            cert_path=cert_path,
            work_dir=work_dir,
        )
        print(
            json.dumps(
                {
                    "status": "passed",
                    "audit": audit_results,
                    "results": results,
                    "embeddings": embedding_results,
                    "retrieval": retrieval_results,
                },
                indent=2,
            )
        )
        return 0
    finally:
        if api_process is not None:
            api_process.terminate()
            try:
                api_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api_process.kill()
                api_process.wait(timeout=5)
        if artifact_server is not None:
            artifact_server.shutdown()
        if artifact_thread is not None:
            artifact_thread.join(timeout=5)
        if supabase_started:
            _run(["supabase", "stop", "--no-backup"], cwd=supabase_root)
        if args.keep_workdir:
            print(f"Kept staging workdir: {work_dir}")
        elif temp_context is not None:
            temp_context.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
