from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from sciona.principal.evaluator import (
    _finalize_runtime_artifacts,
    _build_runtime_artifacts,
    _collect_runtime_inputs_from_frames,
)
from sciona.principal.models import NodeTelemetry


def test_build_runtime_artifacts_persists_canonical_runtime_evidence(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        json.dumps(
            {
                "node_id": "det",
                "execution_time_ms": 1.0,
                "peak_memory_bytes": 1024,
                "output_summaries": {
                    "rpeaks": {
                        "count": 3.0,
                        "outlier_fraction": 0.25,
                        "interval_median_samples": 250.0,
                    }
                },
            }
        )
        + "\n"
    )
    signal = np.sin(np.linspace(0.0, 20.0, 2000))

    artifacts = _build_runtime_artifacts(
        trace_path=trace_path,
        stdout_payload={
            "intermediates": {"rpeaks": [100.0, 350.0, 600.0]},
            "outputs": {"heart_rate": [70.0, 71.0, 69.5]},
        },
        runtime_inputs={
            "signal": list(np.linspace(0.0, 1.0, 30)),
            "sampling_rate": 21.0,
            "capnostream_value": list(np.linspace(0.0, 1.0, 30)),
            "capnostream_sampling_rate": 21.0,
            "h10_ecg_value": signal,
            "ecg_sampling_rate": 100.0,
            "h10_ecg_t": list(np.linspace(0.0, 20.0, 2000)),
        },
        invocation_summary={
            "mode": "adapter_runner",
            "dataset_root": "/tmp/synthetic_evaluation",
            "dataset_vars": {"tracker": "single"},
            "slice_start_s": 5.0,
            "slice_stop_s": 305.0,
        },
    )

    assert artifacts["runtime_inputs"]["h10_ecg_value"] is signal
    assert artifacts["signal_data"]["h10_ecg_value"] is signal
    assert artifacts["runtime_inputs"]["sampling_rate"] == 100.0
    assert artifacts["signal_data"]["sampling_rate"] == 100.0
    assert artifacts["intermediates"]["events"] == [100.0, 350.0, 600.0]
    assert artifacts["intermediate_summaries"]["rpeaks"]["outlier_fraction"] == 0.25
    assert (
        artifacts["canonical_runtime_context"]["canonical_inputs"]["sampling_rate"]["stream_id"]
        == "ecg"
    )
    assert artifacts["telemetry_summary"]["events"]["source_key"] == "rpeaks"
    assert artifacts["telemetry_summary"]["intermediates"]["rpeaks"]["outlier_fraction"] == 0.25
    assert artifacts["telemetry_summary"]["outputs"]["heart_rate"]["mean"] > 0.0
    assert artifacts["heuristics"]
    assert artifacts["heuristic_summary"]["heuristic_count"] == len(
        artifacts["heuristics"]
    )
    assert artifacts["invocation_summary"]["dataset_vars"]["tracker"] == "single"
    assert "interval_instability" in artifacts["heuristic_summary"]["heuristic_ids"]
    assert artifacts["usability_assessment"]["assessment_id"] == "runtime_usability_assessment"
    assert "quality_instability" in artifacts["usability_assessment"]["heuristic_signature"]

    evidence_path = tmp_path / "runtime_evidence.json"
    assert evidence_path.exists()
    persisted = json.loads(evidence_path.read_text())
    assert persisted["runtime_context"]["canonical_inputs"]["signal"] == "h10_ecg_value"
    assert persisted["telemetry_summary"]["events"]["source_key"] == "rpeaks"
    assert persisted["heuristics"]
    assert persisted["heuristic_summary"]["heuristic_count"] == len(
        persisted["heuristics"]
    )
    assert persisted["invocation_summary"]["slice_start_s"] == 5.0
    assert persisted["invocation_summary"]["slice_stop_s"] == 305.0
    assert persisted["usability_assessment"]["assessment_id"] == "runtime_usability_assessment"
    assert "quality_instability" in persisted["usability_assessment"]["heuristic_signature"]


def test_collect_runtime_inputs_from_frames_prefers_primary_waveform_stream() -> None:
    class _Series:
        def __init__(self, values):
            self._values = np.asarray(values)

        def to_numpy(self):
            return self._values

    class _Frame:
        def __init__(self, columns: dict[str, np.ndarray]) -> None:
            self._columns = {name: _Series(values) for name, values in columns.items()}
            self.columns = list(columns.keys())

        def __getitem__(self, key: str):
            return self._columns[key]

    frames = {
        "capnostream": _Frame(
            {
                "capnostream_t": np.linspace(0.0, 10.0, 210),
                "capnostream_value": np.linspace(0.0, 1.0, 210),
            }
        ),
        "h10_ecg": _Frame(
            {
                "h10_ecg_t": np.linspace(0.0, 10.0, 1300),
                "h10_ecg_value": np.sin(np.linspace(0.0, 30.0, 1300)),
            }
        ),
    }

    runtime_inputs = _collect_runtime_inputs_from_frames(frames)

    assert "signal" in runtime_inputs
    assert "sampling_rate" in runtime_inputs
    assert runtime_inputs["signal"].shape == runtime_inputs["h10_ecg_value"].shape
    assert runtime_inputs["sampling_rate"] > 100.0


def test_finalize_runtime_artifacts_soft_accepts_scoreable_nonzero_exit(
    tmp_path: Path,
) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        json.dumps(
            {
                "node_id": "det",
                "execution_time_ms": 1.0,
                "peak_memory_bytes": 1024,
                "output_summaries": {
                    "heart_rate": {"count": 3.0, "mean": 70.0, "std": 1.0}
                },
            }
        )
        + "\n"
    )
    artifacts = _build_runtime_artifacts(
        trace_path=trace_path,
        stdout_payload={"loss": 7.95, "outputs": {"heart_rate": [70.0, 71.0, 69.5]}},
        runtime_inputs={
            "signal": list(np.linspace(0.0, 1.0, 30)),
            "sampling_rate": 100.0,
        },
        invocation_summary={
            "mode": "dataset_path",
            "dataset_path": "/tmp/manifest.json",
            "trace_path": str(trace_path),
        },
    )

    finalized = _finalize_runtime_artifacts(
        artifacts,
        process_returncode=-10,
        global_loss=7.95,
        telemetry={
            "det": NodeTelemetry(
                node_id="det",
                execution_time_ms=1.0,
                peak_memory_bytes=1024,
                error_expansion=0.0,
            )
        },
    )

    assert finalized["execution_summary"]["soft_accepted_nonzero_exit"] is True
    assert finalized["execution_summary"]["loss_is_finite"] is True
    assert finalized["invocation_summary"]["dataset_path"] == "/tmp/manifest.json"
    assert finalized["usability_assessment"]["usable_for_scoring"] is True
    persisted = json.loads((tmp_path / "runtime_evidence.json").read_text())
    assert persisted["invocation_summary"]["dataset_path"] == "/tmp/manifest.json"
    assert persisted["execution_summary"]["soft_accepted_nonzero_exit"] is True
