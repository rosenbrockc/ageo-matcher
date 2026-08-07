"""Tests for the sciona.principal module."""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from sciona.architect.handoff import CDGExport
from sciona.architect.catalog import PrimitiveCatalog
from sciona.architect.models import (
    AlgorithmicPrimitive,
    AlgorithmicNode,
    ConceptType,
    IOSpec,
    NodeStatus,
    ParamStatus,
    PrimitiveParamSpec,
)
from sciona.architect.planning_contract import build_planning_artifact
from sciona.principal.models import (
    BenchmarkResult,
    NodeGradient,
    NodeTelemetry,
    OptimizationMetric,
)
from sciona.synthesizer.ghost_sim import GhostSimReport

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_telemetry(
    node_id: str, time_ms: float, mem: int, err: float
) -> NodeTelemetry:
    return NodeTelemetry(
        node_id=node_id,
        execution_time_ms=time_ms,
        peak_memory_bytes=mem,
        error_expansion=err,
    )


def _make_cdg(*atomic_specs: tuple[str, str]) -> CDGExport:
    """Build a minimal CDGExport with ATOMIC leaf nodes.

    Each *atomic_spec* is (node_id, name).
    """
    nodes = [
        AlgorithmicNode(
            node_id=nid,
            name=name,
            description=f"Test node {name}",
            concept_type=ConceptType.ARITHMETIC,
            status=NodeStatus.ATOMIC,
        )
        for nid, name in atomic_specs
    ]
    return CDGExport(nodes=nodes, edges=[])


def _make_benchmark(*node_specs: tuple[str, float, int, float]) -> BenchmarkResult:
    """Build a BenchmarkResult from (node_id, time_ms, peak_mem, error_expansion) tuples."""
    telemetry = {}
    total_loss = 0.0
    for nid, t, m, e in node_specs:
        telemetry[nid] = _make_telemetry(nid, t, m, e)
        total_loss += t
    return BenchmarkResult(global_loss=total_loss, node_telemetry=telemetry)


# ===================================================================
# Tests for sciona.principal.models
# ===================================================================


class TestOptimizationMetric:
    def test_values(self):
        assert OptimizationMetric.LATENCY.value == "latency"
        assert OptimizationMetric.MEMORY.value == "memory"
        assert OptimizationMetric.PRECISION.value == "precision"
        assert OptimizationMetric.FLOP_COUNT.value == "flop_count"
        assert OptimizationMetric.STRUCTURE.value == "structure"
        assert OptimizationMetric.CONVERGENCE.value == "convergence"

    def test_str_enum(self):
        assert isinstance(OptimizationMetric.LATENCY, str)
        assert OptimizationMetric("latency") == OptimizationMetric.LATENCY


class TestNodeTelemetry:
    def test_construction(self):
        t = _make_telemetry("n1", 12.5, 1024, 0.01)
        assert t.node_id == "n1"
        assert t.execution_time_ms == 12.5
        assert t.peak_memory_bytes == 1024
        assert t.error_expansion == 0.01

    def test_frozen(self):
        t = _make_telemetry("n1", 1.0, 100, 0.0)
        with pytest.raises(Exception):
            t.node_id = "n2"


class TestBenchmarkResult:
    def test_empty(self):
        br = BenchmarkResult(global_loss=0.5)
        assert br.node_telemetry == {}

    def test_with_telemetry(self):
        tel = {"a": _make_telemetry("a", 1.0, 100, 0.0)}
        br = BenchmarkResult(global_loss=1.0, node_telemetry=tel)
        assert "a" in br.node_telemetry


class TestOptunaHyperparams:
    def test_suggest_node_params_returns_assignments(self):
        from sciona.principal.hpo import OptunaManager

        catalog = PrimitiveCatalog()
        catalog.add(
            AlgorithmicPrimitive(
                name="compute_event_rate_smoothed",
                source="test",
                category=ConceptType.ANALYSIS,
                description="Smoothed event-rate estimator",
                inputs=[
                    IOSpec(name="events", type_desc="np.ndarray"),
                    IOSpec(name="sampling_rate", type_desc="float"),
                ],
                outputs=[IOSpec(name="rate", type_desc="tuple[np.ndarray, np.ndarray]")],
                tunable_params=[
                    PrimitiveParamSpec(
                        name="smoothing_window",
                        kind="int",
                        default=5,
                        min_value=1,
                        max_value=15,
                        step=2,
                    )
                ],
                param_status=ParamStatus.APPROVED,
            )
        )
        cdg = CDGExport(
            nodes=[
                AlgorithmicNode(
                    node_id="n1",
                    name="Compute Event Rate",
                    description="Rate from event indices",
                    concept_type=ConceptType.ANALYSIS,
                    status=NodeStatus.ATOMIC,
                    matched_primitive="compute_event_rate_smoothed",
                )
            ],
            edges=[],
        )

        manager = OptunaManager(study_name="test-principal")
        suggested = manager.suggest_node_params(
            signature="sigA",
            cdg=cdg,
            catalog=catalog,
        )
        assert set(suggested.assignments) == {"n1"}
        assert "smoothing_window" in suggested.assignments["n1"]
        assert suggested.assignments["n1"]["smoothing_window"] == 5
        manager.complete_trial(
            signature=suggested.signature,
            trial_number=suggested.trial_number,
            loss=1.23,
        )

        second = manager.suggest_node_params(
            signature="sigA",
            cdg=cdg,
            catalog=catalog,
        )
        assert second.assignments["n1"]["smoothing_window"] in {1, 3, 5, 7, 9, 11, 13, 15}


class TestNodeGradient:
    def test_construction(self):
        g = NodeGradient(
            node_id="n1",
            gradient_score=85.0,
            metric_type=OptimizationMetric.LATENCY,
            bottleneck_reason="slow",
        )
        assert g.node_id == "n1"
        assert g.gradient_score == 85.0


# ===================================================================
# Tests for sciona.principal.evaluator
# ===================================================================


class TestParseTrace:
    def test_valid_trace(self, tmp_path: Path):
        from sciona.principal.evaluator import _parse_trace

        trace = tmp_path / "trace.jsonl"
        records = [
            {
                "node_id": "a",
                "execution_time_ms": 10.0,
                "peak_memory_bytes": 100,
                "error_expansion": 0.01,
            },
            {
                "node_id": "b",
                "execution_time_ms": 20.0,
                "peak_memory_bytes": 200,
                "error_expansion": 0.02,
            },
        ]
        trace.write_text("\n".join(json.dumps(r) for r in records))

        result = _parse_trace(trace)
        assert len(result) == 2
        assert result["a"].execution_time_ms == 10.0
        assert result["b"].peak_memory_bytes == 200

    def test_missing_file(self, tmp_path: Path):
        from sciona.principal.evaluator import _parse_trace

        result = _parse_trace(tmp_path / "nope.jsonl")
        assert result == {}

    def test_malformed_line(self, tmp_path: Path):
        from sciona.principal.evaluator import _parse_trace

        trace = tmp_path / "trace.jsonl"
        trace.write_text('not json\n{"node_id": "a", "execution_time_ms": 1}\n')

        result = _parse_trace(trace)
        assert len(result) == 1
        assert "a" in result

    def test_missing_node_id(self, tmp_path: Path):
        from sciona.principal.evaluator import _parse_trace

        trace = tmp_path / "trace.jsonl"
        trace.write_text('{"execution_time_ms": 1}\n')

        result = _parse_trace(trace)
        assert result == {}


