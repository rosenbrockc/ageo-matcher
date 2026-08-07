"""Dependency-light deterministic assembly of a bound catalog CDG."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import keyword
from pathlib import Path
import re
from typing import Any

from sciona.provider_runtime import ProviderInstaller, RemoteCatalogClient


@dataclass(frozen=True)
class ArtifactBuildResult:
    artifact_fqdn: str
    output_path: Path
    function_name: str
    selected_fqdns: tuple[str, ...]


def _identifier(value: str, *, fallback: str) -> str:
    text = re.sub(r"\W+", "_", str(value or "")).strip("_")
    if not text or text[0].isdigit() or keyword.iskeyword(text):
        text = f"{fallback}_{text}".rstrip("_")
    return text


def _topological_nodes(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {str(node.get("node_id", "")): node for node in nodes}
    indegree = {node_id: 0 for node_id in by_id}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    for edge in edges:
        source = str(edge.get("source_id", ""))
        target = str(edge.get("target_id", ""))
        if source not in by_id or target not in by_id:
            continue
        outgoing[source].append(target)
        indegree[target] += 1
    ready = sorted(node_id for node_id, count in indegree.items() if count == 0)
    ordered: list[dict[str, Any]] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(by_id[node_id])
        for target in sorted(outgoing[node_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    if len(ordered) != len(by_id):
        raise ValueError("Catalog CDG contains a cycle or dangling dependency")
    return ordered


def _boundary_inputs(document: dict[str, Any]) -> list[str]:
    rows = [
        row
        for row in (document.get("io_specs") or [])
        if str(row.get("direction", "")) == "input"
    ]
    rows.sort(key=lambda row: (int(row.get("ordinal", 0)), str(row.get("name", ""))))
    return [str(row.get("name", "")) for row in rows if str(row.get("name", ""))]


async def build_catalog_artifact(
    *,
    client: RemoteCatalogClient,
    artifact_fqdn: str,
    output_path: Path,
    function_name: str = "solve",
    installer: ProviderInstaller | None = None,
) -> ArtifactBuildResult:
    """Install bound providers and emit executable Python for one catalog CDG."""
    if not function_name.isidentifier() or keyword.iskeyword(function_name):
        raise ValueError(f"Invalid Python function name {function_name!r}")
    document = await client.artifact_document(artifact_fqdn)
    nodes = [dict(row) for row in (document.get("cdg_nodes") or [])]
    edges = [dict(row) for row in (document.get("cdg_edges") or [])]
    bindings = {
        str(row.get("node_id", "")): str(row.get("bound_artifact_fqdn", ""))
        for row in (document.get("cdg_bindings") or [])
        if row.get("node_id") and row.get("bound_artifact_fqdn")
    }
    if not nodes or len(bindings) != len(nodes):
        missing = sorted(
            str(node.get("node_id", ""))
            for node in nodes
            if str(node.get("node_id", "")) not in bindings
        )
        raise ValueError(
            f"Artifact {artifact_fqdn!r} is not fully bound; missing nodes: {missing}"
        )

    provider_installer = installer or ProviderInstaller()
    callables: dict[str, Any] = {}
    selected: list[str] = []
    for fqdn in sorted(set(bindings.values())):
        candidate = await client.find(fqdn)
        callables[fqdn] = provider_installer.materialize(candidate)
        selected.append(fqdn)

    ordered = _topological_nodes(nodes, edges)
    root_inputs = _boundary_inputs(document)
    if not root_inputs:
        raise ValueError(f"Artifact {artifact_fqdn!r} does not publish boundary inputs")
    incoming: dict[str, dict[str, tuple[str, str]]] = {}
    outgoing_names: dict[str, set[str]] = {}
    outgoing_targets: dict[str, int] = {}
    for edge in edges:
        source = str(edge.get("source_id", ""))
        target = str(edge.get("target_id", ""))
        output_name = str(edge.get("output_name", "") or "result")
        input_name = str(edge.get("input_name", "") or output_name)
        incoming.setdefault(target, {})[input_name] = (source, output_name)
        outgoing_names.setdefault(source, set()).add(output_name)
        outgoing_targets[source] = outgoing_targets.get(source, 0) + 1

    aliases = {
        fqdn: f"_atom_{index}"
        for index, fqdn in enumerate(sorted(set(bindings.values())))
    }
    lines = ["from __future__ import annotations", ""]
    for fqdn, alias in aliases.items():
        module, symbol = fqdn.rsplit(".", 1)
        lines.append(f"from {module} import {symbol} as {alias}")
    lines.extend(
        [
            "",
            f"SELECTED_ARTIFACT = {artifact_fqdn!r}",
            f"SELECTED_ATOMS = {tuple(selected)!r}",
            "",
            f"def {function_name}({', '.join(root_inputs)}):",
        ]
    )

    output_vars: dict[tuple[str, str], str] = {}
    result_vars: dict[str, str] = {}
    for node in ordered:
        node_id = str(node.get("node_id", ""))
        fqdn = bindings[node_id]
        signature = inspect.signature(callables[fqdn])
        positional: list[str] = []
        keywords: list[str] = []
        node_inputs = incoming.get(node_id, {})
        for parameter in signature.parameters.values():
            if parameter.kind in {
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            }:
                continue
            expression = ""
            if parameter.name in node_inputs:
                source_id, output_name = node_inputs[parameter.name]
                expression = output_vars[(source_id, output_name)]
            elif parameter.name in root_inputs:
                expression = parameter.name
            elif parameter.default is not inspect.Parameter.empty:
                continue
            else:
                raise ValueError(
                    f"Cannot bind required parameter {parameter.name!r} for {fqdn}"
                )
            if parameter.kind == inspect.Parameter.POSITIONAL_ONLY:
                positional.append(expression)
            else:
                keywords.append(f"{parameter.name}={expression}")
        result_var = f"_{_identifier(node_id, fallback='stage')}_result"
        args = ", ".join([*positional, *keywords])
        lines.append(f"    {result_var} = {aliases[fqdn]}({args})")
        result_vars[node_id] = result_var
        names = sorted(outgoing_names.get(node_id, set()))
        if len(names) <= 1:
            output_vars[(node_id, names[0] if names else "result")] = result_var
        else:
            unpacked = [
                f"_{_identifier(node_id, fallback='stage')}_{_identifier(name, fallback='output')}"
                for name in names
            ]
            lines.append(f"    {', '.join(unpacked)} = {result_var}")
            for name, variable in zip(names, unpacked, strict=True):
                output_vars[(node_id, name)] = variable

    sinks = [
        str(node.get("node_id", ""))
        for node in ordered
        if outgoing_targets.get(str(node.get("node_id", "")), 0) == 0
    ]
    if not sinks:
        raise ValueError(f"Artifact {artifact_fqdn!r} has no output stage")
    if len(sinks) == 1:
        lines.append(f"    return {result_vars[sinks[0]]}")
    else:
        lines.append("    return " + ", ".join(result_vars[node_id] for node_id in sinks))
    lines.append("")

    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    return ArtifactBuildResult(
        artifact_fqdn=artifact_fqdn,
        output_path=output_path,
        function_name=function_name,
        selected_fqdns=tuple(selected),
    )
