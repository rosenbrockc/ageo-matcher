"""Ghost Witness simulation pass for the synthesizer pipeline.

Converts a CDG + MatchResults into ghost SimNodes and runs the abstract
simulation to catch structural mismatches (shape, dtype, domain) before
committing to expensive compilation.

Requires a compatible ``<package>.ghost`` backend (optional dependency). If no
configured provider exposes one, the simulation pass is silently skipped.
"""

from __future__ import annotations

import importlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from collections import deque

from sciona.architect.handoff import CDGExport
from sciona.architect.models import (
    AlgorithmicNode,
    ConceptType,
    DependencyEdge,
    NodeStatus,
)
from sciona.atom_identity import known_atom_package_prefixes
from sciona.julia_runtime import configure_juliacall_env
from sciona.synthesizer.toposort import detect_cycle_partition, toposort_nodes
from sciona.synthesizer.uncertainty import (
    AtomUncertaintyEstimate,
    HeuristicBackend,
    UncertaintyBackend,
)
from sciona.types import MatchResult

logger = logging.getLogger(__name__)

_GHOST_PACKAGE_ROOT = ""


class PlanError(Exception):
    """Fallback placeholder when no ghost backend is available."""


SimNode: Any = object
SimResult: Any = object
simulate_graph: Any = None
AbstractDistribution: Any = object
AbstractSignal: Any = object
AbstractMatrix: Any = object
AbstractFilterCoefficients: Any = object
AbstractGraphMeta: Any = object
list_registered: Any = lambda: ()


def _candidate_ghost_package_roots() -> tuple[str, ...]:
    """Return candidate package roots that may expose ``ghost`` modules.

    ``sciona`` is always the first candidate (the matcher ships
    ``sciona.ghost``).  Additional roots are drawn from configured
    sources and known atom-package prefixes.
    """
    candidates: list[str] = ["sciona"]
    seen: set[str] = {"sciona"}

    try:
        from sciona.sources import load_sources

        config = load_sources()
        for source in config.sources:
            package = str(source.package or "").strip()
            if not package or package in seen:
                continue
            seen.add(package)
            candidates.append(package)
    except Exception:
        logger.debug("Failed to load configured ghost package roots", exc_info=True)

    for prefix in known_atom_package_prefixes():
        package = str(prefix or "").strip()
        if not package or package in seen:
            continue
        seen.add(package)
        candidates.append(package)

    return tuple(candidates)


def _load_ghost_backend() -> tuple[str, dict[str, Any]] | None:
    """Import the first available ghost backend across configured package roots."""
    configure_juliacall_env()
    for root in _candidate_ghost_package_roots():
        try:
            simulator = importlib.import_module(f"{root}.ghost.simulator")
            abstract = importlib.import_module(f"{root}.ghost.abstract")
            registry = importlib.import_module(f"{root}.ghost.registry")
            witnesses = importlib.import_module(f"{root}.ghost.witnesses")
        except ImportError:
            continue
        return root, {
            "SimNode": simulator.SimNode,
            "SimResult": simulator.SimResult,
            "simulate_graph": simulator.simulate_graph,
            "PlanError": simulator.PlanError,
            "AbstractDistribution": abstract.AbstractDistribution,
            "AbstractSignal": abstract.AbstractSignal,
            "AbstractMatrix": abstract.AbstractMatrix,
            "list_registered": registry.list_registered,
            "AbstractFilterCoefficients": witnesses.AbstractFilterCoefficients,
            "AbstractGraphMeta": witnesses.AbstractGraphMeta,
        }
    return None


_ghost_backend = _load_ghost_backend()
if _ghost_backend is None:
    _GHOST_AVAILABLE = False
else:
    _GHOST_PACKAGE_ROOT, _ghost_symbols = _ghost_backend
    SimNode = _ghost_symbols["SimNode"]
    SimResult = _ghost_symbols["SimResult"]
    simulate_graph = _ghost_symbols["simulate_graph"]
    PlanError = _ghost_symbols["PlanError"]
    AbstractDistribution = _ghost_symbols["AbstractDistribution"]
    AbstractSignal = _ghost_symbols["AbstractSignal"]
    AbstractMatrix = _ghost_symbols["AbstractMatrix"]
    list_registered = _ghost_symbols["list_registered"]
    AbstractFilterCoefficients = _ghost_symbols["AbstractFilterCoefficients"]
    AbstractGraphMeta = _ghost_symbols["AbstractGraphMeta"]
    _GHOST_AVAILABLE = True


_LEGACY_ATOM_IMPORT_SUFFIXES: tuple[str, ...] = (
    "numpy.fft",
    "scipy.fft",
    "scipy.signal",
    "scipy.sparse_graph",
    "algorithms.sorting",
    "algorithms.graph",
    "algorithms.search",
)


def _fallback_atom_modules() -> tuple[str, ...]:
    modules: list[str] = []
    seen: set[str] = set()
    for prefix in known_atom_package_prefixes():
        for suffix in _LEGACY_ATOM_IMPORT_SUFFIXES:
            module_name = f"{prefix}.{suffix}"
            if module_name in seen:
                continue
            seen.add(module_name)
            modules.append(module_name)
    return tuple(modules)


