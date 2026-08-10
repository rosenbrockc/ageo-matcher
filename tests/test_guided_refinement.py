from __future__ import annotations

from sciona.architect.handoff import CDGExport
from sciona.architect.models import AlgorithmicNode, ConceptType, NodeStatus
from sciona.principal.guided_refinement import propose_guided_refinement


def test_guided_refinement_uses_selected_fully_qualified_variant() -> None:
    prefix = "sciona.atoms.signal_processing.biosppy.ecg"
    cdg = CDGExport(
        nodes=[
            AlgorithmicNode(
                node_id="measure",
                name="Measure Event Rate",
                description="Convert recurring event intervals into rates.",
                concept_type=ConceptType.SIGNAL_TRANSFORM,
                status=NodeStatus.ATOMIC,
                matched_primitive=f"{prefix}.heart_rate_computation",
            )
        ],
        edges=[],
    )

    result = propose_guided_refinement(
        cdg,
        guidance="Use the more robust median-smoothed rate estimate",
        selected_node_id="measure",
    )

    assert result["status"] == "proposed"
    assert result["operation"] == "local_mutation"
    assert result["updated_cdg"]["nodes"][0]["matched_primitive"] == (
        f"{prefix}.heart_rate_computation_median_smoothed"
    )
    assert result["graph_diff"]["changed_nodes"]
