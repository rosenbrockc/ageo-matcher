"""Catalog search and atom-document endpoints."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query

from sciona.api import deps as api_deps
from sciona.api.models import CatalogEntry, ProviderInstallInfo
from sciona.catalog_embeddings import (
    CatalogEmbeddingConfig,
    embedding_config_from_row,
    ordered_response_embeddings,
)
from sciona.catalog_query import expand_catalog_query_tokens

router = APIRouter()

_FALLBACK_STOP_WORDS = {
    "a", "an", "and", "for", "from", "in", "into", "of", "on", "or", "the", "then", "to", "with",
}


def _fallback_text_filter(query: str, *columns: str) -> str:
    tokens = [
        token
        for token in sorted(expand_catalog_query_tokens(query))
        if len(token) >= 3 and token not in _FALLBACK_STOP_WORDS
    ][:20]
    return ",".join(
        f"{column}.ilike.%{token}%" for token in tokens for column in columns
    )


def _catalog_entry_from_row(
    row: dict,
    *,
    default_kind: str,
    provider: ProviderInstallInfo | None = None,
) -> CatalogEntry:
    return CatalogEntry(
        fqdn=row["fqdn"],
        description=row.get("technical_description", "") or "",
        artifact_kind=row.get("artifact_kind", default_kind) or default_kind,
        domain_tags=row.get("domain_tags", []) or [],
        status="approved",
        overall_verdict=row.get("overall_verdict", "") or "",
        risk_tier=row.get("risk_tier", "") or "",
        trust_readiness=row.get("trust_readiness", "") or "",
        provider=provider,
        score=float(
            row.get("similarity")
            or row.get("hybrid_score")
            or row.get("fts_rank")
            or 0.0
        ),
    )


async def _active_embedding_config(supabase) -> CatalogEmbeddingConfig | None:
    try:
        result = await supabase.rpc(
            "get_active_embedding_configuration", {}
        ).execute()
    except Exception:
        return None
    data = result.data
    if isinstance(data, list):
        row = data[0] if data else None
    else:
        row = data
    if not isinstance(row, dict) or not row:
        return None
    try:
        config = embedding_config_from_row(row)
    except (KeyError, TypeError, ValueError):
        return None
    if row.get("embedding_space_id") != config.space_id:
        return None
    return config


async def _embed_catalog_query(
    query: str,
    config: CatalogEmbeddingConfig,
) -> list[float] | None:
    """Embed a query on the API side so clients never need embedding credentials."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return None
    try:
        response = await AsyncOpenAI(api_key=api_key).embeddings.create(
            model=config.model,
            input=[query],
            dimensions=config.dimensions,
        )
        return ordered_response_embeddings(
            response,
            expected_count=1,
            dimensions=config.dimensions,
        )[0]
    except Exception:
        return None


async def _provider_installations(supabase, fqdns: list[str]) -> dict[str, ProviderInstallInfo]:
    if not fqdns:
        return {}
    try:
        result = await (
            supabase.table("catalog_atom_installations")
            .select(
                "fqdn,provider_id,distribution_name,distribution_version,"
                "install_requirement,import_module,import_symbol,wheel_url,wheel_sha256"
            )
            .in_("fqdn", fqdns)
            .execute()
        )
    except Exception:
        return {}
    return {
        str(row["fqdn"]): ProviderInstallInfo(**row)
        for row in (result.data or [])
        if row.get("fqdn")
    }


async def _catalog_entries(
    rows: list[dict],
    *,
    default_kind: str,
    supabase,
) -> list[CatalogEntry]:
    installations = await _provider_installations(
        supabase,
        [str(row.get("fqdn", "")) for row in rows if row.get("fqdn")],
    )
    return [
        _catalog_entry_from_row(
            row,
            default_kind=default_kind,
            provider=installations.get(str(row.get("fqdn", ""))),
        )
        for row in rows
    ]


