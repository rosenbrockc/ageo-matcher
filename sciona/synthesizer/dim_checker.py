"""Dimensional analysis checker for the synthesis pipeline.

Runs during compilation (pre-synthesis) to enforce that every GlueEdge
connecting two atoms has compatible dimensional signatures.  This is the
"compiler contract" described in the symbolic_math design doc.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from sciona.ghost.dimensions import DimensionalSignature
from sciona.ghost.registry import REGISTRY
from sciona.synthesizer.models import AssemblyUnit, GlueEdge

logger = logging.getLogger(__name__)


class DimError(BaseModel):
    """A single dimensional mismatch on a CDG edge."""

    source_id: str
    target_id: str
    output_name: str
    input_name: str
    source_dim: str
    target_dim: str
    message: str


class DimCheckResult(BaseModel):
    """Aggregate result of dimensional analysis across all edges."""

    passed: bool = True
    errors: list[DimError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _lookup_dim(
    unit: AssemblyUnit,
    port_name: str,
    direction: str,
) -> DimensionalSignature | None:
    """Look up the dimensional signature for a port on an assembly unit.

    Checks three sources in order:
    1. The ``dim_signature`` field on the unit's IOSpec (from CDG/architect).
    2. The ``dim_signature`` dict in the global REGISTRY for the atom.
    3. Returns ``None`` if no dim info is available.
    """
    # 1. Check IOSpec on the unit
    specs = unit.outputs if direction == "output" else unit.inputs
    for spec in specs:
        if spec.name == port_name and spec.dim_signature:
            try:
                return DimensionalSignature.from_compact(spec.dim_signature)
            except Exception:
                pass

    # 2. Check the global registry
    entry = REGISTRY.get(unit.declaration_name) or REGISTRY.get(unit.name)
    if entry:
        dim_sig: dict[str, DimensionalSignature] = entry.get("dim_signature", {})
        if port_name in dim_sig:
            return dim_sig[port_name]
        # Try "return" key for outputs
        if direction == "output" and "return" in dim_sig:
            return dim_sig["return"]

    return None


def check_dimensional_consistency(
    units: list[AssemblyUnit],
    glue_edges: list[GlueEdge],
) -> DimCheckResult:
    """Check dimensional consistency across all GlueEdges.

    Rules:
    - Both sides annotated, compatible: pass silently.
    - Both sides annotated, incompatible: error.
    - One side annotated, other missing: warning.
    - Both sides missing: silent pass.
    """
    unit_map = {u.node_id: u for u in units}
    result = DimCheckResult()

    for edge in glue_edges:
        src_unit = unit_map.get(edge.source_id)
        tgt_unit = unit_map.get(edge.target_id)

        if src_unit is None or tgt_unit is None:
            continue

        # Try to get dims from the GlueEdge itself first (set by assembler)
        src_dim: DimensionalSignature | None = None
        tgt_dim: DimensionalSignature | None = None
        invalid_source_dim = False
        invalid_target_dim = False

        if edge.source_dim:
            try:
                src_dim = DimensionalSignature.from_compact(edge.source_dim)
            except Exception:
                invalid_source_dim = True
        if edge.target_dim:
            try:
                tgt_dim = DimensionalSignature.from_compact(edge.target_dim)
            except Exception:
                invalid_target_dim = True

        if invalid_source_dim or invalid_target_dim:
            result.passed = False
            result.errors.append(
                DimError(
                    source_id=edge.source_id,
                    target_id=edge.target_id,
                    output_name=edge.output_name,
                    input_name=edge.input_name,
                    source_dim=edge.source_dim or "",
                    target_dim=edge.target_dim or "",
                    message=(
                        "Malformed dimensional annotation on edge "
                        f"{src_unit.name}.{edge.output_name} -> "
                        f"{tgt_unit.name}.{edge.input_name}"
                    ),
                )
            )
            continue

        # Fall back to registry/IOSpec lookup
        if src_dim is None:
            src_dim = _lookup_dim(src_unit, edge.output_name, "output")
        if tgt_dim is None:
            tgt_dim = _lookup_dim(tgt_unit, edge.input_name, "input")

        # Apply rules
        if src_dim is not None and tgt_dim is not None:
            if not src_dim.is_compatible(tgt_dim):
                result.passed = False
                result.errors.append(DimError(
                    source_id=edge.source_id,
                    target_id=edge.target_id,
                    output_name=edge.output_name,
                    input_name=edge.input_name,
                    source_dim=src_dim.to_compact(),
                    target_dim=tgt_dim.to_compact(),
                    message=(
                        f"Dimensional mismatch on edge "
                        f"{src_unit.name}.{edge.output_name} -> "
                        f"{tgt_unit.name}.{edge.input_name}: "
                        f"{src_dim.to_compact()} != {tgt_dim.to_compact()}"
                    ),
                ))
        elif src_dim is not None or tgt_dim is not None:
            known_side = "source" if src_dim is not None else "target"
            unknown_side = "target" if src_dim is not None else "source"
            result.warnings.append(
                f"Partial dim annotation on edge "
                f"{src_unit.name}.{edge.output_name} -> "
                f"{tgt_unit.name}.{edge.input_name}: "
                f"{known_side} has dim info but {unknown_side} does not"
            )

    return result
