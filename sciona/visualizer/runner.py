"""Local execution runner for CDGs."""

from __future__ import annotations

import dataclasses
import inspect
import json
import logging
from pathlib import Path
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from sciona.architect.models import AlgorithmicNode, DependencyEdge, NodeStatus
from sciona.ghost.registry import REGISTRY
from sciona.synthesizer.ghost_sim import _ensure_atoms_imported

logger = logging.getLogger(__name__)

RUNS_DIR = Path("output/runs")


def safe_eval_slice(array: np.ndarray, slice_str: str) -> np.ndarray:
    """Safely parses and applies a numpy-style slice string to an array.
    
    E.g. "[0:100, :, 0]" -> array[0:100, :, 0]
    """
    slice_str = slice_str.strip().lstrip("[").rstrip("]")
    if not slice_str:
        return array
    
    parts = slice_str.split(",")
    slices = []
    for p in parts:
        p = p.strip()
        if p == ":":
            slices.append(slice(None))
        elif ":" in p:
            subparts = p.split(":")
            start = int(subparts[0]) if subparts[0] else None
            stop = int(subparts[1]) if len(subparts) > 1 and subparts[1] else None
            step = int(subparts[2]) if len(subparts) > 2 and subparts[2] else None
            slices.append(slice(start, stop, step))
        else:
            try:
                slices.append(int(p))
            except ValueError:
                # Fallback to slice(None) if parsing fails
                slices.append(slice(None))
                
    if len(slices) == 1:
        res = array[slices[0]]
    else:
        res = array[tuple(slices)]
        
    # Ensure it's a numpy array (slicing can return scalar)
    if not isinstance(res, np.ndarray):
        res = np.array(res)
    return res


async def load_cdg_from_memgraph(driver, repo: str) -> Tuple[List[AlgorithmicNode], List[DependencyEdge], Dict[str, Any]]:
    """Loads the CDG nodes, edges, and metadata for a given repo path from Memgraph."""
    async with driver.session() as session:
        # Load nodes and ports
        node_result = await session.run(
            """
            MATCH (a:Atom)
            WHERE a.repo = $repo
            OPTIONAL MATCH (a)-[:HAS_INPUT]->(ip:InputPort)
            OPTIONAL MATCH (a)-[:HAS_OUTPUT]->(op:OutputPort)
            OPTIONAL MATCH (a)-[:PARENT_OF]->(child:Atom)
            OPTIONAL MATCH (parent:Atom)-[:PARENT_OF]->(a)
            RETURN a, collect(DISTINCT ip) AS inputs,
                   collect(DISTINCT op) AS outputs,
                   collect(DISTINCT child.node_id) AS children,
                   parent.node_id AS parent_id
            """,
            parameters={"repo": repo},
        )
        node_records = [r async for r in node_result]
        if not node_records:
            raise ValueError(f"CDG repo not found in Memgraph: {repo}")

        # Load edges
        edge_result = await session.run(
            """
            MATCH (s:Atom)-[r:DATA_FLOW]->(t:Atom)
            WHERE s.repo = $repo AND t.repo = $repo
            RETURN s.node_id AS source_id, t.node_id AS target_id,
                   r.output_name AS output_name, r.input_name AS input_name,
                   r.source_type AS source_type, r.target_type AS target_type,
                   r.requires_glue AS requires_glue
            """,
            parameters={"repo": repo},
        )
        edge_records = [r async for r in edge_result]

    nodes: List[AlgorithmicNode] = []
    metadata = {"repo": repo, "goal": "", "paradigm": "", "thread_id": ""}
    
    for rec in node_records:
        atom = dict(rec["a"])
        
        # Parse inputs/outputs to IOSpec dicts
        inputs = [
            {
                "name": dict(ip).get("name", ""),
                "type_desc": dict(ip).get("type_desc", ""),
                "constraints": dict(ip).get("constraints", ""),
            }
            for ip in rec["inputs"]
            if ip is not None
        ]
        outputs = [
            {
                "name": dict(op).get("name", ""),
                "type_desc": dict(op).get("type_desc", ""),
                "constraints": dict(op).get("constraints", ""),
            }
            for op in rec["outputs"]
            if op is not None
        ]

        # Construct AlgorithmicNode
        node_dict = {
            "node_id": atom.get("node_id", ""),
            "parent_id": rec["parent_id"],
            "name": atom.get("name", ""),
            "description": atom.get("description", ""),
            "concept_type": atom.get("concept_type", "custom"),
            "status": atom.get("status", "atomic"),
            "depth": atom.get("depth", 0),
            "type_signature": atom.get("type_signature", ""),
            "inputs": inputs,
            "outputs": outputs,
            "children": [c for c in rec["children"] if c is not None],
            "matched_primitive": atom.get("matched_primitive", ""),
        }
        nodes.append(AlgorithmicNode(**node_dict))
        
        # Capture metadata from root if available
        if not rec["parent_id"]:
            metadata["goal"] = atom.get("goal", "")
            metadata["paradigm"] = atom.get("paradigm", "")
            metadata["thread_id"] = atom.get("thread_id", "")

    edges: List[DependencyEdge] = []
    for rec in edge_records:
        edges.append(
            DependencyEdge(
                source_id=rec["source_id"],
                target_id=rec["target_id"],
                output_name=rec["output_name"] or "",
                input_name=rec["input_name"] or "",
                source_type=rec["source_type"] or "",
                target_type=rec["target_type"] or "",
                requires_glue=bool(rec["requires_glue"]),
            )
        )
        
    return nodes, edges, metadata