async def _fetch_artifact_benchmarks(
    fqdn: str,
    *,
    supabase,
) -> list[dict]:
    artifact_result = await (
        supabase.table("artifacts")
        .select("artifact_id")
        .eq("fqdn", fqdn)
        .limit(1)
        .execute()
    )
    artifact_rows = artifact_result.data or []
    if not artifact_rows:
        return []
    artifact_id = artifact_rows[0].get("artifact_id")
    if not artifact_id:
        return []

    version_result = await (
        supabase.table("artifact_versions")
        .select("version_id,content_hash")
        .eq("artifact_id", artifact_id)
        .execute()
    )
    version_rows = version_result.data or []
    if not version_rows:
        return []
    content_hash_by_version = {
        str(row["version_id"]): str(row.get("content_hash", ""))
        for row in version_rows
        if row.get("version_id")
    }
    version_ids = sorted(content_hash_by_version)
    if not version_ids:
        return []

    benchmark_result = await (
        supabase.table("artifact_benchmarks")
        .select("version_id,benchmark_name,metric_name,metric_value,dataset_tag,measured_at")
        .in_("version_id", version_ids)
        .execute()
    )
    benchmark_rows = benchmark_result.data or []
    rows: list[dict] = []
    for row in benchmark_rows:
        version_id = str(row.get("version_id", ""))
        rows.append(
            {
                "artifact_fqdn": fqdn,
                "content_hash": content_hash_by_version.get(version_id, ""),
                "benchmark_id": row.get("benchmark_name", "") or "",
                "benchmark_name": row.get("benchmark_name", "") or "",
                "metric_name": row.get("metric_name", "") or "",
                "metric_value": row.get("metric_value"),
                "dataset_tag": row.get("dataset_tag", "") or "",
                "measured_at": row.get("measured_at", "") or "",
            }
        )
    rows.sort(
        key=lambda row: (
            str(row.get("benchmark_name", "")),
            str(row.get("metric_name", "")),
            str(row.get("content_hash", "")),
            str(row.get("measured_at", "")),
        )
    )
    return rows


@router.get("/search")
async def catalog_search(
    q: str,
    domain_tag: str | None = None,
    limit: int = Query(default=50, le=200),
    supabase=Depends(api_deps.get_supabase),
) -> list[CatalogEntry]:
    """Hybrid semantic search across the Postgres-backed atom catalog."""
    if q:
        try:
            embedding_config = await _active_embedding_config(supabase)
            query_embedding = (
                await _embed_catalog_query(q, embedding_config)
                if embedding_config is not None
                else None
            )
            params = {
                "query_text": q,
                "mode": "hybrid" if query_embedding is not None else "fts",
                "result_limit": limit,
                "result_offset": 0,
            }
            if query_embedding is not None:
                params["query_embedding"] = query_embedding
            rpc_result = await supabase.rpc(
                "search_atoms_hybrid",
                params,
            ).execute()
            rows = rpc_result.data or []
            if domain_tag:
                rows = [
                    row
                    for row in rows
                    if domain_tag in (row.get("domain_tags") or [])
                ]
            if rows:
                return await _catalog_entries(
                    rows[:limit], default_kind="atom", supabase=supabase
                )
        except Exception:
            pass
    query = supabase.table("catalog_atoms_served").select(
        "fqdn, technical_description, domain_tags, overall_verdict, risk_tier, trust_readiness"
    )
    if q:
        text_filter = _fallback_text_filter(q, "fqdn", "technical_description")
        if text_filter:
            query = query.or_(text_filter)
    if domain_tag:
        query = query.contains("domain_tags", [domain_tag])
    result = await query.limit(limit).execute()
    return await _catalog_entries(
        result.data or [], default_kind="atom", supabase=supabase
    )


