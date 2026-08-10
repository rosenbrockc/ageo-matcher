from __future__ import annotations

from sciona.architect.handoff import CDGExport
from sciona.architect.models import AlgorithmicNode, AlgorithmicPrimitive, ConceptType, IOSpec, NodeStatus
from sciona.principal.atom_ledger import AtomLedger, compute_slot_signature
from sciona.principal.variant_mutation import (
    LedgerVariantFamily,
    SignalEventRateVariantFamily,
    maybe_apply_bottleneck_variant,
)


def _atomic_node(
    node_id: str,
    name: str,
    *,
    matched_primitive: str | None,
) -> AlgorithmicNode:
    return AlgorithmicNode(
        node_id=node_id,
        name=name,
        description=name,
        concept_type=ConceptType.SIGNAL_FILTER,
        status=NodeStatus.ATOMIC,
        matched_primitive=matched_primitive,
    )


def test_signal_event_rate_family_matches_curated_scaffold() -> None:
    family = SignalEventRateVariantFamily()
    cdg = CDGExport(
        nodes=[
            _atomic_node(
                "n1",
                "Filter Signal For Detection",
                matched_primitive="filter_signal_for_detection",
            ),
            _atomic_node(
                "n2",
                "Detect Peaks In Signal",
                matched_primitive="detect_peaks_in_signal",
            ),
            _atomic_node(
                "n3",
                "Compute Event Rate",
                matched_primitive="compute_event_rate",
            ),
        ],
        edges=[],
    )

    assert family.matches(cdg) is True


def test_signal_event_rate_family_rejects_unrelated_scaffold() -> None:
    family = SignalEventRateVariantFamily()
    cdg = CDGExport(
        nodes=[
            _atomic_node(
                "n1",
                "Unrelated Stage",
                matched_primitive="some_other_primitive",
            ),
        ],
        edges=[],
    )

    assert family.matches(cdg) is False


def test_maybe_apply_bottleneck_variant_returns_family_metadata() -> None:
    cdg = CDGExport(
        nodes=[
            _atomic_node(
                "n1",
                "Filter Signal For Detection",
                matched_primitive="filter_signal_for_detection",
            ),
            _atomic_node(
                "n2",
                "Detect Peaks In Signal",
                matched_primitive="detect_peaks_in_signal",
            ),
            _atomic_node(
                "n3",
                "Compute Event Rate",
                matched_primitive="compute_event_rate",
            ),
        ],
        edges=[],
    )

    result = maybe_apply_bottleneck_variant(
        cdg,
        bottleneck_name="Compute Event Rate",
    )

    assert result.applied is True
    assert result.family == "signal_event_rate"
    assert result.variant_name == "compute_event_rate_median_smoothed"
    updated = {
        node.name: node.matched_primitive
        for node in result.cdg.nodes
        if node.status == NodeStatus.ATOMIC
    }
    assert updated["Compute Event Rate"] == "compute_event_rate_median_smoothed"
    assert updated["Detect Peaks In Signal"] == "detect_peaks_in_signal"


def test_signal_event_rate_family_preserves_fully_qualified_provider_namespace() -> None:
    prefix = "sciona.atoms.signal_processing.biosppy.ecg"
    cdg = CDGExport(
        nodes=[
            _atomic_node(
                "n1",
                "Measure Event Rate",
                matched_primitive=f"{prefix}.heart_rate_computation",
            ),
        ],
        edges=[],
    )

    result = maybe_apply_bottleneck_variant(
        cdg,
        bottleneck_name="Measure Event Rate",
    )

    assert result.applied is True
    assert result.variant_name == f"{prefix}.heart_rate_computation_median_smoothed"
    assert result.cdg.nodes[0].matched_primitive == result.variant_name


def test_maybe_apply_bottleneck_variant_is_noop_without_family_variant() -> None:
    cdg = CDGExport(
        nodes=[
            _atomic_node(
                "n1",
                "Filter Signal For Detection",
                matched_primitive="filter_signal_for_detection",
            ),
            _atomic_node(
                "n2",
                "Detect Peaks In Signal",
                matched_primitive="detect_peaks_in_signal",
            ),
        ],
        edges=[],
    )

    result = maybe_apply_bottleneck_variant(
        cdg,
        bottleneck_name="Detect Peaks In Signal",
    )

    assert result.applied is False
    assert result.family == "signal_event_rate"
    assert result.variant_name is None
    assert result.allow_redecompose is False
    assert result.cdg == cdg


