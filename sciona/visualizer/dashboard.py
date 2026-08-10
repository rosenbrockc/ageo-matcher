"""Dashboard telemetry routes."""

from __future__ import annotations

import asyncio
import json
import queue
import time
from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from sciona.telemetry import list_runtime_runs, load_persisted_runs, load_runs_from_store
from sciona.visualizer.dashboard_helpers import (
    _compute_coverage_from_dicts,
    _compute_errors_from_dicts,
    _decorate_dashboard_run,
    _merge_runs,
)

router = APIRouter()


class HumanGuidanceRequest(BaseModel):
    version_id: str
    action: Literal["refine", "reject", "accept", "stop"]
    note: str = ""
    node_id: str = ""


@router.get("/api/dashboard/runs")
async def list_dashboard_runs(
    limit: int = Query(50, ge=1, le=500),
    state: str = Query("all", description="Filter by state: all|running|completed|failed"),
) -> dict[str, Any]:
    from sciona.config import AgeomConfig

    config = AgeomConfig()
    wanted = state.strip().lower()
    status_filter = wanted if wanted != "all" else None
    pg_runs = await load_runs_from_store(limit=max(limit * 3, 100), status=status_filter)
    file_runs = (
        load_persisted_runs(config.telemetry_runs_dir, limit=max(limit * 3, 100))
        if pg_runs is None
        else []
    )
    rows = _merge_runs(pg_runs or file_runs, list_runtime_runs())
    if wanted != "all" and pg_runs is None:
        rows = [r for r in rows if str(r.get("status", "")).lower() == wanted]
    now = time.time()
    rows = [
        _decorate_dashboard_run(
            r, stale_seconds=max(5, int(config.telemetry_stale_seconds)), now=now
        )
        for r in rows[:limit]
    ]
    return {"runs": rows, "count": len(rows)}


@router.get("/api/dashboard/runs/{run_id}")
async def get_dashboard_run(run_id: str) -> dict[str, Any]:
    from sciona.config import AgeomConfig
    from sciona.telemetry import get_persisted_run, get_runtime_run, load_run_from_store

    config = AgeomConfig()
    row = get_runtime_run(run_id)
    if row is None:
        row = await load_run_from_store(run_id)
    if row is None:
        row = get_persisted_run(config.telemetry_runs_dir, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return _decorate_dashboard_run(
        row,
        stale_seconds=max(5, int(config.telemetry_stale_seconds)),
        now=time.time(),
    )


@router.get("/api/dashboard/runs/{run_id}/evolution")
async def get_run_evolution(run_id: str) -> dict[str, Any]:
    """Return graph versions and evaluated transitions for a Principal run."""
    run = await get_dashboard_run(run_id)
    evolution = run.get("optimize_summary", {}).get("evolution", {})
    if not isinstance(evolution, dict) or not evolution.get("versions"):
        raise HTTPException(
            status_code=404,
            detail=f"No graph evolution snapshots found for run: {run_id}",
        )
    return evolution


@router.post("/api/dashboard/runs/{run_id}/guidance")
async def record_run_guidance(
    run_id: str, body: HumanGuidanceRequest
) -> dict[str, Any]:
    """Persist a human search-direction decision in the run event stream."""
    await get_dashboard_run(run_id)
    from sciona.telemetry import log_event

    event = log_event(
        "human",
        "guidance",
        "HUMAN_GUIDANCE_RECORDED",
        run_id=run_id,
        node_id=body.node_id,
        payload={
            "version_id": body.version_id,
            "action": body.action,
            "note": body.note.strip(),
        },
    )
    return {
        "recorded": True,
        "run_id": run_id,
        "timestamp": event.timestamp,
    }


@router.get("/api/dashboard/latest")
async def get_latest_dashboard_run() -> dict[str, Any]:
    payload = await list_dashboard_runs(limit=1, state="all")
    runs = payload.get("runs", [])
    if not isinstance(runs, list) or not runs:
        raise HTTPException(status_code=404, detail="No telemetry runs found")
    latest = runs[0]
    if not isinstance(latest, dict):
        raise HTTPException(status_code=404, detail="No telemetry runs found")
    return latest


@router.get("/api/dashboard/runs/{run_id}/events")
async def list_run_events(
    run_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=2000),
    phase: str | None = Query(None),
    event_type: str | None = Query(None),
    prompt_key: str | None = Query(None),
    round_name: str | None = Query(None, alias="round"),
    has_error: bool | None = Query(None),
) -> dict[str, Any]:
    from sciona.telemetry import get_event_log, load_events_from_store

    pg_result = await load_events_from_store(
        run_id,
        offset=offset,
        limit=limit,
        phase=phase,
        event_type=event_type,
        prompt_key=prompt_key,
        round_name=round_name,
        has_error=has_error,
    )
    if pg_result is not None:
        events, total = pg_result
        return {"run_id": run_id, "total": total, "offset": offset, "limit": limit, "events": events}
    events = get_event_log().events_for_run(run_id)
    filtered: list[dict[str, Any]] = []
    for ev in events:
        if phase and ev.phase != phase:
            continue
        if event_type and ev.event_type != event_type:
            continue
        if prompt_key and ev.prompt_key != prompt_key:
            continue
        if round_name and ev.round != round_name:
            continue
        d = asdict(ev)
        is_error = "ERROR" in ev.event_type or "FAIL" in ev.event_type or "error" in ev.payload
        if has_error is True and not is_error:
            continue
        if has_error is False and is_error:
            continue
        filtered.append(d)
    total = len(filtered)
    page = filtered[offset : offset + limit]
    return {"run_id": run_id, "total": total, "offset": offset, "limit": limit, "events": page}


