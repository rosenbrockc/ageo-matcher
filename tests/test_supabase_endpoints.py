from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable
from uuid import UUID

import pytest

from sciona.api.models import AtomPublishRequest, BountyCreateRequest, SubmissionRequest, UpdateTargetRequest
from sciona.api.routers import bounty, catalog, dashboard, registry, verification


@dataclass
class FakeResult:
    data: Any = None
    count: int | None = None


class FakeQuery:
    def __init__(
        self,
        client: "FakeSupabaseClient",
        kind: str,
        name: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.client = client
        self.kind = kind
        self.name = name
        self.params = params or {}
        self.action = "select"
        self.select_fields = ""
        self.filters: list[tuple[str, Any, Any]] = []
        self.orderings: list[tuple[str, bool]] = []
        self.payload: Any = None
        self.count: str | None = None
        self.range_args: tuple[int, int] | None = None
        self.limit_value: int | None = None
        self.mode = ""

    def select(self, fields: str, count: str | None = None):
        self.action = "select"
        self.select_fields = fields
        self.count = count
        return self

    def insert(self, payload: Any):
        self.action = "insert"
        self.payload = payload
        return self

    def update(self, payload: Any):
        self.action = "update"
        self.payload = payload
        return self

    def eq(self, field: str, value: Any):
        self.filters.append(("eq", field, value))
        return self

    def contains(self, field: str, value: Any):
        self.filters.append(("contains", field, value))
        return self

    def or_(self, clause: str):
        self.filters.append(("or", clause, None))
        return self

    def in_(self, field: str, values: list[Any]):
        self.filters.append(("in", field, values))
        return self

    def order(self, field: str, desc: bool = False):
        self.orderings.append((field, desc))
        return self

    def range(self, start: int, end: int):
        self.range_args = (start, end)
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    def maybe_single(self):
        self.mode = "maybe_single"
        return self

    def single(self):
        self.mode = "single"
        return self

    async def execute(self) -> FakeResult:
        return self.client.table_handler(self)


class FakeRpcQuery:
    def __init__(
        self,
        client: "FakeSupabaseClient",
        name: str,
        params: dict[str, Any],
    ) -> None:
        self.client = client
        self.name = name
        self.params = params

    async def execute(self) -> FakeResult:
        return self.client.rpc_handler(self)


class FakeSupabaseClient:
    def __init__(
        self,
        table_handler: Callable[[FakeQuery], FakeResult],
        rpc_handler: Callable[[FakeRpcQuery], FakeResult] | None = None,
    ) -> None:
        self.table_handler = table_handler
        self.rpc_handler = rpc_handler or (lambda _query: FakeResult(data=[]))

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, "table", name)

    def rpc(self, name: str, params: dict[str, Any]) -> FakeRpcQuery:
        return FakeRpcQuery(self, name, params)


def _uuid(value: str) -> UUID:
    return UUID(value)


@pytest.mark.asyncio
async def test_publish_atom_supabase() -> None:
    content_hash = hashlib.sha256(b"a").hexdigest()
    atom_id = _uuid("11111111-1111-1111-1111-111111111111")
    version_id = _uuid("22222222-2222-2222-2222-222222222222")
    user_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    calls: list[tuple[str, str, list[tuple[str, Any, Any]], Any]] = []

    def handler(query: FakeQuery) -> FakeResult:
        calls.append((query.name, query.action, query.filters, query.payload))
        if query.name == "atom_versions" and query.action == "select":
            return FakeResult(data=None)
        if query.name == "atoms" and query.action == "select":
            return FakeResult(data=None)
        if query.name == "atoms" and query.action == "insert":
            assert query.payload["owner_id"] == user_id
            return FakeResult(data=[{"atom_id": atom_id}])
        if query.name == "atom_versions" and query.action == "update":
            return FakeResult(data=[])
        if query.name == "atom_versions" and query.action == "insert":
            assert query.payload["content_hash"] == content_hash
            return FakeResult(data=[{"version_id": version_id}])
        raise AssertionError(f"Unexpected query: {query.name} {query.action}")

    client = FakeSupabaseClient(handler)
    body = AtomPublishRequest(
        fqdn="pkg.filter",
        semver="1.0.0",
        description="Filter atom",
        domain_tags=["signal"],
        source_tar_b64="YQ==",
        fingerprint="f" * 64,
    )
    user = SimpleNamespace(user_id=user_id)

    result = await registry.publish_atom(body, user=user, supabase=client)

    assert result.atom_id == atom_id
    assert result.version_id == version_id
    assert result.content_hash == content_hash
    assert result.is_new_atom is True
    assert [call[0:2] for call in calls] == [
        ("atom_versions", "select"),
        ("atoms", "select"),
        ("atoms", "insert"),
        ("atom_versions", "select"),
        ("atom_versions", "update"),
        ("atom_versions", "insert"),
    ]