# ---------------------------------------------------------------------------
# Ledger bandit family tests
# ---------------------------------------------------------------------------


def _generic_node(
    node_id: str,
    name: str,
    *,
    matched_primitive: str | None,
    parent_id: str | None = None,
    concept_type: ConceptType = ConceptType.CUSTOM,
) -> AlgorithmicNode:
    return AlgorithmicNode(
        node_id=node_id,
        name=name,
        description=name,
        concept_type=concept_type,
        status=NodeStatus.ATOMIC,
        parent_id=parent_id,
        inputs=[IOSpec(name="x", type_desc="np.ndarray")],
        outputs=[IOSpec(name="y", type_desc="np.ndarray")],
        matched_primitive=matched_primitive,
    )


def _mock_catalog_with(
    *primitive_specs: tuple[str, list[IOSpec], list[IOSpec]],
    concept_type: ConceptType = ConceptType.CUSTOM,
) -> PrimitiveCatalog:
    from sciona.architect.catalog import PrimitiveCatalog

    catalog = PrimitiveCatalog()
    prims = []
    for name, inputs, outputs in primitive_specs:
        prim = AlgorithmicPrimitive(
            name=name,
            source="test",
            category=concept_type,
            description=name,
            inputs=inputs,
            outputs=outputs,
            type_signature="test",
        )
        prims.append(prim)
        catalog.add(prim)
    return catalog


def test_ledger_variant_family_swaps_atom() -> None:
    ledger = AtomLedger()
    ports_in = [IOSpec(name="x", type_desc="np.ndarray")]
    ports_out = [IOSpec(name="y", type_desc="np.ndarray")]
    catalog = _mock_catalog_with(
        ("atom_a", ports_in, ports_out),
        ("atom_b", ports_in, ports_out),
    )

    cdg = CDGExport(
        nodes=[_generic_node("n1", "Step", matched_primitive="atom_a")],
        edges=[],
    )

    # Record: atom_a is bad, atom_b is good
    node = cdg.nodes[0]
    slot = compute_slot_signature(node, None)
    for t in range(5):
        ledger.record(slot, "atom_a", 80.0, trial=t)
        ledger.record(slot, "atom_b", 10.0, trial=t)

    result = maybe_apply_bottleneck_variant(
        cdg,
        bottleneck_name="Step",
        atom_ledger=ledger,
        catalog=catalog,
    )

    assert result.applied is True
    assert result.family == "ledger_bandit"
    assert result.variant_name == "atom_b"
    assert result.cdg.nodes[0].matched_primitive == "atom_b"


def test_ledger_variant_family_noop_when_current_is_best() -> None:
    ledger = AtomLedger()
    ports_in = [IOSpec(name="x", type_desc="np.ndarray")]
    ports_out = [IOSpec(name="y", type_desc="np.ndarray")]
    catalog = _mock_catalog_with(
        ("atom_a", ports_in, ports_out),
        ("atom_b", ports_in, ports_out),
    )

    cdg = CDGExport(
        nodes=[_generic_node("n1", "Step", matched_primitive="atom_a")],
        edges=[],
    )

    node = cdg.nodes[0]
    slot = compute_slot_signature(node, None)
    for t in range(5):
        ledger.record(slot, "atom_a", 10.0, trial=t)
        ledger.record(slot, "atom_b", 80.0, trial=t)

    result = maybe_apply_bottleneck_variant(
        cdg,
        bottleneck_name="Step",
        atom_ledger=ledger,
        catalog=catalog,
    )

    assert result.applied is False
    assert result.family == "ledger_bandit"
    assert result.allow_redecompose is True


def test_curated_family_takes_priority_over_ledger() -> None:
    ledger = AtomLedger()
    catalog = _mock_catalog_with(
        ("compute_event_rate", [IOSpec(name="x", type_desc="np.ndarray")], [IOSpec(name="y", type_desc="np.ndarray")]),
        ("compute_event_rate_smoothed", [IOSpec(name="x", type_desc="np.ndarray")], [IOSpec(name="y", type_desc="np.ndarray")]),
        concept_type=ConceptType.SIGNAL_FILTER,
    )

    # Build a signal_event_rate scaffold — curated family should match first
    cdg = CDGExport(
        nodes=[
            _atomic_node("n1", "Filter Signal For Detection", matched_primitive="filter_signal_for_detection"),
            _atomic_node("n2", "Detect Peaks In Signal", matched_primitive="detect_peaks_in_signal"),
            _atomic_node("n3", "Compute Event Rate", matched_primitive="compute_event_rate"),
        ],
        edges=[],
    )

    result = maybe_apply_bottleneck_variant(
        cdg,
        bottleneck_name="Compute Event Rate",
        atom_ledger=ledger,
        catalog=catalog,
    )

    # Curated family fires, not ledger
    assert result.applied is True
    assert result.family == "signal_event_rate"


