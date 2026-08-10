"""Tests for the CDG Visualizer API."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture()
def mock_driver():
    """Create a mock Neo4j async driver."""
    driver = AsyncMock()
    return driver


@pytest.fixture()
def client(mock_driver):
    """Create a TestClient with mocked Neo4j driver (no real lifespan)."""
    from contextlib import asynccontextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sciona.visualizer_api import app

    # Replace lifespan to avoid real Neo4j connection
    @asynccontextmanager
    async def _test_lifespan(a: FastAPI):
        a.state.driver = mock_driver
        yield

    app.router.lifespan_context = _test_lifespan

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


class _FakeSession:
    """Mock async session with async context manager support."""

    def __init__(self, run_side_effects):
        self._effects = list(run_side_effects)
        self._call_idx = 0

    async def run(self, *args, **kwargs):
        result = self._effects[self._call_idx]
        self._call_idx += 1
        return result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _make_result(records):
    """Create an async-iterable mock result from a list of dicts."""
    result = AsyncMock()

    async def _aiter(self):
        for rec in records:
            yield rec

    result.__aiter__ = _aiter
    return result


class TestListCDGs:
    def test_returns_list(self, client, mock_driver):
        records = [
            {
                "repo": "biosppy",
                "node_count": 5,
                "concept_types": ["sorting", "filtering"],
                "statuses": ["atomic", "decomposed"],
            }
        ]
        session = _FakeSession([_make_result(records)])
        mock_driver.session = MagicMock(return_value=session)

        resp = client.get("/api/cdgs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["repo"] == "biosppy"
        assert data[0]["node_count"] == 5

    def test_empty_list(self, client, mock_driver):
        session = _FakeSession([_make_result([])])
        mock_driver.session = MagicMock(return_value=session)

        resp = client.get("/api/cdgs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_filter_by_concept_type(self, client, mock_driver):
        records = [
            {
                "repo": "repo_a",
                "node_count": 3,
                "concept_types": ["sorting"],
                "statuses": ["atomic"],
            },
            {
                "repo": "repo_b",
                "node_count": 7,
                "concept_types": ["graph_traversal"],
                "statuses": ["decomposed"],
            },
        ]
        session = _FakeSession([_make_result(records)])
        mock_driver.session = MagicMock(return_value=session)

        resp = client.get("/api/cdgs?concept_type=sorting")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["repo"] == "repo_a"

    def test_filter_by_status(self, client, mock_driver):
        records = [
            {
                "repo": "repo_a",
                "node_count": 3,
                "concept_types": ["sorting"],
                "statuses": ["atomic"],
            },
        ]
        session = _FakeSession([_make_result(records)])
        mock_driver.session = MagicMock(return_value=session)

        resp = client.get("/api/cdgs?status=decomposed")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_filter_by_search_query(self, client, mock_driver):
        records = [
            {
                "repo": "biosppy",
                "node_count": 5,
                "concept_types": ["sorting"],
                "statuses": ["atomic"],
            }
        ]
        session = _FakeSession([_make_result(records)])
        mock_driver.session = MagicMock(return_value=session)

        resp = client.get("/api/cdgs?q=bio")
        assert resp.status_code == 200
        # The `q` filter is applied at the Cypher level, so we just check it passes through
        data = resp.json()
        assert len(data) == 1


class TestGetCDG:
    def test_returns_cdg(self, client, mock_driver):
        node_records = [
            {
                "a": {
                    "node_id": "n1",
                    "name": "Node 1",
                    "description": "A node",
                    "concept_type": "sorting",
                    "status": "atomic",
                    "depth": 0,
                    "type_signature": "list -> list",
                    "is_optional": False,
                    "is_opaque": False,
                    "is_external": False,
                    "parallelizable": False,
                    "conceptual_summary": "",
                },
                "inputs": [{"name": "data", "type_desc": "list", "constraints": ""}],
                "outputs": [{"name": "sorted", "type_desc": "list", "constraints": ""}],
                "children": [],
                "parent_id": None,
            }
        ]
        edge_records = [
            {
                "source_id": "n1",
                "target_id": "n2",
                "output_name": "sorted",
                "input_name": "data",
                "source_type": "list",
                "target_type": "list",
                "requires_glue": False,
            }
        ]

        session = _FakeSession([
            _make_result(node_records),
            _make_result(edge_records),
        ])
        mock_driver.session = MagicMock(return_value=session)

        resp = client.get("/api/cdgs/test_repo")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert "metadata" in data
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["node_id"] == "n1"
        assert len(data["edges"]) == 1
        assert data["metadata"]["repo"] == "test_repo"

    def test_not_found(self, client, mock_driver):
        session = _FakeSession([
            _make_result([]),  # no nodes
        ])
        mock_driver.session = MagicMock(return_value=session)

        resp = client.get("/api/cdgs/nonexistent")
        assert resp.status_code == 404

    def test_edges_empty(self, client, mock_driver):
        node_records = [
            {
                "a": {
                    "node_id": "n1",
                    "name": "Leaf",
                    "description": "",
                    "concept_type": "",
                    "status": "atomic",
                    "depth": 0,
                    "type_signature": "",
                    "is_optional": False,
                    "is_opaque": False,
                    "is_external": False,
                    "parallelizable": False,
                    "conceptual_summary": "",
                },
                "inputs": [],
                "outputs": [],
                "children": [],
                "parent_id": None,
            }
        ]
        session = _FakeSession([
            _make_result(node_records),
            _make_result([]),
        ])
        mock_driver.session = MagicMock(return_value=session)

        resp = client.get("/api/cdgs/leaf_repo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["edges"] == []


class TestStaticFiles:
    def test_index_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "CDG Visualizer" in resp.text


class TestDashboardAPI:
    def test_list_runs_and_latest(self, client, monkeypatch, tmp_path):
        from sciona.telemetry import reset_telemetry_runtime

        reset_telemetry_runtime()
        now = time.time()
        payload = {
            "run_id": "abc123",
            "pipeline": "algorithm_creation",
            "status": "running",
            "started_at": now - 3.0,
            "last_update_at": now - 1.0,
            "stages": {},
            "prompt_by_key": {},
            "inflight_prompts": {},
            "prompt_dispatches": 2,
            "prompt_successes": 1,
            "prompt_failures": 0,
            "prompt_inflight": 1,
            "events_count": 4,
            "metadata": {"goal": "test"},
        }
        (tmp_path / "run_abc123.json").write_text(json.dumps(payload))
        monkeypatch.setenv("SCIONA_TELEMETRY_RUNS_DIR", str(tmp_path))

        resp = client.get("/api/dashboard/runs")
        assert resp.status_code == 200
        data = resp.json()
        run_ids = [r.get("run_id") for r in data["runs"]]
        assert "abc123" in run_ids

        latest = client.get("/api/dashboard/latest")
        assert latest.status_code == 200
        assert latest.json()["run_id"] == "abc123"

    def test_dashboard_run_includes_routing_and_retrieval_summaries(
        self, client, monkeypatch, tmp_path
    ):
        from sciona.telemetry import reset_telemetry_runtime

        reset_telemetry_runtime()
        now = time.time()
        payload = {
            "run_id": "route123",
            "pipeline": "algorithm_creation",
            "status": "running",
            "started_at": now - 2.0,
            "last_update_at": now - 1.0,
            "stages": {},
            "prompt_by_key": {},
            "inflight_prompts": {},
            "prompt_dispatches": 1,
            "prompt_successes": 1,
            "prompt_failures": 0,
            "prompt_inflight": 0,
            "events_count": 2,
            "metadata": {
                "execution_mode": "structured",
                "execution_path": "structured_single_pass",
                "rapid_direct_path": False,
                "retrieval_policy": {
                    "confidence_band": "high",
                    "skill_index": True,
                    "graph_retrieval": False,
                    "semantic_backend": "lexical",
                    "hunter_mode": "standard",
                },
                "llm_routing": {
                    "architect": {
                        "default_provider": "anthropic",
                        "default_model": "claude-sonnet",
                        "active_overrides": [
                            {
                                "prompt_key": "architect_strategy",
                                "provider": "codex_shim",
                                "model": "gpt-5.3-codex",
                            }
                        ],
                        "suppressed_default_overrides": [],
                        "custom_nonbenchmark_overrides": [],
                    },
                    "hunter": {
                        "default_provider": "llama_cpp",
                        "default_model": "qwen2.5-coder:7b",
                        "active_overrides": [],
                        "suppressed_default_overrides": ["hunter_unused"],
                        "custom_nonbenchmark_overrides": [],
                    },
                },
                "catalog_alignment": {
                    "catalog_size": 488,
                    "total_candidates": 473,
                    "added": 450,
                    "merged": 12,
                    "structural_skips": 11,
                    "source_live_registry_candidates": 120,
                    "source_ast_candidates": 353,
                    "source_cdg_metadata_matches": 200,
                    "source_witness_doc_fallbacks": 30,
                    "source_witness_signature_fallbacks": 18,
                    "source_breakdown": {
                        "sciona-atoms": {
                            "added": 127,
                            "live_registry_candidates": 100,
                            "ast_candidates": 27,
                        },
                        "hpy-atoms": {
                            "added": 7,
                            "live_registry_candidates": 0,
                            "ast_candidates": 7,
                        },
                    },
                    "merge_details": [
                        {
                            "candidate": "heap_sort_v2",
                            "incumbent": "heapsort",
                            "similarity": 0.92,
                        },
                        {
                            "candidate": "stable_bandpass",
                            "incumbent": "design_bandpass_filter",
                            "similarity": 0.88,
                        },
                    ],
                },
            },
        }
        (tmp_path / "run_route123.json").write_text(json.dumps(payload))
        monkeypatch.setenv("SCIONA_TELEMETRY_RUNS_DIR", str(tmp_path))

        resp = client.get("/api/dashboard/runs/route123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution_summary"]["mode"] == "structured"
        assert data["execution_summary"]["path"] == "structured_single_pass"
        assert data["execution_summary"]["rapid_direct"] is False
        assert data["retrieval_summary"]["confidence_band"] == "high"
        assert data["retrieval_summary"]["semantic_backend"] == "lexical"
        assert data["routing_summary"]["architect"]["default"] == "anthropic:claude-sonnet"
        assert data["routing_summary"]["architect"]["active_count"] == 1
        assert data["routing_summary"]["hunter"]["suppressed_count"] == 1
        assert data["catalog_alignment_summary"]["catalog_size"] == 488
        assert data["catalog_alignment_summary"]["added"] == 450
        assert data["catalog_alignment_summary"]["merged"] == 12
        assert data["catalog_alignment_summary"]["live_registry"] == 120
        assert data["catalog_alignment_summary"]["ast_fallback"] == 353
        assert data["catalog_alignment_summary"]["source_count"] == 2
        assert data["catalog_alignment_summary"]["top_sources"] == [
            {
                "source": "sciona-atoms",
                "added": 127,
                "live_registry_candidates": 100,
                "ast_candidates": 27,
            },
            {
                "source": "hpy-atoms",
                "added": 7,
                "live_registry_candidates": 0,
                "ast_candidates": 7,
            },
        ]
        assert data["catalog_alignment_summary"]["top_merges"] == [
            {
                "candidate": "heap_sort_v2",
                "incumbent": "heapsort",
                "similarity": 0.92,
            },
            {
                "candidate": "stable_bandpass",
                "incumbent": "design_bandpass_filter",
                "similarity": 0.88,
            },
        ]
        assert data["provider_complexity"]["provider_count"] == 3
        assert data["provider_complexity"]["provider_model_count"] == 3
        assert sorted(data["provider_complexity"]["transports"]) == [
            "api",
            "local_server",
            "persistent_shim",
        ]

    def test_dashboard_run_includes_benchmark_and_release_validation_summaries(
        self, client, monkeypatch, tmp_path
    ):
        from sciona.telemetry import reset_telemetry_runtime

        reset_telemetry_runtime()
        now = time.time()
        payload = {
            "run_id": "bench123",
            "pipeline": "release_validation",
            "status": "completed",
            "started_at": now - 5.0,
            "last_update_at": now - 1.0,
            "ended_at": now - 1.0,
            "stages": {},
            "prompt_by_key": {},
            "inflight_prompts": {},
            "prompt_dispatches": 0,
            "prompt_successes": 0,
            "prompt_failures": 0,
            "prompt_inflight": 0,
            "events_count": 1,
            "metadata": {
                "benchmark_validation": {
                    "status": "failed",
                    "summary_report": "build/release_validation/benchmarks/summary.json",
                    "prompt_report": "build/release_validation/benchmarks/prompt_benchmark.json",
                    "flow_report": "build/release_validation/benchmarks/flow_benchmark.json",
                    "prompt_cases": 12,
                    "prompt_results": 24,
                    "prompt_summary": "prompt summary",
                    "prompt_stability_summary": "fixture_good/tuned 12/12",
                    "flow_cases": 4,
                    "flow_results": 16,
                    "flow_summary": "flow summary",
                    "flow_stability_summary": "rapid 4/4, verified 4/4",
                    "flow_gate_summary": "required[structured,verified] 0/0; comparison[direct_baseline,rapid] 2/0",
                    "flow_execution_path_summary": "rapid=rapid_direct, structured=structured_single_pass, verified=verified_orchestration",
                    "runtime_override_policy_summary": "rapid=0/0/0, structured=0/0/0, verified=1/1/1",
                    "health_summary": "warnings=subcheck=comparison_failures warning=flow_comparison_failures=2 failures=subcheck=runtime_budget failure=legacy_providers_present=codex_cli",
                    "warning_summary": "subcheck=comparison_failures warning=flow_comparison_failures=2",
                    "top_warning_subcheck": "comparison_failures",
                    "top_warning": "flow_comparison_failures=2",
                    "failure_summary": "subcheck=runtime_budget failure=legacy_providers_present=codex_cli",
                    "top_failed_subcheck": "runtime_budget",
                    "top_failure": "legacy_providers_present=codex_cli",
                    "flow_required_variants": ["structured", "verified"],
                    "flow_comparison_variants": ["direct_baseline", "rapid"],
                    "flow_execution_paths": {
                        "expected": {
                            "rapid": "rapid_direct",
                            "structured": "structured_single_pass",
                            "verified": "verified_orchestration",
                        },
                        "observed": {
                            "rapid": ["rapid_direct"],
                            "structured": ["structured_single_pass"],
                            "verified": ["verified_orchestration"],
                        },
                        "violations": [],
                    },
                    "flow_prompt_volume": {
                        "averages": {
                            "direct_baseline": 2.0,
                            "rapid": 6.0,
                            "structured": 7.0,
                            "verified": 8.0,
                        },
                        "violations": [],
                    },
                    "flow_prompt_volume_summary": "direct_baseline=2.0, rapid=6.0, structured=7.0, verified=8.0",
                    "single_agent_comparison": {
                        "present": True,
                        "overhead_driver": "tool_chatter",
                        "avg_planner_tool_dispatches": 4.0,
                        "avg_planner_escalations": 1.0,
                    },
                    "single_agent_comparison_summary": "driver=tool_chatter",
                    "flow_avg_prompt_calls": {"rapid": 6.0, "verified": 7.0},
                    "flow_avg_planner_tool_dispatches": {
                        "rapid": 0.0,
                        "single_agent": 4.0,
                    },
                    "flow_avg_planner_tool_latency_ms": {
                        "rapid": 0.0,
                        "single_agent": 180.0,
                    },
                    "flow_avg_planner_escalations": {
                        "rapid": 0.0,
                        "single_agent": 1.0,
                    },
                    "prompt_avg_latency_ms": {
                        "codex_shim:tuned": 3954.8,
                        "gemini_shim:tuned": 3771.8,
                    },
                    "flow_avg_latency_ms": {"rapid": 412.5, "verified": 611.2},
                    "runtime_complexity": {
                        "provider_count": 5,
                        "transport_count": 4,
                        "override_policy": {
                            "required_active_overrides": [
                                {"prompt_key": "hunter_score", "provider": "codex_shim"}
                            ],
                            "missing_required_overrides": [
                                {"prompt_key": "hunter_score", "provider": "codex_shim"}
                            ],
                            "unexpected_active_overrides": [
                                {"prompt_key": "hunter_score", "provider": "anthropic"}
                            ],
                        },
                        "by_mode": {
                            "rapid": {
                                "provider_count": 2,
                                "provider_model_count": 2,
                                "transport_count": 2,
                            },
                            "structured": {
                                "provider_count": 2,
                                "provider_model_count": 2,
                                "transport_count": 2,
                            },
                            "verified": {
                                "provider_count": 5,
                                "provider_model_count": 6,
                                "transport_count": 4,
                            },
                        },
                        "violations": ["legacy_providers_present=codex_cli"],
                    },
                    "prompt_tuned_failures": 0,
                    "prompt_tuned_unstable_groups": 0,
                    "flow_mode_failures": 0,
                    "flow_mode_unstable_groups": 0,
                    "flow_comparison_failures": 2,
                    "flow_comparison_unstable_groups": 0,
                },
                "release_validation": {
                    "manifest": "build/release_validation/release_validation.json",
                    "benchmarks_dir": "build/release_validation/benchmarks",
                    "status": "passed",
                    "warning_summary": "runtime=1 top=legacy_providers_present=codex_cli catalog=0",
                    "runtime_warning_count": 1,
                    "catalog_warning_count": 0,
                    "benchmark_warning_count": 1,
                    "top_runtime_warning": "legacy_providers_present=codex_cli",
                    "top_catalog_warning": "",
                    "top_benchmark_warning": "flow_comparison_failures=2",
                    "failure_summary": "check=runtime_complexity benchmark_check=runtime_budget benchmark=legacy_providers_present=codex_cli runtime=legacy_providers_present=codex_cli catalog=missing_source:hpy-atoms",
                    "top_failed_check": "runtime_complexity",
                    "top_benchmark_subcheck": "runtime_budget",
                    "top_benchmark_failure": "legacy_providers_present=codex_cli",
                    "top_runtime_failure": "legacy_providers_present=codex_cli",
                    "top_catalog_failure": "missing_source:hpy-atoms",
                    "catalog_validation": {
                        "status": "failed",
                        "report": "build/release_validation/catalog/catalog_validation.json",
                        "configured_sources": 2,
                        "resolved_sources": 1,
                        "source_candidates": 3,
                        "source_added": 3,
                        "coverage_summary": "resolved=1/2 added=3/3 missing=1 zero=1",
                        "alignment_summary": "severity=critical matched=3 registry_only=1 ast_only=2 drift=1",
                        "warning_summary": "warnings=0 high=0 medium=0",
                        "high_severity_sources": [],
                        "medium_severity_sources": [],
                        "warnings": [],
                        "missing_sources": ["hpy-atoms"],
                        "zero_candidate_sources": ["hpy-atoms"],
                        "violations": ["missing_source:hpy-atoms"],
                        "alignment": {
                            "source_count": 2,
                            "matched_total": 3,
                            "registry_only_total": 1,
                            "ast_only_total": 2,
                            "highest_severity": "critical",
                            "severity_counts": {
                                "healthy": 1,
                                "medium": 0,
                                "high": 0,
                                "critical": 1,
                            },
                            "drift_sources": ["hpy-atoms"],
                            "registry_error_sources": ["hpy-atoms"],
                            "rows": [
                                {
                                    "source": "hpy-atoms",
                                    "registry_only_count": 1,
                                    "ast_only_count": 2,
                                    "severity": "critical",
                                    "registry_only_examples": ["live_only_atom"],
                                    "ast_only_examples": ["ast_only_a", "ast_only_b"],
                                }
                            ],
                        },
                    },
                },
            },
        }
        (tmp_path / "run_bench123.json").write_text(json.dumps(payload))
        monkeypatch.setenv("SCIONA_TELEMETRY_RUNS_DIR", str(tmp_path))

        resp = client.get("/api/dashboard/runs/bench123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["benchmark_summary"]["prompt_cases"] == 12
        assert data["benchmark_summary"]["prompt_results"] == 24
        assert data["benchmark_summary"]["flow_cases"] == 4
        assert data["benchmark_summary"]["flow_results"] == 16
        assert data["benchmark_summary"]["status"] == "failed"
        assert data["benchmark_summary"]["prompt_stability_summary"] == "fixture_good/tuned 12/12"
        assert data["benchmark_summary"]["flow_stability_summary"] == "rapid 4/4, verified 4/4"
        assert data["benchmark_summary"]["flow_avg_prompt_calls"]["rapid"] == 6.0
        assert data["benchmark_summary"]["flow_avg_planner_tool_dispatches"]["single_agent"] == 4.0
        assert data["benchmark_summary"]["prompt_avg_latency_ms"]["codex_shim:tuned"] == 3954.8
        assert data["benchmark_summary"]["flow_avg_latency_ms"]["verified"] == 611.2
        assert data["benchmark_summary"]["runtime_complexity"]["provider_count"] == 5
        assert data["benchmark_summary"]["runtime_complexity"]["by_mode"]["rapid"]["provider_count"] == 2
        assert data["benchmark_summary"]["runtime_complexity"]["override_policy"]["missing_required_overrides"][0]["prompt_key"] == "hunter_score"
        assert data["benchmark_summary"]["prompt_tuned_failures"] == 0
        assert data["benchmark_summary"]["flow_mode_failures"] == 0
        assert "required[structured,verified]" in data["benchmark_summary"]["flow_gate_summary"]
        assert "structured=structured_single_pass" in data["benchmark_summary"]["flow_execution_path_summary"]
        assert "verified=8.0" in data["benchmark_summary"]["flow_prompt_volume_summary"]
        assert "verified=1/1/1" in data["benchmark_summary"]["runtime_override_policy_summary"]
        assert data["benchmark_summary"]["single_agent_comparison"]["overhead_driver"] == "tool_chatter"
        assert data["benchmark_summary"]["single_agent_comparison_summary"] == "driver=tool_chatter"
        assert data["benchmark_summary"]["health_summary"] == (
            "warnings=subcheck=comparison_failures warning=flow_comparison_failures=2 "
            "failures=subcheck=runtime_budget failure=legacy_providers_present=codex_cli"
        )
        assert data["benchmark_summary"]["warning_summary"] == (
            "subcheck=comparison_failures warning=flow_comparison_failures=2"
        )
        assert data["benchmark_summary"]["top_warning_subcheck"] == "comparison_failures"
        assert data["benchmark_summary"]["top_warning"] == "flow_comparison_failures=2"
        assert data["benchmark_summary"]["failure_summary"] == (
            "subcheck=runtime_budget failure=legacy_providers_present=codex_cli"
        )
        assert data["benchmark_summary"]["top_failed_subcheck"] == "runtime_budget"
        assert data["benchmark_summary"]["top_failure"] == "legacy_providers_present=codex_cli"
        assert data["benchmark_summary"]["flow_required_variants"] == ["structured", "verified"]
        assert set(data["benchmark_summary"]["flow_comparison_variants"]) == {
            "direct_baseline",
            "rapid",
        }
        assert data["benchmark_summary"]["flow_execution_paths"]["observed"]["rapid"] == ["rapid_direct"]
        assert data["benchmark_summary"]["flow_comparison_failures"] == 2
        assert data["benchmark_summary"]["release_status"] == "passed"
        assert data["benchmark_summary"]["release_warning_summary"] == "runtime=1 top=legacy_providers_present=codex_cli catalog=0"
        assert data["benchmark_summary"]["release_runtime_warning_count"] == 1
        assert data["benchmark_summary"]["release_catalog_warning_count"] == 0
        assert data["benchmark_summary"]["release_benchmark_warning_count"] == 1
        assert data["benchmark_summary"]["release_top_runtime_warning"] == "legacy_providers_present=codex_cli"
        assert data["benchmark_summary"]["release_top_catalog_warning"] == ""
        assert data["benchmark_summary"]["release_top_benchmark_warning"] == "flow_comparison_failures=2"
        assert data["benchmark_summary"]["release_failure_summary"].startswith(
            "check=runtime_complexity benchmark_check=runtime_budget "
        )
        assert data["benchmark_summary"]["release_top_failed_check"] == "runtime_complexity"
        assert data["benchmark_summary"]["release_top_benchmark_subcheck"] == "runtime_budget"
        assert data["benchmark_summary"]["release_top_benchmark_failure"] == "legacy_providers_present=codex_cli"
        assert data["benchmark_summary"]["release_top_runtime_failure"] == "legacy_providers_present=codex_cli"
        assert data["benchmark_summary"]["release_top_catalog_failure"] == "missing_source:hpy-atoms"
        assert data["benchmark_summary"]["manifest"].endswith("release_validation.json")
        assert data["catalog_validation_summary"]["status"] == "failed"
        assert data["catalog_validation_summary"]["missing_sources"] == ["hpy-atoms"]
        assert "resolved=1/2" in data["catalog_validation_summary"]["coverage_summary"]
        assert "severity=critical" in data["catalog_validation_summary"]["alignment_summary"]
        assert data["catalog_validation_summary"]["alignment"]["registry_only_total"] == 1
        assert data["catalog_validation_summary"]["alignment"]["ast_only_total"] == 2
        assert data["catalog_validation_summary"]["alignment"]["highest_severity"] == "critical"
        assert data["catalog_validation_summary"]["top_drift_sources"][0]["source"] == "hpy-atoms"
        assert data["catalog_validation_summary"]["top_drift_sources"][0]["severity"] == "critical"
        assert data["catalog_validation_summary"]["top_drift_sources"][0]["registry_only_examples"] == ["live_only_atom"]

    def test_dashboard_run_includes_shared_context_summary(
        self, client, monkeypatch, tmp_path
    ):
        from sciona.telemetry import reset_telemetry_runtime

        reset_telemetry_runtime()
        now = time.time()
        payload = {
            "run_id": "shared123",
            "pipeline": "algorithm_creation",
            "status": "completed",
            "started_at": now - 12.0,
            "last_update_at": now - 1.0,
            "ended_at": now - 1.0,
            "stages": {},
            "prompt_by_key": {},
            "inflight_prompts": {},
            "prompt_dispatches": 4,
            "prompt_successes": 4,
            "prompt_failures": 0,
            "prompt_inflight": 0,
            "events_count": 10,
            "metadata": {
                "shared_context": {
                    "metrics_path": "build/demo/shared_context_metrics.json",
                    "contexts": {
                        "architect": {
                            "backend": "postgres",
                            "searches_total": 5,
                            "search_hits": 3,
                            "puts_total": 7,
                            "injected_blocks": 2,
                            "promotions_total": 1,
                            "template_searches_total": 2,
                            "template_search_hits": 1,
                            "template_puts_total": 3,
                            "template_injected_blocks": 1,
                            "failure_searches_total": 3,
                            "failure_search_hits": 2,
                            "failure_puts_total": 2,
                            "failure_injected_blocks": 1,
                        },
                        "hunter": {
                            "backend": "postgres",
                            "searches_total": 4,
                            "search_hits": 1,
                            "puts_total": 2,
                            "injected_blocks": 1,
                            "promotions_total": 0,
                            "template_searches_total": 0,
                            "template_search_hits": 0,
                            "template_puts_total": 0,
                            "template_injected_blocks": 0,
                            "failure_searches_total": 1,
                            "failure_search_hits": 0,
                            "failure_puts_total": 1,
                            "failure_injected_blocks": 0,
                        },
                    },
                }
            },
        }
        (tmp_path / "run_shared123.json").write_text(json.dumps(payload))
        monkeypatch.setenv("SCIONA_TELEMETRY_RUNS_DIR", str(tmp_path))

        resp = client.get("/api/dashboard/runs/shared123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["shared_context_summary"]["context_count"] == 2
        assert data["shared_context_summary"]["active_context_count"] == 2
        assert data["shared_context_summary"]["total_searches"] == 9
        assert data["shared_context_summary"]["total_hits"] == 4
        assert data["shared_context_summary"]["total_puts"] == 9
        assert data["shared_context_summary"]["total_template_hits"] == 1
        assert data["shared_context_summary"]["total_template_puts"] == 3
        assert data["shared_context_summary"]["total_failure_searches"] == 4
        assert data["shared_context_summary"]["total_failure_hits"] == 2
        assert data["shared_context_summary"]["total_failure_puts"] == 3
        assert data["shared_context_summary"]["metrics_path"].endswith(
            "shared_context_metrics.json"
        )
        assert data["shared_context_summary"]["backends"] == ["postgres"]
        assert [row["label"] for row in data["shared_context_summary"]["contexts"]] == [
            "architect",
            "hunter",
        ]

    def test_dashboard_run_includes_optimize_summary(
        self, client, monkeypatch, tmp_path
    ):
        from sciona.telemetry import reset_telemetry_runtime

        reset_telemetry_runtime()
        now = time.time()
        payload = {
            "run_id": "opt123",
            "pipeline": "optimization",
            "status": "completed",
            "started_at": now - 18.0,
            "last_update_at": now - 1.0,
            "ended_at": now - 1.0,
            "stages": {},
            "prompt_by_key": {},
            "inflight_prompts": {},
            "prompt_dispatches": 0,
            "prompt_successes": 0,
            "prompt_failures": 0,
            "prompt_inflight": 0,
            "events_count": 3,
            "metadata": {
                "command": "optimize",
                "optimize": {
                    "objective": "rmse",
                    "execution_metric": "precision",
                    "benchmark_path": "datasets/private-evaluation-v1/adapter.yml",
                    "max_trials": 6,
                    "trials_run": 4,
                    "best_loss": 8.55304557280547,
                    "best_trial": 3,
                    "parameterized_trials": 4,
                    "primitive_change_trials": 1,
                    "topology_change_trials": 0,
                    "expansion_applied_trials": 2,
                    "rollback_trials": 1,
                    "proposal_selection_trials": 3,
                    "proposal_rejected_trials": 1,
                    "cached_reuse_trials": 1,
                    "cached_reruns_avoided": 1,
                    "selected_proposal_counts": {
                        "expansion": 1,
                        "local_mutation": 1,
                    },
                    "skeleton_proposal_trials": 2,
                    "accepted_skeleton_proposals": 1,
                    "rejected_skeleton_proposals": 1,
                    "mean_skeleton_complexity_penalty": 0.95,
                    "mean_skeleton_objective_gain": 1.42,
                    "skeleton_retention_rate": 1.0,
                    "unique_primitive_signatures": 2,
                    "unique_topologies": 1,
                    "expansion_rules_applied": [
                        "insert_sqi_before_filter",
                        "insert_outlier_rejection_after_detection_smoothed",
                    ],
                    "max_family_entropy": 0.811278,
                    "max_distinct_primitive_families": 2,
                    "mean_expansion_loss_delta": 0.732,
                    "worst_expansion_loss_delta": 1.125,
                    "mean_selected_proposal_improvement": 1.486,
                    "best_selected_proposal_improvement": 1.971,
                    "trial_history_path": "output/principal_optimize_20260319_140027/trial_history.json",
                    "best_structure": {
                        "node_count": 4,
                        "edge_count": 2,
                        "topo_hash": "05c468d81ba2b726",
                        "primitive_signature": "c3fe45feb08a06c7",
                        "distinct_primitive_family_count": 2,
                        "family_entropy": 0.811278,
                        "cross_family_edge_count": 1,
                    },
                    "best_parameter_assignments": {
                        "n_filter": {"filter_order": 4},
                        "n_rate": {"smoothing_window": 5},
                    },
                    "trial_rows": [
                        {
                            "trial": 1,
                            "loss": 10.524294403000809,
                            "node_count": 4,
                            "edge_count": 2,
                            "primitive_signature": "sig_a",
                            "has_parameters": True,
                            "parameter_node_count": 2,
                            "topology_changed": False,
                            "primitive_assignment_changed": False,
                            "expansion_applied": False,
                            "distinct_primitive_family_count": 1,
                            "family_entropy": 0.0,
                            "cross_family_edge_count": 0,
                            "rollback_applied": False,
                            "rollback_restored_trial": 0,
                            "rollback_reason": "",
                            "reused_cached_evaluation": False,
                            "proposal_selected": "",
                            "proposal_candidate_count": 2,
                            "proposal_candidates": [
                                "expansion",
                                "local_mutation",
                            ],
                            "proposal_rejected": True,
                            "proposal_baseline_loss": 10.524294403000809,
                            "proposal_selected_loss": None,
                            "proposal_improvement": None,
                            "proposal_selection_record": {
                                "selected_proposal": "",
                                "selected_kind": "none",
                                "proposal_candidates": [
                                    {"label": "expansion", "loss": 10.7},
                                    {"label": "local_mutation", "loss": 10.9},
                                ],
                                "skipped_due_to_admissibility": True,
                                "skip_reason": "hard_reject",
                            },
                            "skeleton_proposal": {
                                "target_node": "Estimate Event Rate",
                                "source_family": "signal_filter",
                                "inserted_node_count": 3,
                                "inserted_edge_count": 2,
                                "complexity_penalty": 0.9,
                                "objective_gain": 1.1,
                                "accepted": False,
                                "retained": False,
                                "reverted": False,
                            },
                        },
                        {
                            "trial": 3,
                            "loss": 8.55304557280547,
                            "node_count": 4,
                            "edge_count": 2,
                            "primitive_signature": "sig_b",
                            "has_parameters": True,
                            "parameter_node_count": 3,
                            "topology_changed": False,
                            "primitive_assignment_changed": True,
                            "expansion_applied": True,
                            "distinct_primitive_family_count": 2,
                            "family_entropy": 0.811278,
                            "cross_family_edge_count": 1,
                            "rollback_applied": True,
                            "rollback_restored_trial": 2,
                            "rollback_reason": "expanded structure increased objective loss",
                            "reused_cached_evaluation": True,
                            "proposal_selected": "expansion",
                            "proposal_candidate_count": 2,
                            "proposal_candidates": [
                                "expansion",
                                "local_mutation",
                            ],
                            "proposal_rejected": False,
                            "proposal_baseline_loss": 10.524294403000809,
                            "proposal_selected_loss": 8.55304557280547,
                            "proposal_improvement": 1.9712488301953387,
                            "proposal_selection_record": {
                                "selected_proposal": "expansion",
                                "selected_kind": "expansion",
                                "proposal_candidates": [
                                    {"label": "expansion", "loss": 8.55304557280547},
                                    {"label": "local_mutation", "loss": 8.9},
                                ],
                                "proposal_improvement": 1.9712488301953387,
                            },
                            "skeleton_proposal": {
                                "target_node": "Estimate Event Rate",
                                "source_family": "sequential_filter",
                                "inserted_node_count": 3,
                                "inserted_edge_count": 2,
                                "complexity_penalty": 1.0,
                                "objective_gain": 1.74,
                                "accepted": True,
                                "retained": True,
                                "reverted": False,
                            },
                        },
                    ],
                }
            },
        }
        (tmp_path / "run_opt123.json").write_text(json.dumps(payload))
        monkeypatch.setenv("SCIONA_TELEMETRY_RUNS_DIR", str(tmp_path))

        resp = client.get("/api/dashboard/runs/opt123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["optimize_summary"]["objective"] == "rmse"
        assert data["optimize_summary"]["execution_metric"] == "precision"
        assert data["optimize_summary"]["best_loss"] == 8.55304557280547
        assert data["optimize_summary"]["best_trial"] == 3
        assert data["optimize_summary"]["parameterized_trials"] == 4
        assert data["optimize_summary"]["primitive_change_trials"] == 1
        assert data["optimize_summary"]["expansion_applied_trials"] == 2
        assert data["optimize_summary"]["rollback_trials"] == 1
        assert data["optimize_summary"]["proposal_selection_trials"] == 3
        assert data["optimize_summary"]["proposal_rejected_trials"] == 1
        assert data["optimize_summary"]["cached_reuse_trials"] == 1
        assert data["optimize_summary"]["cached_reruns_avoided"] == 1
        assert data["optimize_summary"]["selected_proposal_counts"] == {
            "expansion": 1,
            "local_mutation": 1,
        }
        assert data["optimize_summary"]["skeleton_proposal_trials"] == 2
        assert data["optimize_summary"]["accepted_skeleton_proposals"] == 1
        assert data["optimize_summary"]["rejected_skeleton_proposals"] == 1
        assert data["optimize_summary"]["mean_skeleton_complexity_penalty"] == 0.95
        assert data["optimize_summary"]["mean_skeleton_objective_gain"] == 1.42
        assert data["optimize_summary"]["skeleton_retention_rate"] == 1.0
        assert data["optimize_summary"]["unique_primitive_signatures"] == 2
        assert data["optimize_summary"]["max_distinct_primitive_families"] == 2
        assert data["optimize_summary"]["mean_expansion_loss_delta"] == 0.732
        assert data["optimize_summary"]["worst_expansion_loss_delta"] == 1.125
        assert data["optimize_summary"]["mean_selected_proposal_improvement"] == 1.486
        assert data["optimize_summary"]["best_selected_proposal_improvement"] == 1.971
        assert set(data["optimize_summary"]["expansion_rules_applied"]) == {
            "insert_sqi_before_filter",
            "insert_outlier_rejection_after_detection_smoothed",
        }
        assert (
            data["optimize_summary"]["best_structure"]["primitive_signature"]
            == "c3fe45feb08a06c7"
        )
        assert data["optimize_summary"]["best_structure"]["cross_family_edge_count"] == 1
        assert (
            data["optimize_summary"]["best_parameter_assignments"]["n_rate"][
                "smoothing_window"
            ]
            == 5
        )
        assert data["optimize_summary"]["trial_rows"][1][
            "primitive_assignment_changed"
        ] is True
        assert data["optimize_summary"]["trial_rows"][1]["expansion_applied"] is True
        assert data["optimize_summary"]["trial_rows"][1]["rollback_applied"] is True
        assert data["optimize_summary"]["trial_rows"][1]["rollback_restored_trial"] == 2
        assert data["optimize_summary"]["trial_rows"][0]["proposal_rejected"] is True
        assert data["optimize_summary"]["trial_rows"][0]["reused_cached_evaluation"] is False
        assert data["optimize_summary"]["trial_rows"][0]["skeleton_proposal"]["accepted"] is False
        assert data["optimize_summary"]["trial_rows"][0]["skeleton_proposal"]["target_node"] == "Estimate Event Rate"
        assert data["optimize_summary"]["trial_rows"][1]["proposal_selected"] == "expansion"
        assert data["optimize_summary"]["trial_rows"][1]["proposal_candidate_count"] == 2
        assert data["optimize_summary"]["trial_rows"][1]["reused_cached_evaluation"] is True
        assert data["optimize_summary"]["trial_rows"][1]["skeleton_proposal"]["accepted"] is True
        assert data["optimize_summary"]["trial_rows"][1]["skeleton_proposal"]["retained"] is True

    def test_dashboard_run_includes_single_agent_summary(
        self, client, monkeypatch, tmp_path
    ):
        from sciona.telemetry import reset_telemetry_runtime

        reset_telemetry_runtime()
        now = time.time()
        payload = {
            "run_id": "singleagent123",
            "pipeline": "algorithm_creation",
            "status": "completed",
            "started_at": now - 8.0,
            "last_update_at": now - 1.0,
            "ended_at": now - 1.0,
            "stages": {},
            "prompt_by_key": {},
            "inflight_prompts": {},
            "prompt_dispatches": 1,
            "prompt_successes": 1,
            "prompt_failures": 0,
            "prompt_inflight": 0,
            "events_count": 6,
            "metadata": {
                "execution_mode": "single_agent",
                "execution_path": "single_agent_direct",
                "single_agent": {
                    "termination_reason": "direct_verified",
                    "verification_status": "verified",
                    "step_budget": 6,
                    "steps_used": 1,
                    "tool_dispatch_count_total": 1,
                    "tool_latency_ms_total": 1.25,
                    "tool_metrics": {
                        "hunter.match_goal": {
                            "dispatches": 1,
                            "latency_ms_total": 1.25,
                            "avg_latency_ms": 1.25,
                        }
                    },
                    "escalation_events": [],
                    "open_failures": [],
                    "attempt_history": ["direct_match"],
                    "artifact_manifest_path": "build/demo/planner_artifacts.json",
                    "concrete_artifacts": {
                        "cdg": {
                            "source": "direct_goal_cdg",
                            "path": "build/demo/cdg.json",
                            "exists": True,
                            "mutations": 1,
                        },
                        "match_results": {
                            "source": "direct_match_result",
                            "path": "build/demo/matches.json",
                            "exists": True,
                            "mutations": 1,
                        },
                    },
                    "policy": {
                        "direct_grounding_enabled": True,
                        "decomposition_mode": "single_pass",
                        "retrieval_intensity": "light",
                        "repair_policy": "bounded",
                    },
                },
            },
        }
        (tmp_path / "run_singleagent123.json").write_text(json.dumps(payload))
        monkeypatch.setenv("SCIONA_TELEMETRY_RUNS_DIR", str(tmp_path))

        resp = client.get("/api/dashboard/runs/singleagent123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["execution_summary"]["mode"] == "single_agent"
        assert data["single_agent_summary"]["termination_reason"] == "direct_verified"
        assert data["single_agent_summary"]["verification_status"] == "verified"
        assert data["single_agent_summary"]["step_budget"] == 6
        assert data["single_agent_summary"]["steps_used"] == 1
        assert data["single_agent_summary"]["artifact_manifest_path"].endswith(
            "planner_artifacts.json"
        )
        assert data["single_agent_summary"]["artifact_count"] == 2
        assert data["single_agent_summary"]["artifacts"][0]["name"] == "cdg"
        assert data["single_agent_summary"]["tool_dispatch_count_total"] == 1
        assert data["single_agent_summary"]["tool_metrics"][0]["name"] == "hunter.match_goal"
        assert data["single_agent_summary"]["escalation_events"] == []
        assert data["single_agent_summary"]["policy"]["retrieval_intensity"] == "light"

    def test_dashboard_run_includes_architect_summary(
        self, client, monkeypatch, tmp_path
    ):
        from sciona.telemetry import reset_telemetry_runtime

        reset_telemetry_runtime()
        now = time.time()
        payload = {
            "run_id": "architect123",
            "pipeline": "decompose",
            "status": "failed",
            "started_at": now - 9.0,
            "last_update_at": now - 1.0,
            "ended_at": now - 1.0,
            "stages": {},
            "prompt_by_key": {},
            "inflight_prompts": {},
            "prompt_dispatches": 3,
            "prompt_successes": 2,
            "prompt_failures": 1,
            "prompt_inflight": 0,
            "events_count": 7,
            "metadata": {
                "architect_metrics": {
                    "node_status_counts": {"blocked": 2, "atomic": 5},
                    "unresolved_leaf_count": 4,
                    "blocked_node_names": ["Design Filter", "Validate Stability"],
                    "blocked_reason": "Critique retries exhausted",
                    "any_port_pct": 0.1,
                    "any_edge_pct": 0.0,
                    "rewrite_actions": [
                        {"stage": "primitive_normalization"},
                        {"stage": "wrapper_elision"},
                    ],
                    "last_node_name": "Validate Stability",
                    "critique_reject_counts_by_category": {
                        "type_mismatch": 3,
                        "missing_flow": 1,
                    },
                    "retry_counts_by_node": {
                        "Design Filter": 2,
                        "Validate Stability": 1,
                    },
                }
            },
        }
        (tmp_path / "run_architect123.json").write_text(json.dumps(payload))
        monkeypatch.setenv("SCIONA_TELEMETRY_RUNS_DIR", str(tmp_path))

        resp = client.get("/api/dashboard/runs/architect123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["architect_summary"]["unresolved_leaf_count"] == 4
        assert data["architect_summary"]["blocked_count"] == 2
        assert data["architect_summary"]["blocked_reason"] == "Critique retries exhausted"
        assert data["architect_summary"]["last_node_name"] == "Validate Stability"
        assert data["architect_summary"]["rewrite_action_count"] == 2
        assert data["architect_summary"]["critique_reject_total"] == 4
        assert data["architect_summary"]["retry_total"] == 3
        assert data["architect_summary"]["critique_reject_categories"] == [
            {"category": "type_mismatch", "count": 3},
            {"category": "missing_flow", "count": 1},
        ]

    def test_dashboard_run_includes_hunter_summary(
        self, client, monkeypatch, tmp_path
    ):
        from sciona.telemetry import reset_telemetry_runtime

        reset_telemetry_runtime()
        now = time.time()
        payload = {
            "run_id": "hunter123",
            "pipeline": "match",
            "status": "completed",
            "started_at": now - 8.0,
            "last_update_at": now - 1.0,
            "ended_at": now - 1.0,
            "stages": {},
            "prompt_by_key": {},
            "inflight_prompts": {},
            "prompt_dispatches": 4,
            "prompt_successes": 4,
            "prompt_failures": 0,
            "prompt_inflight": 0,
            "events_count": 9,
            "metadata": {
                "hunter_metrics": {
                    "search_iterations": 2,
                    "embedding_results_total": 7,
                    "type_results_total": 2,
                    "new_candidates_total": 5,
                    "candidate_pool_size": 4,
                    "rank_calls": 2,
                    "ranked_candidate_count": 4,
                    "verify_batches": 2,
                    "verified_candidates_total": 3,
                    "verification_success_total": 1,
                    "verification_failure_total": 2,
                    "verified_matches": 1,
                    "reformulations": 1,
                    "failure_analyses": 1,
                    "reformulate_fallbacks": 0,
                    "empty_search_terminations": 0,
                    "empty_verify_batches": 0,
                    "query_count": 2,
                    "iteration": 1,
                    "last_query": "Nat.add_comm addition commutative",
                    "last_verified_candidate": "Nat.add_comm",
                }
            },
        }
        (tmp_path / "run_hunter123.json").write_text(json.dumps(payload))
        monkeypatch.setenv("SCIONA_TELEMETRY_RUNS_DIR", str(tmp_path))

        resp = client.get("/api/dashboard/runs/hunter123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["hunter_summary"]["search_iterations"] == 2
        assert data["hunter_summary"]["new_candidates_total"] == 5
        assert data["hunter_summary"]["verified_matches"] == 1
        assert data["hunter_summary"]["verification_success_total"] == 1
        assert data["hunter_summary"]["verification_failure_total"] == 2
        assert data["hunter_summary"]["query_count"] == 2
        assert data["hunter_summary"]["last_verified_candidate"] == "Nat.add_comm"

    def test_run_hang_annotation(self, client, monkeypatch, tmp_path):
        from sciona.telemetry import reset_telemetry_runtime

        reset_telemetry_runtime()
        now = time.time()
        payload = {
            "run_id": "hung-run",
            "pipeline": "algorithm_creation",
            "status": "running",
            "started_at": now - 200.0,
            "last_update_at": now - 50.0,
            "stages": {
                "hunter_round_1": {
                    "name": "hunter_round_1",
                    "status": "running",
                    "started_at": now - 180.0,
                    "last_heartbeat_at": now - 25.0,
                    "ended_at": None,
                    "message": "pending=4",
                    "completed": 1,
                    "total": 4,
                }
            },
            "prompt_by_key": {},
            "inflight_prompts": {},
            "prompt_dispatches": 3,
            "prompt_successes": 1,
            "prompt_failures": 0,
            "prompt_inflight": 2,
            "events_count": 8,
            "metadata": {},
        }
        (tmp_path / "run_hung-run.json").write_text(json.dumps(payload))
        monkeypatch.setenv("SCIONA_TELEMETRY_RUNS_DIR", str(tmp_path))
        monkeypatch.setenv("SCIONA_TELEMETRY_STALE_SECONDS", "5")

        resp = client.get("/api/dashboard/runs/hung-run")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_hung"] is True
        assert len(data["stale_stages"]) == 1


class TestVisualizerAPIAdditionalEndpoints:
    def test_ghost_sim_endpoint(self, client):
        payload = {
            "nodes": [
                {
                    "node_id": "n1",
                    "name": "Node 1",
                    "description": "test description",
                    "concept_type": "signal_filter",
                    "status": "atomic",
                    "depth": 1,
                }
            ],
            "edges": [],
            "metadata": {"goal": "test"},
        }
        with patch("sciona.visualizer.cdg.run_ghost_simulation") as mock_sim:
            from sciona.synthesizer.ghost_sim import GhostSimReport
            mock_sim.return_value = GhostSimReport(
                ran=True,
                passed=False,
                node_count=1,
                skipped_nodes=[],
                trace=["Node 1"],
                error="domain mismatch",
                error_node="Node 1",
                error_function="fft",
            )
            resp = client.post("/api/cdg/ghost_sim", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["ran"] is True
            assert data["passed"] is False
            assert data["error"] == "domain mismatch"
            assert data["error_node"] == "Node 1"

    def test_recommendations_endpoint(self, client):
        payload = {
            "cdg": {
                "nodes": [
                    {
                        "node_id": "n1",
                        "name": "Node 1",
                        "description": "test description",
                        "concept_type": "ml_model_selection",
                        "status": "atomic",
                        "depth": 1,
                    }
                ],
                "edges": [],
                "metadata": {"goal": "test"},
            },
            "selected_node_id": "n1"
        }
        with patch("sciona.visualizer.cdg.plan_expansion_delta") as mock_plan:
            from sciona.principal.expansion_delta_planner import DeltaPlan, DeltaPlanCandidate, DeltaAdaptationKind
            mock_op = MagicMock()
            mock_op.rule_name = "apply_kfold_ensemble"
            mock_seq = MagicMock()
            mock_seq.operations = (mock_op,)
            
            candidate = DeltaPlanCandidate(
                adaptation_kind=DeltaAdaptationKind.EXPANSION,
                operation_sequence=mock_seq,
                projected_coverage=1.0,
                intrusion_cost=0.5,
                utility_score=2.0,
                covered_terms=("term1",),
                missing_terms_after_plan=(),
                path=("base_cdg", "rule_name", "adapted_cdg"),
                rationale="test rationale"
            )
            mock_plan.return_value = DeltaPlan(
                decision=DeltaAdaptationKind.EXPANSION,
                base_coverage=0.5,
                direct_use_coverage=0.5,
                candidate_count=1,
                candidates=(candidate,),
                selected=candidate,
                should_compose_novel=False
            )
            resp = client.post("/api/delta_planner/recommendations", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["decision"] == "expansion"
            assert len(data["candidates"]) == 1
            assert data["candidates"][0]["operation_rule_names"] == ["apply_kfold_ensemble"]

    def test_apply_fix_endpoint(self, client):
        payload = {
            "cdg": {
                "nodes": [
                    {
                        "node_id": "n1",
                        "name": "Node 1",
                        "description": "test description",
                        "concept_type": "ml_model_selection",
                        "status": "atomic",
                        "depth": 1,
                    }
                ],
                "edges": [],
                "metadata": {"goal": "test"},
            },
            "rule_name": "apply_kfold_ensemble"
        }
        with patch("sciona.principal.expansion.ExpansionEngine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_rule = MagicMock()
            mock_engine._rule_index = {"apply_kfold_ensemble": mock_rule}
            mock_engine_cls.return_value = mock_engine
            
            mock_result = MagicMock()
            mock_result.is_failure = False
            
            from sciona.architect.handoff import CDGExport
            updated_cdg = CDGExport(
                nodes=[
                    {
                        "node_id": "n1",
                        "name": "Node 1 (Updated)",
                        "description": "test description",
                        "concept_type": "ml_model_selection",
                        "status": "atomic",
                        "depth": 1,
                    }
                ],
                edges=[],
                metadata={"goal": "test"}
            )
            mock_result.unwrap.return_value = updated_cdg
            mock_engine._rewriter.apply_rule.return_value = mock_result
            
            resp = client.post("/api/delta_planner/apply_fix", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["updated_cdg"]["nodes"][0]["name"] == "Node 1 (Updated)"
            assert "apply_kfold_ensemble" in data["logs"]
            assert "kfold_ensemble.py" in data["diff"]


def test_evolution_endpoint_and_human_guidance_event(client):
    from sciona.telemetry import get_event_log, reset_telemetry_runtime, start_run

    reset_telemetry_runtime()
    run_id = start_run(
        "optimization",
        run_id="evolution-test",
        metadata={
            "optimize": {
                "evolution": {
                    "schema_version": "1.0",
                    "versions": [
                        {
                            "version_id": "trial-1",
                            "label": "Initial match",
                            "graph": {"nodes": [], "edges": [], "metadata": {}},
                        }
                    ],
                    "transitions": [],
                }
            }
        },
    )

    response = client.get(f"/api/dashboard/runs/{run_id}/evolution")
    assert response.status_code == 200
    assert response.json()["versions"][0]["version_id"] == "trial-1"

    response = client.post(
        f"/api/dashboard/runs/{run_id}/guidance",
        json={
            "version_id": "trial-1",
            "action": "refine",
            "note": "Inspect the unstable intermediate before expanding.",
        },
    )
    assert response.status_code == 200
    assert response.json()["recorded"] is True
    event = get_event_log().events_for_run(run_id)[-1]
    assert event.event_type == "HUMAN_GUIDANCE_RECORDED"
    assert event.payload["action"] == "refine"