def get_topo_sorted_leaves(nodes: List[AlgorithmicNode], edges: List[DependencyEdge], target_node_id: Optional[str] = None) -> List[AlgorithmicNode]:
    """Finds and topologically sorts all leaf nodes.
    
    If target_node_id is specified, sorts only the target node and its upstream dependencies.
    """
    node_map = {n.node_id: n for n in nodes}
    
    # Identify leaf nodes (status == atomic)
    leaf_ids = {n.node_id for n in nodes if n.status == NodeStatus.ATOMIC}
    
    # Tracing ancestors if target_node_id is specified
    if target_node_id:
        if target_node_id not in leaf_ids:
            raise ValueError(f"Target node '{target_node_id}' is not an atomic leaf node.")
            
        # Tracing upstream data-flow recursively
        visited = set()
        to_visit = [target_node_id]
        
        # Build backward adjacency target -> sources
        incoming_edges: Dict[str, List[str]] = {nid: [] for nid in leaf_ids}
        for edge in edges:
            if edge.target_id in incoming_edges and edge.source_id in leaf_ids:
                incoming_edges[edge.target_id].append(edge.source_id)
                
        while to_visit:
            curr = to_visit.pop()
            if curr not in visited:
                visited.add(curr)
                to_visit.extend(incoming_edges.get(curr, []))
                
        active_ids = visited
    else:
        active_ids = leaf_ids

    # Sort the active leaves topologically
    # Kahn's algorithm
    in_degree = {nid: 0 for nid in active_ids}
    successors = {nid: [] for nid in active_ids}
    
    for edge in edges:
        if edge.source_id in active_ids and edge.target_id in active_ids:
            successors[edge.source_id].append(edge.target_id)
            in_degree[edge.target_id] += 1
            
    queue = [nid for nid in active_ids if in_degree[nid] == 0]
    sorted_ids = []
    
    while queue:
        # Sort queue to ensure deterministic behavior
        queue.sort()
        curr = queue.pop(0)
        sorted_ids.append(curr)
        for succ in successors[curr]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)
                
    if len(sorted_ids) != len(active_ids):
        raise ValueError("Data-flow cycle detected among executing nodes!")
        
    return [node_map[nid] for nid in sorted_ids]


