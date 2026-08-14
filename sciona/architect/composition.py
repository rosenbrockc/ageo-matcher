"""Typed composition of independently evolved CDGs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Literal

from sciona.architect.handoff import CDGExport
from sciona.architect.models import AlgorithmicNode, DependencyEdge, IOSpec


class CompositionError(ValueError):
    """Raised when a child CDG cannot satisfy its parent boundary contract."""


@dataclass(frozen=True)
class BoundaryPort:
    node_id: str
    spec: IOSpec


def _normalized_type(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _compatible(expected: IOSpec, actual: IOSpec) -> bool:
    if _normalized_type(expected.type_desc) != _normalized_type(actual.type_desc):
        return False
    for field in ("data_kind", "time_basis"):
        left = getattr(expected, field, "")
        right = getattr(actual, field, "")
        if left and right and left != right:
            return False
    return True


def graph_boundary(graph: CDGExport) -> tuple[list[BoundaryPort], list[BoundaryPort]]:
    """Return externally supplied inputs and externally observable outputs."""
    incoming = {(edge.target_id, edge.input_name) for edge in graph.edges}
    outgoing = {(edge.source_id, edge.output_name) for edge in graph.edges}
    inputs = [
        BoundaryPort(node.node_id, spec)
        for node in graph.nodes
        for spec in node.inputs
        if (node.node_id, spec.name) not in incoming
    ]
    outputs = [
        BoundaryPort(node.node_id, spec)
        for node in graph.nodes
        for spec in node.outputs
        if (node.node_id, spec.name) not in outgoing
    ]
    return inputs, outputs


def _match_ports(
    expected: list[BoundaryPort], actual: list[BoundaryPort], *, boundary_name: str
) -> dict[tuple[str, str], BoundaryPort]:
    if len(expected) != len(actual):
        raise CompositionError(
            f"{boundary_name} contract requires {len(expected)} ports but child exposes "
            f"{len(actual)}"
        )
    remaining = list(actual)
    mapping: dict[tuple[str, str], BoundaryPort] = {}
    for required in expected:
        candidates = [
            port
            for port in remaining
            if port.spec.name == required.spec.name
            and _compatible(required.spec, port.spec)
        ]
        if not candidates:
            candidates = [
                port for port in remaining if _compatible(required.spec, port.spec)
            ]
        if len(candidates) != 1:
            raise CompositionError(
                f"Child {boundary_name} cannot uniquely satisfy "
                f"'{required.spec.name}: {required.spec.type_desc}'"
            )
        selected = candidates[0]
        remaining.remove(selected)
        mapping[(required.node_id, required.spec.name)] = selected
    return mapping


def _namespaced_child(
    child: CDGExport, namespace: str
) -> tuple[list[AlgorithmicNode], list[DependencyEdge], dict[str, str]]:
    prefix = re.sub(r"[^a-zA-Z0-9_-]", "-", namespace).strip("-") or "component"
    identifiers = {node.node_id: f"{prefix}__{node.node_id}" for node in child.nodes}
    nodes = []
    for original in child.nodes:
        node = original.model_copy(deep=True)
        node.node_id = identifiers[original.node_id]
        if node.parent_id in identifiers:
            node.parent_id = identifiers[node.parent_id]
        node.children = [identifiers.get(child_id, child_id) for child_id in node.children]
        nodes.append(node)
    edges = []
    for original in child.edges:
        edge = original.model_copy(deep=True)
        edge.source_id = identifiers[original.source_id]
        edge.target_id = identifiers[original.target_id]
        edges.append(edge)
    return nodes, edges, identifiers


def compose_cdg(
    parent: CDGExport,
    child: CDGExport,
    *,
    operation: Literal["predecessor", "replacement"],
    target_node_id: str,
    target_input_name: str = "",
    workspace_id: str,
    version_id: str,
) -> CDGExport:
    """Insert a child before a port or replace a node with a typed child graph."""
    result = deepcopy(parent)
    target = next((node for node in result.nodes if node.node_id == target_node_id), None)
    if target is None:
        raise CompositionError(f"Parent node '{target_node_id}' was not found")
    child_inputs, child_outputs = graph_boundary(child)

    if operation == "replacement":
        expected_inputs = [BoundaryPort(target.node_id, spec) for spec in target.inputs]
        expected_outputs = [BoundaryPort(target.node_id, spec) for spec in target.outputs]
    else:
        selected_specs = (
            [spec for spec in target.inputs if spec.name == target_input_name]
            if target_input_name
            else list(target.inputs)
        )
        if not selected_specs:
            raise CompositionError(
                f"Input '{target_input_name}' was not found on '{target_node_id}'"
            )
        expected_inputs = [BoundaryPort(target.node_id, spec) for spec in selected_specs]
        expected_outputs = [BoundaryPort(target.node_id, spec) for spec in selected_specs]

    input_mapping = _match_ports(expected_inputs, child_inputs, boundary_name="input")
    output_mapping = _match_ports(expected_outputs, child_outputs, boundary_name="output")
    namespace = f"component-{workspace_id}-{version_id}"
    child_nodes, child_edges, identifiers = _namespaced_child(child, namespace)

    rewritten_edges: list[DependencyEdge] = []
    if operation == "replacement":
        for edge in result.edges:
            if edge.target_id == target.node_id:
                port = input_mapping[(target.node_id, edge.input_name)]
                edge = edge.model_copy(deep=True)
                edge.target_id = identifiers[port.node_id]
                edge.input_name = port.spec.name
                edge.target_type = port.spec.type_desc
            elif edge.source_id == target.node_id:
                port = output_mapping[(target.node_id, edge.output_name)]
                edge = edge.model_copy(deep=True)
                edge.source_id = identifiers[port.node_id]
                edge.output_name = port.spec.name
                edge.source_type = port.spec.type_desc
            rewritten_edges.append(edge)
        result.nodes = [node for node in result.nodes if node.node_id != target.node_id]
    else:
        selected_names = {port.spec.name for port in expected_inputs}
        for edge in result.edges:
            if edge.target_id == target.node_id and edge.input_name in selected_names:
                child_input = input_mapping[(target.node_id, edge.input_name)]
                edge = edge.model_copy(deep=True)
                edge.target_id = identifiers[child_input.node_id]
                edge.input_name = child_input.spec.name
                edge.target_type = child_input.spec.type_desc
            rewritten_edges.append(edge)
        for required in expected_outputs:
            child_output = output_mapping[(required.node_id, required.spec.name)]
            rewritten_edges.append(
                DependencyEdge(
                    source_id=identifiers[child_output.node_id],
                    target_id=target.node_id,
                    output_name=child_output.spec.name,
                    input_name=required.spec.name,
                    source_type=child_output.spec.type_desc,
                    target_type=required.spec.type_desc,
                )
            )

    result.nodes.extend(child_nodes)
    result.edges = rewritten_edges + child_edges
    references = result.metadata.setdefault("cdg_references", [])
    references.append(
        {
            "operation": operation,
            "workspace_id": workspace_id,
            "repo": child.metadata.get("repo", ""),
            "version_id": version_id,
            "target_node_id": target_node_id,
            "target_input_name": target_input_name,
            "namespace": namespace,
            "input_mapping": {
                expected.spec.name: actual.spec.name
                for expected, actual in zip(expected_inputs, input_mapping.values())
            },
            "output_mapping": {
                expected.spec.name: actual.spec.name
                for expected, actual in zip(expected_outputs, output_mapping.values())
            },
        }
    )
    return result
