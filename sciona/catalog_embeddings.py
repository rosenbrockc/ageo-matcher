"""Shared configuration and validation for the Postgres catalog embedding space."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
import time
from typing import Any, Iterable


DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_DIMENSIONS = 1536
DEFAULT_INPUT_SCHEMA_VERSION = "atom-search-v1"


@dataclass(frozen=True)
class CatalogEmbeddingConfig:
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    model_revision: str = DEFAULT_MODEL
    dimensions: int = DEFAULT_DIMENSIONS
    input_schema_version: str = DEFAULT_INPUT_SCHEMA_VERSION

    @property
    def space_id(self) -> str:
        return ":".join(
            (
                self.provider,
                self.model,
                self.model_revision,
                str(self.dimensions),
                self.input_schema_version,
            )
        )

    def rpc_params(self) -> dict[str, object]:
        return {
            "expected_provider": self.provider,
            "expected_model": self.model,
            "expected_model_revision": self.model_revision,
            "expected_dimensions": self.dimensions,
            "expected_input_schema_version": self.input_schema_version,
            "expected_embedding_space_id": self.space_id,
        }


def embedding_config_from_env() -> CatalogEmbeddingConfig:
    provider = os.environ.get("SCIONA_CATALOG_EMBEDDING_PROVIDER", DEFAULT_PROVIDER)
    model = os.environ.get("SCIONA_CATALOG_EMBEDDING_MODEL", DEFAULT_MODEL)
    revision = os.environ.get("SCIONA_CATALOG_EMBEDDING_MODEL_REVISION", model)
    dimensions = int(
        os.environ.get(
            "SCIONA_CATALOG_EMBEDDING_DIMENSIONS", str(DEFAULT_DIMENSIONS)
        )
    )
    input_schema = os.environ.get(
        "SCIONA_CATALOG_EMBEDDING_INPUT_SCHEMA", DEFAULT_INPUT_SCHEMA_VERSION
    )
    config = CatalogEmbeddingConfig(
        provider=provider.strip(),
        model=model.strip(),
        model_revision=revision.strip(),
        dimensions=dimensions,
        input_schema_version=input_schema.strip(),
    )
    if config.provider != "openai":
        raise ValueError(f"Unsupported catalog embedding provider {config.provider!r}")
    if not config.model or not config.model_revision or not config.input_schema_version:
        raise ValueError("Catalog embedding model metadata cannot be empty")
    if config.dimensions != DEFAULT_DIMENSIONS:
        raise ValueError(
            f"Catalog pgvector schema requires {DEFAULT_DIMENSIONS} dimensions, "
            f"got {config.dimensions}"
        )
    return config


def embedding_config_from_row(row: dict[str, Any]) -> CatalogEmbeddingConfig:
    return CatalogEmbeddingConfig(
        provider=str(row["provider"]),
        model=str(row["model"]),
        model_revision=str(row["model_revision"]),
        dimensions=int(row["dimensions"]),
        input_schema_version=str(row["input_schema_version"]),
    )


def build_embedding_input(atom: dict[str, Any]) -> str:
    parts = (
        str(atom.get("fqdn", "") or ""),
        str(atom.get("technical_description", "") or ""),
        str(atom.get("dejargonized_description", "") or ""),
        " ".join(str(tag) for tag in (atom.get("domain_tags", []) or [])),
    )
    return "\n".join(part for part in parts if part)


def compute_input_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def validate_embedding(vector: Iterable[float], *, dimensions: int) -> list[float]:
    values = [float(value) for value in vector]
    if len(values) != dimensions:
        raise ValueError(
            f"Embedding dimension mismatch: expected {dimensions}, got {len(values)}"
        )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Embedding contains a non-finite value")
    return values


def ordered_response_embeddings(
    response: Any,
    *,
    expected_count: int,
    dimensions: int,
) -> list[list[float]]:
    items = list(response.data)
    if len(items) != expected_count:
        raise ValueError(
            f"Embedding response count mismatch: expected {expected_count}, got {len(items)}"
        )
    if all(hasattr(item, "index") for item in items):
        items.sort(key=lambda item: int(item.index))
    return [validate_embedding(item.embedding, dimensions=dimensions) for item in items]


def create_embeddings_with_retry(
    openai_client: Any,
    texts: list[str],
    *,
    config: CatalogEmbeddingConfig,
    sleep: Any = time.sleep,
) -> tuple[Any, list[list[float]]]:
    """Create and validate one embedding batch with bounded transient retries."""
    attempts = int(os.environ.get("SCIONA_CATALOG_EMBEDDING_MAX_ATTEMPTS", "3"))
    backoff = float(
        os.environ.get("SCIONA_CATALOG_EMBEDDING_RETRY_BACKOFF_SECONDS", "1")
    )
    if attempts < 1 or backoff < 0:
        raise ValueError("Embedding retry attempts must be positive and backoff non-negative")
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = openai_client.embeddings.create(
                model=config.model,
                input=texts,
                dimensions=config.dimensions,
            )
            return response, ordered_response_embeddings(
                response,
                expected_count=len(texts),
                dimensions=config.dimensions,
            )
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts and backoff:
                sleep(backoff * (2**attempt))
    assert last_error is not None
    raise last_error