def parse_input_value(raw_val: Any, type_desc: str) -> Any:
    """Parses user input strings/dicts into the expected Python types based on type signatures."""
    type_desc = type_desc.strip()

    # Resolve catalog identities selected by the visualizer. Legacy s3://
    # values remain recognized, but are no longer allowed to bypass catalog
    # verification unless synthetic fallback is explicitly enabled.
    dataset_fqn = None
    if isinstance(raw_val, dict) and set(raw_val) == {"$dataset"}:
        dataset_fqn = raw_val["$dataset"]
    elif isinstance(raw_val, str) and raw_val.startswith("s3://"):
        dataset_fqn = raw_val
    if dataset_fqn is not None:
        if not isinstance(dataset_fqn, str) or not dataset_fqn:
            raise ValueError("Catalog dataset input requires a non-empty FQN")
        from sciona.visualizer.dataset_manager import DatasetManager
        try:
            logger.info("Resolving catalog dataset FQN: %s", dataset_fqn)
            return DatasetManager().load_dataset(dataset_fqn)
        except Exception as e:
            logger.error("Failed to load catalog dataset %s: %s", dataset_fqn, e)
            raise ValueError(f"Failed to load catalog dataset {dataset_fqn}: {e}")
    
    # Handle files
    if isinstance(raw_val, str) and (raw_val.endswith(".npy") or raw_val.endswith(".parquet") or raw_val.endswith(".csv") or raw_val.endswith(".json")):
        path = Path(raw_val)
        if not path.exists():
            raise FileNotFoundError(f"Input file path not found: {raw_val}")
            
        if raw_val.endswith(".npy"):
            return np.load(path)
        elif raw_val.endswith(".parquet"):
            return pd.read_parquet(path)
        elif raw_val.endswith(".csv"):
            df = pd.read_csv(path)
            # Try to return values as ndarray if appropriate, or DataFrame
            return df.values
        elif raw_val.endswith(".json"):
            with open(path, "r") as f:
                return json.load(f)

    # Cast primitives
    if type_desc == "int":
        return int(raw_val)
    elif type_desc == "float":
        return float(raw_val)
    elif type_desc == "bool":
        if isinstance(raw_val, str):
            return raw_val.lower() in ("true", "1", "yes")
        return bool(raw_val)
    elif type_desc == "str":
        return str(raw_val)
        
    # Cast collections using json load if raw_val is string
    if isinstance(raw_val, str) and (raw_val.startswith("[") or raw_val.startswith("{")):
        try:
            raw_val = json.loads(raw_val)
        except Exception:
            pass

    return raw_val


