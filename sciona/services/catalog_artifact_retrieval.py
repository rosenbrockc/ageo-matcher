"""Catalog-backed macro artifact retrieval with local skeleton fallback."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from sciona.architect.handoff import CDGExport
from sciona.architect.models import AlgorithmicNode, DependencyEdge
from sciona.services.artifact_retrieval import MacroArtifactRetriever
from sciona.services.models import (
    MacroArtifactCandidate,
    MacroMatchRequest,
    MacroMatchResult,
)
from sciona.services.skeleton_artifacts import build_local_skeleton_macro_retriever

log = logging.getLogger(__name__)

_REQUIRED_CONTEXT_PATTERN = re.compile(r"\[requires-context:([^\]]+)\]", re.I)


def _required_context_tags(*texts: str) -> list[str]:
    tags: set[str] = set()
    for text in texts:
        for match in _REQUIRED_CONTEXT_PATTERN.findall(str(text or "")):
            tags.update(
                token.strip().lower()
                for token in match.split(",")
                if token.strip()
            )
    return sorted(tags)


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _artifact_domain_tags(row: dict[str, Any], document: dict[str, Any]) -> list[str]:
    artifact = dict(document.get("artifact") or {})
    tags = list(row.get("domain_tags") or [])
    for value in (
        artifact.get("artifact_kind"),
        artifact.get("namespace_root"),
        artifact.get("namespace_path"),
        artifact.get("source_package"),
        artifact.get("source_symbol"),
    ):
        text = str(value or "").strip()
        if text and text not in tags:
            tags.append(text)
    return tags


def _goal_matches_artifact_row(goal: str, row: dict[str, Any]) -> bool:
    goal_tokens = {
        token
        for token in str(goal or "").lower().replace("_", " ").split()
        if token
    }
    if not goal_tokens:
        return False
    haystack = " ".join(
        str(value or "")
        for value in (
            row.get("fqdn"),
            row.get("description"),
            row.get("namespace_root"),
            row.get("namespace_path"),
            row.get("source_symbol"),
        )
    ).lower().replace("_", " ")
    return bool(goal_tokens & {token for token in haystack.split() if token})


def _artifact_document_to_cdg(document: dict[str, Any]) -> CDGExport:
    artifact = dict(document.get("artifact") or {})
    nodes = [
        AlgorithmicNode.model_validate(
            {
                "node_id": row.get("node_id", ""),
                "parent_id": row.get("parent_node_id") or None,
                "name": row.get("name", ""),
                "description": row.get("description", ""),
                "concept_type": row.get("concept_type", "custom"),
                "status": row.get("status", "atomic"),
                "type_signature": row.get("type_signature", ""),
                "matched_primitive": row.get("matched_primitive") or None,
            }
        )
        for row in (document.get("cdg_nodes") or [])
    ]
    edges = [
        DependencyEdge.model_validate(
            {
                "source_id": row.get("source_id", ""),
                "target_id": row.get("target_id", ""),
                "output_name": row.get("output_name", ""),
                "input_name": row.get("input_name", ""),
                "source_type": row.get("source_type", ""),
                "target_type": row.get("target_type", ""),
            }
        )
        for row in (document.get("cdg_edges") or [])
    ]
    return CDGExport(
        nodes=nodes,
        edges=edges,
        metadata={
            "artifact_kind": str(artifact.get("artifact_kind", "cdg") or "cdg"),
            "artifact_fqdn": str(artifact.get("fqdn", "") or ""),
            "artifact_source": "supabase_catalog",
            "num_nodes": len(nodes),
            "num_edges": len(edges),
        },
    )


class CatalogMacroArtifactRetriever:
    """Macro retriever that reads CDG artifacts from the unified catalog."""

    def __init__(
        self,
        supabase: Any,
        *,
        fallback: Any | None = None,
        min_score: float = 0.55,
        result_limit: int = 10,
    ) -> None:
        self._supabase = supabase
        self._fallback = fallback
        self._min_score = min_score
        self._result_limit = result_limit

    async def _search_rows(self, goal: str) -> list[dict[str, Any]]:
        try:
            result = await self._supabase.rpc(
                "search_artifacts_hybrid",
                {
                    "query_text": goal,
                    "mode": "fts",
                    "result_limit": self._result_limit,
                    "result_offset": 0,
                },
            ).execute()
            rows = list(result.data or [])
            if rows:
                return rows
        except Exception:
            pass
        try:
            filtered = await (
                self._supabase.table("catalog_artifacts_served")
                .select(
                    "artifact_id, artifact_kind, fqdn, technical_description, domain_tags, overall_verdict, risk_tier, trust_readiness"
                )
                .or_(f"fqdn.ilike.%{goal}%,technical_description.ilike.%{goal}%")
                .limit(self._result_limit)
                .execute()
            )
            rows = list(filtered.data or [])
            if rows:
                return rows
            raw = await (
                self._supabase.table("artifacts")
                .select(
                    "artifact_id, artifact_kind, fqdn, description, namespace_root, namespace_path, source_symbol, verified_leaf_coverage, visibility_tier, is_publishable"
                )
                .eq("artifact_kind", "cdg")
                .limit(max(self._result_limit, 50))
                .execute()
            )
            raw_rows = [
                row
                for row in list(raw.data or [])
                if _goal_matches_artifact_row(goal, row)
            ]
            if raw_rows:
                return raw_rows[: self._result_limit]
            result = await (
                self._supabase.table("catalog_artifacts_served")
                .select(
                    "artifact_id, artifact_kind, fqdn, technical_description, domain_tags, overall_verdict, risk_tier, trust_readiness"
                )
                .eq("artifact_kind", "cdg")
                .limit(max(self._result_limit, 50))
                .execute()
            )
            return list(result.data or [])
        except Exception:
            log.exception("Catalog macro retrieval fallback search failed")
            return []

    async def _latest_version(self, artifact_id: str) -> dict[str, Any]:
        try:
            result = await (
                self._supabase.table("artifact_versions")
                .select("version_id, semver, content_hash")
                .eq("artifact_id", artifact_id)
                .eq("is_latest", True)
                .maybe_single()
                .execute()
            )
            if isinstance(result.data, dict):
                return dict(result.data)
        except Exception:
            log.exception("Failed to fetch latest artifact version for %s", artifact_id)
        return {}

    async def _document(self, fqdn: str) -> dict[str, Any] | None:
        try:
            result = await self._supabase.rpc(
                "get_artifact_document",
                {"request_fqdn": fqdn},
            ).execute()
            if isinstance(result.data, dict):
                return dict(result.data)
        except Exception:
            log.exception("Failed to fetch artifact document for %s", fqdn)
        return None

    def _candidate_from_document(
        self,
        *,
        row: dict[str, Any],
        document: dict[str, Any],
        version: dict[str, Any],
    ) -> MacroArtifactCandidate:
        artifact = dict(document.get("artifact") or {})
        description = str(
            row.get("technical_description", "")
            or row.get("description", "")
            or artifact.get("description", "")
            or ""
        )
        conceptual_summary = str(
            next(
                (
                    entry.get("content", "")
                    for entry in (document.get("descriptions") or [])
                    if entry.get("kind") == "dejargonized"
                ),
                artifact.get("description", ""),
            )
            or ""
        )
        return MacroArtifactCandidate(
            fqdn=str(row.get("fqdn", "") or ""),
            semver=str(version.get("semver", "") or ""),
            content_hash=str(version.get("content_hash", "") or ""),
            artifact_kind="cdg",
            name=str(
                artifact.get("source_symbol", "")
                or str(row.get("fqdn", "")).rsplit(".", 1)[-1]
            ),
            description=description,
            conceptual_summary=conceptual_summary,
            domain_tags=_artifact_domain_tags(row, document),
            required_context_tags=_required_context_tags(
                description,
                conceptual_summary,
            ),
            verified_leaf_coverage=float(
                artifact.get("verified_leaf_coverage", row.get("verified_leaf_coverage", 0.0))
                or 0.0
            ),
            score=float(
                row.get("score", 0.2 if not bool(row.get("is_publishable", False)) else 0.0)
                or 0.0
            ),
            visibility_tier=str(
                artifact.get("visibility_tier", row.get("visibility_tier", "general"))
                or "general"
            ),
            cdg=_artifact_document_to_cdg(document),
            terminal_on_match=False,
        )

    async def _catalog_candidates(self, goal: str) -> list[MacroArtifactCandidate]:
        rows = await self._search_rows(goal)
        candidates: list[MacroArtifactCandidate] = []
        for row in rows:
            if str(row.get("artifact_kind", "") or "") != "cdg":
                continue
            fqdn = str(row.get("fqdn", "") or "").strip()
            artifact_id = str(row.get("artifact_id", "") or "").strip()
            if not fqdn:
                continue
            document = await self._document(fqdn)
            if not document:
                continue
            version = await self._latest_version(artifact_id)
            candidates.append(
                self._candidate_from_document(
                    row=row,
                    document=document,
                    version=version,
                )
            )
        return candidates

    async def match_goal(self, request: MacroMatchRequest) -> MacroMatchResult:
        candidates = await self._catalog_candidates(request.goal)
        if candidates:
            result = await MacroArtifactRetriever(
                candidates,
                min_score=self._min_score,
            ).match_goal(request)
            if result.success:
                return result
        if self._fallback is not None:
            return await self._fallback.match_goal(request)
        return MacroMatchResult(
            success=False,
            ranked_candidates=[],
            rejection_reason="no_catalog_macro_candidates",
        )


async def build_default_macro_retriever(
    *,
    min_score: float = 0.55,
    result_limit: int = 10,
) -> Any:
    """Build the runtime macro retriever, preferring the unified catalog."""
    fallback = build_local_skeleton_macro_retriever(min_score=min_score)
    url = _first_env("SCIONA_SUPABASE_URL", "SUPABASE_URL")
    key = _first_env(
        "SCIONA_SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SCIONA_SUPABASE_SERVICE_KEY",
        "SUPABASE_SERVICE_KEY",
    )
    if not (url and key):
        return fallback
    try:
        from supabase import acreate_client
    except ImportError:
        return fallback
    try:
        supabase = await acreate_client(url, key)
    except Exception:
        log.exception("Failed to create Supabase client for catalog macro retrieval")
        return fallback
    return CatalogMacroArtifactRetriever(
        supabase,
        fallback=fallback,
        min_score=min_score,
        result_limit=result_limit,
    )