@dataclass
class ErrorInterval:
    """Interval arithmetic bounds for numerical approximation error."""

    lo: float = 0.0
    hi: float = 0.0

    @property
    def width(self) -> float:
        """Width of the error interval (hi - lo)."""
        return self.hi - self.lo


@dataclass
class GhostSimReport:
    """Result of the ghost simulation pass."""

    ran: bool = False
    """Whether the simulation actually executed (False if no ghost backend available)."""
    passed: bool = False
    """Whether the simulation completed without errors."""
    node_count: int = 0
    """Number of nodes that were simulated."""
    skipped_nodes: list[str] = field(default_factory=list)
    """Names of nodes that were skipped (no registered witness)."""
    trace: list[str] = field(default_factory=list)
    """Ordered list of node names that were simulated."""
    error: str = ""
    """Human-readable error if the simulation failed."""
    error_node: str = ""
    """Name of the node where the simulation failed."""
    error_function: str = ""
    """Function name where the simulation failed."""
    coverage: float = 0.0
    """Ratio of simulable / total atomic nodes (0.0 - 1.0)."""
    precision_gradients: dict[str, float] = field(default_factory=dict)
    """Per-node error expansion (output interval width - input interval width)."""
    uncalibrated_nodes: list[str] = field(default_factory=list)
    """Node IDs whose uncertainty estimate has mode='unknown'."""
    node_confidence: dict[str, float] = field(default_factory=dict)
    """Per-node confidence in the uncertainty estimate (0.0-1.0)."""
    cyclic_deadlock: bool = False
    """True if iterative message passing detected an unbroken cycle."""
    deadlock_nodes: list[str] = field(default_factory=list)
    """Node names involved in the deadlocked cycle."""
    iterations_used: int = 0
    """Number of message-passing iterations before convergence/deadlock."""
    signal_length: int = 0
    """Input signal length when structural heuristics need window-count context."""


def _extract_atom_name(declaration_name: str) -> str:
    """Extract the short atom name from a possibly qualified declaration name.

    Examples:
        "sciona.atoms.numpy.fft.fft" -> "fft"
        "numpy.fft.fft" -> "fft"
        "fft" -> "fft"
        "scipy.signal.butter" -> "butter"
    """
    return declaration_name.rsplit(".", 1)[-1]


_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("signal", "conditioned_signal", "filtered", "filtered_signal", "waveform"),
    ("events", "rpeaks", "peaks", "beats", "onsets"),
    ("rate", "heart_rate", "bpm"),
)


def _aliases_for(name: str) -> tuple[str, ...]:
    lowered = str(name).strip().lower()
    for group in _ALIAS_GROUPS:
        if lowered in group:
            return group
    return (lowered,)


def _parse_signature_param_names(type_signature: str) -> list[str]:
    match = re.match(r"\(([^)]*)\)", str(type_signature or "").strip())
    if match is None:
        return []
    params = match.group(1).strip()
    if not params:
        return []
    names: list[str] = []
    for raw in params.split(","):
        chunk = raw.strip()
        if not chunk:
            continue
        name = chunk.split(":", 1)[0].strip().lstrip("*")
        if name:
            names.append(name)
    return names


def _parse_raw_code_param_names(raw_code: str) -> list[str]:
    match = re.search(
        r"def\s+\w+\s*\((.*?)\)\s*(?:->[^:]+)?\s*:",
        str(raw_code or ""),
        re.DOTALL,
    )
    if match is None:
        return []
    params = match.group(1).strip()
    if not params:
        return []
    names: list[str] = []
    for raw in params.split(","):
        chunk = raw.strip()
        if not chunk:
            continue
        name = chunk.split(":", 1)[0].split("=", 1)[0].strip().lstrip("*")
        if not name or name in {"self", "cls", "state"}:
            continue
        names.append(name)
    return names


def _declared_param_names(match_result: MatchResult) -> set[str]:
    verified = match_result.verified_match
    if verified is None:
        return set()
    declaration = verified.candidate.declaration
    names = _parse_signature_param_names(declaration.type_signature)
    if not names:
        names = _parse_raw_code_param_names(declaration.raw_code)
    return set(names)


def _resolve_signature_param_name(port_name: str, declared_names: set[str]) -> str:
    if not declared_names:
        return port_name
    lowered_map = {str(name).lower(): name for name in declared_names}
    if port_name.lower() in lowered_map:
        return lowered_map[port_name.lower()]
    for alias in _aliases_for(port_name):
        if alias in lowered_map:
            return lowered_map[alias]
    return port_name


def _resolve_source_output_name(
    requested_name: str,
    source_node: AlgorithmicNode | None,
) -> str:
    """Resolve an edge output label to the source node's declared output port."""
    if source_node is None or not source_node.outputs:
        return requested_name
    lowered_map = {
        str(output.name).lower(): output.name
        for output in source_node.outputs
        if output.name
    }
    if requested_name.lower() in lowered_map:
        return lowered_map[requested_name.lower()]
    for alias in _aliases_for(requested_name):
        if alias in lowered_map:
            return lowered_map[alias]
    return requested_name