def reconstruct_parameters(func: Callable, args_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Casts input dictionaries to their corresponding dataclasses or types declared in annotations."""
    import inspect
    import typing
    try:
        type_hints = typing.get_type_hints(func)
    except Exception:
        type_hints = {}
        
    sig = inspect.signature(func)
    reconstructed = {}
    
    for param_name, param in sig.parameters.items():
        if param_name not in args_dict:
            continue
        val = args_dict[param_name]
        param_type = type_hints.get(param_name, param.annotation)
        
        # If type annotation is a dataclass and we received a dict, reconstruct it
        if dataclasses.is_dataclass(param_type) and isinstance(val, dict):
            try:
                reconstructed[param_name] = param_type(**val)
            except Exception as e:
                logger.warning(f"Failed to reconstruct dataclass {param_type} for {param_name}: {e}")
                reconstructed[param_name] = val
        else:
            reconstructed[param_name] = val
            
    return reconstructed


def get_numpy_statistics(arr: np.ndarray) -> Dict[str, Any]:
    """Computes basic metrics for numeric numpy arrays to display as summary metadata."""
    stats = {
        "type": "ndarray",
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "size": int(arr.size)
    }
    # Check if numeric to extract statistical aggregates
    if np.issubdtype(arr.dtype, np.number):
        try:
            stats.update({
                "min": float(np.min(arr)),
                "max": float(np.max(arr)),
                "mean": float(np.mean(arr)),
                "std": float(np.std(arr))
            })
        except Exception:
            pass
    return stats


def save_intermediate_value(run_dir: Path, node_id: str, name: str, val: Any) -> None:
    """Serializes and saves a node's intermediate input/output value to disk."""
    var_dir = run_dir / node_id
    var_dir.mkdir(parents=True, exist_ok=True)
    
    meta_path = var_dir / f"{name}.json"
    data_path = var_dir / f"{name}.npy"
    
    if isinstance(val, np.ndarray):
        # Save raw numpy array
        np.save(data_path, val)
        # Save metadata
        stats = get_numpy_statistics(val)
        with open(meta_path, "w") as f:
            json.dump(stats, f)
    else:
        # Standard python object
        # Save value in metadata directly
        meta = {
            "type": type(val).__name__,
            "value": val
        }
        # If it is a pandas DataFrame, export stats
        if isinstance(val, pd.DataFrame):
            meta["type"] = "DataFrame"
            meta["shape"] = list(val.shape)
            meta["columns"] = list(val.columns)
            
        with open(meta_path, "w") as f:
            try:
                json.dump(meta, f)
            except TypeError:
                # Fallback for non-serializable objects
                meta["value"] = str(val)
                json.dump(meta, f)


def load_cached_outputs(run_dir: Path, node_id: str) -> Optional[Dict[str, Any]]:
    """Loads previously computed outputs of a node from disk if they exist."""
    node_dir = run_dir / node_id
    if not node_dir.exists():
        return None
        
    outputs = {}
    # Scan files in directory
    for f in node_dir.glob("out_*.npy"):
        name = f.stem
        try:
            outputs[name] = np.load(f)
        except Exception:
            pass
            
    for f in node_dir.glob("out_*.json"):
        name = f.stem
        if name in outputs:
            continue
        try:
            with open(f, "r") as fh:
                meta = json.load(fh)
                if "value" in meta:
                    outputs[name] = meta["value"]
        except Exception:
            pass
            
    return outputs if outputs else None


class CDGExecutionSession:
    """Handles sequential topological execution of a CDG."""

    def __init__(self, driver, repo: str, run_id: str):
        self.driver = driver
        self.repo = repo
        self.run_id = run_id
        self.run_dir = RUNS_DIR / run_id
        
    async def execute(
        self,
        user_inputs: Dict[str, Any],
        target_node_id: Optional[str] = None,
        cdg: Any | None = None,
        execution_id: str = "",
        version_id: str = "",
    ) -> Dict[str, Any]:
        """Runs the CDG execution pipeline, caching intermediate states."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        
        # 1. Load CDG
        if cdg is None:
            nodes, edges, metadata = await load_cdg_from_memgraph(self.driver, self.repo)
        else:
            nodes = list(cdg.nodes)
            edges = list(cdg.edges)
            metadata = dict(cdg.metadata)

        graph_snapshot = {
            "nodes": [node.model_dump(mode="json") for node in nodes],
            "edges": [edge.model_dump(mode="json") for edge in edges],
            "metadata": metadata,
        }
        with open(self.run_dir / "cdg.json", "w") as f:
            json.dump(graph_snapshot, f)
        
        # Save run metadata
        meta_file = self.run_dir / "run_metadata.json"
        with open(meta_file, "w") as f:
            json.dump({
                "run_id": self.run_id,
                "repo": self.repo,
                "timestamp": time.time(),
                "target_node_id": target_node_id,
                "execution_id": execution_id,
                "version_id": version_id,
                "status": "running"
            }, f)
            
        # Ensure atoms are imported and witnesses registered
        _ensure_atoms_imported()
        
        # 2. Check grounding of all atomic leaf nodes
        ungrounded = []
        for n in nodes:
            if n.status == NodeStatus.ATOMIC:
                mp = str(n.matched_primitive or "").strip()
                if not mp or mp not in REGISTRY:
                    ungrounded.append(f"{n.name} (primitive: '{mp}')")
                    
        if ungrounded:
            error_msg = "CDG is not fully grounded! Missing verified matched primitives in registry for:\n- " + "\n- ".join(ungrounded)
            # Update metadata to failed
            with open(meta_file, "w") as f:
                json.dump({
                    "run_id": self.run_id,
                    "repo": self.repo,
                    "timestamp": time.time(),
                    "execution_id": execution_id,
                    "version_id": version_id,
                    "status": "failed",
                    "error": error_msg
                }, f)
            raise ValueError(error_msg)
            
        # 3. Sort nodes
        exec_nodes = get_topo_sorted_leaves(nodes, edges, target_node_id)
        
        # Build mapping target_id -> list of incoming edges
        incoming_edges: Dict[str, List[DependencyEdge]] = {n.node_id: [] for n in exec_nodes}
        for edge in edges:
            if edge.target_id in incoming_edges:
                incoming_edges[edge.target_id].append(edge)
                
        # Runtime execution state dictionary
        # Stores mapping of "node_id/output_name" -> runtime value
        state: Dict[str, Any] = {}
        trace = []
        from sciona.telemetry import log_event
        
        # Load user inputs into state
        # Root inputs are mapped as "inputs/port_name" -> parsed value
        for key, val in user_inputs.items():
            state[f"inputs/{key}"] = val
            
        # 4. Sequential execution loop
        for node_index, node in enumerate(exec_nodes):
            node_id = node.node_id
            primitive_name = node.matched_primitive
            impl = REGISTRY[primitive_name]["impl"]
            node_started_at = time.perf_counter()
            event_payload = {
                "name": node.name,
                "primitive": primitive_name,
                "position": node_index + 1,
                "total": len(exec_nodes),
                "input_names": [item.name for item in node.inputs],
                "output_names": [item.name for item in node.outputs],
            }
            log_event(
                "executor",
                "node_execution",
                "CDG_NODE_STARTED",
                run_id=self.run_id,
                stage="execute_cdg",
                node_id=node_id,
                payload=event_payload,
            )
            
            # Check caching: can we reuse computed outputs?
            # A supplied graph is an editable evolution snapshot. Node IDs may be
            # stable while implementations or wiring change between versions, so
            # the run-level cache is not valid for these executions.
            cached_outputs = None if cdg is not None else load_cached_outputs(self.run_dir, node_id)
            if cached_outputs is not None:
                logger.info(f"Reusing cached outputs for node: {node.name}")
                for name, val in cached_outputs.items():
                    # Strip the "out_" prefix from cached key names
                    real_name = name[4:] if name.startswith("out_") else name
                    state[f"{node_id}/{real_name}"] = val
                trace.append({"node_id": node_id, "name": node.name, "cached": True})
                log_event(
                    "executor",
                    "node_execution",
                    "CDG_NODE_CACHE_HIT",
                    run_id=self.run_id,
                    stage="execute_cdg",
                    node_id=node_id,
                    duration_ms=(time.perf_counter() - node_started_at) * 1000.0,
                    payload={**event_payload, "cached": True},
                )
                continue
                
            # Gather inputs
            args = {}
            node_in_edges = incoming_edges.get(node_id, [])
            
            # Map parameters by name
            for inp in node.inputs:
                param_name = inp.name
                
                # Check if there is an incoming dataflow edge feeding this input port
                edge_found = False
                for edge in node_in_edges:
                    if edge.input_name == param_name:
                        src_key = f"{edge.source_id}/{edge.output_name}"
                        if src_key in state:
                            args[param_name] = state[src_key]
                            edge_found = True
                            break
                            
                # If no edge feeding it, look at root inputs or default inputs
                if not edge_found:
                    root_key = f"inputs/{param_name}"
                    if root_key in state:
                        args[param_name] = parse_input_value(state[root_key], inp.type_desc)
                    elif inp.required:
                        # Missing mandatory input
                        error = KeyError(f"Missing mandatory input '{param_name}' for node '{node.name}'.")
                        log_event(
                            "executor",
                            "node_execution",
                            "CDG_NODE_FAILED",
                            run_id=self.run_id,
                            stage="execute_cdg",
                            node_id=node_id,
                            duration_ms=(time.perf_counter() - node_started_at) * 1000.0,
                            payload={
                                **event_payload,
                                "error": str(error),
                                "error_type": type(error).__name__,
                            },
                        )
                        raise error
                        
            # Reconstruct parameters (e.g. dicts to dataclasses)
            func_args = reconstruct_parameters(impl, args)
            
            # Save intermediate inputs for visual inspection
            for param_name, param_val in func_args.items():
                save_intermediate_value(self.run_dir, node_id, f"in_{param_name}", param_val)
                
            # Execute node implementation
            try:
                if inspect.iscoroutinefunction(impl):
                    result = await impl(**func_args)
                else:
                    result = impl(**func_args)
            except Exception as e:
                log_event(
                    "executor",
                    "node_execution",
                    "CDG_NODE_FAILED",
                    run_id=self.run_id,
                    stage="execute_cdg",
                    node_id=node_id,
                    duration_ms=(time.perf_counter() - node_started_at) * 1000.0,
                    payload={
                        **event_payload,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                )
                # Save failure status
                with open(self.run_dir / "execution_trace.json", "w") as f:
                    json.dump(trace, f)
                with open(meta_file, "w") as f:
                    json.dump({
                        "run_id": self.run_id,
                        "repo": self.repo,
                        "timestamp": time.time(),
                        "execution_id": execution_id,
                        "version_id": version_id,
                        "status": "failed",
                        "error_node": node_id,
                        "error": str(e)
                    }, f)
                raise RuntimeError(f"Error executing atom '{primitive_name}' at node '{node.name}': {e}")
                
            # Distribute output values to state
            # If the atom returns multiple values as a tuple, map them to outputs
            if len(node.outputs) > 1 and isinstance(result, tuple):
                for idx, out in enumerate(node.outputs):
                    val = result[idx] if idx < len(result) else None
                    state[f"{node_id}/{out.name}"] = val
                    save_intermediate_value(self.run_dir, node_id, f"out_{out.name}", val)
            elif len(node.outputs) == 1:
                out = node.outputs[0]
                state[f"{node_id}/{out.name}"] = result
                save_intermediate_value(self.run_dir, node_id, f"out_{out.name}", result)
            else:
                # No formal outputs or returning single value for default "result" output port
                out_name = node.outputs[0].name if node.outputs else "result"
                state[f"{node_id}/{out_name}"] = result
                save_intermediate_value(self.run_dir, node_id, f"out_{out_name}", result)
                
            trace.append({"node_id": node_id, "name": node.name, "cached": False})
            log_event(
                "executor",
                "node_execution",
                "CDG_NODE_COMPLETED",
                run_id=self.run_id,
                stage="execute_cdg",
                node_id=node_id,
                duration_ms=(time.perf_counter() - node_started_at) * 1000.0,
                payload={**event_payload, "cached": False},
            )

        # Save success status
        with open(self.run_dir / "execution_trace.json", "w") as f:
            json.dump(trace, f)
        with open(meta_file, "w") as f:
            json.dump({
                "run_id": self.run_id,
                "repo": self.repo,
                "timestamp": time.time(),
                "target_node_id": target_node_id,
                "execution_id": execution_id,
                "version_id": version_id,
                "status": "completed"
            }, f)
            
        return {
            "run_id": self.run_id,
            "status": "completed",
            "trace": trace
        }
