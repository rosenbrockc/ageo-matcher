"""Tests for CDG runner, slicing, and API endpoints."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sciona.architect.models import AlgorithmicNode, DependencyEdge, NodeStatus
from sciona.ghost.registry import REGISTRY
from sciona.visualizer.runner import (
    CDGExecutionSession,
    get_numpy_statistics,
    get_topo_sorted_leaves,
    parse_input_value,
    reconstruct_parameters,
    safe_eval_slice,
)
from sciona.visualizer_api import app


@dataclass
class DummyOptions:
    val: int
    name: str


def dummy_func_reconstruct(opts: DummyOptions, extra: float):
    pass


@pytest.fixture()
def temp_run_dir(monkeypatch):
    """Fixture to redirect output runs to a temp directory and clean it up."""
    # Use a local temporary directory path
    temp_dir = Path("output/test_runs")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Patch the RUNS_DIR module variables directly
    monkeypatch.setattr("sciona.visualizer.runner.RUNS_DIR", temp_dir)
    monkeypatch.setattr("sciona.visualizer.runner_api.RUNS_DIR", temp_dir)
    
    yield temp_dir
    
    if temp_dir.exists():
        shutil.rmtree(temp_dir)


@pytest.fixture()
def client(temp_run_dir):
    """FastAPI test client."""
    mock_driver = AsyncMock()
    
    # Replace lifespan to avoid real Neo4j connection
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _test_lifespan(a):
        a.state.driver = mock_driver
        yield
        
    app.router.lifespan_context = _test_lifespan
    
    with TestClient(app, raise_server_exceptions=True) as c:
        c.app.state.driver = mock_driver
        yield c


def test_safe_eval_slice():
    """Test slicing 1D and 2D numpy arrays with standard slice format."""
    arr1d = np.array([10, 20, 30, 40, 50])
    assert np.array_equal(safe_eval_slice(arr1d, "[1:4]"), np.array([20, 30, 40]))
    assert np.array_equal(safe_eval_slice(arr1d, "[:2]"), np.array([10, 20]))
    assert safe_eval_slice(arr1d, "[3]").item() == 40
    
    arr2d = np.array([[1, 2], [3, 4]])
    assert np.array_equal(safe_eval_slice(arr2d, "[:, 0]"), np.array([1, 3]))
    assert np.array_equal(safe_eval_slice(arr2d, "[1, :]"), np.array([3, 4]))
    assert safe_eval_slice(arr2d, "[0, 0]").item() == 1


def test_get_topo_sorted_leaves():
    """Verify Kahn's topological sort and ancestor tracing works correctly."""
    n1 = AlgorithmicNode(
        node_id="n1",
        name="Node 1",
        description="",
        concept_type="sorting",
        status=NodeStatus.ATOMIC,
        depth=0,
        type_signature="",
        inputs=[],
        outputs=[{"name": "o1", "type_desc": "int", "constraints": ""}],
        children=[]
    )
    n2 = AlgorithmicNode(
        node_id="n2",
        name="Node 2",
        description="",
        concept_type="sorting",
        status=NodeStatus.ATOMIC,
        depth=1,
        type_signature="",
        inputs=[{"name": "i2", "type_desc": "int", "constraints": ""}],
        outputs=[{"name": "o2", "type_desc": "int", "constraints": ""}],
        children=[]
    )
    n3 = AlgorithmicNode(
        node_id="n3",
        name="Node 3",
        description="",
        concept_type="sorting",
        status=NodeStatus.ATOMIC,
        depth=2,
        type_signature="",
        inputs=[{"name": "i3", "type_desc": "int", "constraints": ""}],
        outputs=[{"name": "o3", "type_desc": "int", "constraints": ""}],
        children=[]
    )
    
    nodes = [n1, n2, n3]
    edges = [
        DependencyEdge(source_id="n1", target_id="n2", output_name="o1", input_name="i2", source_type="int", target_type="int", requires_glue=False),
        DependencyEdge(source_id="n2", target_id="n3", output_name="o2", input_name="i3", source_type="int", target_type="int", requires_glue=False),
    ]
    
    # 1. Full topological sort
    sorted_nodes = get_topo_sorted_leaves(nodes, edges)
    assert [n.node_id for n in sorted_nodes] == ["n1", "n2", "n3"]
    
    # 2. Target specific node (e.g. n2 should execute n1 and n2, but not n3)
    sorted_n2 = get_topo_sorted_leaves(nodes, edges, target_node_id="n2")
    assert [n.node_id for n in sorted_n2] == ["n1", "n2"]