@pytest.mark.asyncio
async def test_list_bounties_supabase() -> None:
    bounty_id = _uuid("33333333-3333-3333-3333-333333333333")

    def handler(query: FakeQuery) -> FakeResult:
        if query.name != "bounties" or query.action != "select":
            raise AssertionError(query.name)
        assert query.count == "exact"
        assert ("eq", "status", "open") in query.filters
        return FakeResult(
            data=[
                {
                    "bounty_id": bounty_id,
                    "title": "Improve filter",
                    "escrow_amount": 50.0,
                    "status": "open",
                    "deadline": None,
                    "tier": "standard",
                }
            ],
            count=1,
        )

    client = FakeSupabaseClient(handler)
    result = await bounty.list_bounties(
        status="open",
        limit=50,
        supabase=client,
    )

    assert result.total == 1
    assert result.items[0].bounty_id == bounty_id
    assert result.items[0].status == "open"


@pytest.mark.asyncio
async def test_catalog_search_supabase_uses_phase6_rpc(monkeypatch) -> None:
    rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def table_handler(query: FakeQuery) -> FakeResult:
        raise AssertionError(f"unexpected table fallback: {query.name}")

    def rpc_handler(query: FakeRpcQuery) -> FakeResult:
        rpc_calls.append((query.name, query.params))
        if query.name == "get_active_embedding_configuration":
            return FakeResult(data=[])
        assert query.name == "search_atoms_hybrid"
        return FakeResult(
            data=[
                {
                    "fqdn": "pkg.filter",
                    "technical_description": "Filter signal",
                    "domain_tags": ["signal"],
                    "overall_verdict": "trusted",
                    "risk_tier": "low",
                    "trust_readiness": "ready",
                }
            ]
        )

    client = FakeSupabaseClient(table_handler=table_handler, rpc_handler=rpc_handler)

    result = await catalog.catalog_search(
        q="kalman filter",
        domain_tag="signal",
        limit=10,
        supabase=client,
    )

    assert [entry.fqdn for entry in result] == ["pkg.filter"]
    assert result[0].overall_verdict == "trusted"
    assert result[0].risk_tier == "low"
    assert result[0].trust_readiness == "ready"
    assert rpc_calls == [
        ("get_active_embedding_configuration", {}),
        (
            "search_atoms_hybrid",
            {
                "query_text": "kalman filter",
                "mode": "fts",
                "result_limit": 10,
                "result_offset": 0,
            },
        )
    ]


@pytest.mark.asyncio
async def test_find_catalog_atom_uses_exact_served_lookup() -> None:
    fqdn = "sciona.atoms.signal.ops.peak_correction"

    def table_handler(query: FakeQuery) -> FakeResult:
        if query.name == "atoms":
            assert ("eq", "fqdn", fqdn) in query.filters
            assert ("eq", "is_publishable", True) in query.filters
            assert query.limit_value == 1
            return FakeResult(
                data=[
                    {
                        "fqdn": fqdn,
                        "description": "Correct detected peaks",
                        "domain_tags": ["signal"],
                        "source_repo_id": "signal-repo",
                        "import_module": "sciona.atoms.signal.ops",
                        "namespace_root": "sciona.atoms",
                        "source_module_path": "signal.ops",
                        "source_symbol": "peak_correction",
                    }
                ]
            )
        if query.name == "atom_source_repositories":
            assert ("eq", "source_repo_id", "signal-repo") in query.filters
            assert ("eq", "active", True) in query.filters
            return FakeResult(
                data=[
                    {
                        "repo_name": "signal",
                        "distribution_name": "sciona-atoms-signal",
                        "distribution_version": "1.0.0",
                        "install_requirement": "sciona-atoms-signal==1.0.0",
                        "wheel_url": "",
                        "wheel_sha256": "",
                    }
                ]
            )
        raise AssertionError(query.name)

    entry = await catalog.find_catalog_atom(
        fqdn, supabase=FakeSupabaseClient(table_handler=table_handler)
    )

    assert entry.fqdn == fqdn
    assert entry.provider is not None
    assert entry.provider.import_symbol == "peak_correction"