def test_exhausted_curated_family_blocks_unsafe_ledger_fallback() -> None:
    """When a family explicitly disallows fallback, Principal should stop there."""
    ledger = AtomLedger()
    catalog = _mock_catalog_with(
        (
            "compute_event_rate_smoothed",
            [IOSpec(name="events", type_desc="np.ndarray"), IOSpec(name="sampling_rate", type_desc="float")],
            [IOSpec(name="rate", type_desc="tuple[np.ndarray, np.ndarray]")],
        ),
        (
            "compute_event_rate_windowed",
            [IOSpec(name="events", type_desc="np.ndarray"), IOSpec(name="sampling_rate", type_desc="float")],
            [IOSpec(name="rate", type_desc="tuple[np.ndarray, np.ndarray]")],
        ),
        concept_type=ConceptType.SIGNAL_FILTER,
    )

    # Scaffold where the curated variant was already applied (smoothed).
    # The curated family has no further variant for smoothed, so it will
    # return applied=False, allow_redecompose=False.
    cdg = CDGExport(
        nodes=[
            _atomic_node("n1", "Filter Signal For Detection", matched_primitive="filter_signal_for_detection"),
            _atomic_node("n2", "Detect Peaks In Signal", matched_primitive="detect_peaks_in_signal"),
            _atomic_node("n3", "Compute Event Rate", matched_primitive="compute_event_rate_smoothed"),
        ],
        edges=[],
    )

    # Record ledger observations: smoothed is bad, windowed is good
    node = cdg.nodes[2]
    slot = compute_slot_signature(node, None)
    for t in range(5):
        ledger.record(slot, "compute_event_rate_smoothed", 75.0, trial=t)
        ledger.record(slot, "compute_event_rate_windowed", 15.0, trial=t)

    result = maybe_apply_bottleneck_variant(
        cdg,
        bottleneck_name="Compute Event Rate",
        atom_ledger=ledger,
        catalog=catalog,
    )

    assert result.applied is False
    assert result.family == "signal_event_rate"
    assert result.variant_name is None
    assert result.allow_redecompose is False


def test_ledger_allows_redecompose_when_no_family_matches() -> None:
    """When no curated family owns the graph, the ledger remains the fallback."""
    ledger = AtomLedger()
    catalog = _mock_catalog_with(
        ("atom_a", [IOSpec(name="x", type_desc="np.ndarray")], [IOSpec(name="y", type_desc="np.ndarray")]),
        ("atom_b", [IOSpec(name="x", type_desc="np.ndarray")], [IOSpec(name="y", type_desc="np.ndarray")]),
    )

    cdg = CDGExport(
        nodes=[
            _generic_node("n1", "Step", matched_primitive="atom_a"),
        ],
        edges=[],
    )

    node = cdg.nodes[0]
    slot = compute_slot_signature(node, None)
    ledger.record(slot, "atom_a", 10.0, trial=1)
    ledger.record(slot, "atom_b", 80.0, trial=1)

    result = maybe_apply_bottleneck_variant(
        cdg,
        bottleneck_name="Step",
        atom_ledger=ledger,
        catalog=catalog,
    )

    assert result.applied is False
    assert result.family == "ledger_bandit"
    assert result.allow_redecompose is True


