"""Variant-family mutation support for Principal trial updates."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sciona.architect.handoff import CDGExport
from sciona.architect.models import AlgorithmicNode, AlgorithmicPrimitive, NodeStatus
from sciona.expansion_atoms.baseline_steps_registry import (
    BASELINE_STEPS_DECLARATIONS,
    next_baseline_analysis_variant,
)
from sciona.provider_expansion_declarations import (
    load_provider_expansion_declarations,
)

if TYPE_CHECKING:
    from sciona.architect.catalog import PrimitiveCatalog
    from sciona.principal.atom_ledger import AtomLedger

logger = logging.getLogger(__name__)


_SIGNAL_EVENT_RATE_ALTERNATIVES = {
    "compute_event_rate": (
        "compute_event_rate_median_smoothed",
        "compute_event_rate_smoothed",
    ),
    "heart_rate_computation": (
        "heart_rate_computation_median_smoothed",
    ),
}


def _signal_event_rate_declarations() -> dict[str, tuple[str, str, str]]:
    return load_provider_expansion_declarations(
        "signal_event_rate",
        "SIGNAL_EVENT_RATE_DECLARATIONS",
    )


def _next_signal_event_rate_variant(primitive_name: str) -> str | None:
    current = str(primitive_name or "").strip()
    short_name = current.rsplit(".", 1)[-1]
    variants = _SIGNAL_EVENT_RATE_ALTERNATIVES.get(short_name)
    if not variants:
        return None
    variant = variants[0]
    if "." not in current:
        return variant
    return current.rsplit(".", 1)[0] + "." + variant


@dataclass(frozen=True)
class VariantMutationResult:
    """Outcome of attempting an in-place family-specific mutation."""

    cdg: CDGExport
    applied: bool
    family: str | None = None
    variant_name: str | None = None
    allow_redecompose: bool = True


class VariantFamily(Protocol):
    """Pluggable family that can detect and mutate compatible CDGs."""

    name: str

    def matches(self, cdg: CDGExport) -> bool:
        """Return whether this family owns the given CDG."""

    def mutate(
        self,
        cdg: CDGExport,
        *,
        bottleneck_name: str | None,
    ) -> VariantMutationResult:
        """Return a mutated CDG when a valid family-specific variant exists."""


class SignalEventRateVariantFamily:
    """Curated variants for signal -> event -> rate pipelines."""

    name = "signal_event_rate"

    # Anchor atoms that identify this family.  The CDG is recognized as a
    # signal-event-rate pipeline if *any* anchor is present — not requiring
    # every atom to be registered.  This allows expanded CDGs (with SQI,
    # jump-removal, or cross-domain atoms) to still match the family for
    # variant swapping on the core nodes.
    def matches(self, cdg: CDGExport) -> bool:
        anchors = set(_signal_event_rate_declarations()) | set(
            _SIGNAL_EVENT_RATE_ALTERNATIVES
        )
        atomic_nodes = [node for node in cdg.nodes if node.status == NodeStatus.ATOMIC]
        if not atomic_nodes or not anchors:
            return False
        return any(
            str(node.matched_primitive or "").rsplit(".", 1)[-1] in anchors
            for node in atomic_nodes
        )

    def mutate(
        self,
        cdg: CDGExport,
        *,
        bottleneck_name: str | None,
    ) -> VariantMutationResult:
        if not bottleneck_name or not self.matches(cdg):
            return VariantMutationResult(cdg=cdg, applied=False)

        updated_nodes = []
        variant_name: str | None = None
        changed = False
        for node in cdg.nodes:
            if node.status != NodeStatus.ATOMIC or node.name != bottleneck_name:
                updated_nodes.append(node)
                continue
            current = str(node.matched_primitive or "")
            candidate = _next_signal_event_rate_variant(current)
            if not candidate:
                updated_nodes.append(node)
                continue
            updated_nodes.append(
                node.model_copy(update={"matched_primitive": candidate})
            )
            changed = True
            variant_name = candidate

        if not changed:
            return VariantMutationResult(
                cdg=cdg,
                applied=False,
                family=self.name,
                allow_redecompose=False,
            )

        return VariantMutationResult(
            cdg=cdg.model_copy(update={"nodes": updated_nodes}),
            applied=True,
            family=self.name,
            variant_name=variant_name,
            allow_redecompose=False,
        )


class BaselineAnalysisVariantFamily:
    """Curated variants for baseline analysis processing atoms."""

    name = "baseline_analysis"

    _ANCHORS = set(BASELINE_STEPS_DECLARATIONS.keys())

    def matches(self, cdg: CDGExport) -> bool:
        atomic_nodes = [node for node in cdg.nodes if node.status == NodeStatus.ATOMIC]
        if not atomic_nodes:
            return False
        return any(
            str(node.matched_primitive or "") in self._ANCHORS
            for node in atomic_nodes
        )

    def mutate(
        self,
        cdg: CDGExport,
        *,
        bottleneck_name: str | None,
    ) -> VariantMutationResult:
        if not bottleneck_name or not self.matches(cdg):
            return VariantMutationResult(cdg=cdg, applied=False)

        updated_nodes = []
        variant_name: str | None = None
        changed = False
        for node in cdg.nodes:
            if node.status != NodeStatus.ATOMIC or node.name != bottleneck_name:
                updated_nodes.append(node)
                continue
            current = str(node.matched_primitive or "")
            candidate = next_baseline_analysis_variant(current)
            if not candidate:
                updated_nodes.append(node)
                continue
            updated_nodes.append(
                node.model_copy(update={"matched_primitive": candidate})
            )
            changed = True
            variant_name = candidate

        if not changed:
            return VariantMutationResult(
                cdg=cdg,
                applied=False,
                family=self.name,
                allow_redecompose=False,
            )

        return VariantMutationResult(
            cdg=cdg.model_copy(update={"nodes": updated_nodes}),
            applied=True,
            family=self.name,
            variant_name=variant_name,
            allow_redecompose=False,
        )


VARIANT_FAMILIES: tuple[VariantFamily, ...] = (
    SignalEventRateVariantFamily(),
    BaselineAnalysisVariantFamily(),
)


class LedgerVariantFamily:
    """Universal fallback that uses UCB1 bandit rankings to select atom variants."""

    name = "ledger_bandit"

    def __init__(self, ledger: AtomLedger, catalog: PrimitiveCatalog) -> None:
        self._ledger = ledger
        self._catalog = catalog

    def matches(self, cdg: CDGExport) -> bool:
        return True

    def mutate(
        self,
        cdg: CDGExport,
        *,
        bottleneck_name: str | None,
    ) -> VariantMutationResult:
        from sciona.principal.atom_ledger import compute_slot_signature

        if not bottleneck_name:
            return VariantMutationResult(cdg=cdg, applied=False)

        node_map = {n.node_id: n for n in cdg.nodes}
        target = None
        for node in cdg.nodes:
            if node.status == NodeStatus.ATOMIC and node.name == bottleneck_name:
                target = node
                break

        if target is None or not target.matched_primitive:
            return VariantMutationResult(cdg=cdg, applied=False)

        parent = node_map.get(target.parent_id) if target.parent_id else None
        slot = compute_slot_signature(target, parent)

        candidates = [
            p.name
            for p in self._catalog.all_primitives()
            if _is_primitive_structurally_compatible(target, p)
        ]
        if not candidates:
            return VariantMutationResult(cdg=cdg, applied=False)
        if target.matched_primitive not in candidates:
            candidates.append(target.matched_primitive)

        ranked = self._ledger.rank_candidates(slot, candidates)
        if not ranked:
            return VariantMutationResult(cdg=cdg, applied=False)
        ranked = self._apply_family_prior(target, ranked)

        best_name, _best_score = ranked[0]
        if best_name == target.matched_primitive:
            return VariantMutationResult(
                cdg=cdg, applied=False, family=self.name, allow_redecompose=True
            )

        updated_nodes = []
        for node in cdg.nodes:
            if node.node_id == target.node_id:
                updated_nodes.append(
                    node.model_copy(update={"matched_primitive": best_name})
                )
            else:
                updated_nodes.append(node)

        logger.info(
            "Ledger bandit: swapping '%s' -> '%s' for '%s'",
            target.matched_primitive,
            best_name,
            bottleneck_name,
        )
        return VariantMutationResult(
            cdg=cdg.model_copy(update={"nodes": updated_nodes}),
            applied=True,
            family=self.name,
            variant_name=best_name,
            allow_redecompose=True,
        )

    def _apply_family_prior(
        self,
        target: AlgorithmicNode,
        ranked: list[tuple[str, float]],
    ) -> list[tuple[str, float]]:
        """Prefer same-family primitives without forbidding cross-family swaps."""
        finite_scores = [s for _, s in ranked if not math.isinf(s)]
        score_range = (max(finite_scores) - min(finite_scores)) if len(finite_scores) >= 2 else 1.0
        penalty = max(0.01, 0.05 * score_range)
        adjusted: list[tuple[str, float]] = []
        for primitive_name, score in ranked:
            primitive = self._catalog.get(primitive_name)
            same_family = (
                primitive is not None and primitive.category == target.concept_type
            )
            adjusted_score = _apply_family_prior_score(
                score,
                same_family=same_family,
                penalty=penalty,
            )
            adjusted.append((primitive_name, adjusted_score))
        adjusted.sort(key=lambda item: -item[1])
        return adjusted


def maybe_apply_bottleneck_variant(
    cdg: CDGExport,
    *,
    bottleneck_name: str | None,
    atom_ledger: AtomLedger | None = None,
    catalog: PrimitiveCatalog | None = None,
) -> VariantMutationResult:
    """Apply the first matching family-specific mutation for the bottleneck node.

    Families are tried in order.  A successful mutation (``applied=True``)
    short-circuits immediately.  An exhausted family (``applied=False``)
    is recorded but does **not** block later families — this lets the
    ledger bandit try after a curated family runs out of variants.
    """
    if not bottleneck_name:
        return VariantMutationResult(cdg=cdg, applied=False)

    families: list[VariantFamily] = list(VARIANT_FAMILIES)
    if atom_ledger is not None and catalog is not None:
        families.append(LedgerVariantFamily(atom_ledger, catalog))

    last_exhausted: VariantMutationResult | None = None
    for family in families:
        if not family.matches(cdg):
            continue
        result = family.mutate(cdg, bottleneck_name=bottleneck_name)
        if result.applied:
            return result
        if result.family is not None and not result.allow_redecompose:
            return result
        # Family matched but had nothing to apply — remember it but keep
        # trying remaining families.  Later families override earlier ones
        # so that e.g. the ledger's allow_redecompose=True supersedes a
        # curated family's allow_redecompose=False.
        if result.family is not None:
            last_exhausted = result

    if last_exhausted is not None:
        return last_exhausted
    return VariantMutationResult(cdg=cdg, applied=False)


def _normalize_type_desc(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def _is_primitive_structurally_compatible(
    node: AlgorithmicNode,
    primitive: AlgorithmicPrimitive,
) -> bool:
    """Return whether a primitive is safe to slot into a node without rewiring."""
    if len(node.inputs) != len(primitive.inputs):
        return False
    if len(node.outputs) != len(primitive.outputs):
        return False

    node_input_types = [_normalize_type_desc(port.type_desc) for port in node.inputs]
    prim_input_types = [_normalize_type_desc(port.type_desc) for port in primitive.inputs]
    node_output_types = [_normalize_type_desc(port.type_desc) for port in node.outputs]
    prim_output_types = [_normalize_type_desc(port.type_desc) for port in primitive.outputs]
    return node_input_types == prim_input_types and node_output_types == prim_output_types


def _apply_family_prior_score(score: float, *, same_family: bool, penalty: float = 0.15) -> float:
    """Apply a scaled penalty to cross-family candidates after ledger ranking."""
    if same_family:
        return score
    if math.isinf(score):
        return 1e6
    return score - penalty