def _build_abstract_value(type_desc: str, constraints: str) -> Any:
    """Build an abstract value from IOSpec metadata.

    Parses type_desc and constraints to construct the appropriate
    abstract type (AbstractSignal, AbstractFilterCoefficients, or
    AbstractGraphMeta).  Falls back to a generic AbstractSignal.
    """
    if not _GHOST_AVAILABLE:
        return None

    type_lower = type_desc.lower()
    constraints_lower = constraints.lower()

    # Primitive scalar types — return sensible defaults
    if type_lower in ("int", "integer"):
        return 4
    if type_lower in ("float", "double", "number"):
        return 1000.0
    if type_lower in ("str", "string"):
        return "low"
    if type_lower in ("bool", "boolean"):
        return True

    # Graph / Laplacian
    if any(kw in type_lower for kw in ("graph", "laplacian", "adjacency")):
        n_nodes = 10  # default
        for part in constraints_lower.replace(",", " ").split():
            if part.startswith("n=") or part.startswith("n_nodes="):
                try:
                    n_nodes = int(part.split("=", 1)[1])
                except ValueError:
                    pass
        symmetric = (
            "symmetric" in constraints_lower or "undirected" in constraints_lower
        )
        return AbstractGraphMeta(n_nodes=n_nodes, is_symmetric=symmetric)

    # Filter coefficients
    if any(kw in type_lower for kw in ("filter", "coefficients", "sos")):
        order = 4  # default
        return AbstractFilterCoefficients(order=order, btype="low", is_stable=True)

    # Default: treat as signal
    shape = (1024,)
    dtype = "float64"
    sampling_rate = 44100.0
    domain = "time"
    units = "amplitude"

    # Parse constraints for hints
    if "freq" in constraints_lower:
        domain = "freq"
    if "complex" in constraints_lower or "complex" in type_lower:
        dtype = "complex128"
    if "int" in type_lower and "point" not in type_lower:
        dtype = "int64"

    # Parse shape hints like "shape=(512,)" or "(1024,)"
    for token in constraints_lower.replace(",", " ").split():
        if token.startswith("shape="):
            try:
                shape_str = token.split("=", 1)[1].strip("()")
                shape = tuple(int(x) for x in shape_str.split(",") if x.strip())
            except (ValueError, IndexError):
                pass

    # Parse sampling_rate hints
    for token in constraints_lower.replace(",", " ").split():
        if (
            token.startswith("fs=")
            or token.startswith("sr=")
            or token.startswith("sampling_rate=")
        ):
            try:
                sampling_rate = float(token.split("=", 1)[1])
            except ValueError:
                pass

    return AbstractSignal(
        shape=shape,
        dtype=dtype,
        sampling_rate=sampling_rate,
        domain=domain,
        units=units,
    )


# Parameter name -> sensible default for scalar inputs to witnesses.
# These override the generic _build_abstract_value defaults when the
# IOSpec type is a scalar and the parameter name is semantically known.
_PARAM_DEFAULTS: dict[str, Any] = {
    "order": 4,
    "n": 1024,
    "wn": 100.0,  # cutoff frequency — must be < nyquist
    "fs": 44100.0,  # sampling rate — high default to keep wn < nyquist
    "rp": 1.0,  # passband ripple (dB)
    "rs": 40.0,  # stopband attenuation (dB)
    "btype": "low",
    "numtaps": 65,
    "t": 0.5,  # diffusion time
    "k": 6,  # number of eigenvectors
    "n_freqs": 512,
    "worN": 512,
}


def _build_initial_value(param_name: str, type_desc: str, constraints: str) -> Any:
    """Build an initial abstract value, using parameter-name heuristics for scalars."""
    type_lower = type_desc.lower()
    # Check if this is a known scalar parameter
    if type_lower in (
        "int",
        "integer",
        "float",
        "double",
        "number",
        "str",
        "string",
        "bool",
        "boolean",
    ):
        if param_name in _PARAM_DEFAULTS:
            return _PARAM_DEFAULTS[param_name]
    return _build_abstract_value(type_desc, constraints)


# ---------------------------------------------------------------------------
# Interval arithmetic for precision gradient
# ---------------------------------------------------------------------------

# Default uncertainty backend (lazily initialised on first use).
_DEFAULT_BACKEND: UncertaintyBackend | None = None


def _parse_error_bounds(constraints: str) -> ErrorInterval:
    """Extract ``error_bounds=(lo,hi)`` from an IOSpec constraints string.

    Falls back to a zero-width interval when no bounds are declared.
    """
    match = re.search(
        r"error_bounds\s*=\s*\(\s*([^,]+)\s*,\s*([^)]+)\s*\)", constraints
    )
    if match:
        try:
            return ErrorInterval(lo=float(match.group(1)), hi=float(match.group(2)))
        except ValueError:
            pass
    return ErrorInterval(0.0, 0.0)


