from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from scripts.generate_embeddings import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    backfill,
    build_embedding_input,
    compute_input_hash,
    drain_queue,
)


@dataclass
class FakeResult:
    data: Any = None


class FakeQuery:
    def __init__(self, client: "FakeSupabaseClient", name: str) -> None:
        self.client = client
        self.name = name
        self.action = "select"
        self.payload: Any = None
        self.filters: list[tuple[str, Any, Any]] = []
        self.order_field = ""
        self.limit_value: int | None = None

    def select(self, fields: str):
        self.action = "select"
        self.payload = fields
        return self

    def upsert(self, payload: Any, **kwargs: Any):
        self.action = "upsert"
        self.payload = payload
        return self

    def update(self, payload: Any):
        self.action = "update"
        self.payload = payload
        return self

    def eq(self, field: str, value: Any):
        self.filters.append(("eq", field, value))
        return self

    def in_(self, field: str, values: list[Any]):
        self.filters.append(("in", field, values))
        return self

    def order(self, field: str):
        self.order_field = field
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    def single(self):
        self.filters.append(("single", "", None))
        return self

    def execute(self) -> FakeResult:
        return self.client.table_handler(self)


class FakeRpcQuery:
    def __init__(self, client: "FakeSupabaseClient", name: str, params: dict[str, Any]):
        self.client = client
        self.name = name
        self.params = params

    def execute(self) -> FakeResult:
        return self.client.rpc_handler(self)


class FakeSupabaseClient:
    def __init__(
        self,
        table_handler: Callable[[FakeQuery], FakeResult],
        rpc_handler: Callable[[FakeRpcQuery], FakeResult],
    ) -> None:
        self.table_handler = table_handler
        self.rpc_handler = rpc_handler

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def rpc(self, name: str, params: dict[str, Any]) -> FakeRpcQuery:
        return FakeRpcQuery(self, name, params)


class FakeEmbeddingItem:
    def __init__(self, embedding: list[float]) -> None:
        self.embedding = embedding


class FakeOpenAIClient:
    def __init__(self, embeddings: list[list[float]] | Exception) -> None:
        self.calls: list[dict[str, Any]] = []
        self._embeddings = embeddings
        self.embeddings = self

    def create(self, *, model: str, input: list[str], dimensions: int) -> Any:
        self.calls.append(
            {
                "model": model,
                "input": input,
                "dimensions": dimensions,
            }
        )
        if isinstance(self._embeddings, Exception):
            raise self._embeddings
        return type(
            "Response",
            (),
            {"data": [FakeEmbeddingItem(value) for value in self._embeddings]},
        )()


def test_build_embedding_input_and_hash() -> None:
    text = build_embedding_input(
        {
            "fqdn": "pkg.filter",
            "technical_description": "Filter signal",
            "dejargonized_description": "",
            "domain_tags": ["signal", "audio"],
        }
    )

    assert text == "pkg.filter\nFilter signal\nsignal audio"
    assert compute_input_hash(text) == compute_input_hash(text)
    assert len(compute_input_hash(text)) == 16


def test_backfill_upserts_embeddings(monkeypatch) -> None:
    calls: list[tuple[str, str, Any]] = []

    def table_handler(query: FakeQuery) -> FakeResult:
        calls.append((query.name, query.action, query.payload))
        if query.name == "atom_embeddings" and query.action == "upsert":
            return FakeResult(data=query.payload)
        if query.name == "catalog_embedding_configuration" and query.action == "upsert":
            return FakeResult(data=query.payload)
        if query.name == "embedding_refresh_queue" and query.action == "update":
            return FakeResult(data=[])
        raise AssertionError(f"unexpected table query: {query.name} {query.action}")

    def rpc_handler(query: FakeRpcQuery) -> FakeResult:
        assert query.name == "get_atoms_needing_embeddings"
        return FakeResult(
            data=[
                {
                    "atom_id": "a1",
                    "fqdn": "pkg.filter",
                    "technical_description": "Filter signal",
                    "dejargonized_description": "Clean a signal",
                    "domain_tags": ["signal"],
                }
            ]
        )

    monkeypatch.setattr("scripts.generate_embeddings.time.sleep", lambda _: None)
    supabase = FakeSupabaseClient(table_handler=table_handler, rpc_handler=rpc_handler)
    openai_client = FakeOpenAIClient([[0.1] * EMBEDDING_DIMENSIONS])

    backfill(supabase, openai_client)

    assert openai_client.calls == [
        {
            "model": EMBEDDING_MODEL,
            "input": ["pkg.filter\nFilter signal\nClean a signal\nsignal"],
            "dimensions": EMBEDDING_DIMENSIONS,
        }
    ]
    upsert_payload = next(
        payload
        for name, action, payload in calls
        if name == "atom_embeddings" and action == "upsert"
    )
    assert upsert_payload[0]["atom_id"] == "a1"
    assert upsert_payload[0]["model"] == EMBEDDING_MODEL
    assert upsert_payload[0]["dimensions"] == EMBEDDING_DIMENSIONS
    assert upsert_payload[0]["input_text_hash"] == compute_input_hash(
        "pkg.filter\nFilter signal\nClean a signal\nsignal"
    )
    assert upsert_payload[0]["updated_at"].endswith("Z")
    assert upsert_payload[0]["embedding_space_id"].endswith(":atom-search-v1")
    assert calls[-1][0] == "catalog_embedding_configuration"