def test_ledger_filters_out_structurally_incompatible_candidates() -> None:
    ledger = AtomLedger()
    catalog = _mock_catalog_with(
        ("atom_a", [IOSpec(name="x", type_desc="np.ndarray")], [IOSpec(name="y", type_desc="np.ndarray")]),
        ("atom_bad", [IOSpec(name="signal", type_desc="np.ndarray"), IOSpec(name="rate", type_desc="float")], [IOSpec(name="y", type_desc="np.ndarray")]),
    )
    cdg = CDGExport(
        nodes=[_generic_node("n1", "Step", matched_primitive="atom_a")],
        edges=[],
    )

    node = cdg.nodes[0]
    slot = compute_slot_signature(node, None)
    ledger.record(slot, "atom_a", 10.0, trial=1)
    ledger.record(slot, "atom_bad", 0.0, trial=1)

    result = maybe_apply_bottleneck_variant(
        cdg,
        bottleneck_name="Step",
        atom_ledger=ledger,
        catalog=catalog,
    )

    assert result.applied is False
    assert result.family == "ledger_bandit"


def test_ledger_can_swap_to_cross_family_candidate_when_structurally_compatible() -> None:
    from sciona.architect.catalog import PrimitiveCatalog

    ledger = AtomLedger()
    ports_in = [IOSpec(name="x", type_desc="np.ndarray")]
    ports_out = [IOSpec(name="y", type_desc="np.ndarray")]
    catalog = PrimitiveCatalog()
    catalog.add(
        AlgorithmicPrimitive(
            name="atom_local",
            source="test.local",
            category=ConceptType.CUSTOM,
            description="local atom",
            inputs=ports_in,
            outputs=ports_out,
            type_signature="test",
        )
    )
    catalog.add(
        AlgorithmicPrimitive(
            name="atom_foreign",
            source="test.foreign",
            category=ConceptType.SIGNAL_FILTER,
            description="foreign atom",
            inputs=ports_in,
            outputs=ports_out,
            type_signature="test",
        )
    )

    cdg = CDGExport(
        nodes=[
            _generic_node(
                "n1",
                "Step",
                matched_primitive="atom_local",
                concept_type=ConceptType.CUSTOM,
            )
        ],
        edges=[],
    )

    node = cdg.nodes[0]
    slot = compute_slot_signature(node, None)
    for t in range(5):
        ledger.record(slot, "atom_local", 80.0, trial=t)
        ledger.record(slot, "atom_foreign", 10.0, trial=t)

    result = maybe_apply_bottleneck_variant(
        cdg,
        bottleneck_name="Step",
        atom_ledger=ledger,
        catalog=catalog,
    )

    assert result.applied is True
    assert result.family == "ledger_bandit"
    assert result.variant_name == "atom_foreign"
    assert result.cdg.nodes[0].matched_primitive == "atom_foreign"


def test_ledger_prefers_same_family_when_scores_are_tied() -> None:
    from sciona.architect.catalog import PrimitiveCatalog

    ledger = AtomLedger()
    ports_in = [IOSpec(name="x", type_desc="np.ndarray")]
    ports_out = [IOSpec(name="y", type_desc="np.ndarray")]
    catalog = PrimitiveCatalog()
    catalog.add(
        AlgorithmicPrimitive(
            name="atom_local",
            source="test.local",
            category=ConceptType.CUSTOM,
            description="local atom",
            inputs=ports_in,
            outputs=ports_out,
            type_signature="test",
        )
    )
    catalog.add(
        AlgorithmicPrimitive(
            name="atom_foreign",
            source="test.foreign",
            category=ConceptType.SIGNAL_FILTER,
            description="foreign atom",
            inputs=ports_in,
            outputs=ports_out,
            type_signature="test",
        )
    )

    cdg = CDGExport(
        nodes=[
            _generic_node(
                "n1",
                "Step",
                matched_primitive="atom_local",
                concept_type=ConceptType.CUSTOM,
            )
        ],
        edges=[],
    )

    node = cdg.nodes[0]
    slot = compute_slot_signature(node, None)
    for t in range(5):
        ledger.record(slot, "atom_local", 10.0, trial=t)
        ledger.record(slot, "atom_foreign", 10.0, trial=t)

    result = maybe_apply_bottleneck_variant(
        cdg,
        bottleneck_name="Step",
        atom_ledger=ledger,
        catalog=catalog,
    )

    assert result.applied is False
    assert result.family == "ledger_bandit"
    assert result.allow_redecompose is True


def test_backward_compatible_without_ledger() -> None:
    cdg = CDGExport(
        nodes=[_generic_node("n1", "Step", matched_primitive="some_atom")],
        edges=[],
    )

    # No ledger, no catalog — should not error, just return no-op
    result = maybe_apply_bottleneck_variant(
        cdg,
        bottleneck_name="Step",
    )

    assert result.applied is False
    assert result.family is None