@pytest.mark.asyncio
async def test_catalog_search_uses_hybrid_postgres_and_returns_provider_metadata(
    monkeypatch,
) -> None:
    async def embed(
        _query: str, _config: catalog.CatalogEmbeddingConfig
    ) -> list[float]:
        return [0.25, 0.75]

    monkeypatch.setattr(catalog, "_embed_catalog_query", embed)
    rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def table_handler(query: FakeQuery) -> FakeResult:
        assert query.name == "catalog_atom_installations"
        assert ("in", "fqdn", ["sciona.atoms.signal.filter.apply_filter"]) in query.filters
        return FakeResult(
            data=[
                {
                    "fqdn": "sciona.atoms.signal.filter.apply_filter",
                    "provider_id": "sciona-atoms-signal",
                    "distribution_name": "sciona-atoms-signal",
                    "distribution_version": "1.2.0",
                    "install_requirement": "sciona-atoms-signal==1.2.0",
                    "import_module": "sciona.atoms.signal.filter",
                    "import_symbol": "apply_filter",
                    "wheel_url": "",
                    "wheel_sha256": "",
                }
            ]
        )

    def rpc_handler(query: FakeRpcQuery) -> FakeResult:
        rpc_calls.append((query.name, query.params))
        if query.name == "get_active_embedding_configuration":
            return FakeResult(
                data=[
                    {
                        "provider": "openai",
                        "model": "text-embedding-3-small",
                        "model_revision": "text-embedding-3-small",
                        "dimensions": 1536,
                        "input_schema_version": "atom-search-v1",
                        "embedding_space_id": (
                            "openai:text-embedding-3-small:"
                            "text-embedding-3-small:1536:atom-search-v1"
                        ),
                    }
                ]
            )
        return FakeResult(
            data=[
                {
                    "fqdn": "sciona.atoms.signal.filter.apply_filter",
                    "technical_description": "Apply a general signal filter",
                    "domain_tags": ["signal"],
                }
            ]
        )

    result = await catalog.catalog_search(
        q="remove noise from observations",
        limit=5,
        supabase=FakeSupabaseClient(table_handler, rpc_handler),
    )

    assert rpc_calls == [
        ("get_active_embedding_configuration", {}),
        (
            "search_atoms_hybrid",
            {
                "query_text": "remove noise from observations",
                "query_embedding": [0.25, 0.75],
                "mode": "hybrid",
                "result_limit": 5,
                "result_offset": 0,
            },
        )
    ]
    assert result[0].provider is not None
    assert result[0].provider.install_requirement == "sciona-atoms-signal==1.2.0"


@pytest.mark.asyncio
async def test_catalog_search_supabase_falls_back_to_served_view() -> None:
    rpc_calls: list[tuple[str, dict[str, Any]]] = []

    def table_handler(query: FakeQuery) -> FakeResult:
        assert query.name == "catalog_atoms_served"
        return FakeResult(
            data=[
                {
                    "fqdn": "pkg.filter",
                    "technical_description": "Filter signal",
                    "domain_tags": ["signal"],
                    "overall_verdict": "trusted",
                    "risk_tier": "low",
                    "trust_readiness": "ready",
                }
            ]
        )

    def rpc_handler(query: FakeRpcQuery) -> FakeResult:
        rpc_calls.append((query.name, query.params))
        raise RuntimeError("rpc unavailable")

    client = FakeSupabaseClient(table_handler=table_handler, rpc_handler=rpc_handler)

    result = await catalog.catalog_search(
        q="kalman filter",
        domain_tag=None,
        limit=10,
        supabase=client,
    )

    assert [entry.fqdn for entry in result] == ["pkg.filter"]
    assert rpc_calls == [
        ("get_active_embedding_configuration", {}),
        (
            "search_atoms_hybrid",
            {
                "query_text": "kalman filter",
                "mode": "fts",
                "result_limit": 10,
                "result_offset": 0,
            },
        )
    ]