def test_drain_queue_updates_status_and_embeddings() -> None:
    queue_updates: list[dict[str, Any]] = []
    embedding_upserts: list[dict[str, Any]] = []

    def table_handler(query: FakeQuery) -> FakeResult:
        if query.name == "embedding_refresh_queue" and query.action == "select":
            return FakeResult(
                data=[{"queue_id": 7, "atom_id": "a1", "attempts": 0}]
            )
        if query.name == "embedding_refresh_queue" and query.action == "update":
            queue_updates.append(query.payload)
            return FakeResult(data=[])
        if query.name == "catalog_atoms_served" and query.action == "select":
            return FakeResult(
                data=[
                    {
                        "atom_id": "a1",
                        "fqdn": "pkg.filter",
                        "technical_description": "Filter signal",
                        "dejargonized_description": "Clean a signal",
                        "domain_tags": ["signal"],
                    }
                ]
            )
        if query.name == "atom_embeddings" and query.action == "upsert":
            embedding_upserts.extend(query.payload)
            return FakeResult(data=[])
        raise AssertionError(f"unexpected table query: {query.name} {query.action}")

    supabase = FakeSupabaseClient(
        table_handler=table_handler,
        rpc_handler=lambda query: FakeResult(data=[]),
    )
    openai_client = FakeOpenAIClient([[0.1] * EMBEDDING_DIMENSIONS])

    drain_queue(supabase, openai_client)

    assert queue_updates[0]["status"] == "processing"
    assert queue_updates[0]["attempts"] == 1
    assert queue_updates[0]["started_at"].endswith("Z")
    assert queue_updates[-1]["status"] == "completed"
    assert queue_updates[-1]["completed_at"].endswith("Z")
    assert embedding_upserts[0]["atom_id"] == "a1"
    assert embedding_upserts[0]["model"] == EMBEDDING_MODEL


def test_drain_queue_records_failures() -> None:
    queue_updates: list[dict[str, Any]] = []

    def table_handler(query: FakeQuery) -> FakeResult:
        if query.name == "embedding_refresh_queue" and query.action == "select":
            return FakeResult(
                data=[{"queue_id": 7, "atom_id": "a1", "attempts": 1}]
            )
        if query.name == "embedding_refresh_queue" and query.action == "update":
            queue_updates.append(query.payload)
            return FakeResult(data=[])
        if query.name == "catalog_atoms_served" and query.action == "select":
            return FakeResult(
                data=[
                    {
                        "atom_id": "a1",
                        "fqdn": "pkg.filter",
                        "technical_description": "Filter signal",
                        "dejargonized_description": "",
                        "domain_tags": ["signal"],
                    }
                ]
            )
        if query.name == "atom_embeddings" and query.action == "upsert":
            raise AssertionError("upsert should not be reached on embedding failure")
        raise AssertionError(f"unexpected table query: {query.name} {query.action}")

    supabase = FakeSupabaseClient(
        table_handler=table_handler,
        rpc_handler=lambda query: FakeResult(data=[]),
    )
    openai_client = FakeOpenAIClient(RuntimeError("embedding failed"))

    drain_queue(supabase, openai_client)

    assert queue_updates[0]["status"] == "processing"
    assert queue_updates[-1]["status"] == "failed"
    assert queue_updates[-1]["error_message"] == "embedding failed"


def test_phase6_migration_matches_plan() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "supabase"
        / "migrations"
        / "20260331000400_phase6_search_and_embeddings.sql"
    )
    sql = migration_path.read_text()

    assert "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;" in sql
    assert "CREATE TABLE public.atom_embeddings" in sql
    assert "embedding extensions.vector(1536) NOT NULL" in sql
    assert "USING hnsw (embedding extensions.vector_cosine_ops)" in sql
    assert "CREATE TABLE public.embedding_refresh_queue" in sql
    assert "CREATE OR REPLACE FUNCTION public.search_atoms_fts" in sql
    assert "CREATE OR REPLACE FUNCTION public.search_atoms_vector" in sql
    assert "CREATE OR REPLACE FUNCTION public.search_atoms_hybrid" in sql
    assert "CREATE OR REPLACE FUNCTION public.get_atoms_needing_embeddings()" in sql
    assert "ae.input_text_hash IS DISTINCT FROM public.atom_embedding_input_hash" in sql


def test_embedding_provenance_migration_tracks_and_refreshes_model_space() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "supabase"
        / "migrations"
        / "20260806020000_embedding_provenance.sql"
    )
    sql = migration_path.read_text()

    assert "CREATE TABLE IF NOT EXISTS public.catalog_embedding_configuration" in sql
    assert "ADD COLUMN IF NOT EXISTS model_revision" in sql
    assert "ADD COLUMN IF NOT EXISTS embedding_space_id" in sql
    assert "ae.model_revision IS DISTINCT FROM expected_model_revision" in sql
    assert "ae.embedding_space_id IS DISTINCT FROM expected_embedding_space_id" in sql
    assert "get_active_embedding_configuration" in sql


def test_catalog_intent_search_migration_is_generic_and_mirrored() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    relative_path = Path(
        "supabase/migrations/20260806030000_catalog_intent_fts.sql"
    )
    matcher_sql = (repo_root / relative_path).read_text()
    infra_sql = (repo_root.parent / "sciona-infra" / relative_path).read_text()

    assert matcher_sql == infra_sql
    assert "CREATE OR REPLACE FUNCTION public.catalog_relaxed_tsquery" in matcher_sql
    assert "tsvector_to_array(" in matcher_sql
    assert "array_to_string(values, ' | ')" in matcher_sql
    assert "websearch_to_tsquery(" in matcher_sql
    assert "candidates.document @@ candidates.strict_tsq" in matcher_sql
    assert "candidates.document @@ candidates.relaxed_tsq" in matcher_sql
