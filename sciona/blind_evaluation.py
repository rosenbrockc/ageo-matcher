"""Cross-disciplinary blind retrieval, build, and correctness evaluation."""

from __future__ import annotations

import asyncio
import ast
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from sciona.deterministic_builder import build_catalog_artifact
from sciona.edf import read_edf_channel
from sciona.open_datasets import (
    read_dimacs_graph,
    read_uci_gps_points,
    read_wfdb_212_channel,
    read_wfdb_annotations,
)
from sciona.provider_runtime import ProviderInstaller, RemoteCatalogClient


class BlindPromptVariant(BaseModel):
    variant: Literal["explicit", "masked", "nearby_domain"]
    query: str
    acceptable_artifacts: list[str] = Field(min_length=1)
    accuracy_required: bool = True


class BlindTask(BaseModel):
    task_id: str
    discipline: str
    evaluator: Literal[
        "ecg_rate",
        "dijkstra",
        "wgs84_roundtrip",
        "linear_state_prediction",
        "mit_bih_ecg_rate",
        "dimacs_dijkstra",
        "uci_wgs84_ecef",
        "uci_wgs84_roundtrip",
        "uci_linear_motion",
    ]
    dataset_id: str | None = None
    function_name: str = "solve"
    contract: str
    variants: list[BlindPromptVariant] = Field(min_length=3)


class BlindSuite(BaseModel):
    schema_version: str
    suite_id: str
    tasks: list[BlindTask] = Field(min_length=3)


def load_blind_suite(path: Path) -> BlindSuite:
    suite = BlindSuite.model_validate_json(path.read_text())
    disciplines = {task.discipline for task in suite.tasks}
    if len(disciplines) < 3:
        raise ValueError("Blind suite must cover at least three disciplines")
    for task in suite.tasks:
        variants = {variant.variant for variant in task.variants}
        if variants != {"explicit", "masked", "nearby_domain"}:
            raise ValueError(
                f"Task {task.task_id!r} must define explicit, masked, and nearby_domain variants"
            )
    return suite


