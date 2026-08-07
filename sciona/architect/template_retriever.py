"""Unified template retriever: coarse Memgraph candidates → GraphAlignmentScorer rerank.

Replaces the weak topo_hash + Jaccard cascade with scored structural alignment
for finding reusable decomposition templates.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sciona.architect.graph_alignment import AlignmentScore, GraphAlignmentScorer
from sciona.architect.graph_retrieval import (
    CDGSubgraphRetriever,
    ExampleDecomposition,
    _parse_record,
)

if TYPE_CHECKING:
    from sciona.architect.models import AlgorithmicNode, DependencyEdge
    from sciona.graph_store import GraphStore

logger = logging.getLogger(__name__)


@dataclass
class TemplateMatch:
    """A scored template match from the retriever."""

    example: ExampleDecomposition
    alignment: AlignmentScore
    confidence: float
    source: str  # which retrieval layer produced the candidate


class TemplateRetriever:
    """Orchestrates candidate generation, alignment scoring, and confidence thresholds.

    Flow: Memgraph fetches coarse candidates (existing 3-layer cascade) →
    GraphAlignmentScorer reranks → return top results above confidence threshold.
    """

    def __init__(
        self,
        store: GraphStore | None,
        scorer: GraphAlignmentScorer,
        *,
        confidence_threshold: float = 0.6,
        max_candidates: int = 50,
        max_results: int = 3,
        exclude_repo: str = "",
        timeout_ms: int = 2000,
    ) -> None:
        self._store = store
        self._scorer = scorer
        self._confidence_threshold = confidence_threshold
        self._max_candidates = max_candidates
        self._max_results = max_results
        self._exclude_repo = exclude_repo
        self._timeout_ms = timeout_ms
        # Create the underlying CDG retriever for coarse candidates
        self._retriever: CDGSubgraphRetriever | None = None
        if store is not None:
            self._retriever = CDGSubgraphRetriever(
                store=store,
                timeout_ms=timeout_ms,
                max_examples=max_candidates,
                min_children=2,
                exclude_repo=exclude_repo,
            )

    async def find_templates(
        self,
        node: AlgorithmicNode,
        all_nodes: list[AlgorithmicNode],
        all_edges: list[DependencyEdge],
    ) -> list[TemplateMatch]:
        """Find the best-matching templates for a decomposition query.

        Returns up to max_results matches above confidence_threshold,
        sorted by confidence descending.
        """
        if self._retriever is None:
            return []

        try:
            timeout_s = self._timeout_ms / 1000.0
            return await asyncio.wait_for(
                self._run(node, all_nodes, all_edges),
                timeout=timeout_s,
            )
        except Exception:
            logger.debug("template_retriever: failed or timed out", exc_info=True)
            return []

    async def _run(
        self,
        node: AlgorithmicNode,
        all_nodes: list[AlgorithmicNode],
        all_edges: list[DependencyEdge],
    ) -> list[TemplateMatch]:
        assert self._retriever is not None

        # Step 1: Get coarse candidates from the 3-layer cascade
        candidates = await self._retriever.find_similar(node, all_nodes, all_edges)
        if not candidates:
            return []

        # Step 2: Collect query children and edges
        query_children = [n for n in all_nodes if n.parent_id == node.node_id]
        query_edges = [
            e for e in all_edges
            if e.source_id in {c.node_id for c in query_children}
            and e.target_id in {c.node_id for c in query_children}
        ]

        # Step 3: Score each candidate with GraphAlignmentScorer
        matches: list[TemplateMatch] = []
        for candidate in candidates:
            alignment = self._scorer.score(
                node, query_children, query_edges, candidate
            )
            confidence = alignment.total
            source = f"layer_{candidate.retrieval_layer}"
            if confidence >= self._confidence_threshold:
                matches.append(
                    TemplateMatch(
                        example=candidate,
                        alignment=alignment,
                        confidence=confidence,
                        source=source,
                    )
                )

        # Step 4: Sort by confidence and return top N
        matches.sort(key=lambda m: m.confidence, reverse=True)
        return matches[: self._max_results]

    async def find_refinement_templates(
        self,
        failed_node: AlgorithmicNode,
        failure_context: dict[str, Any],
    ) -> list[TemplateMatch]:
        """Search for previously resolved nodes similar to a failed one.

        Uses the same retriever but looks for candidates with
        verified_leaf_coverage > 0 or matched_primitive set.
        """
        if self._store is None:
            return []

        try:
            concept_type = failed_node.concept_type.value
            query_templates = getattr(self._store, "query_verified_exemplar_templates", None)
            if inspect.iscoroutinefunction(query_templates):
                same_family_records = await query_templates(
                    concept_type=concept_type,
                    n_inputs=len(failed_node.inputs),
                    n_outputs=len(failed_node.outputs),
                    min_coverage=0.5,
                    limit=10,
                )
                cross_family_records = await query_templates(
                    concept_type="",
                    n_inputs=len(failed_node.inputs),
                    n_outputs=len(failed_node.outputs),
                    min_coverage=0.5,
                    limit=10,
                )
            else:
                same_family_records = await self._store.query_verified_exemplars(
                    concept_type=concept_type,
                    n_inputs=len(failed_node.inputs),
                    n_outputs=len(failed_node.outputs),
                    min_coverage=0.5,
                    limit=10,
                )
                cross_family_records = await self._store.query_verified_exemplars(
                    concept_type="",
                    n_inputs=len(failed_node.inputs),
                    n_outputs=len(failed_node.outputs),
                    min_coverage=0.5,
                    limit=10,
                )

            merged_records: list[dict[str, Any]] = []
            seen_fqns: set[str] = set()
            for record in same_family_records + cross_family_records:
                if not isinstance(record, dict):
                    continue
                fqn = str(record.get("fqn", "") or "")
                if not fqn or fqn in seen_fqns:
                    continue
                seen_fqns.add(fqn)
                merged_records.append(record)
            if not merged_records:
                return []

            matches: list[TemplateMatch] = []
            description_tokens = set(
                str(
                    failure_context.get("description")
                    or failure_context.get("statement")
                    or ""
                ).lower().split()
            )
            for rec in merged_records:
                coverage = float(rec.get("verified_leaf_coverage", 0.0))
                candidate_concept = str(rec.get("concept_type", "") or "")
                candidate_n_inputs = int(rec.get("n_inputs", 0) or 0)
                candidate_n_outputs = int(rec.get("n_outputs", 0) or 0)
                concept_match = 1.0 if candidate_concept == concept_type else 0.0
                io_delta = abs(candidate_n_inputs - len(failed_node.inputs)) + abs(
                    candidate_n_outputs - len(failed_node.outputs)
                )
                io_match = max(0.0, 1.0 - 0.15 * io_delta)
                rec_text = " ".join(
                    str(part or "")
                    for part in [rec.get("name"), rec.get("description"), rec.get("fqn")]
                ).lower()
                rec_tokens = set(rec_text.split())
                context_overlap = (
                    len(description_tokens & rec_tokens) / max(1, len(description_tokens))
                    if description_tokens
                    else 0.0
                )
                confidence = min(
                    0.98,
                    coverage * (0.60 + 0.20 * io_match + 0.10 * concept_match + 0.10 * context_overlap),
                )
                if confidence >= self._confidence_threshold:
                    if rec.get("children") is not None:
                        example = _parse_record(rec, layer=0, score=coverage)
                        if not example.concept_type:
                            example.concept_type = candidate_concept or concept_type
                    else:
                        example = ExampleDecomposition(
                            fqn=rec.get("fqn", ""),
                            name="",
                            description="",
                            concept_type=candidate_concept or concept_type,
                            repo=rec.get("repo", ""),
                            topo_hash=rec.get("topo_hash", ""),
                            children=[],
                            edges=[],
                            retrieval_layer=0,
                            score=coverage,
                            verified_leaf_coverage=coverage,
                            provenance_kind="verified_exemplar",
                        )
                    alignment = AlignmentScore(
                        total=confidence,
                        concept_type_match=concept_match,
                        io_arity_match=io_match,
                        child_concept_overlap=0.0,
                        topo_match=0.0,
                        type_class_match=0.0,
                        witness_type_match=0.0,
                    )
                    matches.append(
                        TemplateMatch(
                            example=example,
                            alignment=alignment,
                            confidence=confidence,
                            source=(
                                "verified_exemplar_same_family"
                                if concept_match >= 1.0
                                else "verified_exemplar_cross_family"
                            ),
                        )
                    )

            matches.sort(key=lambda m: m.confidence, reverse=True)
            return matches[: self._max_results]
        except Exception:
            logger.debug("template_retriever: refinement search failed", exc_info=True)
            return []
