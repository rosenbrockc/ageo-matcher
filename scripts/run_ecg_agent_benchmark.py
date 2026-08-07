#!/usr/bin/env python3
"""Compare small Sciona-assisted and unassisted agents on real ECG data."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ecg_data_e2e_runtime import _read_edf_channel


TASK_TEMPLATE = """Implement `solution.py` in the current directory.

It must export:

    {function_name}(signal: numpy.ndarray, sampling_rate: float)
        -> tuple[numpy.ndarray, numpy.ndarray]

The first returned array contains sample indices for the rate estimates. The
second contains heart rate in beats per minute. Both arrays must be finite,
one-dimensional, aligned, and contain at least ten values for the supplied
five-minute `input.npz` recording. Use `input.npz` only for smoke testing; it
contains `signal` and scalar `sampling_rate`, but no reference answer.

Do not read files outside the current directory and do not use the internet.
Finish only after `solution.py` runs successfully with the supplied input.
"""

BASELINE_RULES = """
Use Python, NumPy, and SciPy only. Do not import or inspect Sciona, BioSPPy,
NeuroKit, HeartPy, or another packaged ECG/heart-rate implementation. Develop
the signal conditioning, peak detection, and rate calculation yourself.
"""

TOOL_RULES_TEMPLATE = """
Use Sciona's deterministic catalog builder rather than implementing the algorithm
yourself. The following agent tools are available in this directory:

    ./sciona-search-artifacts "natural-language problem"
    ./sciona-build "natural-language problem"

Run `./sciona-build {query}`. It selects a compatible published CDG, installs only
its bound providers, and writes `solution.py`. Do not replace or modify the
generated algorithm. Use the supplied input only for a smoke test.
"""

FORBIDDEN_BASELINE_IMPORTS = {
    "biosppy",
    "heartpy",
    "neurokit2",
    "sciona",
    "wfdb",
}


def _write_input(work_dir: Path, signal: np.ndarray, sampling_rate: float) -> None:
    np.savez_compressed(
        work_dir / "input.npz",
        signal=np.asarray(signal, dtype=np.float64),
        sampling_rate=np.asarray(float(sampling_rate)),
    )


def _write_tool_wrappers(
    work_dir: Path,
    python: Path,
    api_url: str,
    *,
    function_name: str,
) -> None:
    sciona = python.parent / "sciona"
    commands = {
        "sciona-search-artifacts": (
            f"exec {sciona!s} catalog search-artifacts \"$@\" "
            f"--limit 40 --api-url {api_url!s}\n"
        ),
        "sciona-build": (
            f"exec {sciona!s} catalog build \"$@\" "
            f"--output solution.py --function-name {shlex.quote(function_name)} "
            f"--api-url {api_url!s}\n"
        ),
    }
    for name, command in commands.items():
        path = work_dir / name
        path.write_text("#!/bin/sh\nset -eu\n" + command)
        path.chmod(0o755)


def _codex_command(*, model: str, work_dir: Path, prompt: str) -> list[str]:
    return [
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


def _run_agent(
    *,
    name: str,
    model: str,
    prompt: str,
    work_dir: Path,
    env: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    command = _codex_command(model=model, work_dir=work_dir, prompt=prompt)
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=work_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    elapsed = time.monotonic() - started
    events: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    usage: dict[str, int] = {}
    for event in events:
        if event.get("type") == "turn.completed":
            usage = {key: int(value) for key, value in event.get("usage", {}).items()}
    (work_dir / "agent.jsonl").write_text(completed.stdout)
    (work_dir / "agent.stderr.txt").write_text(completed.stderr)
    return {
        "name": name,
        "model": model,
        "exit_code": completed.returncode,
        "wall_time_seconds": elapsed,
        "usage": usage,
        "event_count": len(events),
    }


def _baseline_imports_are_allowed(source: str) -> tuple[bool, list[str]]:
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    forbidden = sorted(imported & FORBIDDEN_BASELINE_IMPORTS)
    return not forbidden, forbidden


def _execute_solution(
    python: Path, work_dir: Path, timeout: float, *, function_name: str
) -> Path:
    output = work_dir / "prediction.npz"
    script = """
import importlib.util
import numpy as np
from pathlib import Path

