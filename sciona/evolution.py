"""Domain-neutral graph evolution traces for Principal runs and visualizers."""

from __future__ import annotations

from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _node_map(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node.get("node_id", "")): node
        for node in _list(graph.get("nodes"))
        if isinstance(node, dict) and node.get("node_id")
    }


def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(edge.get("source_id", "")),
        str(edge.get("target_id", "")),
        str(edge.get("output_name", "")),
        str(edge.get("input_name", "")),
    )


def graph_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, stable structural diff between two CDG snapshots."""
    before_nodes = _node_map(before)
    after_nodes = _node_map(after)
    before_ids = set(before_nodes)
    after_ids = set(after_nodes)
    changed_nodes: list[dict[str, Any]] = []
    for node_id in sorted(before_ids & after_ids):
        old = before_nodes[node_id]
        new = after_nodes[node_id]
        changes = {}
        for field in ("name", "status", "concept_type", "matched_primitive", "type_signature"):
            if old.get(field) != new.get(field):
                changes[field] = {"before": old.get(field), "after": new.get(field)}
        if changes:
            changed_nodes.append({"node_id": node_id, "changes": changes})

    before_edges = {
        _edge_key(edge) for edge in _list(before.get("edges")) if isinstance(edge, dict)
    }
    after_edges = {
        _edge_key(edge) for edge in _list(after.get("edges")) if isinstance(edge, dict)
    }
    return {
        "added_nodes": [after_nodes[node_id] for node_id in sorted(after_ids - before_ids)],
        "removed_nodes": [before_nodes[node_id] for node_id in sorted(before_ids - after_ids)],
        "changed_nodes": changed_nodes,
        "added_edges": [list(edge) for edge in sorted(after_edges - before_edges)],
        "removed_edges": [list(edge) for edge in sorted(before_edges - after_edges)],
    }


def build_evolution_trace(
    history: list[dict[str, Any]],
    *,
    goal: str = "",
    objective: str = "",
) -> dict[str, Any]:
    """Shape Principal history as graph versions connected by evaluated operations."""
    versions: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        graph = _dict(entry.get("cdg_snapshot"))
        if not graph:
            continue
        trial = int(entry.get("trial", len(versions) + 1) or len(versions) + 1)
        version_id = f"trial-{trial}"
        versions.append(
            {
                "version_id": version_id,
                "label": "Initial match" if not versions else f"Trial {trial}",
                "phase": "initial_match" if not versions else "refinement",
                "trial": trial,
                "loss": float(entry.get("loss", 0.0) or 0.0),
                "graph": graph,
                "structure": _dict(entry.get("structure")),
                "runtime_evidence": _dict(entry.get("runtime_evidence")),
                "admissibility": _dict(entry.get("admissibility")),
                "parameter_assignments": _dict(entry.get("parameter_assignments")),
            }
        )
        entries.append(entry)

    transitions: list[dict[str, Any]] = []
    for index in range(len(versions) - 1):
        source = versions[index]
        target = versions[index + 1]
        source_entry = entries[index]
        proposal = _dict(source_entry.get("proposal_selection"))
        selected_label = str(proposal.get("selected", "") or "")
        selected = next(
            (
                row
                for row in _list(proposal.get("candidates"))
                if isinstance(row, dict) and str(row.get("label", "")) == selected_label
            ),
            {},
        )
        transitions.append(
            {
                "transition_id": f"{source['version_id']}--{target['version_id']}",
                "source_version_id": source["version_id"],
                "target_version_id": target["version_id"],
                "operation": selected_label or "parameter_refinement",
                "status": "accepted",
                "baseline_loss": source["loss"],
                "candidate_loss": target["loss"],
                "loss_delta": target["loss"] - source["loss"],
                "rules_applied": _list(selected.get("rules_applied")),
                "applied_assets": _list(selected.get("applied_assets")),
                "selection_reason": str(selected.get("selection_reason", "") or ""),
                "admissibility": _dict(selected.get("admissibility")),
                "structural_delta": _dict(selected.get("structural_delta")),
                "graph_diff": graph_diff(source["graph"], target["graph"]),
                "candidates": _list(proposal.get("candidates")),
            }
        )

    return {
        "schema_version": "1.0",
        "goal": goal,
        "objective": objective,
        "versions": versions,
        "transitions": transitions,
    }
