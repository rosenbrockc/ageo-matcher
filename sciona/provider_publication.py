"""First-class publication workflow for federated atom providers."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Any

from sciona.catalog_embeddings import (
    DEFAULT_DIMENSIONS,
    DEFAULT_MODEL,
    CatalogEmbeddingConfig,
    build_embedding_input,
    compute_input_hash,
    create_embeddings_with_retry,
    embedding_config_from_env,
)

EMBEDDING_MODEL = DEFAULT_MODEL
EMBEDDING_DIMENSIONS = DEFAULT_DIMENSIONS
EMBEDDING_BATCH_SIZE = 100
EMBEDDING_FETCH_PAGE_SIZE = 1000


def _embedding_text(atom: dict[str, Any]) -> str:
    return build_embedding_input(atom)


def _backfill_error_counts(
    summary: dict[str, Any], *, prefix: str = ""
) -> dict[str, int]:
    errors: dict[str, int] = {}
    for name, value in summary.items():
        path = f"{prefix}.{name}" if prefix else name
        if name == "errors" and isinstance(value, int) and value > 0:
            errors[prefix or "backfill"] = value
        elif isinstance(value, dict):
            errors.update(_backfill_error_counts(value, prefix=path))
    return errors


def _validate_audit_inventory_coverage(
    seed_summary: dict[str, Any], backfill_summary: dict[str, Any]
) -> None:
    """Reject publication when seeded atoms have no file-backed audit record."""
    seeded = seed_summary.get("atom_rows")
    if not isinstance(seeded, int):
        return
    for section_name in ("audit-evidence", "audit-rollups"):
        section = backfill_summary.get(section_name)
        if not isinstance(section, dict):
            continue
        audited = section.get("manifest_atoms")
        if isinstance(audited, int) and audited != seeded:
            raise RuntimeError(
                "Provider publication audit inventory mismatch: "
                f"seeded={seeded}, {section_name}={audited}"
            )


def refresh_catalog_embeddings(
    supabase: Any,
    *,
    openai_client: Any | None = None,
    config: CatalogEmbeddingConfig | None = None,
) -> dict[str, object]:
    """Embed every publishable catalog row whose semantic text changed."""
    config = config or embedding_config_from_env()
    if openai_client is None:
        from openai import OpenAI

        openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    atoms: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = supabase.rpc("get_atoms_needing_embeddings", config.rpc_params())
        if hasattr(query, "range"):
            query = query.range(offset, offset + EMBEDDING_FETCH_PAGE_SIZE - 1)
        page = query.execute().data or []
        atoms.extend(page)
        if len(page) < EMBEDDING_FETCH_PAGE_SIZE or not hasattr(query, "range"):
            break
        offset += EMBEDDING_FETCH_PAGE_SIZE
    embedded = 0
    for start in range(0, len(atoms), EMBEDDING_BATCH_SIZE):
        batch = atoms[start : start + EMBEDDING_BATCH_SIZE]
        texts = [_embedding_text(atom) for atom in batch]
        try:
            response, embeddings = create_embeddings_with_retry(
                openai_client,
                texts,
                config=config,
            )
        except Exception as exc:
            atom_ids = [str(atom["atom_id"]) for atom in batch]
            (
                supabase.table("embedding_refresh_queue")
                .update(
                    {
                        "status": "failed",
                        "error_message": str(exc)[:1000],
                    }
                )
                .in_("atom_id", atom_ids)
                .eq("status", "pending")
                .execute()
            )
            raise
        response_model = str(getattr(response, "model", "") or config.model)
        updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        rows = [
            {
                "atom_id": atom["atom_id"],
                "embedding": embedding,
                "provider": config.provider,
                "model": config.model,
                "model_revision": config.model_revision,
                "response_model": response_model,
                "dimensions": config.dimensions,
                "input_schema_version": config.input_schema_version,
                "embedding_space_id": config.space_id,
                "input_text_hash": compute_input_hash(text),
                "updated_at": updated_at,
            }
            for atom, text, embedding in zip(batch, texts, embeddings, strict=True)
        ]
        if rows:
            supabase.table("atom_embeddings").upsert(rows).execute()
            atom_ids = [row["atom_id"] for row in rows]
            (
                supabase.table("embedding_refresh_queue")
                .update(
                    {
                        "status": "completed",
                        "completed_at": datetime.now(timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "error_message": "",
                    }
                )
                .in_("atom_id", atom_ids)
                .eq("status", "pending")
                .execute()
            )
        embedded += len(rows)
    supabase.table("catalog_embedding_configuration").upsert(
        {
            "configuration_id": True,
            "provider": config.provider,
            "model": config.model,
            "model_revision": config.model_revision,
            "dimensions": config.dimensions,
            "input_schema_version": config.input_schema_version,
            "embedding_space_id": config.space_id,
            "activated_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        },
        on_conflict="configuration_id",
    ).execute()
    return {
        "needed": len(atoms),
        "embedded": embedded,
        "embedding_space_id": config.space_id,
        "model": config.model,
        "model_revision": config.model_revision,
    }


def publish_provider_catalog(
    *,
    workspace_root: Path | None = None,
    apply: bool = False,
    ensure_owner: bool = False,
    database_url: str | None = None,
    allow_duplicate_fqdns: bool = False,
    include_backfills: bool = True,
    include_embeddings: bool = True,
    supabase: Any | None = None,
    openai_client: Any | None = None,
) -> dict[str, Any]:
    """Publish every discovered provider through one auditable workflow."""
    try:
        from sciona.atoms.supabase_backfill import run_backfill_command
        from sciona.atoms.supabase_seed import (
            create_supabase_client_from_env,
            derive_seed_inventory,
            seed_core_supabase,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Provider publication requires the sciona-atoms operator package"
        ) from exc

    inventory = derive_seed_inventory(base_dir=workspace_root)
    if not apply:
        return {
            "seed": seed_core_supabase(
                object(),
                inventory=inventory,
                dry_run=True,
            ),
            "backfill": {"status": "not_run"},
            "embeddings": {"status": "not_run"},
        }

    client = supabase or create_supabase_client_from_env()
    result: dict[str, Any] = {
        "seed": seed_core_supabase(
            client,
            inventory=inventory,
            ensure_owner=ensure_owner,
            database_url=database_url,
            allow_duplicate_fqdns=allow_duplicate_fqdns,
        )
    }
    result["backfill"] = (
        run_backfill_command("all-file-backed", supabase=client)
        if include_backfills
        else {"status": "skipped"}
    )
    backfill_errors = _backfill_error_counts(result["backfill"])
    if backfill_errors:
        details = ", ".join(
            f"{name}={count}" for name, count in sorted(backfill_errors.items())
        )
        raise RuntimeError(f"Provider publication backfills reported errors: {details}")
    if include_backfills:
        _validate_audit_inventory_coverage(result["seed"], result["backfill"])
    result["embeddings"] = (
        refresh_catalog_embeddings(client, openai_client=openai_client)
        if include_embeddings
        else {"status": "skipped"}
    )
    return result
