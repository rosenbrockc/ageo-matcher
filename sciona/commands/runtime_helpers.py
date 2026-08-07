"""Runtime and environment helpers for CLI command handlers."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sciona.config import AgeomConfig
    from sciona.protocols import ProofEnvironment, SemanticIndex
    from sciona.shared_context import SharedContextMetrics, SharedContextStore
    from sciona.types import Prover


def _create_proof_env(prover: "Prover", config: "AgeomConfig") -> "ProofEnvironment":
    """Create the appropriate ProofEnvironment for the given prover."""
    if prover.value == "lean4":
        from sciona.judge.lean_env import LeanEnvironment

        return LeanEnvironment(config.lean_toolchain)
    if prover.value == "python":
        from sciona.judge.python_env import PythonEnvironment

        return PythonEnvironment(
            mypy_path=config.python_mypy_path,
            python_path=config.python_path,
        )
    from sciona.judge.coq_env import CoqEnvironment

    return CoqEnvironment(config.coq_project_path)


def _load_semantic_index(
    index_dir: Path,
    config: "AgeomConfig",
    *,
    backend_override: str | None = None,
) -> tuple["SemanticIndex", str]:
    """Load the configured semantic index, preferring the global Postgres catalog."""
    backend = str(
        backend_override or getattr(config, "semantic_index_backend", "auto")
    ).strip().lower()
    if backend in {"postgres", "remote", "supabase"}:
        from sciona.provider_runtime import PostgresSemanticIndex

        return PostgresSemanticIndex(api_url=getattr(config, "api_url", None)), "postgres"

    from sciona.indexer.builder import SemanticIndexImpl, build_index_from_manifest_sqlite
    from sciona.indexer.embedder import create_embedder
    from sciona.indexer.faiss_store import FAISSStore
    from sciona.indexer.fallback_index import LexicalSemanticIndex
    from sciona.indexer.unified import CompositeSemanticIndex, SemanticIndexSource

    if backend in {"lexical", "lexical_fallback"}:
        return LexicalSemanticIndex.load(index_dir), "lexical_forced"

    try:
        store = FAISSStore.load(index_dir)
        metadata = store._metadata
        embedder = create_embedder(
            backend=(
                metadata.embedding_backend
                if metadata is not None
                else config.embedding_backend
            ),
            model_name=(
                metadata.embedding_model if metadata is not None else config.embedding_model
            ),
        )
        local_index = SemanticIndexImpl(store, embedder)
        manifest_path = Path.home() / ".sciona" / "manifest.sqlite"
        if not manifest_path.is_file():
            return local_index, "faiss"

        try:
            manifest_store = build_index_from_manifest_sqlite(
                manifest_path,
                embedder=embedder,
            )
            manifest_index = SemanticIndexImpl(manifest_store, embedder)
            embedding_space = f"{embedder.backend}:{embedder.model_name}"
            composite = CompositeSemanticIndex(
                [
                    SemanticIndexSource(local_index, embedding_space, name="local"),
                    SemanticIndexSource(manifest_index, embedding_space, name="manifest"),
                ]
            )
            return composite, "faiss+manifest"
        except Exception as exc:
            print(
                f"Warning: failed to load manifest semantic index: {exc}",
                file=sys.stderr,
            )
            return local_index, "faiss"
    except (ImportError, ModuleNotFoundError) as exc:
        if backend == "faiss":
            raise
        if "faiss" not in str(exc).lower():
            raise
        fallback = LexicalSemanticIndex.load(index_dir)
        return fallback, "lexical_fallback"


def _load_architect_catalog(
    args: argparse.Namespace,
    config: "AgeomConfig",
):
    """Load the architect primitive catalog from built-ins, JSON catalogs, and source registries."""
    from sciona.architect.catalog import (
        CatalogReport,
        PrimitiveCatalog,
        seed_builtin_primitives,
        seed_solution_retrieval_aliases,
    )
    from sciona.architect.hyperparams import (
        get_runtime_signal_event_rate_params,
        load_manifest,
    )
    from sciona.architect.source_catalog import (
        seed_catalog_from_manifest_sqlite,
        seed_catalog_from_sources,
    )
    from sciona.sources import load_sources, resolve_source

    catalog = PrimitiveCatalog()
    seed_builtin_primitives(catalog)

    sources_only = bool(getattr(args, "sources_only", False))

    if getattr(args, "catalog", None):
        catalog = PrimitiveCatalog.load(args.catalog)
        seed_builtin_primitives(catalog)
    elif not sources_only:
        search_dir = config.skill_index_dir
        if search_dir.exists():
            for cat_file in sorted(search_dir.glob("catalog_*.json")):
                print(f"Loading catalog: {cat_file.name}")
                partial = PrimitiveCatalog.load(cat_file)
                for prim in partial.all_primitives():
                    catalog.add(prim)

    report = CatalogReport()
    try:
        sources_cfg = load_sources(config.sources_file)
        derived = seed_catalog_from_sources(
            catalog,
            config=sources_cfg,
            base_dir=Path.cwd(),
            include_live_registries=False,
            report=report,
        )
        if derived or report.merged or report.structural_skips:
            parts = [f"{catalog.size} primitives"]
            if derived:
                parts.append(f"{derived} added")
            if report.merged:
                parts.append(f"{report.merged} merged")
            if report.structural_skips:
                parts.append(f"{report.structural_skips} structural skips")
            parts.append(f"from {report.total_candidates} candidates")
            print(f"Catalog: {', '.join(parts)}")
            source_parts: list[str] = []
            if report.source_live_registry_candidates:
                source_parts.append(
                    f"{report.source_live_registry_candidates} live-registry"
                )
            if report.source_ast_candidates:
                source_parts.append(f"{report.source_ast_candidates} ast-fallback")
            if report.source_cdg_metadata_matches:
                source_parts.append(f"{report.source_cdg_metadata_matches} cdg-matched")
            if report.source_witness_doc_fallbacks:
                source_parts.append(f"{report.source_witness_doc_fallbacks} witness-doc")
            if report.source_witness_signature_fallbacks:
                source_parts.append(
                    f"{report.source_witness_signature_fallbacks} witness-signature"
                )
            if source_parts:
                print(f"Catalog source alignment: {', '.join(source_parts)}")
        manifest_sqlite = Path.home() / ".sciona" / "manifest.sqlite"
        if manifest_sqlite.is_file():
            manifest_added = seed_catalog_from_manifest_sqlite(
                catalog,
                manifest_sqlite,
                skip_locally_installed=True,
                report=report,
            )
            if manifest_added:
                print(f"Catalog manifest: {manifest_added} added from {manifest_sqlite}")
    except Exception as exc:
        print(
            f"Warning: failed to derive primitives from configured sources: {exc}",
            file=sys.stderr,
        )
    seed_solution_retrieval_aliases(catalog)

    tunables_map: dict[str, list[Any]] = {}
    try:
        sources_cfg = load_sources(config.sources_file)
        for source in sources_cfg.sources:
            source_root = resolve_source(source, Path.cwd())
            manifest_path = source_root / "data" / "hyperparams" / "manifest.json"
            if manifest_path.is_file():
                tunables_map.update(load_manifest(manifest_path))
        tunables_map.update(get_runtime_signal_event_rate_params())
        attached = catalog.attach_tunables(tunables_map)
        if attached:
            print(f"Catalog tunables: attached to {attached} primitives")
    except Exception as exc:
        print(
            f"Warning: failed to attach primitive tunables: {exc}",
            file=sys.stderr,
        )

    report_payload = {
        "catalog_size": catalog.size,
        "total_candidates": report.total_candidates,
        "added": report.added,
        "merged": report.merged,
        "structural_skips": report.structural_skips,
        "source_live_registry_candidates": report.source_live_registry_candidates,
        "source_ast_candidates": report.source_ast_candidates,
        "source_cdg_metadata_matches": report.source_cdg_metadata_matches,
        "source_witness_doc_fallbacks": report.source_witness_doc_fallbacks,
        "source_witness_signature_fallbacks": report.source_witness_signature_fallbacks,
        "source_breakdown": report.source_breakdown,
        "merge_details": [
            {
                "candidate": candidate,
                "incumbent": incumbent,
                "similarity": similarity,
            }
            for candidate, incumbent, similarity in report.merge_details
        ],
    }
    return catalog, report_payload


def _load_skill_index_or_empty(
    config: "AgeomConfig",
    *,
    enabled: bool = True,
):
    """Load the persisted skill index unless explicitly disabled."""
    from sciona.architect.embedder import SkillIndex

    skill_index = SkillIndex(
        index_dir=config.skill_index_dir,
        embedding_backend=getattr(config, "embedding_backend", "fastembed"),
        embedding_model=getattr(config, "embedding_model", "BAAI/bge-small-en-v1.5"),
    )
    if not enabled:
        print("Warning: skill index disabled by execution mode.", file=sys.stderr)
        return skill_index
    if os.environ.get("SCIONA_DISABLE_SKILL_INDEX", "").strip() in {"1", "true", "yes"}:
        print("Warning: skill index disabled via SCIONA_DISABLE_SKILL_INDEX.", file=sys.stderr)
        return skill_index

    if config.skill_index_dir.exists():
        try:
            return SkillIndex.load(config.skill_index_dir)
        except Exception as exc:
            print(f"Warning: failed to load skill index: {exc}", file=sys.stderr)
    return skill_index


async def _shutdown_telemetry_drain(drain: Any, store: Any) -> None:
    """Gracefully stop Postgres telemetry drain and close the store."""
    if drain is not None:
        try:
            await drain.stop()
        except Exception:
            pass
    if store is not None:
        try:
            await store.close()
        except Exception:
            pass


def _run_async_command(coro: Any) -> None:
    """Run a CLI coroutine and close it if a mocked asyncio.run leaves it pending."""
    try:
        asyncio.run(coro)
    finally:
        if inspect.iscoroutine(coro) and getattr(coro, "cr_frame", None) is not None:
            coro.close()