@pytest.mark.asyncio
async def test_catalog_search_falls_back_when_rpc_has_no_exact_fqdn_match() -> None:
    def table_handler(query: FakeQuery) -> FakeResult:
        assert query.name == "catalog_atoms_served"
        assert query.action == "select"
        return FakeResult(
            data=[
                {
                    "fqdn": "sciona.atoms.ml.diagnostics.score",
                    "technical_description": "Score a model diagnostic",
                    "domain_tags": ["ml"],
                }
            ]
        )

    def rpc_handler(query: FakeRpcQuery) -> FakeResult:
        if query.name == "get_active_embedding_configuration":
            return FakeResult(data=[])
        assert query.name == "search_atoms_hybrid"
        return FakeResult(data=[])

    result = await catalog.catalog_search(
        q="sciona.atoms.ml.diagnostics.score",
        limit=5,
        supabase=FakeSupabaseClient(table_handler, rpc_handler),
    )

    assert [entry.fqdn for entry in result] == ["sciona.atoms.ml.diagnostics.score"]


@pytest.mark.asyncio
async def test_artifact_search_uses_artifact_rpc_then_fallbacks() -> None:
    def table_handler(query: FakeQuery) -> FakeResult:
        raise AssertionError(f"unexpected table fallback: {query.name}")

    def rpc_handler(query: FakeRpcQuery) -> FakeResult:
        assert query.name == "search_artifacts_hybrid"
        return FakeResult(
            data=[
                {
                    "fqdn": "pkg.rate_detector",
                    "artifact_kind": "cdg",
                    "technical_description": "Detect heart rate from ECG",
                    "domain_tags": ["signal"],
                    "overall_verdict": "trusted",
                    "risk_tier": "low",
                    "trust_readiness": "ready",
                }
            ]
        )

    client = FakeSupabaseClient(table_handler=table_handler, rpc_handler=rpc_handler)

    result = await catalog.artifact_search(
        q="heart rate ecg",
        domain_tag="signal",
        limit=10,
        supabase=client,
    )

    assert [entry.fqdn for entry in result] == ["pkg.rate_detector"]
    assert result[0].artifact_kind == "cdg"


@pytest.mark.asyncio
async def test_get_artifact_document_falls_back_to_atom_rpc() -> None:
    rpc_calls: list[str] = []

    def table_handler(query: FakeQuery) -> FakeResult:
        raise AssertionError(query.name)

    def rpc_handler(query: FakeRpcQuery) -> FakeResult:
        rpc_calls.append(query.name)
        if query.name == "get_artifact_document":
            return FakeResult(data=None)
        if query.name == "get_atom_document":
            return FakeResult(data={"atom": {"fqdn": "pkg.filter"}})
        raise AssertionError(query.name)

    client = FakeSupabaseClient(table_handler=table_handler, rpc_handler=rpc_handler)

    result = await catalog.get_artifact_document("pkg.filter", supabase=client)

    assert result["atom"]["fqdn"] == "pkg.filter"
    assert rpc_calls == ["get_artifact_document", "get_atom_document"]


@pytest.mark.asyncio
async def test_get_artifact_document_hydrates_artifact_benchmarks() -> None:
    def table_handler(query: FakeQuery) -> FakeResult:
        if query.name == "artifacts":
            assert query.action == "select"
            assert ("eq", "fqdn", "cdg.skeleton.signal_detect_measure") in query.filters
            return FakeResult(data=[{"artifact_id": "artifact-1"}])
        if query.name == "artifact_versions":
            assert query.action == "select"
            assert ("eq", "artifact_id", "artifact-1") in query.filters
            return FakeResult(
                data=[
                    {"version_id": "version-1", "content_hash": "hash-1"},
                    {"version_id": "version-2", "content_hash": "hash-2"},
                ]
            )
        if query.name == "artifact_benchmarks":
            assert query.action == "select"
            assert ("in", "version_id", ["version-1", "version-2"]) in query.filters
            return FakeResult(
                data=[
                    {
                        "version_id": "version-2",
                        "benchmark_name": "signal.event_rate.ecg.v1",
                        "metric_name": "mae_bpm",
                        "metric_value": 2.7,
                        "dataset_tag": "ecg_event_rate_reference",
                        "measured_at": "2026-04-14T15:10:00Z",
                    }
                ]
            )
        raise AssertionError((query.name, query.action))

    def rpc_handler(query: FakeRpcQuery) -> FakeResult:
        if query.name == "get_artifact_document":
            return FakeResult(data={"artifact": {"fqdn": "cdg.skeleton.signal_detect_measure"}})
        raise AssertionError(query.name)

    client = FakeSupabaseClient(table_handler=table_handler, rpc_handler=rpc_handler)

    result = await catalog.get_artifact_document(
        "cdg.skeleton.signal_detect_measure",
        supabase=client,
    )

    assert result["artifact"]["fqdn"] == "cdg.skeleton.signal_detect_measure"
    assert result["benchmarks"] == [
        {
            "artifact_fqdn": "cdg.skeleton.signal_detect_measure",
            "content_hash": "hash-2",
            "benchmark_id": "signal.event_rate.ecg.v1",
            "benchmark_name": "signal.event_rate.ecg.v1",
            "metric_name": "mae_bpm",
            "metric_value": 2.7,
            "dataset_tag": "ecg_event_rate_reference",
            "measured_at": "2026-04-14T15:10:00Z",
        }
    ]