def _default_error_interval(type_desc: str) -> ErrorInterval:
    """Return a normalized uncertainty interval for unconstrained numeric inputs."""
    lowered = type_desc.strip().lower()
    numeric_markers = (
        "np.ndarray",
        "numpy.ndarray",
        "ndarray",
        "float",
        "double",
        "int",
        "list[float",
        "list[int",
        "tuple[float",
        "tuple[int",
    )
    if any(marker in lowered for marker in numeric_markers):
        return ErrorInterval(-0.5, 0.5)
    return ErrorInterval(0.0, 0.0)


def _get_default_backend() -> UncertaintyBackend:
    """Return the default heuristic backend (singleton)."""
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = HeuristicBackend()
    return _DEFAULT_BACKEND


@dataclass
class _PrecisionGradientResult:
    """Internal result from precision gradient computation."""

    gradients: dict[str, float] = field(default_factory=dict)
    uncalibrated_nodes: list[str] = field(default_factory=list)
    node_confidence: dict[str, float] = field(default_factory=dict)


def _compute_precision_gradients(
    cdg: CDGExport,
    match_map: dict[str, MatchResult],
    sorted_ids: list[str],
    *,
    backend: UncertaintyBackend | None = None,
    iterations_used: int = 0,
) -> _PrecisionGradientResult:
    """Propagate error intervals through the CDG and return per-node expansion.

    Works without sciona.atoms — purely based on IOSpec metadata and known atom
    error factors.  Returns an empty result when no nodes carry error metadata.

    For nodes inside FIXED_POINT bodies, error intervals are scaled by
    ``sqrt(iterations_used)`` to avoid overweighting iterative paths.
    """
    import math

    if backend is None:
        backend = _get_default_backend()

    node_map: dict[str, AlgorithmicNode] = {n.node_id: n for n in cdg.nodes}
    atomic_leaves = {n.node_id for n in cdg.nodes if n.status == NodeStatus.ATOMIC}

    # Identify nodes that are children of FIXED_POINT parents
    fp_child_ids: set[str] = set()
    for n in cdg.nodes:
        if n.concept_type == ConceptType.FIXED_POINT and n.children:
            fp_child_ids.update(n.children)

    # Build edge index: target_id -> list of incoming edges
    incoming: dict[str, list[DependencyEdge]] = {nid: [] for nid in node_map}
    for edge in cdg.edges:
        if edge.target_id in incoming:
            incoming[edge.target_id].append(edge)

    # Track the output error interval per node
    output_intervals: dict[str, ErrorInterval] = {}
    result = _PrecisionGradientResult()

    for nid in sorted_ids:
        if nid not in atomic_leaves:
            continue
        node = node_map[nid]

        # Determine input interval: merge from incoming edges or parse from IOSpec
        input_interval = ErrorInterval(0.0, 0.0)
        in_edges = incoming.get(nid, [])
        upstream_intervals = [
            output_intervals[e.source_id]
            for e in in_edges
            if e.source_id in output_intervals
        ]
        if upstream_intervals:
            # Union of all upstream intervals
            input_interval = ErrorInterval(
                lo=min(iv.lo for iv in upstream_intervals),
                hi=max(iv.hi for iv in upstream_intervals),
            )
        elif node.inputs:
            # No upstream — seed from the first input's constraints
            parsed = _parse_error_bounds(node.inputs[0].constraints)
            if parsed.width > 0:
                input_interval = parsed
            else:
                input_interval = _default_error_interval(node.inputs[0].type_desc)

        # Look up error factor for this atom via uncertainty backend
        mr = match_map.get(nid)
        if mr and mr.success:
            atom_name = _extract_atom_name(mr.verified_match.candidate.declaration.name)
            est = backend.estimate(atom_name)
            factor = est.scalar_factor if est.scalar_factor is not None else 1.0
            result.node_confidence[nid] = est.confidence
            if est.mode == "unknown":
                result.uncalibrated_nodes.append(nid)
        else:
            factor = 1.0
            result.node_confidence[nid] = 0.0
            result.uncalibrated_nodes.append(nid)

        # Compute output interval
        if input_interval.width > 0:
            out = ErrorInterval(
                lo=input_interval.lo * factor,
                hi=input_interval.hi * factor,
            )
        elif node.outputs:
            # Seed from output constraints if present
            out = _parse_error_bounds(node.outputs[0].constraints)
            if out.width == 0:
                out = ErrorInterval(0.0, 0.0)
        else:
            out = ErrorInterval(0.0, 0.0)

        output_intervals[nid] = out

        # Precision gradient = output width - input width
        delta = out.width - input_interval.width

        # Scale FIXED_POINT body nodes by sqrt(iterations_used)
        # to avoid overweighting iterative paths.
        if nid in fp_child_ids and iterations_used > 1:
            delta /= math.sqrt(iterations_used)

        if delta != 0.0 or input_interval.width > 0 or out.width > 0:
            result.gradients[nid] = delta

    return result


