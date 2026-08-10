from __future__ import annotations

from sciona.evolution import build_evolution_trace, graph_diff


def _graph(*node_ids: str) -> dict:
    return {
        "nodes": [
            {
                "node_id": node_id,
                "name": node_id,
                "status": "atomic",
                "concept_type": "custom",
                "matched_primitive": f"provider.{node_id}",
            }
            for node_id in node_ids
        ],
        "edges": [],
        "metadata": {"goal": "domain-neutral fixture"},
    }


def test_graph_diff_reports_structural_and_primitive_changes() -> None:
    before = _graph("prepare", "solve")
    after = _graph("prepare", "solve", "validate")
    after["nodes"][1]["matched_primitive"] = "provider.solve_v2"

    diff = graph_diff(before, after)

    assert [node["node_id"] for node in diff["added_nodes"]] == ["validate"]
    assert diff["removed_nodes"] == []
    assert diff["changed_nodes"] == [
        {
            "node_id": "solve",
            "changes": {
                "matched_primitive": {
                    "before": "provider.solve",
                    "after": "provider.solve_v2",
                }
            },
        }
    ]


def test_build_evolution_trace_connects_selected_proposal_to_next_trial() -> None:
    initial = _graph("prepare", "solve")
    refined = _graph("prepare", "solve", "validate")
    history = [
        {
            "trial": 1,
            "loss": 3.0,
            "cdg_snapshot": initial,
            "proposal_selection": {
                "selected": "expansion",
                "candidates": [
                    {
                        "label": "expansion",
                        "loss": 1.5,
                        "rules_applied": ["insert_validation"],
                    }
                ],
            },
        },
        {"trial": 2, "loss": 1.5, "cdg_snapshot": refined},
    ]

    trace = build_evolution_trace(history, goal="Solve a problem", objective="error")

    assert [version["label"] for version in trace["versions"]] == [
        "Initial match",
        "Trial 2",
    ]
    transition = trace["transitions"][0]
    assert transition["operation"] == "expansion"
    assert transition["loss_delta"] == -1.5
    assert transition["rules_applied"] == ["insert_validation"]
    assert [node["node_id"] for node in transition["graph_diff"]["added_nodes"]] == [
        "validate"
    ]