@pytest.mark.asyncio
async def test_create_bounty_supabase() -> None:
    bounty_id = _uuid("44444444-4444-4444-4444-444444444444")
    user_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    created_row = {
        "bounty_id": bounty_id,
        "principal_id": user_id,
        "title": "Create bounty",
        "escrow_amount": 25.0,
        "status": "draft",
        "deadline": None,
        "tier": "standard",
        "verification_budget": 5,
        "verifications_used": 0,
        "created_at": "2026-03-31T00:00:00Z",
        "updated_at": "2026-03-31T00:00:00Z",
        "config_yml": {"min_metric_value": 0.2},
        "flare_payload": None,
    }

    def handler(query: FakeQuery) -> FakeResult:
        if query.action == "insert":
            if query.name != "bounties":
                raise AssertionError(query.name)
            assert query.payload["principal_id"] == user_id
            assert query.payload["config_yml"] == {"min_metric_value": 0.2}
            return FakeResult(data=[created_row])
        raise AssertionError((query.name, query.action))

    client = FakeSupabaseClient(handler)
    body = BountyCreateRequest(
        title="Create bounty",
        escrow_amount=25.0,
        tier="standard",
        config_yml={"min_metric_value": 0.2},
    )
    user = SimpleNamespace(user_id=user_id)

    result = await bounty.create_bounty(
        body,
        user=user,
        temporal=None,
        supabase=client,
    )

    assert result.bounty_id == bounty_id
    assert str(result.principal_id) == user_id
    assert result.title == "Create bounty"


@pytest.mark.asyncio
async def test_cancel_bounty_supabase() -> None:
    bounty_id = _uuid("55555555-5555-5555-5555-555555555555")
    user_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"

    def handler(query: FakeQuery) -> FakeResult:
        if query.name == "bounties" and query.action == "select":
            return FakeResult(
                data=[
                    {
                        "bounty_id": bounty_id,
                        "principal_id": user_id,
                        "title": "Cancel bounty",
                        "escrow_amount": 10.0,
                        "status": "open",
                        "deadline": None,
                        "tier": "standard",
                        "verification_budget": 5,
                        "verifications_used": 0,
                        "config_yml": {},
                        "created_at": "2026-03-31T00:00:00Z",
                        "updated_at": "2026-03-31T00:00:00Z",
                    }
                ]
            )
        if query.name == "submissions" and query.action == "select":
            assert query.count == "exact"
            return FakeResult(data=[{"submission_id": "sub-1"}], count=1)
        if query.name == "bounties" and query.action == "update":
            assert query.payload["status"] == "cancelled"
            return FakeResult(data=[])
        raise AssertionError((query.name, query.action))

    client = FakeSupabaseClient(handler)
    user = SimpleNamespace(user_id=user_id)

    result = await bounty.cancel_bounty(
        bounty_id,
        user=user,
        temporal=None,
        supabase=client,
    )

    assert result.status == "cancelled"
    assert result.cancellation_fee == 2.5


