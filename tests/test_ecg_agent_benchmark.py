"""Unit tests for the ECG agent-comparison benchmark."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_ecg_agent_benchmark.py"
SPEC = importlib.util.spec_from_file_location("run_ecg_agent_benchmark", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_baseline_import_policy_rejects_packaged_ecg_implementations() -> None:
    allowed, forbidden = MODULE._baseline_imports_are_allowed(
        "import numpy as np\nfrom scipy.signal import find_peaks\n"
    )
    assert allowed
    assert forbidden == []

    allowed, forbidden = MODULE._baseline_imports_are_allowed(
        "from sciona.atoms.signal_processing import biosppy\n"
    )
    assert not allowed
    assert forbidden == ["sciona"]


def test_codex_command_is_ephemeral_and_model_explicit(tmp_path: Path) -> None:
    command = MODULE._codex_command(
        model="small-model", work_dir=tmp_path, prompt="build it"
    )
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--model" in command
    assert command[command.index("--model") + 1] == "small-model"
    assert command[command.index("--sandbox") + 1] == "danger-full-access"
    assert "--search" not in command


def test_tool_wrappers_use_minimal_installed_cli(tmp_path: Path) -> None:
    python = tmp_path / "venv" / "bin" / "python"
    MODULE._write_tool_wrappers(
        tmp_path,
        python,
        "http://127.0.0.1:8123",
        function_name="estimate_rate",
    )

    search = (tmp_path / "sciona-search-artifacts").read_text()
    build = (tmp_path / "sciona-build").read_text()
    assert f"{python.parent / 'sciona'} catalog search-artifacts" in search
    assert f"{python.parent / 'sciona'} catalog build" in build
    assert "--function-name estimate_rate" in build
    assert "python -m sciona.cli" not in search


def test_benchmark_fails_when_agent_or_evaluation_fails() -> None:
    assert MODULE._benchmark_passed(
        [{"exit_code": 0, "evaluation": {"passed": True}}]
    )
    assert not MODULE._benchmark_passed(
        [{"exit_code": 1, "evaluation": {"passed": True}}]
    )
    assert not MODULE._benchmark_passed(
        [{"exit_code": 0, "evaluation": {"passed": False}}]
    )
