from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from sciona.architect.skeleton_assets import load_local_skeleton_assets
from sciona.services.skeleton_catalog_sync import (
    _execute_all_pages,
    build_skeleton_artifact_bundle,
    enrich_bundle_with_catalog_verification,
    load_skeleton_artifact_bundles,
    sync_bundle_to_supabase,
    sync_bundles_to_graph_store,
)


class _FakeTable:
    def __init__(self, client: "_FakeSupabase", name: str) -> None:
        self.client = client
        self.name = name
        self.action = ""
        self.payload = None
        self.filters: list[tuple[str, str, object]] = []
        self.selected_fields = ""

    def upsert(self, payload, **kwargs):
        self.action = "upsert"
        self.payload = payload
        if kwargs:
            self.payload = {"rows": payload, "kwargs": kwargs}
        return self

    def update(self, payload):
        self.action = "update"
        self.payload = payload
        return self

    def delete(self):
        self.action = "delete"
        self.payload = None
        return self

    def select(self, fields: str):
        self.action = "select"
        self.selected_fields = fields
        return self

    def eq(self, field: str, value: object):
        self.filters.append(("eq", field, value))
        return self

    def in_(self, field: str, values: object):
        self.filters.append(("in", field, values))
        return self

    def execute(self):
        if self.action == "select":
            return SimpleNamespace(
                data=self.client.read(self.name, list(self.filters))
            )
        self.client.calls.append(
            {
                "table": self.name,
                "action": self.action,
                "payload": self.payload,
                "filters": list(self.filters),
            }
        )
        return self