def _load_solution(path: Path, function_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(
        f"sciona_blind_candidate_{path.stem}", path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load generated solution {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, function_name)


def _evaluate_dijkstra(function: Any) -> dict[str, Any]:
    from scipy.sparse import csr_array

    graph = csr_array(
        [
            [0.0, 2.0, 8.0, 0.0],
            [0.0, 0.0, 1.0, 7.0],
            [0.0, 0.0, 0.0, 3.0],
            [0.0, 0.0, 0.0, 0.0],
        ]
    )
    expected = np.array([0.0, 2.0, 3.0, 6.0])
    actual = np.asarray(function(graph, 0), dtype=float)
    error = float(np.max(np.abs(actual - expected)))
    return {"passed": actual.shape == expected.shape and error <= 1e-12, "max_abs_error": error}


def _evaluate_wgs84_roundtrip(function: Any) -> dict[str, Any]:
    lat = np.array([0.0, 35.0844, -33.8688, 51.5074])
    lon = np.array([0.0, -106.6504, 151.2093, -0.1278])
    alt = np.array([0.0, 1619.0, 58.0, 35.0])
    result = function(lat, lon, alt)
    if not isinstance(result, tuple) or len(result) != 3:
        return {"passed": False, "error": "expected latitude, longitude, altitude tuple"}
    actual_lat, actual_lon, actual_alt = (np.asarray(value) for value in result)
    lon_error = np.abs((actual_lon - lon + 180.0) % 360.0 - 180.0)
    error = float(
        max(
            np.max(np.abs(actual_lat - lat)),
            np.max(lon_error),
            np.max(np.abs(actual_alt - alt)),
        )
    )
    return {"passed": error <= 1e-5, "max_abs_roundtrip_error": error}


def _evaluate_linear_state_prediction(function: Any) -> dict[str, Any]:
    initial_x = np.array([[10.0], [2.0]])
    initial_p = np.eye(2)
    transition = np.array([[1.0, 0.5], [0.0, 1.0]])
    observation = np.array([[1.0, 0.0]])
    process_noise = np.eye(2) * 0.01
    measurement_noise = np.array([[0.2]])
    actual = float(
        function(
            initial_x,
            initial_p,
            transition,
            observation,
            process_noise,
            measurement_noise,
            0.5,
        )
    )
    error = abs(actual - 11.0)
    return {"passed": error <= 1e-12, "absolute_prediction_error": error}


def _evaluate_ecg_rate(function: Any, *, edf_path: Path | None) -> dict[str, Any]:
    if edf_path is None:
        return {"passed": False, "error": "real ECG evaluation requires --ecg-edf"}
    signal, sampling_rate = read_edf_channel(
        edf_path, "ECG1-ECG2", start_seconds=3600.0, duration_seconds=300.0
    )
    reference, reference_rate = read_edf_channel(
        edf_path, "Pulse", start_seconds=3600.0, duration_seconds=300.0
    )
    indices, rates = function(np.asarray(signal, dtype=float), float(sampling_rate))
    indices = np.asarray(indices, dtype=float).reshape(-1)
    rates = np.asarray(rates, dtype=float).reshape(-1)
    if indices.size != rates.size or indices.size < 10:
        return {"passed": False, "error": "insufficient aligned rate estimates"}
    aligned = np.interp(
        indices / sampling_rate,
        np.arange(reference.size, dtype=float) / reference_rate,
        reference,
    )
    valid = np.isfinite(indices) & np.isfinite(rates) & np.isfinite(aligned)
    valid &= (rates >= 30.0) & (rates <= 220.0)
    valid &= (aligned >= 30.0) & (aligned <= 220.0)
    if int(valid.sum()) < 10:
        return {"passed": False, "error": "insufficient physiologically valid estimates"}
    mae = float(np.mean(np.abs(rates[valid] - aligned[valid])))
    median_error = float(abs(np.median(rates[valid]) - np.median(aligned[valid])))
    return {
        "passed": mae <= 12.0 and median_error <= 8.0,
        "comparison_count": int(valid.sum()),
        "mae_bpm": mae,
        "median_error_bpm": median_error,
    }


def _evaluate_mit_bih_ecg_rate(function: Any, root: Path) -> dict[str, Any]:
    signal, sampling_rate = read_wfdb_212_channel(
        root, "100", channel=0, duration_seconds=300.0
    )
    reference_beats = read_wfdb_annotations(root, "100")
    reference_beats = reference_beats[
        reference_beats < int(round(300.0 * sampling_rate))
    ]
    indices, rates = function(signal, sampling_rate)
    indices = np.asarray(indices, dtype=float).reshape(-1)
    rates = np.asarray(rates, dtype=float).reshape(-1)
    if indices.size != rates.size or indices.size < 100:
        return {"passed": False, "error": "insufficient aligned beat-rate estimates"}
    reference_positions = reference_beats[1:].astype(float)
    reference_rates = 60.0 * sampling_rate / np.diff(reference_beats)
    aligned_rates = np.interp(indices, reference_positions, reference_rates)
    valid = np.isfinite(indices) & np.isfinite(rates) & np.isfinite(aligned_rates)
    valid &= (rates >= 30.0) & (rates <= 220.0)
    if int(valid.sum()) < 100:
        return {"passed": False, "error": "insufficient physiologically valid estimates"}
    detected = np.sort(np.rint(indices[valid]).astype(np.int64))
    tolerance = int(round(0.1 * sampling_rate))

    def alignment_score(reference: np.ndarray) -> tuple[float, float]:
        matched = 0
        reference_index = 0
        for sample in detected:
            while (
                reference_index < reference.size
                and reference[reference_index] < sample - tolerance
            ):
                reference_index += 1
            if (
                reference_index < reference.size
                and abs(int(reference[reference_index]) - int(sample)) <= tolerance
            ):
                matched += 1
                reference_index += 1
        return matched / detected.size, matched / reference.size

    endpoint_score = alignment_score(reference_beats[1:])
    reference_midpoints = (
        reference_beats[:-1] + np.diff(reference_beats) // 2
    )
    midpoint_score = alignment_score(reference_midpoints)
    alignment = (
        "interval_midpoint"
        if min(midpoint_score) > min(endpoint_score)
        else "interval_endpoint"
    )
    precision, recall = max(
        (endpoint_score, midpoint_score), key=lambda score: min(score)
    )
    mae = float(np.mean(np.abs(rates[valid] - aligned_rates[valid])))
    median_error = float(
        abs(np.median(rates[valid]) - np.median(reference_rates))
    )
    return {
        "passed": (
            mae <= 10.0
            and median_error <= 5.0
            and precision >= 0.9
            and recall >= 0.9
        ),
        "comparison_count": int(valid.sum()),
        "reference_beat_count": int(reference_beats.size),
        "mae_bpm": mae,
        "median_error_bpm": median_error,
        "beat_precision": precision,
        "beat_recall": recall,
        "rate_alignment": alignment,
    }


def _heap_distances(graph: Any, source: int) -> np.ndarray:
    import heapq

    distances = np.full(graph.shape[0], np.inf)
    distances[source] = 0.0
    queue = [(0.0, source)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance != distances[node]:
            continue
        start, stop = graph.indptr[node], graph.indptr[node + 1]
        for target, weight in zip(
            graph.indices[start:stop], graph.data[start:stop], strict=True
        ):
            candidate = distance + float(weight)
            if candidate < distances[target]:
                distances[target] = candidate
                heapq.heappush(queue, (candidate, int(target)))
    return distances


def _evaluate_dimacs_dijkstra(function: Any, root: Path) -> dict[str, Any]:
    graph = read_dimacs_graph(root / "rome99.gr")
    expected = _heap_distances(graph, 0)
    actual = np.asarray(function(graph, 0), dtype=float).reshape(-1)
    if actual.shape != expected.shape:
        return {"passed": False, "error": "distance vector shape mismatch"}
    finite = np.isfinite(expected)
    error = float(np.max(np.abs(actual[finite] - expected[finite])))
    infinity_match = bool(np.array_equal(np.isinf(actual), np.isinf(expected)))
    return {
        "passed": error <= 1e-12 and infinity_match,
        "node_count": int(graph.shape[0]),
        "edge_count": int(graph.nnz),
        "max_abs_error": error,
        "infinity_mask_matches": infinity_match,
    }


def _uci_track_points(root: Path, *, limit: int = 256) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows = read_uci_gps_points(root)
    track_id = rows[0]["track_id"]
    selected = [row for row in rows if row["track_id"] == track_id][:limit]
    lat = np.asarray([float(row["latitude"]) for row in selected])
    lon = np.asarray([float(row["longitude"]) for row in selected])
    times = np.asarray(
        [
            np.datetime64(row["time"].replace(" ", "T"), "s").astype(np.int64)
            for row in selected
        ],
        dtype=float,
    )
    return lat, lon, times


def _evaluate_uci_wgs84_roundtrip(function: Any, root: Path) -> dict[str, Any]:
    lat, lon, _ = _uci_track_points(root)
    alt = np.zeros_like(lat)
    result = function(lat, lon, alt)
    if not isinstance(result, tuple) or len(result) != 3:
        return {"passed": False, "error": "expected latitude, longitude, altitude tuple"}
    actual_lat, actual_lon, actual_alt = (np.asarray(value) for value in result)
    lon_error = np.abs((actual_lon - lon + 180.0) % 360.0 - 180.0)
    error = float(
        max(
            np.max(np.abs(actual_lat - lat)),
            np.max(lon_error),
            np.max(np.abs(actual_alt - alt)),
        )
    )
    return {
        "passed": error <= 1e-5,
        "point_count": int(lat.size),
        "max_abs_roundtrip_error": error,
    }


def _evaluate_uci_wgs84_ecef(function: Any, root: Path) -> dict[str, Any]:
    lat, lon, _ = _uci_track_points(root)
    alt = np.zeros_like(lat)
    result = function(lat, lon, alt)
    if not isinstance(result, tuple) or len(result) != 3:
        return {"passed": False, "error": "expected Earth-centered x, y, z tuple"}
    actual_x, actual_y, actual_z = (np.asarray(value, dtype=float) for value in result)
    semi_major = 6378137.0
    eccentricity_squared = 6.69437999014e-3
    lat_radians = np.deg2rad(lat)
    lon_radians = np.deg2rad(lon)
    prime_vertical = semi_major / np.sqrt(
        1.0 - eccentricity_squared * np.sin(lat_radians) ** 2
    )
    expected_x = (prime_vertical + alt) * np.cos(lat_radians) * np.cos(lon_radians)
    expected_y = (prime_vertical + alt) * np.cos(lat_radians) * np.sin(lon_radians)
    expected_z = (
        prime_vertical * (1.0 - eccentricity_squared) + alt
    ) * np.sin(lat_radians)
    error = float(
        max(
            np.max(np.abs(actual_x - expected_x)),
            np.max(np.abs(actual_y - expected_y)),
            np.max(np.abs(actual_z - expected_z)),
        )
    )
    return {
        "passed": error <= 1e-3,
        "point_count": int(lat.size),
        "max_abs_error_meters": error,
    }


def _uci_motion_windows(root: Path) -> list[tuple[float, float, float, float]]:
    lat, lon, times = _uci_track_points(root, limit=64)
    latitude_radians = np.deg2rad(float(np.mean(lat)))
    east = np.deg2rad(lon - lon[0]) * 6378137.0 * np.cos(latitude_radians)
    windows: list[tuple[float, float, float, float]] = []
    for index in range(1, len(east) - 1):
        previous_dt = times[index] - times[index - 1]
        next_dt = times[index + 1] - times[index]
        if previous_dt <= 0 or next_dt <= 0:
            continue
        velocity = (east[index] - east[index - 1]) / previous_dt
        windows.append(
            (
                float(east[index]),
                float(velocity),
                float(next_dt),
                float(east[index + 1]),
            )
        )
    return windows


def _evaluate_uci_linear_motion(function: Any, root: Path) -> dict[str, Any]:
    predictions: list[float] = []
    expected: list[float] = []
    for position, velocity, dt, target in _uci_motion_windows(root):
        transition = np.array([[1.0, dt], [0.0, 1.0]])
        actual = function(
            np.array([[position], [velocity]]),
            np.eye(2),
            transition,
            np.array([[1.0, 0.0]]),
            np.eye(2) * 0.01,
            np.array([[0.2]]),
            dt,
        )
        predictions.append(float(actual))
        expected.append(target)
    if not predictions:
        return {"passed": False, "error": "no valid positive-time motion windows"}
    errors = np.abs(np.asarray(predictions) - np.asarray(expected))
    mae = float(np.mean(errors))
    return {
        "passed": mae <= 15.0,
        "window_count": len(predictions),
        "mae_meters": mae,
        "max_error_meters": float(np.max(errors)),
    }


def evaluate_solution(
    task: BlindTask,
    solution_path: Path,
    *,
    edf_path: Path | None = None,
    dataset_roots: dict[str, Path] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        function = _load_solution(solution_path, task.function_name)
        dataset_root = (
            (dataset_roots or {}).get(task.dataset_id or "")
            if task.dataset_id
            else None
        )
        if task.dataset_id and dataset_root is None:
            raise ValueError(f"Dataset {task.dataset_id!r} was not provided")
        if task.evaluator == "dijkstra":
            result = _evaluate_dijkstra(function)
        elif task.evaluator == "wgs84_roundtrip":
            result = _evaluate_wgs84_roundtrip(function)
        elif task.evaluator == "linear_state_prediction":
            result = _evaluate_linear_state_prediction(function)
        elif task.evaluator == "ecg_rate":
            result = _evaluate_ecg_rate(function, edf_path=edf_path)
        elif task.evaluator == "mit_bih_ecg_rate":
            result = _evaluate_mit_bih_ecg_rate(function, dataset_root)
        elif task.evaluator == "dimacs_dijkstra":
            result = _evaluate_dimacs_dijkstra(function, dataset_root)
        elif task.evaluator == "uci_wgs84_roundtrip":
            result = _evaluate_uci_wgs84_roundtrip(function, dataset_root)
        elif task.evaluator == "uci_wgs84_ecef":
            result = _evaluate_uci_wgs84_ecef(function, dataset_root)
        else:
            result = _evaluate_uci_linear_motion(function, dataset_root)
    except Exception as exc:
        result = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
    result["runtime_seconds"] = time.monotonic() - started
    return result


async def evaluate_catalog_suite(
    suite: BlindSuite,
    *,
    client: RemoteCatalogClient,
    output_dir: Path,
    installer: ProviderInstaller | None = None,
    edf_path: Path | None = None,
    dataset_roots: dict[str, Path] | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    provider_installer = installer or ProviderInstaller()
    reports: list[dict[str, Any]] = []
    for task in suite.tasks:
        for variant in task.variants:
            search_started = time.monotonic()
            candidates = await client.search_artifacts(variant.query, limit=top_k)
            search_seconds = time.monotonic() - search_started
            ranked = [candidate.fqdn for candidate in candidates]
            ranks = [
                ranked.index(fqdn) + 1
                for fqdn in variant.acceptable_artifacts
                if fqdn in ranked
            ]
            rank = min(ranks) if ranks else None
            solution_path = output_dir / task.task_id / variant.variant / "solution.py"
            build_started = time.monotonic()
            build_error = ""
            build_result = None
            selected = None
            selected_ok = False
            installs_before = len(provider_installer.installed_distributions)
            try:
                selected = await client.select_artifact(
                    variant.query, limit=max(top_k, 40)
                )
                selected_ok = selected.fqdn in variant.acceptable_artifacts
                build_result = await build_catalog_artifact(
                    client=client,
                    artifact_fqdn=selected.fqdn,
                    output_path=solution_path,
                    function_name=task.function_name,
                    installer=provider_installer,
                )
            except Exception as exc:
                build_error = f"{type(exc).__name__}: {exc}"
            build_seconds = time.monotonic() - build_started
            provider_installs = provider_installer.installed_distributions[
                installs_before:
            ]
            correctness = (
                evaluate_solution(
                    task,
                    solution_path,
                    edf_path=edf_path,
                    dataset_roots=dataset_roots,
                )
                if build_result is not None
                else {"passed": False, "error": build_error}
            )
            if not variant.accuracy_required and build_result is not None:
                correctness["accuracy_required"] = False
                correctness["passed"] = "error" not in correctness
            reports.append(
                {
                    "task_id": task.task_id,
                    "discipline": task.discipline,
                    "dataset_id": task.dataset_id,
                    "variant": variant.variant,
                    "query": variant.query,
                    "rank": rank,
                    "reciprocal_rank": 0.0 if rank is None else 1.0 / rank,
                    "top_k_hit": rank is not None,
                    "selected_artifact": selected.fqdn if selected else "",
                    "selection_passed": selected_ok,
                    "selected_atoms": list(build_result.selected_fqdns) if build_result else [],
                    "leaf_install_count": len(build_result.selected_fqdns) if build_result else 0,
                    "bound_leaf_count": len(build_result.selected_fqdns) if build_result else 0,
                    "provider_installs": provider_installs,
                    "cold_install": bool(provider_installs),
                    "search_seconds": search_seconds,
                    "build_seconds": build_seconds,
                    "correctness": correctness,
                }
            )
    required = [
        row
        for row in reports
        if next(
            variant.accuracy_required
            for task in suite.tasks
            if task.task_id == row["task_id"]
            for variant in task.variants
            if variant.variant == row["variant"]
        )
    ]
    return {
        "suite_id": suite.suite_id,
        "case_count": len(reports),
        "discipline_count": len({row["discipline"] for row in reports}),
        "top_k_recall": sum(row["top_k_hit"] for row in reports) / len(reports),
        "mean_reciprocal_rank": sum(row["reciprocal_rank"] for row in reports) / len(reports),
        "passed": all(row["selection_passed"] for row in reports)
        and all(row["correctness"]["passed"] for row in required),
        "cases": reports,
    }


def evaluate_postgres_scale(
    database_url: str,
    suite: BlindSuite,
    *,
    target_rows: int = 10_500,
    top_k: int = 10,
) -> dict[str, Any]:
    """Measure PostgreSQL FTS with rollback-only multidisciplinary distractors."""
    import psycopg

    target_rows = max(target_rows, 10_001)
    target_documents = [
        (task.task_id, task.variants[0].query, task.discipline)
        for task in suite.tasks
    ]
    distractor_count = target_rows - len(target_documents)
    latencies: list[float] = []
    reciprocal_ranks: list[float] = []
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TEMP TABLE blind_catalog_scale ("
                "artifact_id text PRIMARY KEY, description text NOT NULL, discipline text NOT NULL, "
                "search_vector tsvector GENERATED ALWAYS AS "
                "(to_tsvector('english', artifact_id || ' ' || description || ' ' || discipline)) STORED"
                ") ON COMMIT DROP"
            )
            cursor.executemany(
                "INSERT INTO blind_catalog_scale (artifact_id, description, discipline) VALUES (%s, %s, %s)",
                target_documents,
            )
            cursor.executemany(
                "INSERT INTO blind_catalog_scale (artifact_id, description, discipline) VALUES (%s, %s, %s)",
                (
                    (
                        f"distractor-{index:05d}",
                        f"generic catalog operation family {index % 257} transform validate aggregate estimate",
                        f"discipline-{index % 23}",
                    )
                    for index in range(distractor_count)
                ),
            )
            cursor.execute(
                "CREATE INDEX blind_catalog_scale_fts_idx ON blind_catalog_scale USING gin(search_vector)"
            )
            cursor.execute("ANALYZE blind_catalog_scale")
            for task in suite.tasks:
                query = task.variants[0].query
                started = time.monotonic()
                cursor.execute(
                    "SELECT artifact_id FROM blind_catalog_scale, "
                    "websearch_to_tsquery('english', %s) query "
                    "WHERE search_vector @@ query "
                    "ORDER BY ts_rank_cd(search_vector, query) DESC, artifact_id LIMIT %s",
                    (query, top_k),
                )
                rows = [row[0] for row in cursor.fetchall()]
                latencies.append(time.monotonic() - started)
                rank = rows.index(task.task_id) + 1 if task.task_id in rows else None
                reciprocal_ranks.append(0.0 if rank is None else 1.0 / rank)
        connection.rollback()
    return {
        "row_count": target_rows,
        "query_count": len(target_documents),
        "top_k_recall": sum(value > 0 for value in reciprocal_ranks) / len(reciprocal_ranks),
        "mean_reciprocal_rank": sum(reciprocal_ranks) / len(reciprocal_ranks),
        "latency_ms": {
            "mean": 1000.0 * sum(latencies) / len(latencies),
            "max": 1000.0 * max(latencies),
        },
    }


def _write_agent_input(
    task: BlindTask,
    work_dir: Path,
    *,
    edf_path: Path | None,
    dataset_roots: dict[str, Path] | None,
) -> None:
    dataset_root = (
        (dataset_roots or {}).get(task.dataset_id or "") if task.dataset_id else None
    )
    if task.evaluator == "ecg_rate":
        if edf_path is None:
            raise ValueError("real ECG agent comparison requires --ecg-edf")
        signal, sampling_rate = read_edf_channel(
            edf_path, "ECG1-ECG2", start_seconds=3600.0, duration_seconds=300.0
        )
        np.savez_compressed(
            work_dir / "input.npz",
            signal=np.asarray(signal, dtype=float),
            sampling_rate=float(sampling_rate),
        )
    elif task.evaluator == "mit_bih_ecg_rate":
        if dataset_root is None:
            raise ValueError("MIT-BIH agent comparison requires its public dataset")
        signal, sampling_rate = read_wfdb_212_channel(
            dataset_root, "100", channel=0, duration_seconds=300.0
        )
        np.savez_compressed(
            work_dir / "input.npz",
            signal=signal,
            sampling_rate=float(sampling_rate),
        )
    elif task.evaluator in {"dijkstra", "dimacs_dijkstra"}:
        if task.evaluator == "dimacs_dijkstra":
            if dataset_root is None:
                raise ValueError("DIMACS agent comparison requires its public dataset")
            graph = read_dimacs_graph(dataset_root / "rome99.gr")[:128, :128].toarray()
        else:
            graph = np.array(
                [
                    [0.0, 2.0, 8.0, 0.0],
                    [0.0, 0.0, 1.0, 7.0],
                    [0.0, 0.0, 0.0, 3.0],
                    [0.0, 0.0, 0.0, 0.0],
                ]
            )
        np.savez_compressed(
            work_dir / "input.npz",
            adj_matrix=graph,
            source=0,
        )
    elif task.evaluator in {
        "wgs84_roundtrip",
        "uci_wgs84_roundtrip",
        "uci_wgs84_ecef",
    }:
        if task.evaluator in {"uci_wgs84_roundtrip", "uci_wgs84_ecef"}:
            if dataset_root is None:
                raise ValueError("UCI GPS agent comparison requires its public dataset")
            lat, lon, _ = _uci_track_points(dataset_root)
            alt = np.zeros_like(lat)
        else:
            lat = np.array([0.0, 35.0844, -33.8688, 51.5074])
            lon = np.array([0.0, -106.6504, 151.2093, -0.1278])
            alt = np.array([0.0, 1619.0, 58.0, 35.0])
        np.savez_compressed(
            work_dir / "input.npz",
            lat=lat,
            lon=lon,
            alt=alt,
        )
    else:
        if task.evaluator == "uci_linear_motion":
            if dataset_root is None:
                raise ValueError("UCI motion comparison requires its public dataset")
            position, velocity, dt, _ = _uci_motion_windows(dataset_root)[0]
        else:
            position, velocity, dt = 10.0, 2.0, 0.5
        np.savez_compressed(
            work_dir / "input.npz",
            initial_x=np.array([[position], [velocity]]),
            initial_P=np.eye(2),
            A=np.array([[1.0, dt], [0.0, 1.0]]),
            H=np.array([[1.0, 0.0]]),
            Q=np.eye(2) * 0.01,
            R=np.array([[0.2]]),
            dt=dt,
        )


def _agent_signature(task: BlindTask) -> str:
    if task.evaluator in {"ecg_rate", "mit_bih_ecg_rate"}:
        return "solve(signal: numpy.ndarray, sampling_rate: float) -> tuple[numpy.ndarray, numpy.ndarray]"
    if task.evaluator in {"dijkstra", "dimacs_dijkstra"}:
        return "solve(adj_matrix: scipy.sparse.csr_array, source: int) -> numpy.ndarray"
    if task.evaluator in {
        "wgs84_roundtrip",
        "uci_wgs84_roundtrip",
        "uci_wgs84_ecef",
    }:
        result_names = (
            "ECEF x, y, z arrays"
            if task.evaluator == "uci_wgs84_ecef"
            else "latitude, longitude, altitude arrays"
        )
        return (
            "solve(lat: numpy.ndarray, lon: numpy.ndarray, alt: numpy.ndarray) "
            f"-> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray] ({result_names})"
        )
    return "solve(initial_x, initial_P, A, H, Q, R, dt: float) -> float"


def _agent_prompt(task: BlindTask, query: str, *, assisted: bool) -> str:
    shared = f"""Implement `solution.py` in the current directory.

It must export `{_agent_signature(task)}`.

Contract: {task.contract}

`input.npz` contains one smoke-test input but no reference answer. Do not read
outside the current directory or use the internet. Finish only after the module
loads and runs on that input.
"""
    if assisted:
        return shared + f"""
Use the deterministic catalog tool by running:

    ./sciona-build {shlex.quote(query)}

The tool selects a published CDG, installs its bound provider leaves, and writes
`solution.py`. Do not replace or modify the generated algorithm.
"""
    return shared + """
Develop the algorithm yourself using Python, NumPy, and SciPy. Do not import or
inspect Sciona or any packaged domain-specific implementation.
"""


def _write_agent_tool(work_dir: Path, python: Path, api_url: str, task: BlindTask) -> None:
    sciona = python.parent / "sciona"
    path = work_dir / "sciona-build"
    path.write_text(
        "#!/bin/sh\nset -eu\n"
        f"exec {shlex.quote(str(sciona))} catalog build \"$@\" "
        f"--output solution.py --function-name {shlex.quote(task.function_name)} "
        f"--api-url {shlex.quote(api_url)}\n"
    )
    path.chmod(0o755)


def _run_codex_agent(
    *,
    model: str,
    prompt: str,
    work_dir: Path,
    env: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    command = [
        "codex",
        "exec",
        "--ephemeral",
        "--json",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "--model",
        model,
        "--config",
        'model_reasoning_effort="low"',
        "--cd",
        str(work_dir),
        prompt,
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=work_dir,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        error = ""
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = str(exc.stdout or "")
        stderr = str(exc.stderr or "")
        error = f"agent timed out after {timeout} seconds"
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    usage: dict[str, int] = {}
    for event in events:
        if event.get("type") == "turn.completed":
            usage = {
                key: int(value) for key, value in (event.get("usage") or {}).items()
            }
    (work_dir / "agent.jsonl").write_text(stdout)
    (work_dir / "agent.stderr.txt").write_text(stderr)
    return {
        "model": model,
        "exit_code": exit_code,
        "wall_time_seconds": time.monotonic() - started,
        "usage": usage,
        "error": error,
    }


def _scratch_source_allowed(path: Path) -> tuple[bool, list[str]]:
    if not path.is_file():
        return False, ["solution.py missing"]
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    forbidden = sorted(imported & {"sciona", "biosppy", "heartpy", "neurokit2", "wfdb"})
    return not forbidden, forbidden


def run_agent_comparison(
    suite: BlindSuite,
    *,
    api_url: str,
    tool_python: Path,
    output_dir: Path,
    small_model: str,
    large_model: str,
    edf_path: Path | None,
    dataset_roots: dict[str, Path] | None = None,
    timeout: float = 600.0,
    repetitions: int = 1,
) -> dict[str, Any]:
    """Compare assisted-small, scratch-small, and scratch-large agents."""
    if repetitions < 1:
        raise ValueError("Agent comparison repetitions must be positive")
    env = os.environ.copy()
    env["SCIONA_API_URL"] = api_url
    reports: list[dict[str, Any]] = []
    for trial in range(1, repetitions + 1):
        for task in suite.tasks:
            explicit = next(
                variant for variant in task.variants if variant.variant == "explicit"
            )
            arms = (
                ("small_sciona", small_model, True),
                ("small_scratch", small_model, False),
                ("large_scratch", large_model, False),
            )
            for arm_name, model, assisted in arms:
                work_dir = output_dir / f"trial-{trial}" / task.task_id / arm_name
                work_dir.mkdir(parents=True, exist_ok=True)
                for stale_name in ("solution.py", "agent.jsonl", "agent.stderr.txt"):
                    (work_dir / stale_name).unlink(missing_ok=True)
                _write_agent_input(
                    task,
                    work_dir,
                    edf_path=edf_path,
                    dataset_roots=dataset_roots,
                )
                if assisted:
                    _write_agent_tool(work_dir, tool_python, api_url, task)
                agent = _run_codex_agent(
                    model=model,
                    prompt=_agent_prompt(task, explicit.query, assisted=assisted),
                    work_dir=work_dir,
                    env=env,
                    timeout=timeout,
                )
                allowed, forbidden = (
                    (True, [])
                    if assisted
                    else _scratch_source_allowed(work_dir / "solution.py")
                )
                correctness = (
                    evaluate_solution(
                        task,
                        work_dir / "solution.py",
                        edf_path=edf_path,
                        dataset_roots=dataset_roots,
                    )
                    if allowed
                    else {
                        "passed": False,
                        "error": f"forbidden imports: {forbidden}",
                    }
                )
                reports.append(
                    {
                        "trial": trial,
                        "task_id": task.task_id,
                        "discipline": task.discipline,
                        "arm": arm_name,
                        "assisted": assisted,
                        **agent,
                        "correctness": correctness,
                    }
                )
    summaries: dict[str, dict[str, Any]] = {}
    for arm in ("small_sciona", "small_scratch", "large_scratch"):
        rows = [row for row in reports if row["arm"] == arm]
        def mean_usage(key: str) -> float:
            return sum(int(row["usage"].get(key, 0)) for row in rows) / len(rows)

        input_tokens = mean_usage("input_tokens")
        output_tokens = mean_usage("output_tokens")
        summaries[arm] = {
            "pass_rate": sum(bool(row["correctness"]["passed"]) for row in rows)
            / len(rows),
            "mean_wall_time_seconds": sum(row["wall_time_seconds"] for row in rows)
            / len(rows),
            "mean_total_tokens": input_tokens + output_tokens,
            "mean_input_tokens": input_tokens,
            "mean_cached_input_tokens": mean_usage("cached_input_tokens"),
            "mean_output_tokens": output_tokens,
            "mean_reasoning_output_tokens": mean_usage("reasoning_output_tokens"),
        }
    return {"arms": summaries, "runs": reports}


def run_catalog_suite(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(evaluate_catalog_suite(**kwargs))


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
