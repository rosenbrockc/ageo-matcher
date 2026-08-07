from __future__ import annotations

import pytest

from sciona.catalog_embeddings import (
    CatalogEmbeddingConfig,
    build_embedding_input,
    compute_input_hash,
    create_embeddings_with_retry,
    ordered_response_embeddings,
    validate_embedding,
)


def test_embedding_space_and_input_are_deterministic() -> None:
    config = CatalogEmbeddingConfig()
    text = build_embedding_input(
        {
            "fqdn": "sciona.atoms.physics.motion.velocity",
            "technical_description": "Compute velocity.",
            "dejargonized_description": "Divide distance by time.",
            "domain_tags": ["physics", "kinematics"],
        }
    )

    assert config.space_id == (
        "openai:text-embedding-3-small:text-embedding-3-small:"
        "1536:atom-search-v1"
    )
    assert text.endswith("physics kinematics")
    assert len(compute_input_hash(text)) == 16


def test_embedding_response_is_ordered_and_dimension_checked() -> None:
    second = type("Item", (), {"index": 1, "embedding": [3.0, 4.0]})()
    first = type("Item", (), {"index": 0, "embedding": [1.0, 2.0]})()
    response = type("Response", (), {"data": [second, first]})()

    assert ordered_response_embeddings(
        response, expected_count=2, dimensions=2
    ) == [[1.0, 2.0], [3.0, 4.0]]

    with pytest.raises(ValueError, match="dimension mismatch"):
        validate_embedding([1.0], dimensions=2)
    with pytest.raises(ValueError, match="non-finite"):
        validate_embedding([float("nan"), 1.0], dimensions=2)


def test_embedding_creation_retries_transient_failure(monkeypatch) -> None:
    monkeypatch.setenv("SCIONA_CATALOG_EMBEDDING_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("SCIONA_CATALOG_EMBEDDING_RETRY_BACKOFF_SECONDS", "0.25")
    calls = 0

    class Endpoint:
        def create(self, **kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ConnectionError("temporary outage")
            item = type("Item", (), {"index": 0, "embedding": [0.5] * 1536})()
            return type("Response", (), {"data": [item]})()

    sleeps: list[float] = []
    response, embeddings = create_embeddings_with_retry(
        type("Client", (), {"embeddings": Endpoint()})(),
        ["intent"],
        config=CatalogEmbeddingConfig(),
        sleep=sleeps.append,
    )

    assert response.data[0].index == 0
    assert embeddings[0][0] == 0.5
    assert calls == 3
    assert sleeps == [0.25, 0.5]