def _detect_message_passing_cycle(
    nodes: list[AlgorithmicNode],
    edges: list[DependencyEdge],
) -> tuple[set[str], bool]:
    """Detect whether a cycle is a valid MESSAGE_PASSING factor graph cycle.

    Returns (cycle_node_ids, is_message_passing).  If Kahn's algorithm
    completes, returns (empty set, False).  If it doesn't, collects the
    remaining nodes and checks whether ALL of them have
    concept_type in {MESSAGE_PASSING, FIXED_POINT}.

    Delegates to :func:`detect_cycle_partition` from ``toposort`` for the
    actual partitioning logic.
    """
    _acyclic, cycle_ids, is_valid = detect_cycle_partition(nodes, edges)
    if not cycle_ids:
        return set(), False
    # ``is_valid`` accepts both MESSAGE_PASSING and FIXED_POINT.
    # For backward compat, only report True when cycle nodes are
    # MESSAGE_PASSING (the caller handles FIXED_POINT differently).
    node_map = {n.node_id: n for n in nodes}
    is_mp = all(
        getattr(node_map.get(nid), "concept_type", None) == ConceptType.MESSAGE_PASSING
        for nid in cycle_ids
        if nid in node_map
    )
    return cycle_ids, is_mp


def _simulate_message_passing_iterative(
    sim_nodes: list[Any],
    initial_state: dict[str, Any],
    max_iterations: int = 100,
    convergence_tol: float = 1e-8,
) -> tuple[Any, int, bool, list[str]]:
    """Run iterative message passing instead of topological sort.

    Returns (SimResult, iterations_used, deadlocked, deadlock_node_names).
    """
    if not _GHOST_AVAILABLE:
        raise RuntimeError("ghost backend required for iterative simulation")

    state = dict(initial_state)
    trace: list[str] = []
    iterations_used = 0

    for iteration in range(max_iterations):
        iterations_used = iteration + 1
        prev_state = dict(state)

        # Execute each node in round-robin order
        for node in sim_nodes:
            try:
                res = simulate_graph([node], state)
                state.update(res.final_state)
                if iteration == 0:
                    trace.append(node.name)
            except PlanError:
                raise
            except Exception as exc:
                raise PlanError(
                    node_name=node.name,
                    function_name=node.function_name,
                    detail=f"Iterative simulation error: {exc}",
                )

        # Check for convergence via memoization state node
        converged = False
        for node in sim_nodes:
            if node.output_name and "converged" in node.output_name.lower():
                val = state.get(node.output_name)
                if val is True:
                    converged = True
                    break
            # Also check tuple outputs from memo nodes
            if "memo" in node.name.lower() and node.output_name:
                val = state.get(node.output_name)
                if isinstance(val, tuple) and len(val) == 2 and val[1] is True:
                    converged = True
                    break

        if converged:
            return (
                SimResult(node_count=len(sim_nodes), final_state=state, trace=trace),
                iterations_used,
                False,
                [],
            )

        # Deadlock detection: no state change and not converged
        state_unchanged = all(state.get(k) is prev_state.get(k) for k in state)
        if state_unchanged and iteration > 0:
            raise PlanError(
                node_name=sim_nodes[0].name if sim_nodes else "unknown",
                function_name="iterative_message_passing",
                detail="Cyclic deadlock: messages unchanged but convergence not reached",
            )

    # Max iterations reached — treat as convergence
    return (
        SimResult(node_count=len(sim_nodes), final_state=state, trace=trace),
        iterations_used,
        False,
        [],
    )


def _simulate_fixed_point_iterative(
    sim_nodes: list[Any],
    initial_state: dict[str, Any],
    max_iterations: int = 100,
    convergence_field: str = "converged",
    convergence_tol: float = 1e-8,
) -> tuple[Any, int, bool, list[str]]:
    """Run iterative fixed-point simulation.

    Generalisation of :func:`_simulate_message_passing_iterative` that accepts
    *max_iterations* and *convergence_field* from the FIXED_POINT node.

    Returns (SimResult, iterations_used, deadlocked, deadlock_node_names).
    """
    if not _GHOST_AVAILABLE:
        raise RuntimeError("ghost backend required for iterative simulation")

    state = dict(initial_state)
    trace: list[str] = []
    iterations_used = 0

    for iteration in range(max_iterations):
        iterations_used = iteration + 1
        prev_state = dict(state)

        for node in sim_nodes:
            try:
                res = simulate_graph([node], state)
                state.update(res.final_state)
                if iteration == 0:
                    trace.append(node.name)
            except PlanError:
                raise
            except Exception as exc:
                raise PlanError(
                    node_name=node.name,
                    function_name=node.function_name,
                    detail=f"Fixed-point iteration error: {exc}",
                )

        # Check convergence via the configured field name
        converged = False
        for node in sim_nodes:
            if node.output_name:
                # Check exact field match
                if convergence_field and convergence_field in node.output_name.lower():
                    val = state.get(node.output_name)
                    if val is True:
                        converged = True
                        break
                # Legacy: "converged" substring match
                if "converged" in node.output_name.lower():
                    val = state.get(node.output_name)
                    if val is True:
                        converged = True
                        break
                # Tuple check for memo nodes
                if "memo" in node.name.lower() and node.output_name:
                    val = state.get(node.output_name)
                    if isinstance(val, tuple) and len(val) == 2 and val[1] is True:
                        converged = True
                        break

        if converged:
            return (
                SimResult(node_count=len(sim_nodes), final_state=state, trace=trace),
                iterations_used,
                False,
                [],
            )

        # Deadlock detection: no state change and not converged
        state_unchanged = all(state.get(k) is prev_state.get(k) for k in state)
        if state_unchanged and iteration > 0:
            raise PlanError(
                node_name=sim_nodes[0].name if sim_nodes else "unknown",
                function_name="fixed_point_iteration",
                detail="Cyclic deadlock: state unchanged but convergence not reached",
            )

    # Max iterations reached — treat as convergence
    return (
        SimResult(node_count=len(sim_nodes), final_state=state, trace=trace),
        iterations_used,
        False,
        [],
    )


