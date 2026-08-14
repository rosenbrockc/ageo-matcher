"""Tests for the ghost witness simulation pass in the synthesizer pipeline."""

from __future__ import annotations


import importlib

import pytest

from sciona.ghost.simulator import PlanError, SimNode, simulate_graph

from sciona.architect.handoff import CDGExport
from sciona.architect.models import (
    AlgorithmicNode,
    ConceptType,
    DependencyEdge,
    IOSpec,
    NodeStatus,
)
from sciona.synthesizer.ghost_sim import (
    GhostSimReport,
    _compute_precision_gradients,
    _build_abstract_value,
    _candidate_ghost_package_roots,
    _declared_param_names,
    _ensure_atoms_imported,
    _extract_atom_name,
    _load_ghost_backend,
    _parse_raw_code_param_names,
    _resolve_source_output_name,
    _GHOST_AVAILABLE,
    list_registered,
    run_ghost_simulation,
)
from sciona.synthesizer.uncertainty import HeuristicBackend

from sciona.types import (
    CandidateMatch,
    Declaration,
    MatchResult,
    PDGNode,
    Prover,
    VerificationResult,
)

def _has_dsp_witnesses() -> bool:
    """Check if DSP-specific witnesses (fft, etc.) are in the registry."""
    try:
        from sciona.ghost.registry import REGISTRY
        return "fft" in REGISTRY
    except Exception:
        return False


