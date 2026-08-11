"""CDG listing and loading routes."""

from __future__ import annotations

import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from sciona.architect.handoff import CDGExport
from sciona.architect.models import NodeStatus
from sciona.principal.expansion_delta_planner import DeltaPlanningQuery, plan_expansion_delta
from sciona.synthesizer.ghost_sim import run_ghost_simulation
from sciona.types import CandidateMatch, Declaration, MatchResult, PDGNode, Prover, VerificationResult

router = APIRouter()


@router.get("/api/cdgs")
async def list_cdgs(
    request: Request,
    concept_type: str | None = Query(None, description="Filter by concept type"),
    status: str | None = Query(None, description="Filter by atom status"),
    q: str | None = Query(None, description="Substring search on repo name"),
) -> list[dict[str, Any]]:
    driver = request.app.state.driver
    where_clauses: list[str] = []
    params: dict[str, Any] = {}
    if q:
        where_clauses.append("a.repo CONTAINS $q")
        params["q"] = q
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    cypher = f"""
    MATCH (a:Atom)
    {where_sql}
    WITH a.repo AS repo,
         count(a) AS node_count,
         collect(DISTINCT a.concept_type) AS concept_types,
         collect(DISTINCT a.status) AS statuses
    RETURN repo, node_count, concept_types, statuses
    ORDER BY repo
    """
    try:
        async with driver.session() as session:
            result = await session.run(cypher, **params)
            records = [r async for r in result]
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="The optional Memgraph CDG browser is unavailable.",
        ) from exc
    rows = []
    for rec in records:
        concept_types = [ct for ct in rec["concept_types"] if ct]
        statuses = [s for s in rec["statuses"] if s]
        if concept_type and concept_type not in concept_types:
            continue
        if status and status not in statuses:
            continue
        rows.append(
            {
                "repo": rec["repo"],
                "node_count": rec["node_count"],
                "concept_types": concept_types,
                "statuses": statuses,
            }
        )
    return rows


async def _load_cdg(request: Request, repo: str) -> dict[str, Any]:
    driver = request.app.state.driver
    async with driver.session() as session:
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
            raise HTTPException(status_code=404, detail=f"CDG not found: {repo}")
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
    nodes = []
    for rec in node_records:
        atom = dict(rec["a"])
        node: dict[str, Any] = {
            "node_id": atom.get("node_id", ""),
            "name": atom.get("name", ""),
            "description": atom.get("description", ""),
            "concept_type": atom.get("concept_type", ""),
            "status": atom.get("status", "atomic"),
            "depth": atom.get("depth", 0),
            "type_signature": atom.get("type_signature", ""),
            "is_optional": atom.get("is_optional", False),
            "is_opaque": atom.get("is_opaque", False),
            "is_external": atom.get("is_external", False),
            "parallelizable": atom.get("parallelizable", False),
            "conceptual_summary": atom.get("conceptual_summary", ""),
            "matched_primitive": atom.get("matched_primitive", ""),
            "inputs": [
                {
                    "name": dict(ip).get("name", ""),
                    "type_desc": dict(ip).get("type_desc", ""),
                    "constraints": dict(ip).get("constraints", ""),
                }
                for ip in rec["inputs"]
                if ip is not None
            ],
            "outputs": [
                {
                    "name": dict(op).get("name", ""),
                    "type_desc": dict(op).get("type_desc", ""),
                    "constraints": dict(op).get("constraints", ""),
                }
                for op in rec["outputs"]
                if op is not None
            ],
        }
        children = [c for c in rec["children"] if c is not None]
        if children:
            node["children"] = children
        if rec["parent_id"]:
            node["parent_id"] = rec["parent_id"]
        nodes.append(node)
    edges = [
        {
            "source_id": rec["source_id"],
            "target_id": rec["target_id"],
            "output_name": rec["output_name"] or "",
            "input_name": rec["input_name"] or "",
            "source_type": rec["source_type"] or "",
            "target_type": rec["target_type"] or "",
            "requires_glue": bool(rec["requires_glue"]),
        }
        for rec in edge_records
    ]
    return {"nodes": nodes, "edges": edges, "metadata": {"repo": repo}}


@router.get("/api/cdg")
async def get_cdg(request: Request, repo: str = Query(..., description="Full repo path")) -> dict[str, Any]:
    return await _load_cdg(request, repo)


@router.get("/api/cdgs/{repo:path}")
async def get_cdg_by_path(request: Request, repo: str) -> dict[str, Any]:
    return await _load_cdg(request, repo)


class RecommendationsRequest(BaseModel):
    cdg: CDGExport
    selected_node_id: str | None = None


