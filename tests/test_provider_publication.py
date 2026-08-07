from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from sciona.provider_publication import (
    _backfill_error_counts,
    _validate_audit_inventory_coverage,
    publish_provider_catalog,
    refresh_catalog_embeddings,
)


@dataclass
class Result:
    data: Any


class Query:
    def __init__(self, sink: list[dict[str, Any]]) -> None:
        self.sink = sink

    def upsert(
        self,
        rows: dict[str, Any] | list[dict[str, Any]],
        **kwargs: Any,
    ) -> "Query":
        self.sink.extend(rows if isinstance(rows, list) else [rows])
        return self

    def range(self, start: int, end: int) -> "Query":
        return self

    def update(self, payload: dict[str, Any]) -> "Query":
        return self

    def in_(self, field: str, values: list[str]) -> "Query":
        return self

    def eq(self, field: str, value: str) -> "Query":
        return self

    def execute(self) -> Result:
        return Result(self.sink)


class Supabase:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def rpc(self, name: str, params: dict[str, Any]) -> Query:
        assert name == "get_atoms_needing_embeddings"
        assert params["expected_model"] == "text-embedding-3-small"
        assert params["expected_model_revision"] == "text-embedding-3-small"
        assert params["expected_embedding_space_id"].endswith(":atom-search-v1")
        query = Query(self.rows)
        query.execute = lambda: Result(
            [
                {
                    "atom_id": "atom-1",
                    "fqdn": "sciona.atoms.numerical.average",
                    "technical_description": "Compute an arithmetic mean",
                    "dejargonized_description": "Average some numbers",
                    "domain_tags": ["numerical"],
                }
            ]
        )
        return query

    def table(self, name: str) -> Query:
        assert name in {
            "atom_embeddings",
            "embedding_refresh_queue",
            "catalog_embedding_configuration",
        }
        return Query(self.rows)


class Embeddings:
    def create(self, **kwargs: Any) -> Any:
        assert kwargs["model"] == "text-embedding-3-small"
        assert "arithmetic mean" in kwargs["input"][0]
        item = type("Item", (), {"embedding": [0.1] * 1536, "index": 0})()
        return type(
            "Response",
            (),
            {"data": [item], "model": "text-embedding-3-small"},
        )()


def test_refresh_catalog_embeddings_backfills_changed_rows() -> None:
    supabase = Supabase()
    openai_client = type("OpenAI", (), {"embeddings": Embeddings()})()

    summary = refresh_catalog_embeddings(supabase, openai_client=openai_client)

    assert summary == {
        "needed": 1,
        "embedded": 1,
        "embedding_space_id": (
            "openai:text-embedding-3-small:text-embedding-3-small:"
            "1536:atom-search-v1"
        ),
        "model": "text-embedding-3-small",
        "model_revision": "text-embedding-3-small",
    }
    assert supabase.rows[0]["atom_id"] == "atom-1"
    assert len(supabase.rows[0]["input_text_hash"]) == 16
    assert supabase.rows[0]["embedding_space_id"] == summary["embedding_space_id"]
    assert supabase.rows[-1]["configuration_id"] is True


def test_refresh_failure_marks_queue_and_does_not_activate(monkeypatch) -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    class FailureQuery:
        def __init__(self, name: str, data: list[dict[str, Any]] | None = None):
            self.name = name
            self.data = data or []
            self.payload: dict[str, Any] = {}

        def range(self, start: int, end: int):
            return self

        def update(self, payload: dict[str, Any]):
            self.payload = payload
            return self

        def in_(self, field: str, values: list[str]):
            return self

        def eq(self, field: str, value: str):
            return self

        def execute(self):
            if self.payload:
                events.append((self.name, self.payload))
            return Result(self.data)

    class FailureSupabase:
        def rpc(self, name: str, params: dict[str, Any]):
            return FailureQuery(
                name,
                [
                    {
                        "atom_id": "atom-1",
                        "fqdn": "sciona.atoms.demo",
                        "technical_description": "Demo",
                        "dejargonized_description": "",
                        "domain_tags": [],
                    }
                ],
            )

        def table(self, name: str):
            if name == "catalog_embedding_configuration":
                raise AssertionError("partial embedding space was activated")
            return FailureQuery(name)

    class FailureEndpoint:
        def create(self, **kwargs: Any):
            raise ConnectionError("provider unavailable")

    monkeypatch.setenv("SCIONA_CATALOG_EMBEDDING_MAX_ATTEMPTS", "1")
    client = type("OpenAI", (), {"embeddings": FailureEndpoint()})()

    with pytest.raises(ConnectionError, match="provider unavailable"):
        refresh_catalog_embeddings(FailureSupabase(), openai_client=client)

    assert events == [
        (
            "embedding_refresh_queue",
            {"status": "failed", "error_message": "provider unavailable"},
        )
    ]


def test_backfill_error_counts_reports_nested_failures() -> None:
    summary = {
        "references-registry": {"upserted": 540, "errors": 1},
        "references": {"inserted": 6102, "errors": 2},
        "audit": {"errors": 0},
    }

    assert _backfill_error_counts(summary) == {
        "references-registry": 1,
        "references": 2,
    }


def test_audit_inventory_coverage_rejects_missing_seeded_atoms() -> None:
    with pytest.raises(RuntimeError, match=r"seeded=10, audit-rollups=9"):
        _validate_audit_inventory_coverage(
            {"atom_rows": 10},
            {
                "audit-evidence": {"manifest_atoms": 10},
                "audit-rollups": {"manifest_atoms": 9},
            },
        )


def test_publish_provider_catalog_rejects_partial_backfill(monkeypatch) -> None:
    monkeypatch.setattr(
        "sciona.atoms.supabase_seed.derive_seed_inventory", lambda **kwargs: object()
    )
    monkeypatch.setattr(
        "sciona.atoms.supabase_seed.seed_core_supabase",
        lambda *args, **kwargs: {"applied": True},
    )
    monkeypatch.setattr(
        "sciona.atoms.supabase_backfill.run_backfill_command",
        lambda *args, **kwargs: {"references": {"errors": 1}},
    )

    with pytest.raises(RuntimeError, match="references=1"):
        publish_provider_catalog(
            apply=True,
            supabase=object(),
            include_embeddings=False,
        )