def test_parse_input_value(tmp_path):
    """Test primitive casting, JSON parsing, and numpy file reading."""
    assert parse_input_value("42", "int") == 42
    assert parse_input_value("3.14", "float") == 3.14
    assert parse_input_value("true", "bool") is True
    assert parse_input_value("False", "bool") is False
    assert parse_input_value("hello", "str") == "hello"
    
    # JSON list
    assert parse_input_value("[1, 2, 3]", "list[int]") == [1, 2, 3]
    
    # NumPy load
    npy_file = tmp_path / "test.npy"
    arr = np.array([1, 2, 3])
    np.save(npy_file, arr)
    parsed_arr = parse_input_value(str(npy_file), "np.ndarray")
    assert np.array_equal(parsed_arr, arr)


def test_get_numpy_statistics():
    """Verify statistics calculated for numpy arrays."""
    arr = np.array([1.0, 2.0, 3.0])
    stats = get_numpy_statistics(arr)
    assert stats["type"] == "ndarray"
    assert stats["shape"] == [3]
    assert stats["dtype"] == "float64"
    assert stats["min"] == 1.0
    assert stats["max"] == 3.0
    assert stats["mean"] == 2.0


def test_reconstruct_parameters():
    """Test reconstruct_parameters maps dicts to dataclasses."""
    args = {
        "opts": {"val": 10, "name": "test"},
        "extra": 2.5
    }
    reconstructed = reconstruct_parameters(dummy_func_reconstruct, args)
    assert isinstance(reconstructed["opts"], DummyOptions)
    assert reconstructed["opts"].val == 10
    assert reconstructed["opts"].name == "test"
    assert reconstructed["extra"] == 2.5


@pytest.mark.anyio
async def test_cdg_execution_session(temp_run_dir):
    """Test execution of a simple CDG graph, caching and status writing."""
    # Register dummy atom
    def add_one(x: int) -> int:
        return x + 1
        
    REGISTRY["add_one"] = {
        "impl": add_one,
        "witness": lambda x: x
    }
    
    # Define CDG nodes and edges mock
    mock_nodes = [
        AlgorithmicNode(
            node_id="n1",
            name="Node 1",
            description="",
            concept_type="custom",
            status=NodeStatus.ATOMIC,
            depth=0,
            type_signature="",
            inputs=[{"name": "x", "type_desc": "int", "constraints": ""}],
            outputs=[{"name": "y", "type_desc": "int", "constraints": ""}],
            children=[],
            matched_primitive="add_one"
        )
    ]
    mock_edges = []
    
    session = CDGExecutionSession(driver=None, repo="test_repo", run_id="run_123")
    
    # Override load_cdg_from_memgraph inside CDGExecutionSession execution path
    with patch("sciona.visualizer.runner.load_cdg_from_memgraph", AsyncMock(return_value=(mock_nodes, mock_edges, {}))):
        result = await session.execute(user_inputs={"x": 10})
        
        assert result["run_id"] == "run_123"
        assert result["status"] == "completed"
        assert len(result["trace"]) == 1
        assert result["trace"][0]["node_id"] == "n1"
        assert not result["trace"][0]["cached"]
        
        # Verify output directory was created and output saved
        n1_dir = temp_run_dir / "run_123" / "n1"
        assert n1_dir.exists()
        
        # input value json
        with open(n1_dir / "in_x.json", "r") as f:
            in_x = json.load(f)
            assert in_x["value"] == 10
            
        # output value json
        with open(n1_dir / "out_y.json", "r") as f:
            out_y = json.load(f)
            assert out_y["value"] == 11
            
        # Verify run_metadata status is completed
        with open(temp_run_dir / "run_123" / "run_metadata.json", "r") as f:
            meta = json.load(f)
            assert meta["status"] == "completed"
            assert meta["repo"] == "test_repo"
        graph = json.loads((temp_run_dir / "run_123" / "cdg.json").read_text())
        trace = json.loads((temp_run_dir / "run_123" / "execution_trace.json").read_text())
        assert graph["nodes"][0]["node_id"] == "n1"
        assert trace == result["trace"]