_requires_registered_atoms = pytest.mark.skipif(
    not _has_dsp_witnesses(), reason="DSP atom witnesses not registered (atom providers not installed)"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_match(node_id: str, decl_name: str, type_sig: str = "") -> MatchResult:
    decl = Declaration(name=decl_name, type_signature=type_sig, prover=Prover.PYTHON)
    candidate = CandidateMatch(declaration=decl, score=0.9, retrieval_method="test")
    vr = VerificationResult(candidate=candidate, verified=True)
    return MatchResult(
        pdg_node=PDGNode(predicate_id=node_id, statement=type_sig),
        verified_match=vr,
        all_candidates=[candidate],
        all_verifications=[vr],
    )


# ---------------------------------------------------------------------------
# TestExtractAtomName
# ---------------------------------------------------------------------------


class TestExtractAtomName:
    def test_simple_name(self):
        assert _extract_atom_name("fft") == "fft"

    def test_qualified_name(self):
        assert _extract_atom_name("sciona.atoms.numpy.fft.fft") == "fft"

    def test_two_part_name(self):
        assert _extract_atom_name("signal.butter") == "butter"

    def test_dotted_module(self):
        assert (
            _extract_atom_name("scipy.sparse.csgraph.graph_laplacian")
            == "graph_laplacian"
        )


class TestGhostSignatureParsing:
    def test_parse_raw_code_param_names_ignores_state_and_defaults(self):
        raw_code = """
@register_atom(witness_r_peak_detection)
def r_peak_detection(filtered: np.ndarray, sampling_rate: float = 1000.0, state: ECGPipelineState | None = None) -> np.ndarray:
    return filtered
"""
        assert _parse_raw_code_param_names(raw_code) == ["filtered", "sampling_rate"]

    def test_declared_param_names_fall_back_to_raw_code_when_type_signature_is_unlabeled(self):
        decl = Declaration(
            name="sciona.atoms.signal_processing.biosppy.ecg.r_peak_detection",
            type_signature="np.ndarray, float -> np.ndarray",
            raw_code="""
@register_atom(witness_r_peak_detection)
def r_peak_detection(filtered: np.ndarray, sampling_rate: float = 1000.0, state: ECGPipelineState | None = None) -> np.ndarray:
    return filtered
""",
            prover=Prover.PYTHON,
        )
        candidate = CandidateMatch(declaration=decl, score=0.9, retrieval_method="test")
        vr = VerificationResult(candidate=candidate, verified=True)
        match = MatchResult(
            pdg_node=PDGNode(predicate_id="detect_node", statement=""),
            verified_match=vr,
            all_candidates=[candidate],
            all_verifications=[vr],
        )

        assert _declared_param_names(match) == {"filtered", "sampling_rate"}

    def test_resolve_source_output_name_accepts_aliases(self):
        source = AlgorithmicNode(
            node_id="filter_node",
            name="Filter",
            description="Filter signal",
            concept_type=ConceptType.SIGNAL_FILTER,
            status=NodeStatus.ATOMIC,
            outputs=[
                IOSpec(
                    name="conditioned_signal",
                    type_desc="np.ndarray",
                    constraints="",
                )
            ],
        )

        assert _resolve_source_output_name("signal", source) == "conditioned_signal"
        assert _resolve_source_output_name("filtered", source) == "conditioned_signal"

    def test_resolve_source_output_name_leaves_unknown_names_unchanged(self):
        source = AlgorithmicNode(
            node_id="rate_node",
            name="Rate",
            description="Compute rate",
            concept_type=ConceptType.ANALYSIS,
            status=NodeStatus.ATOMIC,
            outputs=[IOSpec(name="score", type_desc="float", constraints="")],
        )

        assert _resolve_source_output_name("rate", source) == "rate"


# ---------------------------------------------------------------------------
# TestBuildAbstractValue
# ---------------------------------------------------------------------------


@_requires_registered_atoms
class TestBuildAbstractValue:
    def test_signal_default(self):
        val = _build_abstract_value("np.ndarray", "time domain")
        assert val.domain == "time"
        assert val.dtype == "float64"

    def test_freq_domain_from_constraints(self):
        val = _build_abstract_value("np.ndarray", "freq domain")
        assert val.domain == "freq"

    def test_complex_dtype(self):
        val = _build_abstract_value("complex array", "")
        assert val.dtype == "complex128"

    def test_graph_type(self):
        val = _build_abstract_value("Graph Laplacian", "symmetric")
        assert hasattr(val, "n_nodes")
        assert val.is_symmetric is True

    def test_filter_coefficients(self):
        val = _build_abstract_value("Filter coefficients", "")
        assert hasattr(val, "order")
        assert val.is_stable is True


# ---------------------------------------------------------------------------
# TestGhostSimReport
# ---------------------------------------------------------------------------


class TestGhostSimReport:
    def test_default_report(self):
        report = GhostSimReport()
        assert report.ran is False
        assert report.passed is False
        assert report.node_count == 0
        assert report.error == ""


    def test_ensure_atoms_imported_fallback_tries_recognized_prefixes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        attempted: list[str] = []

        def _fail_load_sources():
            raise RuntimeError("missing sources config")

        def _capture_import(name: str):
            attempted.append(name)
            raise ImportError(name)

        monkeypatch.setenv("SCIONA_ATOM_PACKAGE_PREFIXES", "custom.provider")
        monkeypatch.setattr("sciona.sources.load_sources", _fail_load_sources)
        monkeypatch.setattr(importlib, "import_module", _capture_import)

        _ensure_atoms_imported()

        assert "custom.provider.numpy.fft" in attempted
        assert "sciona.atoms.numpy.fft" in attempted

    def test_candidate_ghost_package_roots_prioritize_configured_sources(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _FakeSource:
            def __init__(self, package: str):
                self.package = package

        class _FakeConfig:
            sources = [
                _FakeSource("sciona.atoms.demo"),
            ]

        monkeypatch.setattr(
            "sciona.sources.load_sources",
            lambda: _FakeConfig(),
        )
        monkeypatch.setenv("SCIONA_ATOM_PACKAGE_PREFIXES", "custom.provider")

        roots = _candidate_ghost_package_roots()
        # sciona is always first (matcher ships sciona.ghost)
        assert roots[0] == "sciona"
        # configured source comes next
        assert "sciona.atoms.demo" in roots
        assert "custom.provider" in roots

    def test_load_ghost_backend_uses_configured_provider_root_first(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _FakeSource:
            def __init__(self, package: str):
                self.package = package

        class _FakeConfig:
            sources = [_FakeSource("sciona.atoms.demo")]

        attempted: list[str] = []

        _fake_module = type(
            "_Module",
            (),
            {
                "SimNode": object,
                "SimResult": object,
                "simulate_graph": lambda *args, **kwargs: None,
                "PlanError": RuntimeError,
                "AbstractDistribution": object,
                "AbstractSignal": object,
                "AbstractMatrix": object,
                "list_registered": lambda: (),
                "AbstractFilterCoefficients": object,
                "AbstractGraphMeta": object,
            },
        )()

        def _capture_import(name: str):
            attempted.append(name)
            # Let the configured provider succeed; everything else fails.
            if name.startswith("sciona.atoms.demo.ghost."):
                return _fake_module
            raise ImportError(name)

        monkeypatch.setattr(
            "sciona.sources.load_sources",
            lambda: _FakeConfig(),
        )
        monkeypatch.setattr(importlib, "import_module", _capture_import)
        monkeypatch.setattr(
            "sciona.synthesizer.ghost_sim.configure_juliacall_env",
            lambda: None,
        )

        backend = _load_ghost_backend()

        assert backend is not None
        # sciona is tried first but fails (mocked import_module raises),
        # then configured source succeeds.
        assert backend[0] == "sciona.atoms.demo"
        assert attempted[0] == "sciona.ghost.simulator"
        assert "sciona.atoms.demo.ghost.simulator" in attempted


class TestGhostSimulatorOutputs:
    def test_multi_output_witness_populates_every_declared_state_key(self):
        node = SimNode(
            name="Split table",
            function_name="split_table",
            inputs={"dataset": "root:dataset"},
            output_name="split:X_train",
            output_names=[
                "split:X_train",
                "split:X_test",
                "split:y_train",
                "split:y_test",
            ],
        )

        result = simulate_graph(
            [node],
            {"root:dataset": "table"},
            witness_overrides={
                "split_table": lambda dataset: (
                    dataset,
                    "test",
                    "train-y",
                    "test-y",
                )
            },
        )

        assert result.final_state["split:X_train"] == "table"
        assert result.final_state["split:y_train"] == "train-y"
        assert result.final_state["split:y_test"] == "test-y"

    def test_multi_output_witness_rejects_return_arity_mismatch(self):
        node = SimNode(
            name="Split table",
            function_name="split_table",
            output_names=["split:X", "split:y"],
        )

        with pytest.raises(PlanError, match="declares 2 outputs.*returned 1 values"):
            simulate_graph(
                [node],
                {},
                witness_overrides={"split_table": lambda: ("only-one",)},
            )

    def test_single_output_preserves_tuple_valued_payload(self):
        node = SimNode(
            name="Tuple payload",
            function_name="tuple_payload",
            output_name="node:payload",
            output_names=["node:payload"],
        )

        result = simulate_graph(
            [node],
            {},
            witness_overrides={"tuple_payload": lambda: ("left", "right")},
        )

        assert result.final_state["node:payload"] == ("left", "right")


# ---------------------------------------------------------------------------
# TestRunGhostSimulation — FFT pipeline
# ---------------------------------------------------------------------------


@_requires_registered_atoms
class TestRunGhostSimulationFFT:
    """Test ghost simulation with a simple FFT -> IFFT pipeline."""

    @pytest.fixture
    def fft_cdg(self) -> CDGExport:
        """CDG: root -> fft_node -> ifft_node (frequency domain round-trip)."""
        nodes = [
            AlgorithmicNode(
                node_id="root",
                name="FFT Round Trip",
                description="Forward FFT then inverse FFT",
                concept_type=ConceptType.SIGNAL_TRANSFORM,
                status=NodeStatus.DECOMPOSED,
                children=["fft_node", "ifft_node"],
                depth=0,
            ),
            AlgorithmicNode(
                node_id="fft_node",
                name="Forward FFT",
                description="Compute FFT of time-domain signal",
                concept_type=ConceptType.SIGNAL_TRANSFORM,
                status=NodeStatus.ATOMIC,
                matched_primitive="fft",
                inputs=[
                    IOSpec(
                        name="sig", type_desc="np.ndarray", constraints="time domain"
                    )
                ],
                outputs=[
                    IOSpec(
                        name="spectrum",
                        type_desc="np.ndarray",
                        constraints="freq domain",
                    )
                ],
                depth=1,
            ),
            AlgorithmicNode(
                node_id="ifft_node",
                name="Inverse FFT",
                description="Compute IFFT to reconstruct signal",
                concept_type=ConceptType.SIGNAL_TRANSFORM,
                status=NodeStatus.ATOMIC,
                matched_primitive="ifft",
                inputs=[
                    IOSpec(
                        name="sig", type_desc="np.ndarray", constraints="freq domain"
                    )
                ],
                outputs=[
                    IOSpec(
                        name="reconstructed",
                        type_desc="np.ndarray",
                        constraints="time domain",
                    )
                ],
                depth=1,
            ),
        ]
        edges = [
            DependencyEdge(
                source_id="fft_node",
                target_id="ifft_node",
                output_name="spectrum",
                input_name="sig",
                source_type="np.ndarray",
                target_type="np.ndarray",
            ),
        ]
        return CDGExport(nodes=nodes, edges=edges, metadata={"goal": "FFT round trip"})

    @pytest.fixture
    def fft_matches(self) -> list[MatchResult]:
        return [
            _make_match("fft_node", "sciona.atoms.numpy.fft.fft"),
            _make_match("ifft_node", "sciona.atoms.numpy.fft.ifft"),
        ]

    def test_fft_roundtrip_passes(self, fft_cdg, fft_matches):
        report = run_ghost_simulation(fft_cdg, fft_matches)
        assert report.ran is True
        assert report.passed is True
        assert report.node_count == 2
        assert len(report.trace) == 2
        assert report.error == ""

    def test_fft_roundtrip_trace_order(self, fft_cdg, fft_matches):
        report = run_ghost_simulation(fft_cdg, fft_matches)
        assert report.trace == ["Forward FFT", "Inverse FFT"]


@_requires_registered_atoms
class TestRunGhostSimulationDomainMismatch:
    """Test that domain mismatches are caught."""

    @pytest.fixture
    def bad_cdg(self) -> CDGExport:
        """CDG where FFT output (freq) is fed into another FFT (expects time)."""
        nodes = [
            AlgorithmicNode(
                node_id="root",
                name="Double FFT (invalid)",
                description="Two FFTs chained — invalid!",
                concept_type=ConceptType.SIGNAL_TRANSFORM,
                status=NodeStatus.DECOMPOSED,
                children=["fft1", "fft2"],
                depth=0,
            ),
            AlgorithmicNode(
                node_id="fft1",
                name="First FFT",
                description="First forward FFT",
                concept_type=ConceptType.SIGNAL_TRANSFORM,
                status=NodeStatus.ATOMIC,
                matched_primitive="fft",
                inputs=[
                    IOSpec(
                        name="sig", type_desc="np.ndarray", constraints="time domain"
                    )
                ],
                outputs=[
                    IOSpec(
                        name="spectrum",
                        type_desc="np.ndarray",
                        constraints="freq domain",
                    )
                ],
                depth=1,
            ),
            AlgorithmicNode(
                node_id="fft2",
                name="Second FFT",
                description="Second forward FFT (should fail — freq input)",
                concept_type=ConceptType.SIGNAL_TRANSFORM,
                status=NodeStatus.ATOMIC,
                matched_primitive="fft",
                inputs=[
                    IOSpec(
                        name="sig", type_desc="np.ndarray", constraints="freq domain"
                    )
                ],
                outputs=[
                    IOSpec(
                        name="spectrum2",
                        type_desc="np.ndarray",
                        constraints="freq domain",
                    )
                ],
                depth=1,
            ),
        ]
        edges = [
            DependencyEdge(
                source_id="fft1",
                target_id="fft2",
                output_name="spectrum",
                input_name="sig",
                source_type="np.ndarray",
                target_type="np.ndarray",
            ),
        ]
        return CDGExport(nodes=nodes, edges=edges, metadata={"goal": "bad"})

    @pytest.fixture
    def bad_matches(self) -> list[MatchResult]:
        return [
            _make_match("fft1", "fft"),
            _make_match("fft2", "fft"),
        ]

    def test_domain_mismatch_caught(self, bad_cdg, bad_matches):
        report = run_ghost_simulation(bad_cdg, bad_matches)
        assert report.ran is True
        assert report.passed is False
        assert "time" in report.error.lower() or "domain" in report.error.lower()
        assert report.error_node == "Second FFT"
        assert report.error_function == "fft"


@_requires_registered_atoms
class TestRunGhostSimulationNoWitness:
    """Test behaviour when atoms have no registered witness."""

    @pytest.fixture
    def non_dsp_cdg(self) -> CDGExport:
        """CDG with non-DSP atoms (no witnesses)."""
        nodes = [
            AlgorithmicNode(
                node_id="custom_node",
                name="Custom Op",
                description="A custom operation with no witness",
                concept_type=ConceptType.CUSTOM,
                status=NodeStatus.ATOMIC,
                inputs=[IOSpec(name="data", type_desc="list[int]")],
                outputs=[IOSpec(name="result", type_desc="list[int]")],
                depth=0,
            ),
        ]
        return CDGExport(nodes=nodes, edges=[], metadata={})

    @pytest.fixture
    def non_dsp_matches(self) -> list[MatchResult]:
        return [_make_match("custom_node", "my_custom_unregistered_op")]

    def test_no_simulable_nodes(self, non_dsp_cdg, non_dsp_matches):
        report = run_ghost_simulation(non_dsp_cdg, non_dsp_matches)
        # Should not crash; just reports no simulation ran
        assert report.ran is False
        assert report.skipped_nodes == ["Custom Op"]


@_requires_registered_atoms
class TestRunGhostSimulationFilter:
    """Test ghost simulation with a filter design -> apply pipeline."""

    @pytest.fixture
    def filter_cdg(self) -> CDGExport:
        """CDG: root -> butter_node -> lfilter_node."""
        nodes = [
            AlgorithmicNode(
                node_id="root",
                name="Lowpass Filter Pipeline",
                description="Design and apply a Butterworth filter",
                concept_type=ConceptType.SIGNAL_FILTER,
                status=NodeStatus.DECOMPOSED,
                children=["butter_node", "lfilter_node"],
                depth=0,
            ),
            AlgorithmicNode(
                node_id="butter_node",
                name="Butterworth Design",
                description="Design a Butterworth lowpass filter",
                concept_type=ConceptType.SIGNAL_FILTER,
                status=NodeStatus.ATOMIC,
                matched_primitive="butter",
                inputs=[
                    IOSpec(name="order", type_desc="int", constraints=""),
                    IOSpec(name="wn", type_desc="float", constraints=""),
                    IOSpec(name="fs", type_desc="float", constraints=""),
                ],
                outputs=[IOSpec(name="coefficients", type_desc="Filter coefficients")],
                depth=1,
            ),
            AlgorithmicNode(
                node_id="lfilter_node",
                name="Apply Filter",
                description="Apply the designed filter to a signal",
                concept_type=ConceptType.SIGNAL_FILTER,
                status=NodeStatus.ATOMIC,
                matched_primitive="lfilter",
                inputs=[
                    IOSpec(name="coefficients", type_desc="Filter coefficients"),
                    IOSpec(
                        name="sig", type_desc="np.ndarray", constraints="time domain"
                    ),
                ],
                outputs=[
                    IOSpec(
                        name="filtered",
                        type_desc="np.ndarray",
                        constraints="time domain",
                    )
                ],
                depth=1,
            ),
        ]
        edges = [
            DependencyEdge(
                source_id="butter_node",
                target_id="lfilter_node",
                output_name="coefficients",
                input_name="coefficients",
                source_type="Filter coefficients",
                target_type="Filter coefficients",
            ),
        ]
        return CDGExport(nodes=nodes, edges=edges, metadata={"goal": "Filter pipeline"})

    @pytest.fixture
    def filter_matches(self) -> list[MatchResult]:
        return [
            _make_match("butter_node", "scipy.signal.butter"),
            _make_match("lfilter_node", "scipy.signal.lfilter"),
        ]

    def test_filter_pipeline_passes(self, filter_cdg, filter_matches):
        report = run_ghost_simulation(filter_cdg, filter_matches)
        assert report.ran is True
        assert report.passed is True
        assert report.node_count == 2


@_requires_registered_atoms
class TestRunGhostSimulationMixed:
    """Test CDG with a mix of DSP and non-DSP nodes."""

    @pytest.fixture
    def mixed_cdg(self) -> CDGExport:
        """CDG with one DSP node and one non-DSP node (unregistered), independent."""
        nodes = [
            AlgorithmicNode(
                node_id="fft_node",
                name="FFT",
                description="Forward FFT",
                concept_type=ConceptType.SIGNAL_TRANSFORM,
                status=NodeStatus.ATOMIC,
                matched_primitive="fft",
                inputs=[
                    IOSpec(
                        name="sig", type_desc="np.ndarray", constraints="time domain"
                    )
                ],
                outputs=[IOSpec(name="spectrum", type_desc="np.ndarray")],
                depth=0,
            ),
            AlgorithmicNode(
                node_id="custom_node",
                name="Custom Op",
                description="A custom unregistered operation",
                concept_type=ConceptType.CUSTOM,
                status=NodeStatus.ATOMIC,
                inputs=[IOSpec(name="data", type_desc="list[int]")],
                outputs=[IOSpec(name="result", type_desc="list[int]")],
                depth=0,
            ),
        ]
        return CDGExport(nodes=nodes, edges=[], metadata={})

    @pytest.fixture
    def mixed_matches(self) -> list[MatchResult]:
        return [
            _make_match("fft_node", "fft"),
            _make_match("custom_node", "my_custom_unregistered_op"),
        ]

    def test_mixed_simulates_dsp_only(self, mixed_cdg, mixed_matches):
        report = run_ghost_simulation(mixed_cdg, mixed_matches)
        assert report.ran is True
        assert report.passed is True
        assert report.node_count == 1
        assert "Custom Op" in report.skipped_nodes
        assert "FFT" in report.trace


# ---------------------------------------------------------------------------
# TestPrecisionGradientRoundTrip — Phase 1 verification
# ---------------------------------------------------------------------------


class TestPrecisionGradientRoundTrip:
    """Verify that the refactored backend produces identical precision gradients."""

    @pytest.fixture
    def fft_cdg(self) -> CDGExport:
        nodes = [
            AlgorithmicNode(
                node_id="root",
                name="FFT Round Trip",
                description="Forward FFT then inverse FFT",
                concept_type=ConceptType.SIGNAL_TRANSFORM,
                status=NodeStatus.DECOMPOSED,
                children=["fft_node", "ifft_node"],
                depth=0,
            ),
            AlgorithmicNode(
                node_id="fft_node",
                name="Forward FFT",
                description="FFT",
                concept_type=ConceptType.SIGNAL_TRANSFORM,
                status=NodeStatus.ATOMIC,
                inputs=[IOSpec(name="sig", type_desc="np.ndarray", constraints="time domain")],
                outputs=[IOSpec(name="spectrum", type_desc="np.ndarray", constraints="freq domain")],
                depth=1,
            ),
            AlgorithmicNode(
                node_id="ifft_node",
                name="Inverse FFT",
                description="IFFT",
                concept_type=ConceptType.SIGNAL_TRANSFORM,
                status=NodeStatus.ATOMIC,
                inputs=[IOSpec(name="sig", type_desc="np.ndarray", constraints="freq domain")],
                outputs=[IOSpec(name="out", type_desc="np.ndarray", constraints="time domain")],
                depth=1,
            ),
        ]
        edges = [
            DependencyEdge(
                source_id="fft_node", target_id="ifft_node",
                output_name="spectrum", input_name="sig",
                source_type="np.ndarray", target_type="np.ndarray",
            ),
        ]
        return CDGExport(nodes=nodes, edges=edges, metadata={})

    @pytest.fixture
    def fft_matches(self) -> dict[str, MatchResult]:
        return {
            "fft_node": _make_match("fft_node", "sciona.atoms.numpy.fft.fft"),
            "ifft_node": _make_match("ifft_node", "sciona.atoms.numpy.fft.ifft"),
        }

    @pytest.fixture
    def filter_cdg(self) -> CDGExport:
        nodes = [
            AlgorithmicNode(
                node_id="root",
                name="Filter Pipeline",
                description="butter -> lfilter",
                concept_type=ConceptType.SIGNAL_FILTER,
                status=NodeStatus.DECOMPOSED,
                children=["butter_node", "lfilter_node"],
                depth=0,
            ),
            AlgorithmicNode(
                node_id="butter_node",
                name="Butterworth Design",
                description="Design filter",
                concept_type=ConceptType.SIGNAL_FILTER,
                status=NodeStatus.ATOMIC,
                inputs=[
                    IOSpec(name="order", type_desc="int"),
                    IOSpec(name="wn", type_desc="float"),
                ],
                outputs=[IOSpec(name="coefficients", type_desc="Filter coefficients")],
                depth=1,
            ),
            AlgorithmicNode(
                node_id="lfilter_node",
                name="Apply Filter",
                description="Apply filter",
                concept_type=ConceptType.SIGNAL_FILTER,
                status=NodeStatus.ATOMIC,
                inputs=[
                    IOSpec(name="coefficients", type_desc="Filter coefficients"),
                    IOSpec(name="sig", type_desc="np.ndarray", constraints="time domain"),
                ],
                outputs=[IOSpec(name="filtered", type_desc="np.ndarray", constraints="time domain")],
                depth=1,
            ),
        ]
        edges = [
            DependencyEdge(
                source_id="butter_node", target_id="lfilter_node",
                output_name="coefficients", input_name="coefficients",
                source_type="Filter coefficients", target_type="Filter coefficients",
            ),
        ]
        return CDGExport(nodes=nodes, edges=edges, metadata={})

    @pytest.fixture
    def filter_matches(self) -> dict[str, MatchResult]:
        return {
            "butter_node": _make_match("butter_node", "scipy.signal.butter"),
            "lfilter_node": _make_match("lfilter_node", "scipy.signal.lfilter"),
        }

    def test_fft_gradients_match_legacy(self, fft_cdg, fft_matches):
        """FFT pipeline gradients must match the legacy hardcoded factors."""
        result = _compute_precision_gradients(
            fft_cdg, fft_matches, ["fft_node", "ifft_node"],
        )
        # FFT: factor 1.5, input width 1.0 -> output width 1.5, delta 0.5
        assert "fft_node" in result.gradients
        assert abs(result.gradients["fft_node"] - 0.5) < 1e-10
        # IFFT: factor 1.5, input width 1.5 -> output width 2.25, delta 0.75
        assert "ifft_node" in result.gradients
        assert abs(result.gradients["ifft_node"] - 0.75) < 1e-10

    def test_filter_gradients_match_legacy(self, filter_cdg, filter_matches):
        """Filter pipeline gradients must match the legacy hardcoded factors."""
        result = _compute_precision_gradients(
            filter_cdg, filter_matches, ["butter_node", "lfilter_node"],
        )
        # lfilter node has numeric input via its second input (np.ndarray)
        # butter_node: int/float inputs don't produce numeric intervals
        # So only lfilter_node should have a non-zero gradient
        assert "lfilter_node" in result.gradients

    def test_unknown_atom_gets_factor_one(self):
        """An unknown atom should use factor 1.0 (no expansion)."""
        nodes = [
            AlgorithmicNode(
                node_id="unknown_node",
                name="Unknown",
                description="Unknown op",
                concept_type=ConceptType.CUSTOM,
                status=NodeStatus.ATOMIC,
                inputs=[IOSpec(name="x", type_desc="np.ndarray")],
                outputs=[IOSpec(name="y", type_desc="np.ndarray")],
                depth=0,
            ),
        ]
        cdg = CDGExport(nodes=nodes, edges=[], metadata={})
        matches = {
            "unknown_node": _make_match("unknown_node", "some.totally.unknown.op"),
        }
        result = _compute_precision_gradients(cdg, matches, ["unknown_node"])
        # factor 1.0 -> output width = input width -> delta = 0.0
        # But input_interval.width > 0, so it should still be recorded
        assert "unknown_node" in result.gradients
        assert result.gradients["unknown_node"] == 0.0
        assert "unknown_node" in result.uncalibrated_nodes
        assert result.node_confidence["unknown_node"] == 0.0

    def test_confidence_populated_for_known_atoms(self, fft_cdg, fft_matches):
        """Known atoms should have non-zero confidence."""
        result = _compute_precision_gradients(
            fft_cdg, fft_matches, ["fft_node", "ifft_node"],
        )
        assert result.node_confidence["fft_node"] == 0.2  # heuristic confidence
        assert result.node_confidence["ifft_node"] == 0.2
        assert result.uncalibrated_nodes == []
