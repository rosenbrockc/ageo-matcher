from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from sciona.blind_evaluation import evaluate_solution, load_blind_suite
from sciona.open_datasets import (
    fetch_open_data,
    load_open_data_registry,
    read_dimacs_graph,
    read_uci_gps_points,
    read_wfdb_212_channel,
    read_wfdb_annotations,
)


ROOT = Path(__file__).resolve().parents[1]
OPEN_MANIFEST = ROOT / "evaluations" / "open_data_blind.json"
OPEN_REGISTRY = ROOT / "evaluations" / "open_data_sources.json"


def _write_linear_gps(root: Path) -> None:
    directory = root / "GPS Trajectory"
    directory.mkdir(parents=True)
    rows = ['"id","latitude","longitude","track_id","time"']
    for index in range(64):
        rows.append(
            f'{index + 1},35.0,{-106.0 + index * 0.00001},1,'
            f'"2020-01-01 00:{index // 60:02d}:{index % 60:02d}"'
        )
    (directory / "go_track_trackspoints.csv").write_text("\n".join(rows) + "\n")


def _write_wfdb_record(root: Path) -> None:
    sample_count = 12_100
    (root / "100.hea").write_text(
        f"100 2 100 {sample_count}\n"
        "100.dat 212 200 11 1024 0 0 0 lead-a\n"
        "100.dat 212 200 11 1024 0 0 0 lead-b\n"
    )
    (root / "100.dat").write_bytes(bytes(sample_count * 3))
    annotations = bytearray()
    for _ in range(120):
        interval = 100
        annotations.extend((interval & 0xFF, (1 << 2) | (interval >> 8)))
    annotations.extend((0, 0))
    (root / "100.atr").write_bytes(annotations)


def _task(evaluator: str):
    return next(
        task
        for task in load_blind_suite(OPEN_MANIFEST).tasks
        if task.evaluator == evaluator
    )


def test_open_registry_pins_public_sources_and_integrity() -> None:
    registry = load_open_data_registry(OPEN_REGISTRY)

    assert {source.source_id for source in registry.sources} == {
        "physionet-mitdb-100",
        "dimacs-rome99",
        "uci-gps-trajectories",
    }
    assert all(source.license_spdx for source in registry.sources)
    assert all(source.citation for source in registry.sources)
    assert all(file.sha256 for source in registry.sources for file in source.files)


def test_fetch_open_data_verifies_a_cached_payload(tmp_path: Path) -> None:
    payload = tmp_path / "source.txt"
    payload.write_text("public fixture\n")
    declaration = {
        "schema_version": "1.0",
        "sources": [
            {
                "source_id": "fixture",
                "title": "Fixture",
                "landing_url": "https://example.test/fixture",
                "license_spdx": "CC0-1.0",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "citation": "Synthetic test fixture.",
                "files": [
                    {
                        "path": "payload.txt",
                        "url": payload.as_uri(),
                        "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
                        "size_bytes": payload.stat().st_size,
                    }
                ],
            }
        ],
    }
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(declaration))

    roots = fetch_open_data(
        load_open_data_registry(registry_path), cache_dir=tmp_path / "cache"
    )

    assert (roots["fixture"] / "payload.txt").read_text() == "public fixture\n"


def test_dependency_light_public_data_parsers(tmp_path: Path) -> None:
    _write_wfdb_record(tmp_path)
    signal, sampling_rate = read_wfdb_212_channel(tmp_path, "100", channel=0)
    annotations = read_wfdb_annotations(tmp_path, "100")
    (tmp_path / "graph.gr").write_text("p sp 3 2\na 1 2 4\na 2 3 7\n")
    graph = read_dimacs_graph(tmp_path / "graph.gr")
    _write_linear_gps(tmp_path)

    assert signal.shape == (12_100,)
    assert sampling_rate == 100.0
    assert np.array_equal(annotations[:3], [100, 200, 300])
    assert graph.shape == (3, 3)
    assert graph[0, 1] == 4
    assert len(read_uci_gps_points(tmp_path)) == 64


def test_open_evaluators_use_held_out_targets(tmp_path: Path) -> None:
    wfdb_root = tmp_path / "wfdb"
    wfdb_root.mkdir()
    _write_wfdb_record(wfdb_root)
    gps_root = tmp_path / "gps"
    _write_linear_gps(gps_root)
    roots = {
        "physionet-mitdb-100": wfdb_root,
        "uci-gps-trajectories": gps_root,
    }

    ecg_solution = tmp_path / "ecg.py"
    ecg_solution.write_text(
        "import numpy as np\n"
        "def solve(signal, sampling_rate):\n"
        "    indices = np.arange(100, len(signal), 100)\n"
        "    return indices, np.full(indices.shape, 60.0)\n"
    )
    ecef_solution = tmp_path / "ecef.py"
    ecef_solution.write_text(
        "import numpy as np\n"
        "def solve(lat, lon, alt):\n"
        "    a, e2 = 6378137.0, 6.69437999014e-3\n"
        "    p, l = np.deg2rad(lat), np.deg2rad(lon)\n"
        "    n = a / np.sqrt(1 - e2 * np.sin(p) ** 2)\n"
        "    return ((n + alt) * np.cos(p) * np.cos(l),\n"
        "            (n + alt) * np.cos(p) * np.sin(l),\n"
        "            (n * (1 - e2) + alt) * np.sin(p))\n"
    )
    identity_solution = tmp_path / "identity.py"
    identity_solution.write_text("def solve(lat, lon, alt):\n    return lat, lon, alt\n")
    motion_solution = tmp_path / "motion.py"
    motion_solution.write_text(
        "def solve(initial_x, initial_P, A, H, Q, R, dt):\n"
        "    return float((A @ initial_x)[0, 0])\n"
    )

    assert evaluate_solution(
        _task("mit_bih_ecg_rate"), ecg_solution, dataset_roots=roots
    )["passed"]
    assert evaluate_solution(
        _task("uci_wgs84_ecef"), ecef_solution, dataset_roots=roots
    )["passed"]
    assert not evaluate_solution(
        _task("uci_wgs84_ecef"), identity_solution, dataset_roots=roots
    )["passed"]
    assert evaluate_solution(
        _task("uci_linear_motion"), motion_solution, dataset_roots=roots
    )["passed"]