@pytest.mark.anyio
async def test_cdg_execution_session_uses_supplied_graph_snapshot(temp_run_dir):
    call_count = 0

    def double(x: int) -> int:
        nonlocal call_count
        call_count += 1
        return x * 2

    REGISTRY["double"] = {"impl": double, "witness": lambda x: x}
    node = AlgorithmicNode(
        node_id="double",
        name="Double",
        description="",
        concept_type="custom",
        status=NodeStatus.ATOMIC,
        depth=0,
        type_signature="",
        inputs=[{"name": "x", "type_desc": "int", "constraints": ""}],
        outputs=[{"name": "result", "type_desc": "int", "constraints": ""}],
        children=[],
        matched_primitive="double",
    )
    graph = SimpleNamespace(nodes=[node], edges=[], metadata={"goal": "double"})
    session = CDGExecutionSession(driver=None, repo="snapshot", run_id="snapshot-run")

    with patch(
        "sciona.visualizer.runner.load_cdg_from_memgraph", new_callable=AsyncMock
    ) as load_graph:
        result = await session.execute({"x": 6}, cdg=graph)
        repeated = await session.execute({"x": 7}, cdg=graph)

    load_graph.assert_not_awaited()
    assert call_count == 2
    assert result["status"] == "completed"
    assert repeated["trace"][0]["cached"] is False
    output = json.loads(
        (temp_run_dir / "snapshot-run" / "double" / "out_result.json").read_text()
    )
    assert output["value"] == 14


@pytest.mark.anyio
async def test_cdg_execution_session_caching(temp_run_dir):
    """Verify cached outputs are reused without running implementation."""
    call_count = 0
    def count_calls(x: int) -> int:
        nonlocal call_count
        call_count += 1
        return x + 5
        
    REGISTRY["count_calls"] = {
        "impl": count_calls,
        "witness": lambda x: x
    }
    
    mock_nodes = [
        AlgorithmicNode(
            node_id="n_calc",
            name="Calculator",
            description="",
            concept_type="custom",
            status=NodeStatus.ATOMIC,
            depth=0,
            type_signature="",
            inputs=[{"name": "x", "type_desc": "int", "constraints": ""}],
            outputs=[{"name": "y", "type_desc": "int", "constraints": ""}],
            children=[],
            matched_primitive="count_calls"
        )
    ]
    mock_edges = []
    
    session = CDGExecutionSession(driver=None, repo="test_repo", run_id="run_cache")
    
    with patch("sciona.visualizer.runner.load_cdg_from_memgraph", AsyncMock(return_value=(mock_nodes, mock_edges, {}))):
        # First execution (computes and caches)
        res1 = await session.execute(user_inputs={"x": 2})
        assert res1["trace"][0]["cached"] is False
        assert call_count == 1
        
        # Second execution (reuses cache)
        res2 = await session.execute(user_inputs={"x": 2})
        assert res2["trace"][0]["cached"] is True
        assert call_count == 1  # count_calls not executed again!


@pytest.mark.anyio
async def test_cdg_execution_session_grounding_error(temp_run_dir):
    """Verify grounding checks fail if node matched_primitive is not in registry."""
    mock_nodes = [
        AlgorithmicNode(
            node_id="n_ungrounded",
            name="Ungrounded Node",
            description="",
            concept_type="custom",
            status=NodeStatus.ATOMIC,
            depth=0,
            type_signature="",
            inputs=[],
            outputs=[],
            children=[],
            matched_primitive="non_existent_primitive"
        )
    ]
    
    session = CDGExecutionSession(driver=None, repo="test_repo", run_id="run_ground_fail")
    
    with patch("sciona.visualizer.runner.load_cdg_from_memgraph", AsyncMock(return_value=(mock_nodes, [], {}))):
        with pytest.raises(ValueError, match="CDG is not fully grounded"):
            await session.execute(user_inputs={})