class TestComputeLoss:
    def test_latency(self):
        from sciona.principal.evaluator import _compute_loss

        tel = {
            "a": _make_telemetry("a", 10.0, 100, 0.0),
            "b": _make_telemetry("b", 20.0, 200, 0.0),
        }
        loss = _compute_loss(tel, OptimizationMetric.LATENCY, None)
        assert loss == 30.0

    def test_memory(self):
        from sciona.principal.evaluator import _compute_loss

        tel = {
            "a": _make_telemetry("a", 10.0, 100, 0.0),
            "b": _make_telemetry("b", 20.0, 500, 0.0),
        }
        loss = _compute_loss(tel, OptimizationMetric.MEMORY, None)
        assert loss == 500.0

    def test_precision_from_stdout(self):
        from sciona.principal.evaluator import _compute_loss

        tel = {"a": _make_telemetry("a", 1.0, 100, 0.0)}
        stdout = b'some output\n{"mse": 0.042}\n'
        loss = _compute_loss(tel, OptimizationMetric.PRECISION, stdout)
        assert loss == pytest.approx(0.042)

    def test_precision_prefers_explicit_loss(self):
        from sciona.principal.evaluator import _compute_loss

        tel = {"a": _make_telemetry("a", 1.0, 100, 0.0)}
        stdout = b'{"mse": 0.04, "rmse": 0.2, "loss": 0.3}\n'
        loss = _compute_loss(tel, OptimizationMetric.PRECISION, stdout)
        assert loss == pytest.approx(0.3)

    def test_flop_count_proxied_by_latency(self):
        from sciona.principal.evaluator import _compute_loss

        tel = {
            "a": _make_telemetry("a", 5.0, 0, 0.0),
            "b": _make_telemetry("b", 7.0, 0, 0.0),
        }
        loss = _compute_loss(tel, OptimizationMetric.FLOP_COUNT, None)
        assert loss == 12.0

    def test_empty_telemetry_penalty(self):
        from sciona.principal.evaluator import _compute_loss, _FAILURE_PENALTY

        loss = _compute_loss({}, OptimizationMetric.LATENCY, None)
        assert loss == _FAILURE_PENALTY


class TestParsePrecisionLossFromStdout:
    def test_valid(self):
        from sciona.principal.evaluator import _parse_precision_loss_from_stdout

        assert _parse_precision_loss_from_stdout(b'{"mse": 0.5}') == pytest.approx(0.5)

    def test_none_stdout(self):
        from sciona.principal.evaluator import (
            _FAILURE_PENALTY,
            _parse_precision_loss_from_stdout,
        )

        assert _parse_precision_loss_from_stdout(None) == _FAILURE_PENALTY

    def test_no_mse_key(self):
        from sciona.principal.evaluator import (
            _FAILURE_PENALTY,
            _parse_precision_loss_from_stdout,
        )

        assert _parse_precision_loss_from_stdout(b'{"foo": 1}') == _FAILURE_PENALTY

    def test_last_valid_line(self):
        from sciona.principal.evaluator import _parse_precision_loss_from_stdout

        stdout = b'log line 1\nlog line 2\n{"mse": 0.123}\n'
        assert _parse_precision_loss_from_stdout(stdout) == pytest.approx(0.123)

    def test_prefers_loss_then_rmse(self):
        from sciona.principal.evaluator import _parse_precision_loss_from_stdout

        assert _parse_precision_loss_from_stdout(b'{"rmse": 1.2}') == pytest.approx(1.2)
        assert _parse_precision_loss_from_stdout(b'{"mse": 0.5, "loss": 2.5}') == pytest.approx(2.5)


class TestMetricSelection:
    def test_uncertainty_alias_maps_to_precision(self):
        from sciona.principal.metric_selection import resolve_optimization_objective

        metric, eval_spec, label = resolve_optimization_objective("uncertainty")
        assert metric == OptimizationMetric.PRECISION
        assert eval_spec is None
        assert label == "uncertainty"

    def test_rmse_alias_overrides_eval_spec_loss(self):
        from sciona.principal.metric_selection import resolve_optimization_objective

        metric, eval_spec, label = resolve_optimization_objective(
            "rmse",
            {"loss": "mse", "prediction": {"value_output": 0}, "reference": {"value_source": "x"}},
        )
        assert metric == OptimizationMetric.PRECISION
        assert eval_spec["loss"] == "rmse"
        assert label == "rmse"

    def test_rmse_requires_eval_spec(self):
        from sciona.principal.metric_selection import resolve_optimization_objective

        with pytest.raises(ValueError, match="requires an evaluation spec"):
            resolve_optimization_objective("rmse")

    def test_structure_maps_to_structure_metric(self):
        from sciona.principal.metric_selection import resolve_optimization_objective

        metric, eval_spec, label = resolve_optimization_objective("structure")
        assert metric == OptimizationMetric.STRUCTURE
        assert eval_spec is None
        assert label == "structure"


