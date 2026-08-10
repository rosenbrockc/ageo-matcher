"""Deterministic human-guided proposal generation for visual graph branches."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from sciona.architect.handoff import CDGExport
from sciona.evolution import graph_diff
from sciona.principal.expansion import ExpansionEngine
from sciona.principal.expansion_rules import default_rule_sets
from sciona.principal.variant_mutation import maybe_apply_bottleneck_variant


_STOP_WORDS = {
    "and", "for", "from", "into", "not", "that", "the", "this", "with",
    "graph", "node", "refine", "refinement", "version",
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value).lower())
        if len(token) >= 3 and token not in _STOP_WORDS
    }


@dataclass
class GuidedCandidate:
    operation: str
    graph: CDGExport
    description: str
    rules_applied: list[str] = field(default_factory=list)
    applied_assets: list[dict[str, Any]] = field(default_factory=list)
    target_node_id: str = ""
    base_priority: float = 0.0
    guidance_overlap: float = 0.0

    def summary(self, baseline: CDGExport) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "description": self.description,
            "rules_applied": list(self.rules_applied),
            "applied_assets": list(self.applied_assets),
            "target_node_id": self.target_node_id,
            "guidance_overlap": self.guidance_overlap,
            "graph_diff": graph_diff(
                baseline.model_dump(mode="json"),
                self.graph.model_dump(mode="json"),
            ),
        }


def _candidate_score(candidate: GuidedCandidate, guidance_tokens: set[str]) -> float:
    candidate_tokens = _tokens(
        " ".join(
            [candidate.operation, candidate.description, *candidate.rules_applied]
        )
    )
    overlap = len(guidance_tokens & candidate_tokens) / max(1, len(guidance_tokens))
    candidate.guidance_overlap = overlap
    return overlap * 10.0 + candidate.base_priority


def _ordered_targets(cdg: CDGExport, selected_node_id: str) -> list[Any]:
    selected = [node for node in cdg.nodes if node.node_id == selected_node_id]
    remaining = [node for node in cdg.nodes if node.node_id != selected_node_id]
    return selected + remaining


def propose_guided_refinement(
    cdg: CDGExport,
    *,
    guidance: str,
    selected_node_id: str = "",
    expansion_rule_names: Iterable[str] = (),
) -> dict[str, Any]:
    """Choose one deterministic Principal proposal from a selected graph version."""
    candidates: list[GuidedCandidate] = []
    seen_graphs: set[str] = set()

    def add(candidate: GuidedCandidate) -> None:
        fingerprint = candidate.graph.model_dump_json()
        if fingerprint == cdg.model_dump_json() or fingerprint in seen_graphs:
            return
        seen_graphs.add(fingerprint)
        candidates.append(candidate)

    for index, node in enumerate(_ordered_targets(cdg, selected_node_id)):
        mutation = maybe_apply_bottleneck_variant(cdg, bottleneck_name=node.name)
        if not mutation.applied:
            continue
        add(
            GuidedCandidate(
                operation="local_mutation",
                graph=mutation.cdg,
                description=(
                    f"Replace {node.name} with curated variant "
                    f"{mutation.variant_name or 'candidate'}"
                ),
                target_node_id=node.node_id,
                base_priority=3.0 if index == 0 and selected_node_id else 2.0,
            )
        )

    engine = ExpansionEngine(default_rule_sets())
    for index, rule_name in enumerate(dict.fromkeys(expansion_rule_names)):
        rule = engine._rule_index.get(str(rule_name))
        if rule is None:
            continue
        result = engine._rewriter.apply_rule(rule, cdg)
        if result.is_failure:
            continue
        add(
            GuidedCandidate(
                operation="expansion",
                graph=result.unwrap(),
                description=f"Apply deterministic expansion rule {rule_name}",
                rules_applied=[str(rule_name)],
                base_priority=max(0.0, 1.5 - index * 0.01),
            )
        )

    if not candidates:
        raise LookupError(
            "No deterministic refinement candidate applies to the selected branch point. "
            "Select a more specific node or revise the guidance."
        )

    guidance_tokens = _tokens(guidance)
    candidates.sort(
        key=lambda candidate: (
            -_candidate_score(candidate, guidance_tokens),
            candidate.operation,
            candidate.description,
        )
    )
    selected = candidates[0]
    summaries = [candidate.summary(cdg) for candidate in candidates]
    return {
        "status": "proposed",
        "operation": selected.operation,
        "updated_cdg": selected.graph.model_dump(mode="json"),
        "rules_applied": list(selected.rules_applied),
        "applied_assets": list(selected.applied_assets),
        "selection_reason": (
            "Selected by guidance overlap and deterministic Principal proposal priority."
        ),
        "graph_diff": summaries[0]["graph_diff"],
        "candidates": summaries,
    }