@router.get("/atom/{fqdn:path}")
async def get_atom_document(
    fqdn: str,
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Fetch the full atom documentation bundle via the database RPC."""
    result = await supabase.rpc(
        "get_atom_document",
        {"request_fqdn": fqdn},
    ).execute()
    document = result.data
    if not document:
        raise HTTPException(404, f"Atom {fqdn!r} not found")
    return document


@router.get("/find/{fqdn:path}")
async def find_catalog_atom(
    fqdn: str,
    supabase=Depends(api_deps.get_supabase),
) -> CatalogEntry:
    """Resolve one exact published atom without relevance-search truncation."""
    result = await (
        supabase.table("atoms")
        .select(
            "fqdn,description,domain_tags,source_repo_id,import_module,"
            "namespace_root,source_module_path,source_symbol"
        )
        .eq("fqdn", fqdn)
        .eq("is_publishable", True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise HTTPException(404, f"Audit-ready atom {fqdn!r} not found")
    row = dict(rows[0])
    repository_result = await (
        supabase.table("atom_source_repositories")
        .select(
            "repo_name,distribution_name,distribution_version,install_requirement,"
            "wheel_url,wheel_sha256"
        )
        .eq("source_repo_id", row.get("source_repo_id"))
        .eq("active", True)
        .limit(1)
        .execute()
    )
    repository_rows = repository_result.data or []
    provider = None
    if repository_rows:
        repository = repository_rows[0]
        import_module = str(row.get("import_module", "") or "")
        if not import_module:
            import_module = ".".join(
                part
                for part in (
                    str(row.get("namespace_root", "") or ""),
                    str(row.get("source_module_path", "") or ""),
                )
                if part
            )
        provider = ProviderInstallInfo(
            provider_id=str(repository.get("repo_name", "") or ""),
            distribution_name=str(repository.get("distribution_name", "") or ""),
            distribution_version=str(repository.get("distribution_version", "") or ""),
            install_requirement=str(repository.get("install_requirement", "") or ""),
            import_module=import_module,
            import_symbol=str(row.get("source_symbol", "") or ""),
            wheel_url=str(repository.get("wheel_url", "") or ""),
            wheel_sha256=str(repository.get("wheel_sha256", "") or ""),
        )
    row["technical_description"] = str(row.get("description", "") or "")
    return _catalog_entry_from_row(row, default_kind="atom", provider=provider)


@router.get("/search-artifacts")
async def artifact_search(
    q: str,
    domain_tag: str | None = None,
    limit: int = Query(default=50, le=200),
    supabase=Depends(api_deps.get_supabase),
) -> list[CatalogEntry]:
    """Search across artifact kinds, falling back to the atom catalog when needed."""
    if q:
        try:
            rpc_result = await supabase.rpc(
                "search_artifacts_hybrid",
                {
                    "query_text": q,
                    "mode": "fts",
                    "result_limit": limit,
                    "result_offset": 0,
                },
            ).execute()
            rows = rpc_result.data or []
            if domain_tag:
                rows = [
                    row
                    for row in rows
                    if domain_tag in (row.get("domain_tags") or [])
                ]
            if rows:
                return [
                    _catalog_entry_from_row(row, default_kind="artifact")
                    for row in rows[:limit]
                ]
        except Exception:
            pass
    try:
        query = supabase.table("catalog_artifacts_served").select(
            "fqdn, artifact_kind, technical_description, domain_tags, overall_verdict, risk_tier, trust_readiness"
        )
        if q:
            text_filter = _fallback_text_filter(q, "fqdn", "technical_description")
            if text_filter:
                query = query.or_(text_filter)
        if domain_tag:
            query = query.contains("domain_tags", [domain_tag])
        result = await query.limit(limit).execute()
        return [
            _catalog_entry_from_row(row, default_kind="artifact")
            for row in (result.data or [])
        ]
    except Exception:
        return await catalog_search(q=q, domain_tag=domain_tag, limit=limit, supabase=supabase)


@router.get("/artifact/{fqdn:path}")
async def get_artifact_document(
    fqdn: str,
    supabase=Depends(api_deps.get_supabase),
) -> dict:
    """Fetch the full artifact documentation bundle via the database RPC."""
    try:
        result = await supabase.rpc(
            "get_artifact_document",
            {"request_fqdn": fqdn},
        ).execute()
        document = result.data
    except Exception:
        document = None
    if not document:
        return await get_atom_document(fqdn, supabase=supabase)
    if not document.get("benchmarks"):
        try:
            document["benchmarks"] = await _fetch_artifact_benchmarks(
                fqdn,
                supabase=supabase,
            )
        except Exception:
            pass
    return document
