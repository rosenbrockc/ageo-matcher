"""Ghost Witness simulation engine.

Walks a computation graph and executes *witnesses* instead of heavy
functions.  If the witness graph completes without error, the heavy
graph is guaranteed to be structurally correct (shapes, dtypes, domains
all match at every edge).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sciona.ghost.registry import REGISTRY, get_witness


class PlanError(Exception):
    """Raised when the witness simulation detects a structural error.

    Attributes:
        node_name: Name of the graph node where the error occurred.
        function_name: Name of the atom whose witness failed.
        detail: Human-readable explanation of the mismatch.
    """

    def __init__(
        self,
        node_name: str,
        function_name: str,
        detail: str,
    ) -> None:
        self.node_name = node_name
        self.function_name = function_name
        self.detail = detail
        super().__init__(f"Plan error at '{node_name}' ({function_name}): {detail}")


@dataclass
class SimNode:
    """A node in the simulation graph.

    Mirrors the structure of a CDG ``AlgorithmicNode`` but carries only
    the information the simulator needs.
    """

    name: str
    function_name: str
    inputs: Dict[str, str] = field(default_factory=dict)
    """Map of witness-parameter-name -> state-key."""
    output_name: str = ""
    """Key under which the witness output is stored in the state dict."""
    output_names: List[str] = field(default_factory=list)
    """Keys for declared multi-output witnesses, in return-value order."""
    kwargs: Dict[str, Any] = field(default_factory=dict)
    """Extra keyword arguments forwarded to the witness (e.g. t=0.5)."""


@dataclass
class SimResult:
    """Result of a successful simulation pass."""

    node_count: int
    final_state: Dict[str, Any]
    trace: List[str] = field(default_factory=list)
    """Ordered list of node names that were simulated."""


def simulate_graph(
    nodes: List[SimNode],
    initial_state: Dict[str, Any],
    *,
    witness_overrides: Optional[Dict[str, Callable]] = None,
) -> SimResult:
    """Walk the graph executing witnesses instead of heavy functions.

    The simulator processes nodes in list order (assumed to be a valid
    topological sort).  At each node it:

    1. Looks up the witness from the registry (or ``witness_overrides``).
    2. Gathers ghost inputs from the current state.
    3. Calls the witness with those inputs.
    4. Stores the witness output under its declared state key or keys.

    If any witness raises ``ValueError``, the simulator wraps it in a
    ``PlanError`` and re-raises.

    Args:
        nodes: Topologically-sorted list of simulation nodes.
        initial_state: Mapping of state-key -> initial abstract value
            (e.g. ``{"signal": AbstractSignal(...)}``).
        witness_overrides: Optional dict of function_name -> witness
            callable, used to inject custom witnesses without touching
            the global registry.

    Returns:
        SimResult with the final state and execution trace.

    Raises:
        PlanError: If a witness detects a structural mismatch.
        KeyError: If a required input is missing from the state.
    """
    overrides = witness_overrides or {}
    state = dict(initial_state)
    trace: List[str] = []

    for node in nodes:
        # 1. Resolve witness
        if node.function_name in overrides:
            witness = overrides[node.function_name]
        else:
            try:
                witness = get_witness(node.function_name)
            except KeyError:
                raise PlanError(
                    node_name=node.name,
                    function_name=node.function_name,
                    detail=f"No ghost witness registered for '{node.function_name}'",
                )

        # 2. Gather inputs from state
        inputs: Dict[str, Any] = {}
        for param_name, state_key in node.inputs.items():
            if state_key not in state:
                raise PlanError(
                    node_name=node.name,
                    function_name=node.function_name,
                    detail=(
                        f"Input '{param_name}' expects state key '{state_key}' "
                        f"but it is not in the current state. "
                        f"Available keys: {sorted(state.keys())}"
                    ),
                )
            inputs[param_name] = state[state_key]

        # Merge any extra kwargs
        inputs.update(node.kwargs)

        # 3. Execute the witness
        try:
            output = witness(**inputs)
        except (ValueError, TypeError) as exc:
            raise PlanError(
                node_name=node.name,
                function_name=node.function_name,
                detail=str(exc),
            )

        # 4. Store output. A single declared port retains the complete return
        # value for compatibility with atoms whose one output is tuple-valued.
        output_names = node.output_names or (
            [node.output_name] if node.output_name else []
        )
        if len(output_names) == 1:
            state[output_names[0]] = output
        elif output_names:
            if not isinstance(output, (tuple, list)):
                raise PlanError(
                    node_name=node.name,
                    function_name=node.function_name,
                    detail=(
                        f"Witness declares {len(output_names)} outputs "
                        f"{output_names} but returned {type(output).__name__}"
                    ),
                )
            if len(output) != len(output_names):
                raise PlanError(
                    node_name=node.name,
                    function_name=node.function_name,
                    detail=(
                        f"Witness declares {len(output_names)} outputs "
                        f"{output_names} but returned {len(output)} values"
                    ),
                )
            state.update(zip(output_names, output))

        trace.append(node.name)

    return SimResult(
        node_count=len(nodes),
        final_state=state,
        trace=trace,
    )