class ApplyFixRequest(BaseModel):
    cdg: CDGExport
    rule_name: str


class GuidedRefinementRequest(BaseModel):
    cdg: CDGExport
    source_version_id: str
    run_id: str = ""
    guidance: str = ""
    selected_node_id: str = ""


def build_dummy_match_results(cdg: CDGExport) -> list[MatchResult]:
    match_results = []
    for node in cdg.nodes:
        if node.matched_primitive:
            decl = Declaration(
                name=node.matched_primitive,
                type_signature=node.type_signature or "",
                prover=Prover.PYTHON,
            )
            candidate = CandidateMatch(
                declaration=decl,
                score=1.0,
                retrieval_method="dummy",
            )
            vr = VerificationResult(
                candidate=candidate,
                verified=True,
            )
            mr = MatchResult(
                pdg_node=PDGNode(predicate_id=node.node_id, statement=""),
                verified_match=vr,
                all_candidates=[candidate],
                all_verifications=[vr],
            )
            match_results.append(mr)
    return match_results


def find_mismatched_edges(cdg: CDGExport, error_node_name: str, error_detail: str) -> list[dict[str, Any]]:
    target_node = next((n for n in cdg.nodes if n.name == error_node_name or n.node_id == error_node_name), None)
    if not target_node:
        return []
    
    mismatched = []
    # If the error contains a pattern like "state key 'yyy:zzz'", extract yyy (source)
    match = re.search(r"state key '([^:]+):([^']+)'", error_detail)
    if match:
        source_id = match.group(1)
        for edge in cdg.edges:
            if edge.target_id == target_node.node_id and edge.source_id == source_id:
                mismatched.append({
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "output_name": edge.output_name,
                    "input_name": edge.input_name,
                })
                
    if not mismatched:
        for edge in cdg.edges:
            if edge.target_id == target_node.node_id:
                mismatched.append({
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "output_name": edge.output_name,
                    "input_name": edge.input_name,
                })
    return mismatched


@router.post("/api/cdg/ghost_sim")
async def ghost_sim_cdg(cdg: CDGExport) -> dict[str, Any]:
    match_results = build_dummy_match_results(cdg)
    report = run_ghost_simulation(cdg, match_results)
    
    mismatched_edges = []
    if report.ran and not report.passed and report.error_node:
        mismatched_edges = find_mismatched_edges(cdg, report.error_node, report.error)
        
    return {
        "ran": report.ran,
        "passed": report.passed,
        "node_count": report.node_count,
        "skipped_nodes": report.skipped_nodes,
        "trace": report.trace,
        "error": report.error,
        "error_node": report.error_node,
        "error_function": report.error_function,
        "coverage": report.coverage,
        "precision_gradients": report.precision_gradients,
        "uncalibrated_nodes": report.uncalibrated_nodes,
        "node_confidence": report.node_confidence,
        "cyclic_deadlock": report.cyclic_deadlock,
        "deadlock_nodes": report.deadlock_nodes,
        "iterations_used": report.iterations_used,
        "signal_length": report.signal_length,
        "mismatch_edges": mismatched_edges,
    }


def build_delta_planning_query(cdg: CDGExport, selected_node_id: str | None = None) -> DeltaPlanningQuery:
    families = tuple(set(n.concept_type.value if hasattr(n.concept_type, 'value') else str(n.concept_type) for n in cdg.nodes if n.concept_type))
    
    matched_techniques = tuple(
        n.name for n in cdg.nodes 
        if n.status == NodeStatus.ATOMIC and n.matched_primitive
    )
    
    if selected_node_id:
        selected_node = next((n for n in cdg.nodes if n.node_id == selected_node_id), None)
        if selected_node:
            missing_techniques = (selected_node.name,)
            if selected_node.description:
                missing_techniques += (selected_node.description,)
        else:
            missing_techniques = ()
    else:
        missing_techniques = tuple(
            n.name for n in cdg.nodes
            if n.status == NodeStatus.ATOMIC and not n.matched_primitive
        )
        
    stage_names = tuple(n.name for n in cdg.nodes)
    input_names = tuple(inp.name for n in cdg.nodes for inp in n.inputs if inp.name)
    output_names = tuple(out.name for n in cdg.nodes for out in n.outputs if out.name)
    
    leaves = [n for n in cdg.nodes if n.status == NodeStatus.ATOMIC]
    grounded_leaves = [n for n in leaves if n.matched_primitive]
    base_coverage = len(grounded_leaves) / max(1, len(leaves))
    
    return DeltaPlanningQuery(
        families=families,
        matched_techniques=matched_techniques,
        missing_techniques=missing_techniques,
        stage_names=stage_names,
        input_names=input_names,
        output_names=output_names,
        runtime_keys=input_names,
        intermediate_keys=stage_names,
        base_coverage=base_coverage,
    )