root = Path.cwd()
spec = importlib.util.spec_from_file_location("candidate_solution", root / "solution.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
data = np.load(root / "input.npz")
function = getattr(module, FUNCTION_NAME)
indices, rates = function(
    np.asarray(data["signal"], dtype=float),
    float(data["sampling_rate"]),
)
np.savez(root / "prediction.npz", indices=np.asarray(indices), rates=np.asarray(rates))
"""
    subprocess.run(
        [str(python), "-c", f"FUNCTION_NAME = {function_name!r}\n" + script],
        cwd=work_dir,
        text=True,
        capture_output=True,
        check=True,
        timeout=timeout,
    )
    return output


def _evaluate(
    *,
    python: Path,
    work_dir: Path,
    reference: np.ndarray,
    reference_rate: float,
    sampling_rate: float,
    baseline: bool,
    function_name: str,
) -> dict[str, Any]:
    solution_path = work_dir / "solution.py"
    if not solution_path.is_file():
        return {"passed": False, "error": "solution.py was not created"}
    source = solution_path.read_text()
    if baseline:
        allowed, forbidden = _baseline_imports_are_allowed(source)
        if not allowed:
            return {"passed": False, "error": f"forbidden imports: {forbidden}"}
    else:
        tree = ast.parse(source)
        constants = {
            target.id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance((target := node.targets[0]), ast.Name)
            and target.id in {"SELECTED_ARTIFACT", "SELECTED_ATOMS"}
        }
        selected = constants.get("SELECTED_ATOMS", ())
        if not constants.get("SELECTED_ARTIFACT") or not selected:
            return {"passed": False, "error": "generated catalog provenance is missing"}
    try:
        prediction_path = _execute_solution(
            python, work_dir, timeout=60.0, function_name=function_name
        )
        prediction = np.load(prediction_path)
        indices = np.asarray(prediction["indices"], dtype=float).reshape(-1)
        rates = np.asarray(prediction["rates"], dtype=float).reshape(-1)
    except Exception as exc:
        return {"passed": False, "error": f"execution failed: {type(exc).__name__}: {exc}"}
    if indices.size != rates.size or indices.size < 10:
        return {"passed": False, "error": "prediction arrays are misaligned or too short"}
    reference_times = np.arange(reference.size, dtype=float) / reference_rate
    aligned = np.interp(indices / sampling_rate, reference_times, reference)
    valid = np.isfinite(indices) & np.isfinite(rates) & np.isfinite(aligned)
    valid &= (indices >= 0) & (rates >= 30.0) & (rates <= 220.0)
    valid &= (aligned >= 30.0) & (aligned <= 220.0)
    if int(valid.sum()) < 10:
        return {"passed": False, "error": "fewer than ten physiologically valid predictions"}
    mae = float(np.mean(np.abs(rates[valid] - aligned[valid])))
    median_error = float(abs(np.median(rates[valid]) - np.median(aligned[valid])))
    return {
        "passed": mae <= 12.0 and median_error <= 8.0,
        "comparison_count": int(valid.sum()),
        "mae_bpm": mae,
        "median_error_bpm": median_error,
        "estimated_median_bpm": float(np.median(rates[valid])),
        "reference_median_bpm": float(np.median(aligned[valid])),
        "source_bytes": len(source.encode()),
    }


def _benchmark_passed(reports: list[dict[str, Any]]) -> bool:
    return all(
        int(report.get("exit_code", 1)) == 0
        and bool((report.get("evaluation") or {}).get("passed"))
        for report in reports
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--tool-python", type=Path, required=True)
    parser.add_argument("--edf", type=Path, required=True)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--small-model", default="gpt-5.3-codex-spark")
    parser.add_argument("--large-model", default="gpt-5.6-sol")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    signal, sampling_rate = _read_edf_channel(
        args.edf, "ECG1-ECG2", start_seconds=3600.0, duration_seconds=300.0
    )
    reference, reference_rate = _read_edf_channel(
        args.edf, "Pulse", start_seconds=3600.0, duration_seconds=300.0
    )
    args.output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"SSL_CERT_FILE": str(args.cert), "SCIONA_API_URL": args.api_url})

    arms = (
        (
            "small_sciona_labeled",
            args.small_model,
            "estimate_heart_rate",
            "Detect heart rate from raw ECG signal",
            args.tool_python,
            False,
        ),
        (
            "small_sciona_masked",
            args.small_model,
            "estimate_event_rate",
            "Estimate event rate from an unlabeled sampled waveform",
            args.tool_python,
            False,
        ),
        (
            "small_scratch",
            args.small_model,
            "estimate_heart_rate",
            "",
            Path(sys.executable),
            True,
        ),
        (
            "large_scratch",
            args.large_model,
            "estimate_heart_rate",
            "",
            Path(sys.executable),
            True,
        ),
    )
    reports: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="sciona-ecg-agent-benchmark-") as temp:
        temp_root = Path(temp)
        for name, model, function_name, query, python, baseline in arms:
            work_dir = temp_root / name
            work_dir.mkdir()
            _write_input(work_dir, signal, sampling_rate)
            if not baseline:
                _write_tool_wrappers(
                    work_dir,
                    args.tool_python,
                    args.api_url,
                    function_name=function_name,
                )
            rules = (
                BASELINE_RULES
                if baseline
                else TOOL_RULES_TEMPLATE.format(query=json.dumps(query))
            )
            report = _run_agent(
                name=name,
                model=model,
                prompt=TASK_TEMPLATE.format(function_name=function_name)
                + rules
                + f"\nUse this Python interpreter for smoke tests: {python}\n",
                work_dir=work_dir,
                env=env,
                timeout=args.timeout,
            )
            report["evaluation"] = _evaluate(
                python=python,
                work_dir=work_dir,
                reference=reference,
                reference_rate=reference_rate,
                sampling_rate=sampling_rate,
                baseline=baseline,
                function_name=function_name,
            )
            report["query"] = query
            artifact_dir = args.output / name
            shutil.copytree(work_dir, artifact_dir)
            reports.append(report)

    payload = {
        "schema_version": "1.0",
        "dataset": str(args.edf),
        "window": {"start_seconds": 3600.0, "duration_seconds": 300.0},
        "arms": reports,
    }
    report_path = args.output / "report.json"
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if _benchmark_passed(reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