def run_ghost_simulation(
    cdg: CDGExport,
    match_results: list[MatchResult],
    *,
    uncertainty_backend: UncertaintyBackend | None = None,
) -> GhostSimReport:
    """Run the ghost witness simulation on a CDG.

    Converts atomic leaf nodes to SimNodes using the matched atom names,
    constructs initial abstract state from IOSpec metadata, and runs the
    simulator.  Non-DSP nodes (those without registered witnesses) are
    skipped — the simulation is best-effort.

    Args:
        cdg: The Conceptual Dependency Graph.
        match_results: Verified match results for atomic leaves.
        uncertainty_backend: Optional backend for per-atom error estimates.
            Falls back to ``HeuristicBackend`` when ``None``.

    Returns:
        GhostSimReport describing the simulation outcome.
    """
    report = GhostSimReport()

    # Index match results by node_id (predicate_id)
    match_map: dict[str, MatchResult] = {}
    for mr in match_results:
        match_map[mr.pdg_node.predicate_id] = mr

    # Detect FIXED_POINT nodes by concept_type (not just cycle detection)
    fp_node_map: dict[str, AlgorithmicNode] = {}
    for n in cdg.nodes:
        if n.concept_type == ConceptType.FIXED_POINT:
            fp_node_map[n.node_id] = n
    is_fixed_point_graph = bool(fp_node_map)

    # Topological sort (needed by both precision gradients and ghost sim)
    cycle_ids: set[str] = set()
    is_message_passing_cycle = False
    try:
        sorted_ids = toposort_nodes(cdg.nodes, cdg.edges)
    except ValueError:
        # Topological sort failed — check if this is a valid factor graph cycle
        # or a valid FIXED_POINT cycle
        acyclic_sorted, cycle_ids, is_valid_cycle = detect_cycle_partition(
            cdg.nodes, cdg.edges
        )
        # Check specifically for MESSAGE_PASSING
        node_map_tmp = {n.node_id: n for n in cdg.nodes}
        is_message_passing_cycle = bool(cycle_ids) and all(
            getattr(node_map_tmp.get(nid), "concept_type", None)
            == ConceptType.MESSAGE_PASSING
            for nid in cycle_ids
            if nid in node_map_tmp
        )
        if not is_valid_cycle:
            report.ran = True
            report.error = f"Cycle detected in non-message-passing nodes: {cycle_ids}"
            return report
        # For message-passing cycles, build a partial order for acyclic nodes
        # and use the cycle node ids for iterative simulation
        acyclic_nodes = [n for n in cdg.nodes if n.node_id not in cycle_ids]
        acyclic_edges = [
            e
            for e in cdg.edges
            if e.source_id not in cycle_ids and e.target_id not in cycle_ids
        ]
        try:
            sorted_ids = toposort_nodes(acyclic_nodes, acyclic_edges)
        except ValueError:
            sorted_ids = [n.node_id for n in acyclic_nodes]
        # Append cycle node ids at the end (they'll be simulated iteratively)
        sorted_ids.extend(sorted(cycle_ids))

    # Precision gradients — runs independently of sciona.atoms
    try:
        pg_result = _compute_precision_gradients(
            cdg,
            match_map,
            sorted_ids,
            backend=uncertainty_backend,
        )
        report.precision_gradients = pg_result.gradients
        report.uncalibrated_nodes = pg_result.uncalibrated_nodes
        report.node_confidence = pg_result.node_confidence
    except Exception as exc:
        logger.warning("Precision gradient computation failed: %s", exc)

    if not _GHOST_AVAILABLE:
        logger.info("Ghost simulation skipped: no ghost backend available")
        return report

    # Ensure atom modules are imported so witnesses are registered
    _ensure_atoms_imported()

    registered = set(list_registered())
    if not registered:
        logger.info("Ghost simulation skipped: no witnesses registered")
        return report

    # Index nodes by id
    node_map: dict[str, AlgorithmicNode] = {n.node_id: n for n in cdg.nodes}

    # Get atomic leaves
    atomic_leaves = {n.node_id for n in cdg.nodes if n.status == NodeStatus.ATOMIC}

    # Build edge index: target_id -> list of incoming edges
    incoming_edges: dict[str, list[DependencyEdge]] = {nid: [] for nid in node_map}
    for edge in cdg.edges:
        if edge.target_id in incoming_edges:
            incoming_edges[edge.target_id].append(edge)

    # Determine which nodes have witnesses
    simulable_nodes: list[str] = []
    skipped: list[str] = []

    for nid in sorted_ids:
        if nid not in atomic_leaves:
            continue
        node = node_map[nid]
        mr = match_map.get(nid)
        if mr is None or not mr.success:
            skipped.append(node.name)
            continue

        atom_name = mr.verified_match.candidate.declaration.name
        if atom_name in registered:
            simulable_nodes.append(nid)
        else:
            skipped.append(node.name)

    report.skipped_nodes = skipped

    # Compute coverage
    total_atomic = len(simulable_nodes) + len(skipped)
    if total_atomic > 0:
        report.coverage = len(simulable_nodes) / total_atomic
    else:
        report.coverage = 0.0

    if report.coverage < 0.5 and total_atomic > 0:
        unsimulated_categories = set()
        for nid in sorted_ids:
            if nid not in atomic_leaves:
                continue
            node = node_map[nid]
            mr = match_map.get(nid)
            if mr and mr.success:
                atom_name = mr.verified_match.candidate.declaration.name
                if atom_name not in registered:
                    unsimulated_categories.add(
                        node.concept_type.value
                        if hasattr(node, "concept_type")
                        else "unknown"
                    )
        logger.warning(
            "Ghost simulation coverage is low (%.0f%%): unsimulated categories: %s",
            report.coverage * 100,
            sorted(unsimulated_categories),
        )

    if not simulable_nodes:
        logger.info("Ghost simulation skipped: no simulable nodes")
        return report

    # Build SimNodes and initial state
    sim_nodes: list[SimNode] = []
    initial_state: dict[str, Any] = {}

    # Pre-populate initial state for root inputs
    # (inputs to nodes that have no incoming edges from other atomic leaves)
    for nid in simulable_nodes:
        node = node_map[nid]
        in_edges = incoming_edges.get(nid, [])
        mr = match_map[nid]
        declared_param_names = _declared_param_names(mr)

        # Map incoming edges to state keys
        edge_inputs: dict[str, str] = {}
        for edge in in_edges:
            if edge.source_id in atomic_leaves:
                source_output_name = _resolve_source_output_name(
                    edge.output_name,
                    node_map.get(edge.source_id),
                )
                state_key = f"{edge.source_id}:{source_output_name}"
                target_name = _resolve_signature_param_name(
                    edge.input_name,
                    declared_param_names,
                )
                edge_inputs[target_name] = state_key

        # For inputs not covered by edges from simulable nodes, create initial state
        for inp in node.inputs:
            target_name = _resolve_signature_param_name(inp.name, declared_param_names)
            if target_name not in edge_inputs:
                state_key = f"root:{nid}:{target_name}"
                edge_inputs[target_name] = state_key
                if state_key not in initial_state:
                    initial_state[state_key] = _build_initial_value(
                        inp.name, inp.type_desc, inp.constraints
                    )

        # Build SimNode
        atom_name = mr.verified_match.candidate.declaration.name

        output_names = [f"{nid}:{output.name}" for output in node.outputs]
        output_name = output_names[0] if output_names else ""

        sim_nodes.append(
            SimNode(
                name=node.name,
                function_name=atom_name,
                inputs=edge_inputs,
                output_name=output_name,
                output_names=output_names,
            )
        )

    # Run the simulation
    report.ran = True

    if is_message_passing_cycle:
        # Split sim_nodes into acyclic (run once) and cyclic (run iteratively)
        cyclic_sim_nodes = [
            n
            for n in sim_nodes
            if any(
                nid in cycle_ids
                for nid in simulable_nodes
                if node_map.get(nid, AlgorithmicNode(node_id="")).name == n.name
            )
        ]
        acyclic_sim_nodes = [n for n in sim_nodes if n not in cyclic_sim_nodes]

        # If we can't cleanly separate, treat all as cyclic
        if not cyclic_sim_nodes:
            cyclic_sim_nodes = sim_nodes
            acyclic_sim_nodes = []

        try:
            # Run acyclic nodes first
            if acyclic_sim_nodes:
                result = _simulate_with_bayesian_checks(
                    acyclic_sim_nodes, initial_state
                )
                initial_state.update(result.final_state)

            # Run cyclic nodes iteratively
            result, iters, deadlocked, dl_names = _simulate_message_passing_iterative(
                cyclic_sim_nodes, initial_state
            )
            report.passed = True
            report.node_count = result.node_count + len(acyclic_sim_nodes)
            report.trace = [n.name for n in acyclic_sim_nodes] + result.trace
            report.iterations_used = iters
            logger.info(
                "Ghost simulation (iterative) passed: %d nodes, %d iterations",
                report.node_count,
                iters,
            )
        except PlanError as exc:
            if "deadlock" in str(exc).lower():
                report.cyclic_deadlock = True
                report.deadlock_nodes = [n.name for n in cyclic_sim_nodes]
            report.error = str(exc)
            report.error_node = getattr(exc, "node_name", "")
            report.error_function = getattr(exc, "function_name", "")
            logger.warning("Ghost simulation (iterative) failed: %s", exc)
        except Exception as exc:
            report.error = f"Unexpected error: {exc}"
            logger.warning("Ghost simulation unexpected error: %s", exc)
    else:
        # Standard topological simulation
        try:
            result = _simulate_with_bayesian_checks(sim_nodes, initial_state)
            report.passed = True
            report.node_count = result.node_count
            report.trace = result.trace
            logger.info(
                "Ghost simulation passed: %d nodes simulated, trace: %s",
                result.node_count,
                result.trace,
            )
        except PlanError as exc:
            report.error = str(exc)
            report.error_node = exc.node_name
            report.error_function = exc.function_name
            logger.warning("Ghost simulation failed: %s", exc, exc_info=True)
        except Exception as exc:
            report.error = f"Unexpected error: {exc}"
            logger.warning("Ghost simulation unexpected error: %s", exc)

    return report