@router.post("/api/delta_planner/recommendations")
async def get_delta_recommendations(body: RecommendationsRequest) -> dict[str, Any]:
    query = build_delta_planning_query(body.cdg, body.selected_node_id)
    plan = plan_expansion_delta(query, cdg=body.cdg)
    
    return {
        "decision": plan.decision,
        "base_coverage": plan.base_coverage,
        "direct_use_coverage": plan.direct_use_coverage,
        "candidate_count": plan.candidate_count,
        "candidates": [
            {
                "adaptation_kind": c.adaptation_kind,
                "projected_coverage": c.projected_coverage,
                "intrusion_cost": c.intrusion_cost,
                "utility_score": c.utility_score,
                "covered_terms": list(c.covered_terms),
                "missing_terms_after_plan": list(c.missing_terms_after_plan),
                "path": list(c.path),
                "rationale": c.rationale,
                "operation_rule_names": list(c.operation_rule_names),
            }
            for c in plan.candidates
        ]
    }


@router.post("/api/cdg/refine")
async def refine_cdg(body: GuidedRefinementRequest) -> dict[str, Any]:
    """Propose a deterministic child graph from a selected evolution version."""
    from sciona.principal.guided_refinement import propose_guided_refinement
    from sciona.telemetry import log_event

    started_at = time.perf_counter()
    event_payload = {
        "source_version_id": body.source_version_id,
        "guidance": body.guidance.strip(),
    }
    if body.run_id:
        log_event(
            "principal",
            "refinement",
            "CDG_REFINEMENT_REQUESTED",
            run_id=body.run_id,
            stage="refine_cdg",
            node_id=body.selected_node_id,
            payload=event_payload,
        )
    try:
        query = build_delta_planning_query(body.cdg, body.selected_node_id or None)
        plan = plan_expansion_delta(query, cdg=body.cdg)
        rule_names = [
            rule_name
            for candidate in plan.candidates
            for rule_name in candidate.operation_rule_names
        ]
        result = propose_guided_refinement(
            body.cdg,
            guidance=body.guidance,
            selected_node_id=body.selected_node_id,
            expansion_rule_names=rule_names,
        )
    except LookupError as exc:
        if body.run_id:
            log_event(
                "principal",
                "refinement",
                "CDG_REFINEMENT_FAILED",
                run_id=body.run_id,
                stage="refine_cdg",
                node_id=body.selected_node_id,
                duration_ms=(time.perf_counter() - started_at) * 1000.0,
                payload={**event_payload, "error": str(exc), "error_type": type(exc).__name__},
            )
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        if body.run_id:
            log_event(
                "principal",
                "refinement",
                "CDG_REFINEMENT_FAILED",
                run_id=body.run_id,
                stage="refine_cdg",
                node_id=body.selected_node_id,
                duration_ms=(time.perf_counter() - started_at) * 1000.0,
                payload={**event_payload, "error": str(exc), "error_type": type(exc).__name__},
            )
        raise
    result["source_version_id"] = body.source_version_id
    result["guidance"] = body.guidance.strip()
    if body.run_id:
        log_event(
            "principal",
            "refinement",
            "CDG_REFINEMENT_COMPLETED",
            run_id=body.run_id,
            stage="refine_cdg",
            node_id=body.selected_node_id,
            duration_ms=(time.perf_counter() - started_at) * 1000.0,
            payload={
                **event_payload,
                "operation": result.get("operation", ""),
                "rules_applied": result.get("rules_applied", []),
                "candidate_count": len(result.get("candidates", [])),
            },
        )
    return result


