"""FastAPI routes for CDG local execution and value inspection."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import numpy as np

from sciona.visualizer.runner import CDGExecutionSession, RUNS_DIR, safe_eval_slice

logger = logging.getLogger(__name__)

router = APIRouter()


class RunCDGRequest(BaseModel):
    inputs: Dict[str, Any]


@router.post("/api/cdg/run")
async def run_cdg(
    request: Request,
    body: RunCDGRequest,
    repo: str = Query(..., description="The repository CDG path"),
    run_id: str = Query(..., description="Unique run identifier"),
    target_node_id: Optional[str] = Query(None, description="Optional target node ID for incremental execution"),
):
    driver = request.app.state.driver
    session = CDGExecutionSession(driver, repo, run_id)
    
    try:
        result = await session.execute(body.inputs, target_node_id=target_node_id)
        return result
    except ValueError as e:
        # Grounding error
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Error executing CDG")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/cdg/runs")
async def list_cdg_runs(
    repo: str = Query(..., description="Filter runs by repo path")
):
    runs_dir = RUNS_DIR
    if not runs_dir.exists():
        return {"runs": []}
        
    runs = []
    # Scan subdirectories
    for d in runs_dir.iterdir():
        if not d.is_dir():
            continue
        meta_file = d / "run_metadata.json"
        if meta_file.exists():
            try:
                with open(meta_file, "r") as f:
                    meta = json.load(f)
                    if meta.get("repo") == repo:
                        runs.append(meta)
            except Exception:
                pass
                
    # Sort runs newest first
    runs.sort(key=lambda r: r.get("timestamp", 0), reverse=True)
    return {"runs": runs}


@router.get("/api/cdg/runs/{run_id}/existing")
async def list_existing_run_nodes(run_id: str):
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        return {"nodes": []}
        
    existing_nodes = []
    # Subdirectories represent node execution folders
    for d in run_dir.iterdir():
        if not d.is_dir() or d.name == "uploads":
            continue
            
        # Check if this node has any saved inputs/outputs
        has_outputs = any(f.name.startswith("in_") or f.name.startswith("out_") for f in d.iterdir())
        if has_outputs:
            existing_nodes.append(d.name)
            
    return {"nodes": existing_nodes}


@router.get("/api/cdg/runs/{run_id}/nodes/{node_id}/values")
async def list_node_variables(run_id: str, node_id: str):
    node_dir = RUNS_DIR / run_id / node_id
    if not node_dir.exists():
        return {"inputs": {}, "outputs": {}}
        
    inputs = {}
    outputs = {}
    
    # Read metadata for each value
    for f in node_dir.glob("*.json"):
        name = f.stem
        # Read JSON metadata
        try:
            with open(f, "r") as fh:
                meta = json.load(fh)
                
            # Distinguish inputs vs outputs
            if name.startswith("in_"):
                var_name = name[3:]
                inputs[var_name] = meta
            elif name.startswith("out_"):
                var_name = name[4:]
                outputs[var_name] = meta
        except Exception:
            pass
            
    return {"inputs": inputs, "outputs": outputs}


@router.get("/api/cdg/runs/{run_id}/nodes/{node_id}/values/{value_name}/slice")
async def get_variable_slice(
    run_id: str,
    node_id: str,
    value_name: str,
    slice_query: Optional[str] = Query(None, alias="slice")
):
    node_dir = RUNS_DIR / run_id / node_id
    npy_path = node_dir / f"{value_name}.npy"
    json_path = node_dir / f"{value_name}.json"
    
    # Check for numpy array first
    if npy_path.exists():
        try:
            arr = np.load(npy_path)
            # Apply slice if query param is set
            if slice_query:
                arr = safe_eval_slice(arr, slice_query)
                
            # Format and return slice structure
            if arr.ndim == 0:
                return {
                    "type": "scalar",
                    "data": arr.item(),
                    "dtype": str(arr.dtype)
                }
            elif arr.ndim == 1:
                # Downsample 1D arrays if they are extremely large (>2000 points) to avoid browser lag
                data_list = arr.tolist()
                downsampled = False
                if len(data_list) > 2000:
                    step = len(data_list) // 1000
                    data_list = data_list[::step]
                    downsampled = True
                return {
                    "type": "1d",
                    "data": data_list,
                    "shape": list(arr.shape),
                    "dtype": str(arr.dtype),
                    "downsampled": downsampled
                }
            elif arr.ndim == 2:
                # Downsample 2D grids if too large (e.g. limit to 200x200 values for table rendering)
                data_list = arr.tolist()
                downsampled = False
                if arr.shape[0] > 200 or arr.shape[1] > 200:
                    # Return only metadata and downsample alert
                    downsampled = True
                return {
                    "type": "2d",
                    "data": data_list if not downsampled else data_list[:100][:100],  # partial preview
                    "shape": list(arr.shape),
                    "dtype": str(arr.dtype),
                    "downsampled": downsampled
                }
            else:
                return {
                    "type": "nd",
                    "shape": list(arr.shape),
                    "dtype": str(arr.dtype),
                    "message": "Sliced output remains multi-dimensional. Please specify a more specific slice query (e.g. [0, :, :]) to inspect a 1D or 2D view."
                }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error slicing numpy array: {e}")
            
    # Check for standard json metadata/value
    elif json_path.exists():
        try:
            with open(json_path, "r") as fh:
                meta = json.load(fh)
            return {
                "type": "json",
                "data": meta.get("value"),
                "metadata": meta
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error reading JSON output: {e}")
            
    raise HTTPException(status_code=404, detail="Variable not found.")


@router.post("/api/cdg/upload")
async def upload_file(
    file: UploadFile = File(...),
    run_id: str = Query(..., description="Unique run identifier")
):
    upload_dir = RUNS_DIR / run_id / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = upload_dir / file.filename
    try:
        with open(file_path, "wb") as f:
            f.write(await file.read())
        return {"filepath": str(file_path.resolve())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {e}")


@router.get("/api/datasets")
async def list_datasets(
    consumer_fqdn: str | None = Query(None),
    input_port: str | None = Query(None),
):
    from sciona.visualizer.dataset_manager import DatasetManager
    try:
        return await run_in_threadpool(
            lambda: DatasetManager().list_datasets(
                consumer_fqdn=consumer_fqdn,
                input_port=input_port,
            )
        )
    except Exception as e:
        logger.exception("Failed to list datasets")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/cdg/primitive/{name:path}/curated_inputs")
async def get_curated_inputs(name: str, input_port: str | None = Query(None)):
    from sciona.visualizer.dataset_manager import DatasetManager
    try:
        dm = DatasetManager()
        return await run_in_threadpool(
            lambda: dm.list_datasets(consumer_fqdn=name, input_port=input_port)
        )
    except Exception as e:
        logger.exception("Failed to get curated inputs")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/datasets/preview")
async def preview_dataset(fqn: str = Query(..., description="The dataset FQN")):
    from sciona.visualizer.dataset_manager import DatasetManager
    try:
        return await run_in_threadpool(lambda: DatasetManager().load_manifest(fqn))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Failed to preview dataset")
        raise HTTPException(status_code=500, detail=str(e))