# ---------------------------------------------------------------------------
# Bayesian-aware simulation (refactored from sciona-atoms)
# ---------------------------------------------------------------------------

# Witness functions that produce reparameterized outputs.
_REPARAM_WITNESSES = frozenset(
    {
        "witness_reparameterized_sample",
        "reparameterized_sample",
    }
)

# Witness functions that produce bijector outputs (carry Jacobian).
_BIJECTOR_WITNESSES = frozenset(
    {
        "witness_bijector_transform",
        "bijector_transform",
    }
)

# Witness functions that require reparameterized / bijector inputs.
_ELBO_WITNESSES = frozenset(
    {
        "witness_vi_elbo",
        "vi_elbo",
    }
)

# Abstract types allowed as inputs to stateless MCMC oracle nodes.
_ORACLE_ALLOWED_TYPES: tuple[type, ...] = (float, int, str)
if _GHOST_AVAILABLE:
    _ORACLE_ALLOWED_TYPES = (
        AbstractDistribution,
        AbstractSignal,
        AbstractMatrix,
        float,
        int,
        str,
    )


def _simulate_with_bayesian_checks(
    nodes: list[Any],
    initial_state: dict[str, Any],
) -> Any:
    """Run ghost simulation with Bayesian verification.

    Extends the standard ``simulate_graph`` pass with:
    1. **VI ELBO provenance check** — inputs must originate from
       reparameterized traces or bijector outputs.
    2. **MCMC Oracle isolation check** — oracle nodes must be stateless
       (only receive abstract types and scalars).
    3. **Provenance tracking** — propagates ``is_reparameterized`` and
       ``has_jacobian`` flags through the graph.

    Raises ``PlanError`` on violations.
    """
    state = dict(initial_state)

    # Provenance: state_key -> {"is_reparameterized": bool, "has_jacobian": bool}
    provenance: dict[str, dict[str, bool]] = {
        key: {"is_reparameterized": False, "has_jacobian": False} for key in state
    }

    trace: list[str] = []

    for node in nodes:
        # 1. VI ELBO provenance check
        if node.function_name in _ELBO_WITNESSES:
            for param, state_key in node.inputs.items():
                prov = provenance.get(
                    state_key,
                    {"is_reparameterized": False, "has_jacobian": False},
                )
                if not (prov["is_reparameterized"] or prov["has_jacobian"]):
                    raise PlanError(
                        node_name=node.name,
                        function_name=node.function_name,
                        detail=(
                            f"ELBO input '{param}' (key '{state_key}') must "
                            f"originate from a reparameterized trace or a "
                            f"bijector output (with Jacobian)."
                        ),
                    )

        # 2. MCMC Oracle isolation check
        if "ORACLE" in node.function_name.upper():
            for param, state_key in node.inputs.items():
                val = state.get(state_key)
                if not isinstance(val, _ORACLE_ALLOWED_TYPES):
                    raise PlanError(
                        node_name=node.name,
                        function_name=node.function_name,
                        detail=(
                            f"Oracle nodes must be stateless. Found state "
                            f"input '{param}' of type {type(val).__name__}."
                        ),
                    )

        # 3. Execute single-node simulation step
        res = simulate_graph([node], state)
        state.update(res.final_state)

        # 4. Update provenance for this node's output
        output_names = node.output_names or (
            [node.output_name] if node.output_name else []
        )
        for output_name in output_names:
            provenance[output_name] = {
                "is_reparameterized": node.function_name in _REPARAM_WITNESSES,
                "has_jacobian": node.function_name in _BIJECTOR_WITNESSES,
            }

        trace.append(node.name)

    return SimResult(
        node_count=len(nodes),
        final_state=state,
        trace=trace,
    )


def _ensure_atoms_imported() -> None:
    """Import atom packages from all configured sources to trigger @register_atom."""
    from sciona.sources import load_sources, import_all_sources

    try:
        config = load_sources()
        if config.sources:
            import_all_sources(config)
            return
    except Exception:
        logger.debug("Failed to load sources.yml, falling back to hardcoded imports", exc_info=True)

    # Fallback: preserve legacy imports, but try all recognized atom-package prefixes.
    for mod in _fallback_atom_modules():
        try:
            importlib.import_module(mod)
        except ImportError:
            pass