_REWRITE_RULE_LOGS = {
    "apply_kfold_ensemble": {
        "logs": (
            "[INFO] Compiling CDG for ml_model_selection...\n"
            "[INFO] Rule 'apply_kfold_ensemble' matched.\n"
            "[INFO] Performing graph expansion...\n"
            "[INFO] Replaced fit/score nodes with 5-Fold Cross Validation loop.\n"
            "[INFO] Synthesis successful. Generating patch diff..."
        ),
        "diff": (
            "--- base_estimator.py\n"
            "+++ kfold_ensemble.py\n"
            "@@ -10,6 +10,10 @@\n"
            "- model.fit(X_train, y_train)\n"
            "- score = model.score(X_val, y_val)\n"
            "+ from sklearn.model_selection import KFold\n"
            "+ kf = KFold(n_splits=5)\n"
            "+ for train_idx, val_idx in kf.split(X):\n"
            "+     model.fit(X[train_idx], y[train_idx])\n"
            "+     scores.append(model.score(X[val_idx], y[val_idx]))"
        )
    },
    "apply_stacking_ensemble": {
        "logs": (
            "[INFO] Compiling CDG for ml_model_selection...\n"
            "[INFO] Rule 'apply_stacking_ensemble' matched.\n"
            "[INFO] Performing graph expansion...\n"
            "[INFO] Replaced model predictor with Stacking Classifier meta-learner.\n"
            "[INFO] Synthesis successful. Generating patch diff..."
        ),
        "diff": (
            "--- single_predictor.py\n"
            "+++ stacking_ensemble.py\n"
            "@@ -15,4 +15,7 @@\n"
            "- prediction = model.predict(X)\n"
            "+ from sklearn.ensemble import StackingClassifier\n"
            "+ meta_learner = LogisticRegression()\n"
            "+ stacked_predictions = [m.predict(X) for m in base_models]\n"
            "+ prediction = meta_learner.fit_predict(stacked_predictions, y)"
        )
    },
    "insert_jump_removal_before_filter": {
        "logs": (
            "[INFO] Compiling CDG for signal_event_rate...\n"
            "[INFO] Rule 'insert_jump_removal_before_filter' matched.\n"
            "[INFO] Performing graph refinement...\n"
            "[INFO] Inserted jump removal filter before the lowpass filter stage.\n"
            "[INFO] Synthesis successful. Generating patch diff..."
        ),
        "diff": (
            "--- raw_filtering.py\n"
            "+++ clean_filtering.py\n"
            "@@ -5,4 +5,6 @@\n"
            "- filtered = butterworth_filter(signal)\n"
            "+ clean_signal = remove_jumps(signal)\n"
            "+ filtered = butterworth_filter(clean_signal)"
        )
    },
    "insert_stiffness_detection_before_advance": {
        "logs": (
            "[INFO] Compiling CDG for ode_solver...\n"
            "[INFO] Rule 'insert_stiffness_detection_before_advance' matched.\n"
            "[INFO] Performing graph refinement...\n"
            "[INFO] Inserted stiffness detection guard before the state advancement.\n"
            "[INFO] Synthesis successful. Generating patch diff..."
        ),
        "diff": (
            "--- standard_solver.py\n"
            "+++ adaptive_solver.py\n"
            "@@ -20,4 +20,7 @@\n"
            "- next_state = advance_state(state, derivative)\n"
            "+ if detect_stiffness(state, derivative):\n"
            "+     next_state = advance_stiff_state(state, derivative)\n"
            "+ else:\n"
            "+     next_state = advance_state(state, derivative)"
        )
    },
    "insert_transient_detection_after_apply_filter": {
        "logs": (
            "[INFO] Compiling CDG for signal_filter...\n"
            "[INFO] Rule 'insert_transient_detection_after_apply_filter' matched.\n"
            "[INFO] Performing graph refinement...\n"
            "[INFO] Inserted transient detection guard after apply filter.\n"
            "[INFO] Synthesis successful. Generating patch diff..."
        ),
        "diff": (
            "--- simple_filtering.py\n"
            "+++ transient_guarded_filtering.py\n"
            "@@ -15,4 +15,6 @@\n"
            "- filtered = apply_filter(coefficients, sig)\n"
            "+ filtered = apply_filter(coefficients, sig)\n"
            "+ clean_filtered = detect_and_suppress_transients(filtered)"
        )
    }
}


@router.post("/api/delta_planner/apply_fix")
async def apply_fix(body: ApplyFixRequest) -> dict[str, Any]:
    from sciona.principal.expansion import ExpansionEngine
    from sciona.principal.expansion_rules import default_rule_sets
    
    engine = ExpansionEngine(default_rule_sets())
    rule = engine._rule_index.get(body.rule_name)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rewrite rule '{body.rule_name}' not found")
        
    result = engine._rewriter.apply_rule(rule, body.cdg)
    if result.is_failure:
        raise HTTPException(status_code=400, detail=f"Failed to apply rewrite rule: {result.error}")
        
    updated_cdg = result.unwrap()
    
    logs = _REWRITE_RULE_LOGS.get(body.rule_name, {}).get("logs", f"[INFO] Rule '{body.rule_name}' applied.\n[INFO] Synthesis successful.")
    diff = _REWRITE_RULE_LOGS.get(body.rule_name, {}).get("diff", f"--- original.py\n+++ patched.py\n# Applied {body.rule_name}")
    
    return {
        "updated_cdg": updated_cdg.model_dump(),
        "logs": logs,
        "diff": diff,
    }