class TestAPIEndpoints:
    """Test runner FastAPI routes via TestClient."""

    @patch("sciona.visualizer.runner_api.CDGExecutionSession")
    def test_run_cdg_route(self, mock_session_cls, client):
        """Test POST /api/cdg/run route."""
        mock_instance = AsyncMock()
        mock_instance.execute.return_value = {"run_id": "r1", "status": "completed"}
        mock_session_cls.return_value = mock_instance
        
        resp = client.post("/api/cdg/run?repo=biosppy&run_id=r1", json={"inputs": {"x": 5}})
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    def test_list_cdg_runs_route(self, temp_run_dir, client):
        """Test GET /api/cdg/runs route."""
        # Setup metadata files
        r1_dir = temp_run_dir / "run1"
        r1_dir.mkdir(parents=True)
        with open(r1_dir / "run_metadata.json", "w") as f:
            json.dump({"run_id": "run1", "repo": "biosppy", "timestamp": 1000.0, "status": "completed"}, f)
            
        r2_dir = temp_run_dir / "run2"
        r2_dir.mkdir(parents=True)
        with open(r2_dir / "run_metadata.json", "w") as f:
            json.dump({"run_id": "run2", "repo": "other", "timestamp": 2000.0, "status": "completed"}, f)
            
        resp = client.get("/api/cdg/runs?repo=biosppy")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["runs"]) == 1
        assert data["runs"][0]["run_id"] == "run1"

    def test_list_existing_run_nodes_route(self, temp_run_dir, client):
        """Test GET /api/cdg/runs/{run_id}/existing route."""
        node_dir = temp_run_dir / "run123" / "node_a"
        node_dir.mkdir(parents=True)
        # Create a dummy out_ variable file
        with open(node_dir / "out_res.json", "w") as f:
            json.dump({"value": 42}, f)
            
        resp = client.get("/api/cdg/runs/run123/existing")
        assert resp.status_code == 200
        assert resp.json()["nodes"] == ["node_a"]

    def test_get_cdg_run_returns_replay_snapshot(self, temp_run_dir, client):
        run_dir = temp_run_dir / "run-replay"
        run_dir.mkdir(parents=True)
        metadata = {
            "run_id": "run-replay",
            "repo": "biosppy",
            "timestamp": 1000.0,
            "status": "completed",
            "loss": 0.25,
        }
        graph = {"nodes": [{"node_id": "detect"}], "edges": [], "metadata": {"goal": "Detect"}}
        trace = [{"node_id": "detect", "name": "Detect", "cached": False}]
        evaluation = {"version_id": "refined", "loss": 0.25, "metrics": {}}
        (run_dir / "run_metadata.json").write_text(json.dumps(metadata))
        (run_dir / "cdg.json").write_text(json.dumps(graph))
        (run_dir / "execution_trace.json").write_text(json.dumps(trace))
        (run_dir / "evaluation.json").write_text(json.dumps(evaluation))

        response = client.get("/api/cdg/runs/run-replay")

        assert response.status_code == 200
        assert response.json() == {
            "run_id": "run-replay",
            "metadata": metadata,
            "cdg": graph,
            "trace": trace,
            "evaluation": evaluation,
            "replayable": True,
        }

    def test_get_cdg_run_supports_legacy_artifacts(self, temp_run_dir, client):
        run_dir = temp_run_dir / "legacy-run"
        run_dir.mkdir(parents=True)
        (run_dir / "run_metadata.json").write_text(json.dumps({"run_id": "legacy-run"}))

        response = client.get("/api/cdg/runs/legacy-run")

        assert response.status_code == 200
        assert response.json()["replayable"] is False
        assert response.json()["cdg"] is None

    def test_list_node_variables_route(self, temp_run_dir, client):
        """Test GET /api/cdg/runs/{run_id}/nodes/{node_id}/values."""
        node_dir = temp_run_dir / "r_vals" / "node_x"
        node_dir.mkdir(parents=True)
        with open(node_dir / "in_param.json", "w") as f:
            json.dump({"value": 3.14}, f)
        with open(node_dir / "out_result.json", "w") as f:
            json.dump({"value": 6.28}, f)
            
        resp = client.get("/api/cdg/runs/r_vals/nodes/node_x/values")
        assert resp.status_code == 200
        data = resp.json()
        assert data["inputs"]["param"]["value"] == 3.14
        assert data["outputs"]["result"]["value"] == 6.28

    def test_get_variable_slice_route(self, temp_run_dir, client):
        """Test GET /api/cdg/runs/{run_id}/nodes/{node_id}/values/{value_name}/slice with numpy array."""
        node_dir = temp_run_dir / "r_slice" / "node_arr"
        node_dir.mkdir(parents=True)
        
        arr = np.array([[10, 20], [30, 40]])
        np.save(node_dir / "out_val.npy", arr)
        with open(node_dir / "out_val.json", "w") as f:
            json.dump(get_numpy_statistics(arr), f)
            
        # Slice for column 0: [:, 0] -> [10, 30]
        resp = client.get("/api/cdg/runs/r_slice/nodes/node_arr/values/out_val/slice?slice=[:,0]")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "1d"
        assert data["data"] == [10, 30]

    def test_upload_file_route(self, temp_run_dir, client):
        """Test POST /api/cdg/upload file handler."""
        import io
        file_data = b"dummy file content"
        file_like = io.BytesIO(file_data)
        
        resp = client.post(
            "/api/cdg/upload?run_id=upload_run",
            files={"file": ("test_signal.npy", file_like, "application/octet-stream")}
        )
        assert resp.status_code == 200
        filepath = resp.json()["filepath"]
        assert "uploads" in filepath
        assert "test_signal.npy" in filepath
        assert Path(filepath).exists()
        assert Path(filepath).read_bytes() == file_data
