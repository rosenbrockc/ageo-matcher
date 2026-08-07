"""Adapters that expose local skeleton families as macro CDG artifacts."""

from __future__ import annotations

import hashlib

from sciona.architect.handoff import CDGExport
from sciona.architect.models import AlgorithmicNode, NodeStatus
from sciona.architect.skeleton_assets import (
    SkeletonFamilyAsset,
    load_local_skeleton_assets,
    load_provider_cdg_assets,
    skeleton_asset_content_json,
    skeleton_asset_summary,
)
from sciona.services.artifact_retrieval import MacroArtifactRetriever
from sciona.services.models import MacroArtifactCandidate


def _asset_content_hash(asset: SkeletonFamilyAsset) -> str:
    payload = skeleton_asset_content_json(asset)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _asset_fqdn(asset: SkeletonFamilyAsset) -> str:
    return asset.fqdn or f"cdg.skeleton.{asset.asset_id}"


def _asset_candidate_score(asset: SkeletonFamilyAsset) -> float:
    if asset.asset_id == asset.family:
        return 1.0
    if asset.canonical_for_paradigm:
        return 0.85
    return 0.9


def build_skeleton_asset_cdg(
    asset: SkeletonFamilyAsset,
    *,
    goal: str = "",
) -> CDGExport:
    """Materialize a local skeleton-family asset into a CDG artifact."""
    graph = asset.to_skeleton_graph()
    goal_prefix = f"[{goal}] " if str(goal).strip() else ""
    nodes: list[AlgorithmicNode] = []
    for stage, node in zip(asset.stages, graph.template_nodes, strict=False):
        nodes.append(
            node.model_copy(
                deep=True,
                update={
                    "status": NodeStatus.ATOMIC,
                    "description": f"{goal_prefix}{node.description}",
                    "conceptual_summary": (
                        stage.dejargonized_description
                        or asset.dejargonized_summary
                        or asset.audit.dejargonized_summary
                        or asset.summary
                    ),
                },
            )
        )
    edges = [edge.model_copy(deep=True) for edge in graph.template_edges]
    return CDGExport(
        nodes=nodes,
        edges=edges,
        metadata={
            "goal": goal,
            "artifact_kind": "cdg",
            "artifact_fqdn": _asset_fqdn(asset),
            "artifact_semver": asset.asset_version,
            "artifact_content_hash": _asset_content_hash(asset),
            "artifact_source": "local_skeleton_asset",
            "macro_direct_path": True,
            "selected_via_macro_retrieval": True,
            "skeleton_asset": skeleton_asset_summary(asset),
            "num_nodes": len(nodes),
            "num_edges": len(edges),
        },
    )


def load_local_skeleton_macro_candidates() -> list[MacroArtifactCandidate]:
    """Build deterministic macro candidates from local and provider CDG assets."""
    candidates: list[MacroArtifactCandidate] = []
    for asset in (*load_provider_cdg_assets(), *load_local_skeleton_assets()):
        domain_tags = [
            asset.paradigm.value,
            asset.family,
            asset.asset_id,
            *asset.variant_hints,
            *asset.domain_tags,
        ]
        candidates.append(
            MacroArtifactCandidate(
                fqdn=_asset_fqdn(asset),
                semver=asset.asset_version,
                content_hash=_asset_content_hash(asset),
                artifact_kind="cdg",
                name=asset.name,
                description=asset.summary,
                conceptual_summary=(
                    asset.dejargonized_summary
                    or asset.audit.dejargonized_summary
                    or asset.summary
                ),
                domain_tags=domain_tags,
                required_context_tags=list(asset.required_context_tags),
                verified_leaf_coverage=0.0,
                score=_asset_candidate_score(asset),
                visibility_tier="general",
                cdg=build_skeleton_asset_cdg(asset),
                terminal_on_match=False,
            )
        )
    candidates.sort(
        key=lambda candidate: (
            str(candidate.fqdn),
            str(candidate.semver),
            str(candidate.content_hash),
        )
    )
    return candidates


def build_local_skeleton_macro_retriever(
    *,
    min_score: float = 0.55,
) -> MacroArtifactRetriever:
    """Expose local skeleton families via the macro retriever surface."""
    return MacroArtifactRetriever(
        load_local_skeleton_macro_candidates(),
        min_score=min_score,
    )