class TestExecutionSandbox:
    def test_default_timeout_reads_environment(self, monkeypatch):
        from sciona.principal.evaluator import ExecutionSandbox

        monkeypatch.setenv("SCIONA_EVALUATOR_TIMEOUT_S", "321")
        sandbox = ExecutionSandbox()

        assert sandbox._timeout_s == pytest.approx(321.0)

    def test_evaluate_supports_relative_artifact_paths(self, tmp_path: Path, monkeypatch):
        from sciona.principal.evaluator import ExecutionSandbox
        from sciona.synthesizer.models import ExportBundle

        artifact = tmp_path / "artifact.py"
        artifact.write_text(
            "\n".join(
                [
                    "from pathlib import Path",
                    "import json",
                    "Path('trace.jsonl').write_text("
                    "json.dumps({'node_id': 'n1', 'execution_time_ms': 1.5, 'peak_memory_bytes': 10}) + '\\n'"
                    ")",
                    "print(json.dumps({'mse': 0.25}))",
                ]
            )
        )
        dataset = tmp_path / "dataset.json"
        dataset.write_text("{}")

        monkeypatch.chdir(tmp_path)
        bundle = ExportBundle(
            target="python-pkg",
            output_dir=Path("."),
            source_path=Path("artifact.py"),
            compiled_artifact=Path("artifact.py"),
        )

        result = asyncio.run(
            ExecutionSandbox(timeout_s=5.0).evaluate(
                bundle, str(dataset), OptimizationMetric.PRECISION
            )
        )

        assert result.global_loss == pytest.approx(0.25)
        assert "n1" in result.node_telemetry

    def test_evaluate_adapter_prefers_python_runner(self, tmp_path: Path, monkeypatch):
        from sciona.principal.evaluator import ExecutionSandbox
        from sciona.synthesizer.models import ExportBundle

        runner = tmp_path / "runner.py"
        runner.write_text("print('ok')\n")
        (tmp_path / "sciona.yml").write_text("name: test\n")
        trace = tmp_path / "trace.jsonl"
        bundle = ExportBundle(
            target="python-pkg",
            output_dir=tmp_path,
            source_path=runner,
            compiled_artifact=runner,
            executable_artifact=runner,
        )

        class DummyProc:
            returncode = 0

            async def communicate(self):
                trace.write_text(
                    '{"node_id":"leaf","execution_time_ms":1.0,"peak_memory_bytes":2}\n'
                )
                return (b'{"mse": 0.5}\n', b"")

        calls: list[list[str]] = []

        async def fake_exec(*cmd, **kwargs):
            calls.append(list(cmd))
            return DummyProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        result = asyncio.run(
            ExecutionSandbox(timeout_s=5.0).evaluate_adapter(
                bundle,
                str(tmp_path / "sciona.yml"),
                OptimizationMetric.PRECISION,
                varset={"tracker": "full"},
            )
        )

        assert result.global_loss == pytest.approx(0.5)
        assert calls
        assert "--dataset-root" in calls[0]
        assert str(tmp_path) in calls[0]
        assert "--dataset-var" in calls[0]

    def test_evaluate_adapter_forwards_eval_spec(self, tmp_path: Path, monkeypatch):
        from sciona.principal.evaluator import ExecutionSandbox
        from sciona.synthesizer.models import ExportBundle

        runner = tmp_path / "runner.py"
        runner.write_text("print('ok')\n")
        (tmp_path / "sciona.yml").write_text("name: test\n")
        trace = tmp_path / "trace.jsonl"
        bundle = ExportBundle(
            target="python-pkg",
            output_dir=tmp_path,
            source_path=runner,
            compiled_artifact=runner,
            executable_artifact=runner,
        )

        class DummyProc:
            returncode = 0

            async def communicate(self):
                trace.write_text(
                    '{"node_id":"leaf","execution_time_ms":1.0,"peak_memory_bytes":2}\n'
                )
                return (b'{"loss": 1.5}\n', b"")

        calls: list[list[str]] = []

        async def fake_exec(*cmd, **kwargs):
            calls.append(list(cmd))
            return DummyProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        result = asyncio.run(
            ExecutionSandbox(timeout_s=5.0).evaluate_adapter(
                bundle,
                str(tmp_path / "sciona.yml"),
                OptimizationMetric.PRECISION,
                evaluation_spec='{"loss":"rmse"}',
            )
        )

        assert result.global_loss == pytest.approx(1.5)
        assert "--eval-spec" in calls[0]

    def test_evaluate_adapter_forwards_dataset_slice_to_python_runner(self, tmp_path: Path, monkeypatch):
        from sciona.principal.evaluator import ExecutionSandbox
        from sciona.synthesizer.models import ExportBundle

        runner = tmp_path / "runner.py"
        runner.write_text("print('ok')\n")
        (tmp_path / "sciona.yml").write_text("name: test\n")
        trace = tmp_path / "trace.jsonl"
        bundle = ExportBundle(
            target="python-pkg",
            output_dir=tmp_path,
            source_path=runner,
            compiled_artifact=runner,
            executable_artifact=runner,
        )

        class DummyProc:
            returncode = 0

            async def communicate(self):
                trace.write_text(
                    '{"node_id":"leaf","execution_time_ms":1.0,"peak_memory_bytes":2}\n'
                )
                return (b'{"loss": 0.25}\n', b"")

        calls: list[list[str]] = []

        async def fake_exec(*cmd, **kwargs):
            calls.append(list(cmd))
            return DummyProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        result = asyncio.run(
            ExecutionSandbox(
                timeout_s=5.0,
                dataset_slice_start_s=5.0,
                dataset_slice_stop_s=305.0,
            ).evaluate_adapter(
                bundle,
                str(tmp_path / "sciona.yml"),
                OptimizationMetric.PRECISION,
            )
        )

        assert result.global_loss == pytest.approx(0.25)
        assert "--slice-start" in calls[0]
        assert "5.0" in calls[0]
        assert "--slice-stop" in calls[0]
        assert "305.0" in calls[0]

    def test_evaluate_adapter_applies_dataset_slice_to_collection_preload(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        from sciona.principal.evaluator import ExecutionSandbox
        from sciona.principal.models import BenchmarkResult
        from sciona.synthesizer.models import ExportBundle

        adapter = tmp_path / "sciona.yml"
        adapter.write_text("name: test\n")
        artifact = tmp_path / "artifact.bin"
        artifact.write_text("placeholder\n")
        bundle = ExportBundle(
            target="python",
            output_dir=tmp_path,
            source_path=artifact,
            compiled_artifact=artifact,
            executable_artifact=artifact,
        )

        slice_calls: list[tuple[float | None, float | None]] = []

        class DummyCollection:
            def __init__(self):
                self.data = None

            @classmethod
            def get_filter_options(cls, user, serial, recursive=True):
                return {"user": user, "serial": serial, "recursive": recursive}

            @classmethod
            def from_folder(cls, options=None):
                return cls()

            def autoload(self):
                self.data = type(
                    "DummyData",
                    (),
                    {"min": 1000.0, "to_pandas": lambda self, **kwargs: {}},
                )()
                return self.data

            def slice(self, start=None, stop=None):
                slice_calls.append((start, stop))

            def to_pandas(self):
                return {}

        monkeypatch.setattr(
            "sciona.principal.datasets.create_templated_dataset_collection",
            lambda *args, **kwargs: DummyCollection,
        )

        async def fake_evaluate(*args, **kwargs):
            return BenchmarkResult(global_loss=0.0)

        monkeypatch.setattr(ExecutionSandbox, "evaluate", fake_evaluate)

        result = asyncio.run(
            ExecutionSandbox(
                timeout_s=5.0,
                dataset_slice_start_s=0.0,
                dataset_slice_stop_s=300.0,
            ).evaluate_adapter(
                bundle,
                str(adapter),
                OptimizationMetric.PRECISION,
            )
        )

        assert result.global_loss == pytest.approx(0.0)
        assert slice_calls == [(1000.0, 1300.0)]


# ===================================================================
# Tests for sciona.principal.backprop
# ===================================================================


class TestCreditAssigner:
    def setup_method(self):
        from sciona.principal.backprop import CreditAssigner

        self.assigner = CreditAssigner()
        self.cdg = _make_cdg(("a", "NodeA"), ("b", "NodeB"))

    def test_latency_gradients(self):
        bench = _make_benchmark(("a", 80.0, 100, 0.0), ("b", 20.0, 100, 0.0))
        sim = GhostSimReport()

        grads = self.assigner.compute_gradients(
            self.cdg,
            bench,
            sim,
            OptimizationMetric.LATENCY,
        )
        assert len(grads) == 2
        # Sorted descending
        assert grads[0].node_id == "a"
        assert grads[0].gradient_score == pytest.approx(80.0)
        assert grads[1].gradient_score == pytest.approx(20.0)
        assert grads[0].metric_type == OptimizationMetric.LATENCY

    def test_flop_count_uses_latency(self):
        bench = _make_benchmark(("a", 60.0, 0, 0.0), ("b", 40.0, 0, 0.0))
        sim = GhostSimReport()

        grads = self.assigner.compute_gradients(
            self.cdg,
            bench,
            sim,
            OptimizationMetric.FLOP_COUNT,
        )
        assert len(grads) == 2
        assert grads[0].metric_type == OptimizationMetric.FLOP_COUNT

    def test_memory_gradients(self):
        bench = _make_benchmark(("a", 1.0, 300, 0.0), ("b", 1.0, 700, 0.0))
        sim = GhostSimReport()

        grads = self.assigner.compute_gradients(
            self.cdg,
            bench,
            sim,
            OptimizationMetric.MEMORY,
        )
        assert grads[0].node_id == "b"
        assert grads[0].gradient_score == pytest.approx(70.0)
        assert "70.0%" in grads[0].bottleneck_reason

    def test_precision_from_ghost_sim(self):
        bench = _make_benchmark(("a", 1.0, 100, 0.0), ("b", 1.0, 100, 0.0))
        sim = GhostSimReport(precision_gradients={"a": 3.0, "b": 1.0})

        grads = self.assigner.compute_gradients(
            self.cdg,
            bench,
            sim,
            OptimizationMetric.PRECISION,
        )
        assert grads[0].node_id == "a"
        assert grads[0].gradient_score == pytest.approx(75.0)

    def test_precision_fallback_to_error_expansion(self):
        bench = _make_benchmark(("a", 1.0, 100, 5.0), ("b", 1.0, 100, 15.0))
        sim = GhostSimReport()  # no precision_gradients

        grads = self.assigner.compute_gradients(
            self.cdg,
            bench,
            sim,
            OptimizationMetric.PRECISION,
        )
        assert grads[0].node_id == "b"
        assert grads[0].gradient_score == pytest.approx(75.0)

    def test_structure_gradients_follow_ghost_risk(self):
        bench = BenchmarkResult(global_loss=0.0)
        sim = GhostSimReport(
            ran=True,
            passed=False,
            skipped_nodes=["NodeB"],
            error_node="NodeA",
        )

        grads = self.assigner.compute_gradients(
            self.cdg,
            bench,
            sim,
            OptimizationMetric.STRUCTURE,
        )
        assert grads[0].node_id == "a"
        assert grads[0].metric_type == OptimizationMetric.STRUCTURE
        assert "structural risk" in grads[0].bottleneck_reason

    def test_empty_telemetry_returns_empty(self):
        bench = BenchmarkResult(global_loss=0.0)
        sim = GhostSimReport()

        grads = self.assigner.compute_gradients(
            self.cdg,
            bench,
            sim,
            OptimizationMetric.LATENCY,
        )
        assert grads == []

    def test_bottleneck_reason_includes_name(self):
        bench = _make_benchmark(("a", 100.0, 100, 0.0))
        sim = GhostSimReport()

        grads = self.assigner.compute_gradients(
            self.cdg,
            bench,
            sim,
            OptimizationMetric.LATENCY,
        )
        assert "NodeA" in grads[0].bottleneck_reason

    def test_non_atomic_nodes_excluded(self):
        """Non-ATOMIC nodes should not get gradients."""
        cdg = CDGExport(
            nodes=[
                AlgorithmicNode(
                    node_id="root",
                    name="Root",
                    description="parent",
                    concept_type=ConceptType.ARITHMETIC,
                    status=NodeStatus.DECOMPOSED,
                ),
                AlgorithmicNode(
                    node_id="leaf",
                    name="Leaf",
                    description="child",
                    concept_type=ConceptType.ARITHMETIC,
                    status=NodeStatus.ATOMIC,
                ),
            ],
            edges=[],
        )
        bench = _make_benchmark(("root", 50.0, 100, 0.0), ("leaf", 50.0, 100, 0.0))
        sim = GhostSimReport()

        grads = self.assigner.compute_gradients(
            cdg,
            bench,
            sim,
            OptimizationMetric.LATENCY,
        )
        assert len(grads) == 1
        assert grads[0].node_id == "leaf"


# ===================================================================
# Tests for sciona.principal.hpo
# ===================================================================


class TestOptunaManager:
    def test_creation(self):
        from sciona.principal.hpo import OptunaManager

        mgr = OptunaManager(study_name="test")
        assert mgr.study is not None
        assert mgr.study.study_name == "test"

    def test_check_early_prune_clean(self):
        from sciona.principal.hpo import OptunaManager

        report = GhostSimReport(ran=True, passed=True, precision_gradients={"a": 1.0})
        # Should not raise
        OptunaManager.check_early_prune(report)

    def test_check_early_prune_not_ran(self):
        from sciona.principal.hpo import OptunaManager

        report = GhostSimReport(ran=False, passed=False)
        # ran=False means we don't prune based on failure
        OptunaManager.check_early_prune(report)

    def test_check_early_prune_failed_sim(self):
        from sciona.principal.hpo import OptunaManager, TrialPrunedEarly

        report = GhostSimReport(ran=True, passed=False, error="mismatch")
        with pytest.raises(TrialPrunedEarly, match="mismatch"):
            OptunaManager.check_early_prune(report)

    def test_check_early_prune_inf_gradient(self):
        from sciona.principal.hpo import OptunaManager, TrialPrunedEarly

        report = GhostSimReport(
            ran=True,
            passed=True,
            precision_gradients={"a": float("inf")},
        )
        with pytest.raises(TrialPrunedEarly, match="Infinite/NaN"):
            OptunaManager.check_early_prune(report)

    def test_check_early_prune_nan_gradient(self):
        from sciona.principal.hpo import OptunaManager, TrialPrunedEarly

        report = GhostSimReport(
            ran=True,
            passed=True,
            precision_gradients={"a": float("nan")},
        )
        with pytest.raises(TrialPrunedEarly):
            OptunaManager.check_early_prune(report)

    def test_param_importances_too_few_trials(self):
        from sciona.principal.hpo import OptunaManager

        mgr = OptunaManager(study_name="test_importance")
        result = mgr.param_importances()
        assert result == {}


# ===================================================================
# Tests for sciona.principal.graph (routing + state + build)
# ===================================================================


class TestPrincipalState:
    def test_defaults(self):
        from sciona.principal.graph import PrincipalState

        state = PrincipalState()
        assert state.goal == ""
        assert state.metric == OptimizationMetric.LATENCY
        assert state.max_trials == 50
        assert state.current_trial == 0
        assert state.best_loss == float("inf")
        assert state.done is False
        assert state.trial_history == []
        assert state.cdg is None
        assert state.planning_artifact is None
        assert state.export_bundle is None
        assert state.benchmark is None

    @pytest.mark.asyncio
    async def test_seed_population_preserves_planning_artifact(self):
        from sciona.principal.graph import PrincipalState, seed_population

        planning_artifact = build_planning_artifact(
            goal="Detect peaks in an ECG",
            thread_id="thread-1",
            paradigm="signal_detect_measure",
            variant_hint="peak_detection",
            root_inputs=[IOSpec(name="signal", type_desc="np.ndarray")],
            root_outputs=[IOSpec(name="events", type_desc="np.ndarray")],
            strategy_rationale="Keep the signal contract explicit before detection.",
        )

        class _DummyArchitect:
            async def decompose(self, goal: str, thread_id: str | None = None) -> CDGExport:
                assert goal == "Detect peaks in an ECG"
                assert thread_id is not None
                return CDGExport(
                    nodes=[
                        AlgorithmicNode(
                            node_id="leaf",
                            name="Leaf",
                            description="atomic",
                            concept_type=ConceptType.CUSTOM,
                            status=NodeStatus.ATOMIC,
                        )
                    ],
                    edges=[],
                    planning_artifact=planning_artifact.model_dump(mode="json"),
                )

        deps = SimpleNamespace(
            architect=_DummyArchitect(),
            catalog=PrimitiveCatalog(),
            param_trials_per_structure=0,
        )
        state = PrincipalState(goal="Detect peaks in an ECG")

        result = await seed_population(state, {"configurable": {"deps": deps}})

        assert state.cdg is not None
        assert state.planning_artifact is not None
        assert result["planning_artifact"] is not None
        assert result["planning_artifact"]["artifact_version"] == "phase1.v1"
        assert result["planning_artifact"]["skeleton_intent"]["variant_hint"] == "peak_detection"

    @pytest.mark.asyncio
    async def test_evaluate_run_records_planning_summary(self):
        from sciona.principal.graph import PrincipalState, evaluate_run

        planning_artifact = build_planning_artifact(
            goal="Detect peaks in an ECG",
            thread_id="thread-1",
            paradigm="signal_detect_measure",
            variant_hint="peak_detection",
            root_inputs=[IOSpec(name="signal", type_desc="np.ndarray")],
            root_outputs=[IOSpec(name="events", type_desc="np.ndarray")],
            strategy_rationale="Keep the signal contract explicit before detection.",
        ).model_dump(mode="json")

        state = PrincipalState(
            goal="Detect peaks in an ECG",
            current_trial=1,
            thread_id="thread-1",
            cdg=_make_cdg(("n1", "Detect Peaks")),
            planning_artifact=planning_artifact,
            export_bundle=SimpleNamespace(),
            benchmark=BenchmarkResult(global_loss=1.25),
            reuse_cached_evaluation=True,
        )
        deps = SimpleNamespace(
            sandbox=SimpleNamespace(),
            dataset_varset=None,
            evaluation_spec=None,
            hpo_manager=None,
            catalog=PrimitiveCatalog(),
        )

        result = await evaluate_run(state, {"configurable": {"deps": deps}})

        assert result["trial_history"]
        planning_summary = result["trial_history"][0]["planning_artifact"]
        assert planning_summary["artifact_version"] == "phase1.v1"
        assert planning_summary["paradigm"] == "signal_detect_measure"
        assert planning_summary["constraint_count"] >= 1

    @pytest.mark.asyncio
    async def test_check_admissibility_records_structured_summary(self):
        from sciona.principal.graph import PrincipalState, check_admissibility

        planning_artifact = build_planning_artifact(
            goal="Estimate event rate from a signal",
            thread_id="thread-1",
            paradigm="signal_event_rate",
            family_hint="signal_event_rate",
            root_inputs=[
                IOSpec(name="signal", type_desc="np.ndarray"),
                IOSpec(name="sampling_rate", type_desc="float"),
            ],
            root_outputs=[IOSpec(name="rate", type_desc="np.ndarray")],
        ).model_dump(mode="json")

        root = AlgorithmicNode(
            node_id="root",
            name="Root",
            description="Root waveform-to-rate pipeline",
            concept_type=ConceptType.ANALYSIS,
            status=NodeStatus.DECOMPOSED,
            children=["filt", "rate"],
            inputs=[
                IOSpec(name="signal", type_desc="np.ndarray"),
                IOSpec(name="sampling_rate", type_desc="float"),
            ],
            outputs=[IOSpec(name="rate", type_desc="np.ndarray")],
        )
        filt = AlgorithmicNode(
            node_id="filt",
            parent_id="root",
            name="Filter",
            description="Filter signal",
            concept_type=ConceptType.SIGNAL_FILTER,
            status=NodeStatus.ATOMIC,
            matched_primitive="filter_signal_for_detection",
            inputs=[IOSpec(name="signal", type_desc="np.ndarray")],
            outputs=[IOSpec(name="signal", type_desc="np.ndarray")],
        )
        rate = AlgorithmicNode(
            node_id="rate",
            parent_id="root",
            name="Rate",
            description="Compute rate",
            concept_type=ConceptType.ANALYSIS,
            status=NodeStatus.ATOMIC,
            matched_primitive="compute_event_rate",
            inputs=[IOSpec(name="events", type_desc="np.ndarray")],
            outputs=[IOSpec(name="rate", type_desc="np.ndarray")],
        )
        state = PrincipalState(
            goal="Estimate event rate from a signal",
            current_trial=1,
            cdg=CDGExport(
                nodes=[root, filt, rate],
                edges=[],
                planning_artifact=planning_artifact,
            ),
            planning_artifact=planning_artifact,
            benchmark=BenchmarkResult(
                global_loss=10.0,
                runtime_artifacts={
                    "signal_data": {"signal": [0.0] * 1280, "sampling_rate": 128.0},
                    "intermediates": {"events": [10, 20, 30]},
                },
            ),
            trial_history=[{"trial": 1, "admissibility": {}}],
        )

        result = await check_admissibility(
            state,
            {"configurable": {"deps": SimpleNamespace(admissibility_evaluator=None)}},
        )

        assert result["admissibility_summary"]["decision_count"] >= 1
        assert result["admissibility_hard_rejected"] is True
        assert state.trial_history[0]["admissibility"]["hard_rejected"] is True
        assert state.trial_history[0]["admissibility"]["decisions"]
        assert state.trial_history[0]["admissibility"]["family"] == "signal_event_rate"
        assert state.trial_history[0]["admissibility"]["runtime_context"]["sampling_rate"] == 128.0
        assert state.trial_history[0]["admissibility"]["telemetry"]["events"]["count"] == 3.0

    @pytest.mark.asyncio
    async def test_compute_gradients_forwards_dataset_slice_to_reference_attribution(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from sciona.principal.graph import PrincipalState, compute_gradients

        captured: dict[str, float | None] = {}

        async def _fake_reference_gradients(
            cdg,
            bundle,
            dataset_path,
            evaluation_spec,
            *,
            dataset_varset=None,
            dataset_slice_start_s=None,
            dataset_slice_stop_s=None,
        ):
            del cdg, bundle, dataset_path, evaluation_spec, dataset_varset
            captured["start"] = dataset_slice_start_s
            captured["stop"] = dataset_slice_stop_s
            return [
                NodeGradient(
                    node_id="n1",
                    gradient_score=100.0,
                    metric_type=OptimizationMetric.PRECISION,
                    bottleneck_reason="reference attribution",
                )
            ]

        monkeypatch.setattr(
            "sciona.principal.graph.compute_reference_loss_gradients",
            _fake_reference_gradients,
        )

        state = PrincipalState(
            metric=OptimizationMetric.PRECISION,
            dataset_path="/tmp/synthetic_signal.yml",
            current_trial=1,
            cdg=_make_cdg(("n1", "Detect Peaks")),
            export_bundle=SimpleNamespace(),
            benchmark=BenchmarkResult(global_loss=1.25),
        )
        deps = SimpleNamespace(
            evaluation_spec={"loss": "rmse", "reference": {"value_source": "reference"}},
            dataset_varset={"tracker": "single"},
            dataset_slice_start_s=5.0,
            dataset_slice_stop_s=305.0,
            atom_ledger=None,
        )

        result = await compute_gradients(state, {"configurable": {"deps": deps}})

        assert result["bottleneck_node_id"] == "n1"
        assert captured == {"start": 5.0, "stop": 305.0}

    @pytest.mark.asyncio
    async def test_select_proposal_skips_generation_after_hard_reject(self):
        from sciona.principal.graph import PrincipalState, check_admissibility, select_proposal

        class _SentinelExpansionEngine:
            def __init__(self) -> None:
                self.call_count = 0

            def expand(self, cdg, context):
                self.call_count += 1
                raise AssertionError("expansion should not run after a hard reject")

        planning_artifact = build_planning_artifact(
            goal="Estimate event rate from a signal",
            thread_id="thread-1",
            paradigm="signal_event_rate",
            family_hint="signal_event_rate",
            root_inputs=[
                IOSpec(name="signal", type_desc="np.ndarray"),
                IOSpec(name="sampling_rate", type_desc="float"),
            ],
            root_outputs=[IOSpec(name="rate", type_desc="np.ndarray")],
        ).model_dump(mode="json")

        root = AlgorithmicNode(
            node_id="root",
            name="Root",
            description="Root waveform-to-rate pipeline",
            concept_type=ConceptType.ANALYSIS,
            status=NodeStatus.DECOMPOSED,
            children=["filt", "rate"],
            inputs=[
                IOSpec(name="signal", type_desc="np.ndarray"),
                IOSpec(name="sampling_rate", type_desc="float"),
            ],
            outputs=[IOSpec(name="rate", type_desc="np.ndarray")],
        )
        filt = AlgorithmicNode(
            node_id="filt",
            parent_id="root",
            name="Filter",
            description="Filter signal",
            concept_type=ConceptType.SIGNAL_FILTER,
            status=NodeStatus.ATOMIC,
            matched_primitive="filter_signal_for_detection",
            inputs=[IOSpec(name="signal", type_desc="np.ndarray")],
            outputs=[IOSpec(name="signal", type_desc="np.ndarray")],
        )
        rate = AlgorithmicNode(
            node_id="rate",
            parent_id="root",
            name="Rate",
            description="Compute rate",
            concept_type=ConceptType.ANALYSIS,
            status=NodeStatus.ATOMIC,
            matched_primitive="compute_event_rate",
            inputs=[IOSpec(name="events", type_desc="np.ndarray")],
            outputs=[IOSpec(name="rate", type_desc="np.ndarray")],
        )
        state = PrincipalState(
            goal="Estimate event rate from a signal",
            current_trial=1,
            cdg=CDGExport(
                nodes=[root, filt, rate],
                edges=[],
                planning_artifact=planning_artifact,
            ),
            planning_artifact=planning_artifact,
            benchmark=BenchmarkResult(
                global_loss=10.0,
                runtime_artifacts={
                    "signal_data": {"signal": [0.0] * 1280, "sampling_rate": 128.0},
                    "intermediates": {"events": [10, 20, 30]},
                },
            ),
            trial_history=[{"trial": 1, "admissibility": {}, "expansion": {"applied": False}}],
        )

        await check_admissibility(
            state,
            {"configurable": {"deps": SimpleNamespace(admissibility_evaluator=None)}},
        )

        engine = _SentinelExpansionEngine()
        result = await select_proposal(
            state,
            {
                "configurable": {
                    "deps": SimpleNamespace(
                        expansion_engine=engine,
                        atom_ledger=None,
                        catalog=None,
                    )
                }
            },
        )

        assert result["selected_proposal"] == ""
        assert engine.call_count == 0
        proposal = state.trial_history[0]["proposal_selection"]
        assert proposal["skipped_due_to_admissibility"] is True
        assert proposal["skip_reason"] == "hard_reject"
        assert proposal["hard_reject_rule_ids"] == ["minimum_event_density"]

    @pytest.mark.asyncio
    async def test_select_proposal_records_candidate_disposition_and_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from sciona.principal.expansion import ExpansionResult
        from sciona.principal.graph import PrincipalState, select_proposal

        root = AlgorithmicNode(
            node_id="root",
            name="Root",
            description="Root pipeline",
            concept_type=ConceptType.ANALYSIS,
            status=NodeStatus.DECOMPOSED,
            children=["leaf"],
            inputs=[IOSpec(name="records", type_desc="list[dict]")],
            outputs=[IOSpec(name="score", type_desc="float")],
        )
        leaf = AlgorithmicNode(
            node_id="leaf",
            parent_id="root",
            name="Normalize Records",
            description="Normalize record payloads",
            concept_type=ConceptType.CUSTOM,
            status=NodeStatus.ATOMIC,
            matched_primitive="normalize_records",
            inputs=[IOSpec(name="records", type_desc="list[dict]")],
            outputs=[IOSpec(name="score", type_desc="float")],
        )
        cdg = CDGExport(nodes=[root, leaf], edges=[], metadata={})
        benchmark = BenchmarkResult(
            global_loss=10.0,
            runtime_artifacts={
                "runtime_inputs": {"records": [{"a": 1}]},
                "intermediates": {"score": [1.0]},
            },
        )
        state = PrincipalState(
            goal="Score normalized records",
            current_trial=1,
            cdg=cdg,
            benchmark=benchmark,
            bottleneck_node_id="leaf",
            thread_id="thread-1",
            trial_history=[{"trial": 1, "admissibility": {}, "expansion": {"applied": False}}],
        )

        class _SingleExpansionEngine:
            def expand(self, input_cdg, context):
                return ExpansionResult(
                    cdg=input_cdg.model_copy(deep=True),
                    applied_rules=("insert_normalization_gate",),
                    diagnostics=(),
                    expanded=True,
                    applied_assets=(),
                )

        class _HardRejectReport:
            hard_rejected = True
            routed_to_refinement = False

            def summary(self):
                return {
                    "hard_rejected": True,
                    "routed_to_refinement": False,
                    "decision_count": 1,
                    "hard_reject_rule_ids": ["proposal_guard"],
                    "warning_rule_ids": [],
                    "refinement_rule_ids": [],
                    "decisions": [],
                }

        class _ProposalRejectingEvaluator:
            def evaluate(self, context):
                return _HardRejectReport()

        async def _fake_eval(state_arg, deps_arg, candidate_cdg):
            return (
                8.0,
                None,
                BenchmarkResult(
                    global_loss=8.0,
                    runtime_artifacts={
                        "runtime_inputs": {"records": [{"a": 1}]},
                        "intermediates": {"score": [1.0]},
                    },
                ),
                [],
                GhostSimReport(),
            )

        async def _no_redecompose(state_arg, deps_arg, *, bottleneck_name):
            return None

        monkeypatch.setattr("sciona.principal.graph.evaluate_proposal_candidate", _fake_eval)
        monkeypatch.setattr("sciona.principal.graph.build_redecomposition_candidate", _no_redecompose)

        result = await select_proposal(
            state,
            {
                "configurable": {
                    "deps": SimpleNamespace(
                        expansion_engine=_SingleExpansionEngine(),
                        admissibility_evaluator=_ProposalRejectingEvaluator(),
                        atom_ledger=None,
                        catalog=None,
                    )
                }
            },
        )

        assert result["selected_proposal"] == ""
        assert result["selected_proposal_reason"] == "no_admissible_improvement"
        proposal = state.trial_history[0]["proposal_selection"]
        assert proposal["selected"] == ""
        assert proposal["selected_reason"] == "no_admissible_improvement"
        assert proposal["candidates"][0]["selection_disposition"] == "rejected"
        assert proposal["candidates"][0]["selection_reason"] == "proposal_hard_rejected"
        assert proposal["candidates"][0]["admissibility"]["hard_rejected"] is True

    @pytest.mark.asyncio
    async def test_select_proposal_does_not_reopen_param_search_when_budget_exhausted(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from sciona.principal.expansion import ExpansionResult
        from sciona.principal.graph import PrincipalState, select_proposal

        root = AlgorithmicNode(
            node_id="root",
            name="Root",
            description="Root pipeline",
            concept_type=ConceptType.ANALYSIS,
            status=NodeStatus.DECOMPOSED,
            children=["leaf"],
            inputs=[IOSpec(name="records", type_desc="list[dict]")],
            outputs=[IOSpec(name="score", type_desc="float")],
        )
        leaf = AlgorithmicNode(
            node_id="leaf",
            parent_id="root",
            name="Normalize Records",
            description="Normalize record payloads",
            concept_type=ConceptType.CUSTOM,
            status=NodeStatus.ATOMIC,
            matched_primitive="normalize_records",
            inputs=[IOSpec(name="records", type_desc="list[dict]")],
            outputs=[IOSpec(name="score", type_desc="float")],
        )
        candidate_leaf = leaf.model_copy(
            update={"node_id": "leaf_refined", "matched_primitive": "normalize_records_refined"}
        )
        refined_cdg = CDGExport(nodes=[root, candidate_leaf], edges=[], metadata={})
        benchmark = BenchmarkResult(
            global_loss=10.0,
            runtime_artifacts={
                "runtime_inputs": {"records": [{"a": 1}]},
                "intermediates": {"score": [1.0]},
            },
        )
        improved_benchmark = BenchmarkResult(
            global_loss=8.0,
            runtime_artifacts={
                "runtime_inputs": {"records": [{"a": 1}]},
                "intermediates": {"score": [0.8]},
            },
        )
        state = PrincipalState(
            goal="Score normalized records",
            current_trial=1,
            max_trials=1,
            cdg=CDGExport(nodes=[root, leaf], edges=[], metadata={}),
            benchmark=benchmark,
            bottleneck_node_id="leaf",
            thread_id="thread-1",
            trial_history=[{"trial": 1, "admissibility": {}, "expansion": {"applied": False}}],
        )

        class _SingleExpansionEngine:
            def expand(self, input_cdg, context):
                del input_cdg, context
                return ExpansionResult(
                    cdg=refined_cdg,
                    applied_rules=("insert_normalization_gate",),
                    diagnostics=(),
                    expanded=True,
                    applied_assets=(),
                )

        class _AcceptingReport:
            hard_rejected = False
            routed_to_refinement = False

            def summary(self):
                return {
                    "hard_rejected": False,
                    "routed_to_refinement": False,
                    "decision_count": 0,
                    "hard_reject_rule_ids": [],
                    "warning_rule_ids": [],
                    "refinement_rule_ids": [],
                    "decisions": [],
                }

        class _AcceptingEvaluator:
            def evaluate(self, context):
                del context
                return _AcceptingReport()

        async def _fake_eval(state_arg, deps_arg, candidate_cdg):
            del state_arg, deps_arg, candidate_cdg
            return (8.0, None, improved_benchmark, [], GhostSimReport())

        async def _no_redecompose(state_arg, deps_arg, *, bottleneck_name):
            del state_arg, deps_arg, bottleneck_name
            return None

        monkeypatch.setattr("sciona.principal.graph.evaluate_proposal_candidate", _fake_eval)
        monkeypatch.setattr("sciona.principal.graph.build_redecomposition_candidate", _no_redecompose)

        result = await select_proposal(
            state,
            {
                "configurable": {
                    "deps": SimpleNamespace(
                        expansion_engine=_SingleExpansionEngine(),
                        admissibility_evaluator=_AcceptingEvaluator(),
                        atom_ledger=None,
                        catalog=PrimitiveCatalog(),
                        param_trials_per_structure=2,
                    )
                }
            },
        )

        assert result["selected_proposal"] == "expansion"
        assert state.pending_param_search is False
        assert state.param_trials_remaining == 0
        assert state.reuse_cached_evaluation is True


class TestRouteAfterGradients:
    def test_done(self):
        from sciona.principal.graph import PrincipalState, route_after_gradients

        state = PrincipalState(done=True)
        assert route_after_gradients(state) == "end"

    def test_max_trials_reached(self):
        from sciona.principal.graph import PrincipalState, route_after_gradients

        state = PrincipalState(
            current_trial=50,
            max_trials=50,
            trial_history=[{"trial": i} for i in range(50)],
        )
        assert route_after_gradients(state) == "end"

    def test_budget_exhausted_on_first_trial_prefers_proposal_selection(self):
        from sciona.principal.graph import PrincipalState, route_after_gradients

        state = PrincipalState(
            current_trial=1,
            max_trials=1,
            bottleneck_node_id="leaf",
            trial_history=[{"trial": 1}],
            pending_param_search=True,
            param_trials_remaining=1,
        )
        assert route_after_gradients(state) == "select_proposal"

    def test_non_pruned_error(self):
        from sciona.principal.graph import PrincipalState, route_after_gradients

        state = PrincipalState(error="fatal crash")
        assert route_after_gradients(state) == "end"

    def test_pruned_error_continues(self):
        from sciona.principal.graph import PrincipalState, route_after_gradients

        state = PrincipalState(error="Trial pruned early")
        assert route_after_gradients(state) == "select_proposal"

    def test_normal_continues(self):
        from sciona.principal.graph import PrincipalState, route_after_gradients

        state = PrincipalState()
        assert route_after_gradients(state) == "select_proposal"


class TestRouteAfterAdmissibility:
    def test_done(self):
        from sciona.principal.graph import PrincipalState
        from sciona.principal.graph_routing import route_after_admissibility

        state = PrincipalState(done=True)
        assert route_after_admissibility(state) == "end"

    def test_refinement_route_skips_gradients(self):
        from sciona.principal.graph import PrincipalState
        from sciona.principal.graph_routing import route_after_admissibility

        state = PrincipalState(admissibility_requires_refinement=True)
        assert route_after_admissibility(state) == "select_proposal"

    def test_hard_reject_skips_gradients(self):
        from sciona.principal.graph import PrincipalState
        from sciona.principal.graph_routing import route_after_admissibility

        state = PrincipalState(admissibility_hard_rejected=True)
        assert route_after_admissibility(state) == "select_proposal"

    def test_normal_continues_to_gradients(self):
        from sciona.principal.graph import PrincipalState
        from sciona.principal.graph_routing import route_after_admissibility

        state = PrincipalState()
        assert route_after_admissibility(state) == "gradients"


class TestRouteAfterUpdate:
    def test_done(self):
        from sciona.principal.graph import PrincipalState, route_after_update

        state = PrincipalState(done=True)
        assert route_after_update(state) == "end"

    def test_max_trials(self):
        from sciona.principal.graph import PrincipalState, route_after_update

        state = PrincipalState(
            current_trial=10,
            max_trials=10,
            trial_history=[{"trial": i} for i in range(10)],
        )
        assert route_after_update(state) == "end"

    def test_continues(self):
        from sciona.principal.graph import PrincipalState, route_after_update

        state = PrincipalState(current_trial=5, max_trials=10)
        assert route_after_update(state) == "suggest_params"


class TestRouteAfterProposal:
    def test_done(self):
        from sciona.principal.graph import PrincipalState
        from sciona.principal.graph_routing import route_after_proposal

        state = PrincipalState(done=True)
        assert route_after_proposal(state) == "end"

    def test_selected_proposal_runs_even_when_budget_exhausted(self):
        from sciona.principal.graph import PrincipalState
        from sciona.principal.graph_routing import route_after_proposal

        state = PrincipalState(
            selected_proposal="expansion",
            max_trials=1,
            trial_history=[{"trial": 1}],
        )
        assert route_after_proposal(state) == "suggest_params"

    def test_budget_exhausted_without_proposal_ends(self):
        from sciona.principal.graph import PrincipalState
        from sciona.principal.graph_routing import route_after_proposal

        state = PrincipalState(
            max_trials=1,
            trial_history=[{"trial": 1}],
        )
        assert route_after_proposal(state) == "end"


class TestRouteAfterForward:
    def test_done(self):
        from sciona.principal.graph import PrincipalState, route_after_forward

        state = PrincipalState(done=True)
        assert route_after_forward(state) == "end"

    def test_error_skips_to_time_travel(self):
        from sciona.principal.graph import PrincipalState, route_after_forward

        state = PrincipalState(error="pruned early")
        assert route_after_forward(state) == "time_travel"

    def test_normal_evaluates(self):
        from sciona.principal.graph import PrincipalState, route_after_forward

        state = PrincipalState()
        assert route_after_forward(state) == "evaluate"

    @pytest.mark.asyncio
    async def test_execute_forward_returns_error_when_synthesis_fails(self, monkeypatch):
        from sciona.principal.graph import PrincipalState, execute_forward

        state = PrincipalState(
            cdg=_make_cdg(("leaf", "Leaf")),
            current_trial=1,
            param_signature="sig",
            hpo_trial_number=7,
        )

        class _DummyHPO:
            def __init__(self) -> None:
                self.calls: list[tuple[str, int | None]] = []

            def prune_trial(self, *, signature: str, trial_number: int | None) -> None:
                self.calls.append((signature, trial_number))

        async def _boom(_cdg, _match_results):
            raise RuntimeError("compile failed")

        monkeypatch.setattr(
            "sciona.principal.graph.run_ghost_simulation",
            lambda *_args, **_kwargs: GhostSimReport(ran=False, passed=True),
        )

        hpo = _DummyHPO()
        deps = SimpleNamespace(
            match_results_fn=lambda _cdg: [],
            synthesize_fn=_boom,
            hpo_manager=hpo,
        )

        result = await execute_forward(state, {"configurable": {"deps": deps}})

        assert result["error"] == "compile failed"
        assert result["export_bundle"] is None
        assert result["reuse_cached_evaluation"] is False
        assert hpo.calls == [("sig", 7)]


class TestBuildPrincipalGraph:
    def test_graph_compiles(self):
        from sciona.principal.graph import build_principal_graph

        graph = build_principal_graph()
        assert graph is not None
        # Verify all expected nodes are present
        assert "seed" in graph.nodes
        assert "forward" in graph.nodes
        assert "evaluate" in graph.nodes
        assert "admissibility" in graph.nodes
        assert "gradients" in graph.nodes
        assert "time_travel" in graph.nodes