@pytest.mark.asyncio
async def test_submission_status_supabase() -> None:
    submission_id = _uuid("66666666-6666-6666-6666-666666666666")

    def handler(query: FakeQuery) -> FakeResult:
        if query.name == "submissions":
            return FakeResult(
                data=[
                    {
                        "submission_id": submission_id,
                        "verification_status": "pending",
                    }
                ]
            )
        if query.name == "verification_runs":
            return FakeResult(
                data=[
                    {
                        "status": "completed",
                        "metric_values": {"loss": 0.1},
                        "output_hash": "abc",
                        "is_deterministic": True,
                    }
                ]
            )
        raise AssertionError(query.name)

    client = FakeSupabaseClient(handler)
    result = await verification.get_submission_status(
        submission_id,
        temporal=None,
        supabase=client,
    )

    assert result["submission_id"] == str(submission_id)
    assert result["verification_status"] == "pending"
    assert result["runs"][0]["output_hash"] == "abc"


@pytest.mark.asyncio
async def test_originator_impact_supabase() -> None:
    originator_id = _uuid("77777777-7777-7777-7777-777777777777")

    def table_handler(query: FakeQuery) -> FakeResult:
        if query.name == "originator_impact":
            return FakeResult(
                data=[
                    {
                        "originator_id": originator_id,
                        "github_login": "alice",
                        "bounty_count": 2,
                        "total_bounty_value": 30.0,
                        "atom_count": 4,
                    }
                ]
            )
        if query.name == "bounties":
            return FakeResult(
                data=[
                    {
                        "bounty_id": _uuid("99999999-9999-9999-9999-999999999999"),
                    }
                ]
            )
        if query.name == "compute_preserved":
            return FakeResult(
                data=[
                    {"bounty_id": "b1", "escrow_amount": 10.0, "cdg_node_count": 2},
                    {"bounty_id": "b2", "escrow_amount": 20.0, "cdg_node_count": 3},
                ]
            )
        if query.name == "atoms":
            return FakeResult(
                data=[
                    {
                        "atom_id": _uuid("88888888-8888-8888-8888-888888888888"),
                        "fqdn": "pkg.filter",
                        "description": "Filter atom",
                        "owner_id": originator_id,
                    }
                ]
            )
        if query.name == "atom_authors":
            return FakeResult(data=[{"user_id": originator_id}])
        if query.name == "users":
            return FakeResult(data=[{"github_login": "alice"}])
        raise AssertionError((query.name, query.action))

    def rpc_handler(query: FakeRpcQuery) -> FakeResult:
        if query.name == "get_originator_impact":
            return FakeResult(
                data=[
                    {
                        "originator_id": originator_id,
                        "github_login": "alice",
                        "bounty_count": 2,
                        "total_bounty_value": 30.0,
                        "atom_count": 4,
                    }
                ]
            )
        if query.name == "get_originator_bounty_values":
            return FakeResult(
                data=[{"bounty_id": "b1", "escrow_amount": 10.0}, {"bounty_id": "b2", "escrow_amount": 20.0}]
            )
        if query.name == "get_atom_benchmarks":
            return FakeResult(
                data=[
                    {
                        "benchmark_id": "signal_v1",
                        "benchmark_name": "signal_v1",
                        "metric_name": "loss",
                        "metric_value": 0.1,
                        "dataset_tag": "v1",
                        "measured_at": "2026-03-31T00:00:00Z",
                    }
                ]
            )
        if query.name == "get_bounty_leaderboard":
            return FakeResult(
                data=[
                    {
                        "submission_id": "sub-1",
                        "architect_id": "arch-1",
                        "metric_values": {"loss": 0.1},
                        "verified_at": "2026-03-31T00:00:00Z",
                        "total_count": 1,
                    }
                ]
            )
        raise AssertionError(query.name)

    client = FakeSupabaseClient(table_handler, rpc_handler=rpc_handler)

    impact = await dashboard.get_originator_impact(
        originator_id, supabase=client
    )
    assert impact["originator_id"] == str(originator_id)
    assert impact["total_bounty_value"] == 30.0

    benchmarks = await dashboard.get_atom_benchmarks(
        "pkg.filter", supabase=client
    )
    assert benchmarks[0]["benchmark_id"] == "signal_v1"

    leaderboard = await verification.get_leaderboard(
        bounty_id=_uuid("99999999-9999-9999-9999-999999999999"),
        limit=50,
        supabase=client,
    )
    assert leaderboard.total == 1
    assert leaderboard.items[0].submission_id == "sub-1"
