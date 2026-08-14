from __future__ import annotations

import pytest

from sciona.architect.composition import CompositionError, compose_cdg
from sciona.architect.handoff import CDGExport
from sciona.architect.models import (
    AlgorithmicNode,
    ConceptType,
    DependencyEdge,
    IOSpec,
    NodeStatus,
)


def _node(node_id: str, inputs: list[tuple[str, str]], outputs: list[tuple[str, str]]):
    return AlgorithmicNode(
        node_id=node_id,
        name=node_id,
        description=node_id,
        concept_type=ConceptType.CUSTOM,
        status=NodeStatus.ATOMIC,
        matched_primitive=f"provider.{node_id}",
        inputs=[IOSpec(name=name, type_desc=type_desc) for name, type_desc in inputs],
        outputs=[IOSpec(name=name, type_desc=type_desc) for name, type_desc in outputs],
    )


def _edge(source: str, target: str, output: str, input_name: str, type_desc: str):
    return DependencyEdge(
        source_id=source,
        target_id=target,
        output_name=output,
        input_name=input_name,
        source_type=type_desc,
        target_type=type_desc,
    )


def test_replacement_rewires_matching_parent_boundary_and_records_reference():
    parent = CDGExport(
        nodes=[
            _node("source", [], [("table", "DataFrame")]),
            _node("old", [("table", "DataFrame")], [("features", "DataFrame")]),
            _node("model", [("features", "DataFrame")], [("score", "float")]),
        ],
        edges=[
            _edge("source", "old", "table", "table", "DataFrame"),
            _edge("old", "model", "features", "features", "DataFrame"),
        ],
    )
    child = CDGExport(
        nodes=[
            _node("clean", [("table", "DataFrame")], [("cleaned", "DataFrame")]),
            _node("encode", [("cleaned", "DataFrame")], [("features", "DataFrame")]),
        ],
        edges=[_edge("clean", "encode", "cleaned", "cleaned", "DataFrame")],
        metadata={"repo": "features/general"},
    )

    result = compose_cdg(
        parent,
        child,
        operation="replacement",
        target_node_id="old",
        workspace_id="feature-workspace",
        version_id="v3",
    )

    assert "old" not in {node.node_id for node in result.nodes}
    assert any(edge.source_id == "source" and edge.target_id.endswith("__clean") for edge in result.edges)
    assert any(edge.source_id.endswith("__encode") and edge.target_id == "model" for edge in result.edges)
    assert result.metadata["cdg_references"][0]["version_id"] == "v3"


def test_predecessor_insertion_rewires_only_selected_input():
    parent = CDGExport(
        nodes=[
            _node("source", [], [("table", "DataFrame")]),
            _node("model", [("features", "DataFrame")], [("score", "float")]),
        ],
        edges=[_edge("source", "model", "table", "features", "DataFrame")],
    )
    child = CDGExport(
        nodes=[_node("features", [("features", "DataFrame")], [("features", "DataFrame")])],
        edges=[],
        metadata={"repo": "features/general"},
    )

    result = compose_cdg(
        parent,
        child,
        operation="predecessor",
        target_node_id="model",
        target_input_name="features",
        workspace_id="feature-workspace",
        version_id="v2",
    )

    component_id = next(node.node_id for node in result.nodes if node.node_id.endswith("__features"))
    assert any(edge.source_id == "source" and edge.target_id == component_id for edge in result.edges)
    assert any(edge.source_id == component_id and edge.target_id == "model" for edge in result.edges)


def test_predecessor_can_transform_all_target_inputs_as_one_project():
    parent = CDGExport(
        nodes=[
            _node("source", [], [("X_train", "DataFrame"), ("y_train", "NDArray")]),
            _node("fit", [("X_train", "DataFrame"), ("y_train", "NDArray")], [("model", "Model")]),
        ],
        edges=[
            _edge("source", "fit", "X_train", "X_train", "DataFrame"),
            _edge("source", "fit", "y_train", "y_train", "NDArray"),
        ],
    )
    child = CDGExport(
        nodes=[
            _node(
                "features",
                [("X_train", "DataFrame"), ("y_train", "NDArray")],
                [("X_train", "DataFrame"), ("y_train", "NDArray")],
            )
        ],
        edges=[],
    )

    result = compose_cdg(
        parent,
        child,
        operation="predecessor",
        target_node_id="fit",
        workspace_id="features",
        version_id="v1",
    )

    component_id = next(node.node_id for node in result.nodes if node.node_id.endswith("__features"))
    assert len([edge for edge in result.edges if edge.target_id == component_id]) == 2
    assert len([edge for edge in result.edges if edge.source_id == component_id]) == 2


def test_replacement_rejects_incompatible_contract():
    parent = CDGExport(nodes=[_node("old", [("x", "DataFrame")], [("y", "DataFrame")])], edges=[])
    child = CDGExport(nodes=[_node("wrong", [("x", "NDArray")], [("y", "NDArray")])], edges=[])

    with pytest.raises(CompositionError, match="cannot uniquely satisfy"):
        compose_cdg(
            parent,
            child,
            operation="replacement",
            target_node_id="old",
            workspace_id="wrong",
            version_id="v1",
        )