class _FakeSupabase:
    def __init__(self, *, reads: dict[str, list[dict[str, object]]] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.reads = dict(reads or {})

    def table(self, name: str) -> _FakeTable:
        return _FakeTable(self, name)

    def read(self, name: str, filters: list[tuple[str, str, object]]) -> list[dict[str, object]]:
        rows = [dict(row) for row in self.reads.get(name, [])]
        for op, field, value in filters:
            if op == "eq":
                rows = [row for row in rows if row.get(field) == value]
            elif op == "in":
                allowed = set(value)
                rows = [row for row in rows if row.get(field) in allowed]
        return rows


class _PagedQuery:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.window = (0, 999)
        self.windows: list[tuple[int, int]] = []

    def range(self, start: int, end: int):
        self.window = (start, end)
        self.windows.append(self.window)
        return self

    def execute(self):
        start, end = self.window
        return SimpleNamespace(data=self.rows[start : end + 1])


def test_execute_all_pages_exhausts_catalog_beyond_postgrest_cap() -> None:
    query = _PagedQuery([{"atom_id": str(index)} for index in range(2505)])

    rows = _execute_all_pages(query)

    assert len(rows) == 2505
    assert query.windows == [(0, 999), (1000, 1999), (2000, 2999)]


@dataclass
class _FakeGraphStore:
    ensure_constraints_calls: int = 0
    projections: list[object] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.projections = []

    async def ensure_constraints(self) -> None:
        self.ensure_constraints_calls += 1

    async def upsert_published_cdg(self, projection):
        self.projections.append(projection)
        return {"artifacts": 1, "nodes": len(projection.nodes), "data_flow": len(projection.edges), "parent_of": 0}


def test_build_skeleton_artifact_bundle_signal_detect_measure() -> None:
    bundle = next(
        bundle
        for bundle in load_skeleton_artifact_bundles()
        if bundle.asset_id == "signal_detect_measure"
    )

    assert bundle.artifact["fqdn"] == "cdg.skeleton.signal_detect_measure"
    assert bundle.version["semver"] == "phase2.v1"
    assert bundle.artifact["top_level_input_arity"] == 2
    assert bundle.artifact["top_level_output_arity"] == 1
    assert bundle.artifact["topo_hash"]
    assert any(row["kind"] == "dejargonized" for row in bundle.descriptions)
    assert len(bundle.references_registry) == len(bundle.references) == 2
    assert len(bundle.cdg_nodes) == 3
    assert len(bundle.cdg_edges) == 2


def test_build_skeleton_artifact_bundle_belief_propagation() -> None:
    bundle = next(
        bundle
        for bundle in load_skeleton_artifact_bundles()
        if bundle.asset_id == "belief_propagation"
    )

    assert bundle.artifact["fqdn"] == "cdg.skeleton.belief_propagation"
    assert bundle.version["semver"] == "v1"
    assert bundle.artifact["top_level_input_arity"] == 2
    assert bundle.artifact["top_level_output_arity"] == 2
    assert bundle.artifact["topo_hash"]
    assert any(row["kind"] == "dejargonized" for row in bundle.descriptions)
    assert len(bundle.references_registry) == len(bundle.references) == 1
    assert len(bundle.cdg_nodes) == 4
    assert len(bundle.cdg_edges) == 7


def test_sync_bundle_to_supabase_uses_deterministic_child_table_reload(monkeypatch) -> None:
    bundle = build_skeleton_artifact_bundle(load_local_skeleton_assets()[0])
    supabase = _FakeSupabase()
    monkeypatch.setattr(
        "sciona.services.skeleton_catalog_sync.enrich_bundle_with_catalog_verification",
        lambda bundle, *, supabase: bundle,
    )

    sync_bundle_to_supabase(supabase, bundle)

    operations = [(call["table"], call["action"]) for call in supabase.calls]
    assert operations[0] == ("artifacts", "upsert")
    assert ("artifact_descriptions", "delete") in operations
    assert ("artifact_descriptions", "upsert") in operations
    assert ("artifact_io_specs", "delete") in operations
    assert ("artifact_io_specs", "upsert") in operations
    assert ("artifact_versions", "update") in operations
    assert ("artifact_versions", "upsert") in operations
    assert ("references_registry", "upsert") in operations
    assert ("artifact_references", "upsert") in operations
    assert ("artifact_audit_rollups", "upsert") in operations
    assert ("artifact_cdg_nodes", "upsert") in operations
    assert ("artifact_cdg_edges", "upsert") in operations


def test_enrich_bundle_with_catalog_verification_derives_bindings_and_evidence() -> None:
    asset = next(
        asset for asset in load_local_skeleton_assets() if asset.asset_id == "signal_detect_measure"
    )
    reviewed_asset = asset.model_copy(
        update={
            "audit": asset.audit.model_copy(update={"review_status": "transitional"})
        }
    )
    bundle = build_skeleton_artifact_bundle(reviewed_asset)
    supabase = _FakeSupabase(
        reads={
            "atoms": [
                {
                    "atom_id": "atom-filter",
                    "fqdn": "sciona.atoms.expansion.signal_event_rate.filter_signal_for_detection",
                    "is_publishable": True,
                },
                {
                    "atom_id": "atom-detect",
                    "fqdn": "sciona.atoms.expansion.signal_event_rate.detect_peaks_in_signal",
                    "is_publishable": True,
                },
                {
                    "atom_id": "atom-rate",
                    "fqdn": "sciona.atoms.expansion.signal_event_rate.compute_event_rate",
                    "is_publishable": True,
                },
            ],
            "atom_versions": [
                {"atom_id": "atom-filter", "content_hash": "hash-filter"},
                {"atom_id": "atom-detect", "content_hash": "hash-detect"},
                {"atom_id": "atom-rate", "content_hash": "hash-rate"},
            ],
            "atom_verification_matches": [
                {
                    "atom_id": "atom-filter",
                    "candidate_name": "pkg.signal.filter_signal_for_detection",
                    "candidate_score": 0.9,
                    "retrieval_method": "lexical",
                    "verified": True,
                    "verification_level": "type_checked",
                    "proof_term": "",
                    "compiler_output": "",
                    "error_message": "",
                    "all_candidates": [],
                    "all_verifications": [],
                },
                {
                    "atom_id": "atom-detect",
                    "candidate_name": "pkg.signal.detect_peaks_in_signal",
                    "candidate_score": 0.9,
                    "retrieval_method": "lexical",
                    "verified": True,
                    "verification_level": "contract_checked",
                    "proof_term": "",
                    "compiler_output": "",
                    "error_message": "",
                    "all_candidates": [],
                    "all_verifications": [],
                },
                {
                    "atom_id": "atom-rate",
                    "candidate_name": "pkg.signal.compute_event_rate",
                    "candidate_score": 0.95,
                    "retrieval_method": "lexical",
                    "verified": True,
                    "verification_level": "type_checked",
                    "proof_term": "",
                    "compiler_output": "",
                    "error_message": "",
                    "all_candidates": [],
                    "all_verifications": [],
                },
            ],
            "atom_audit_evidence": [
                {"atom_id": "atom-filter", "audit_type": "smoke_test", "passed": True, "status": "completed"},
                {"atom_id": "atom-detect", "audit_type": "smoke_test", "passed": True, "status": "completed"},
                {"atom_id": "atom-rate", "audit_type": "smoke_test", "passed": True, "status": "completed"},
            ],
            "atom_uncertainty_estimates": [
                {
                    "atom_id": "atom-rate",
                    "mode": "empirical",
                    "scalar_factor": 0.12,
                    "confidence": 0.92,
                    "n_trials": 8,
                    "epsilon": 0.01,
                    "input_regime": "ecg",
                    "notes": "stable",
                }
            ],
            "artifact_benchmarks": [
                {
                    "version_id": bundle.version["version_id"],
                    "benchmark_name": "signal.event_rate.ecg.v1",
                    "metric_name": "mae_bpm",
                    "metric_value": 2.7,
                    "dataset_tag": "ecg_event_rate_reference",
                    "measured_at": "2026-04-14T15:10:00Z",
                }
            ],
        }
    )

    enriched = enrich_bundle_with_catalog_verification(bundle, supabase=supabase)

    assert enriched.artifact["verified_leaf_coverage"] == 1.0
    assert enriched.artifact["is_publishable"] is True
    assert enriched.artifact["status"] == "approved"
    assert len(enriched.cdg_bindings) == 3
    assert len(enriched.verification_matches) == 3
    assert len(enriched.audit_evidence) >= 4
    assert enriched.audit_rollup["runtime_status"] == "pass"
    assert enriched.audit_rollup["semantic_status"] == "pass"
    assert enriched.audit_rollup["trust_readiness"] == "ready"
    assert {row["mode"] for row in enriched.uncertainty_estimates} >= {
        "empirical",
        "propagated",
    }
    semantic_audit = next(
        row for row in enriched.audit_evidence if row["audit_type"] == "semantic_audit"
    )
    assert semantic_audit["details"]["benchmark_pass"] is True


def test_enrich_bundle_with_catalog_verification_requires_benchmark_evidence() -> None:
    asset = next(
        asset for asset in load_local_skeleton_assets() if asset.asset_id == "signal_detect_measure"
    )
    reviewed_asset = asset.model_copy(
        update={
            "audit": asset.audit.model_copy(update={"review_status": "transitional"})
        }
    )
    bundle = build_skeleton_artifact_bundle(reviewed_asset)
    supabase = _FakeSupabase(
        reads={
            "atoms": [
                {
                    "atom_id": "atom-filter",
                    "fqdn": "pkg.signal.filter_signal_for_detection",
                    "is_publishable": True,
                },
                {
                    "atom_id": "atom-detect",
                    "fqdn": "pkg.signal.detect_peaks_in_signal",
                    "is_publishable": True,
                },
                {
                    "atom_id": "atom-rate",
                    "fqdn": "pkg.signal.compute_event_rate",
                    "is_publishable": True,
                },
            ],
            "atom_versions": [
                {"atom_id": "atom-filter", "content_hash": "hash-filter"},
                {"atom_id": "atom-detect", "content_hash": "hash-detect"},
                {"atom_id": "atom-rate", "content_hash": "hash-rate"},
            ],
            "atom_verification_matches": [
                {
                    "atom_id": "atom-filter",
                    "candidate_name": "pkg.signal.filter_signal_for_detection",
                    "candidate_score": 0.9,
                    "retrieval_method": "lexical",
                    "verified": True,
                    "verification_level": "type_checked",
                    "proof_term": "",
                    "compiler_output": "",
                    "error_message": "",
                    "all_candidates": [],
                    "all_verifications": [],
                },
                {
                    "atom_id": "atom-detect",
                    "candidate_name": "pkg.signal.detect_peaks_in_signal",
                    "candidate_score": 0.9,
                    "retrieval_method": "lexical",
                    "verified": True,
                    "verification_level": "contract_checked",
                    "proof_term": "",
                    "compiler_output": "",
                    "error_message": "",
                    "all_candidates": [],
                    "all_verifications": [],
                },
                {
                    "atom_id": "atom-rate",
                    "candidate_name": "pkg.signal.compute_event_rate",
                    "candidate_score": 0.95,
                    "retrieval_method": "lexical",
                    "verified": True,
                    "verification_level": "type_checked",
                    "proof_term": "",
                    "compiler_output": "",
                    "error_message": "",
                    "all_candidates": [],
                    "all_verifications": [],
                },
            ],
            "atom_audit_evidence": [
                {"atom_id": "atom-filter", "audit_type": "smoke_test", "passed": True, "status": "completed"},
                {"atom_id": "atom-detect", "audit_type": "smoke_test", "passed": True, "status": "completed"},
                {"atom_id": "atom-rate", "audit_type": "smoke_test", "passed": True, "status": "completed"},
            ],
            "atom_uncertainty_estimates": [
                {
                    "atom_id": "atom-rate",
                    "mode": "empirical",
                    "scalar_factor": 0.12,
                    "confidence": 0.92,
                    "n_trials": 8,
                    "epsilon": 0.01,
                    "input_regime": "ecg",
                    "notes": "stable",
                }
            ],
            "artifact_benchmarks": [],
        }
    )

    enriched = enrich_bundle_with_catalog_verification(bundle, supabase=supabase)

    assert enriched.artifact["verified_leaf_coverage"] == 1.0
    assert enriched.artifact["is_publishable"] is False
    assert "benchmark_evidence_missing" in enriched.audit_rollup["trust_blockers"]
    assert {row["audit_type"] for row in enriched.audit_evidence} >= {
        "smoke_test",
        "semantic_audit",
        "structural_audit",
        "risk_assessment",
    }
    semantic_audit = next(
        row for row in enriched.audit_evidence if row["audit_type"] == "semantic_audit"
    )
    assert semantic_audit["details"]["benchmark_pass"] is False


def test_enrich_bundle_with_catalog_verification_allows_bindings_without_verification_rows() -> None:
    asset = next(
        asset for asset in load_local_skeleton_assets() if asset.asset_id == "signal_detect_measure"
    )
    bundle = build_skeleton_artifact_bundle(asset)
    supabase = _FakeSupabase(
        reads={
            "atoms": [
                {
                    "atom_id": "atom-filter",
                    "fqdn": "sciona.atoms.expansion.signal_event_rate.filter_signal_for_detection",
                    "is_publishable": True,
                },
                {
                    "atom_id": "atom-detect",
                    "fqdn": "sciona.atoms.expansion.signal_event_rate.detect_peaks_in_signal",
                    "is_publishable": True,
                },
                {
                    "atom_id": "atom-rate",
                    "fqdn": "sciona.atoms.expansion.signal_event_rate.compute_event_rate",
                    "is_publishable": True,
                },
            ],
            "atom_versions": [
                {"atom_id": "atom-filter", "content_hash": "hash-filter"},
                {"atom_id": "atom-detect", "content_hash": "hash-detect"},
                {"atom_id": "atom-rate", "content_hash": "hash-rate"},
            ],
            "atom_verification_matches": [],
            "atom_audit_evidence": [],
            "atom_uncertainty_estimates": [],
        }
    )

    enriched = enrich_bundle_with_catalog_verification(bundle, supabase=supabase)

    assert len(enriched.cdg_bindings) == 3
    assert enriched.artifact["verified_leaf_coverage"] == 1.0
    assert enriched.artifact["is_publishable"] is False
    assert {row["retrieval_method"] for row in enriched.verification_matches} == {
        "matched_primitive_exact"
    }
    assert all(row["verified"] is True for row in enriched.verification_matches)
    assert {row["verification_level"] for row in enriched.verification_matches} == {
        "contract_checked"
    }


@pytest.mark.asyncio
async def test_sync_bundles_to_graph_store_uses_sorted_projection_order() -> None:
    bundles = load_skeleton_artifact_bundles()
    graph_store = _FakeGraphStore()

    results = await sync_bundles_to_graph_store(graph_store, bundles)

    assert graph_store.ensure_constraints_calls == 1
    assert [projection.fqdn for projection in graph_store.projections] == sorted(
        projection.fqdn for projection in graph_store.projections
    )
    assert [asset_id for asset_id, _counts in results] == [bundle.asset_id for bundle in bundles]