@router.get("/api/dashboard/runs/{run_id}/stream")
async def stream_run_events(run_id: str) -> EventSourceResponse:
    from sciona.telemetry import get_runtime_run, subscribe_events, unsubscribe_events

    sub_id, q = subscribe_events(run_id)

    async def event_generator():
        keepalive_counter = 0
        try:
            while True:
                try:
                    event_dict = await asyncio.to_thread(q.get, True, 1.0)
                    yield {"event": event_dict.get("event_type", "event"), "data": json.dumps(event_dict, default=str)}
                    keepalive_counter = 0
                except queue.Empty:
                    keepalive_counter += 1
                    if keepalive_counter >= 15:
                        yield {"comment": "keepalive"}
                        keepalive_counter = 0
                    run = get_runtime_run(run_id)
                    if run and run.get("status") in ("completed", "failed"):
                        while True:
                            try:
                                event_dict = q.get(block=False)
                                yield {"event": event_dict.get("event_type", "event"), "data": json.dumps(event_dict, default=str)}
                            except queue.Empty:
                                break
                        yield {"event": "done", "data": json.dumps({"status": run["status"]})}
                        return
        finally:
            unsubscribe_events(sub_id)

    return EventSourceResponse(event_generator())


@router.get("/api/dashboard/runs/{run_id}/coverage")
async def run_prompt_coverage(run_id: str) -> dict[str, Any]:
    from sciona.telemetry import get_event_log, _pg_store as _store_ref

    if _store_ref is not None:
        try:
            pg_events = await _store_ref.list_events_for_coverage(run_id)
            if pg_events is not None:
                return _compute_coverage_from_dicts(run_id, pg_events)
        except Exception:
            pass
    return _compute_coverage_from_dicts(
        run_id, [asdict(ev) for ev in get_event_log().events_for_run(run_id)]
    )


@router.get("/api/dashboard/runs/{run_id}/errors")
async def run_error_drilldown(run_id: str) -> dict[str, Any]:
    from sciona.telemetry import get_event_log, _pg_store as _store_ref

    if _store_ref is not None:
        try:
            pg_events = await _store_ref.list_events_for_errors(run_id)
            if pg_events is not None:
                return _compute_errors_from_dicts(run_id, pg_events)
        except Exception:
            pass
    return _compute_errors_from_dicts(
        run_id, [asdict(ev) for ev in get_event_log().events_for_run(run_id)]
    )
