"""Helpers for evaluating e2e benchmark runs against search-discipline policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sciona.principal.search_policy import (
    BenchmarkPolicyReport,
    evaluate_asset_migration_readiness,
    evaluate_enriched_cdg_policy,
    enforce_anti_shortcut_policy,
    evaluate_behavioral_benchmark_policy,
    summarize_asset_migration_readiness,
    summarize_proposal_selection,
    summarize_search_discipline,
    validate_required_benchmark_artifacts,
)
from sciona.principal.heuristic_outcomes import (
    extract_heuristic_outcomes,
    summarize_heuristic_outcomes,
)
from sciona.principal.runtime_usability import build_runtime_usability_assessment


def _load_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _serialize_policy(report: BenchmarkPolicyReport) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "violations": list(report.violations),
        "warnings": list(report.warnings),
        "details": dict(report.details),
    }


def _extract_planning_artifact(cdg_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(cdg_payload, dict):
        return {}
    planning_artifact = cdg_payload.get("planning_artifact")
    if isinstance(planning_artifact, dict):
        return dict(planning_artifact)
    metadata = cdg_payload.get("metadata", {})
    if isinstance(metadata, dict):
        nested = metadata.get("planning_artifact")
        if isinstance(nested, dict):
            return dict(nested)
    return {}


def _extract_skeleton_asset(cdg_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(cdg_payload, dict):
        return {}
    metadata = cdg_payload.get("metadata", {})
    if isinstance(metadata, dict):
        asset = metadata.get("skeleton_asset")
        if isinstance(asset, dict):
            return dict(asset)
    planning_artifact = _extract_planning_artifact(cdg_payload)
    skeleton_intent = planning_artifact.get("skeleton_intent", {})
    if isinstance(skeleton_intent, dict):
        asset = skeleton_intent.get("asset")
        if isinstance(asset, dict):
            return dict(asset)
    return {}


def _extract_runtime_context(mode_dir: Path) -> dict[str, Any]:
    for candidate in (
        mode_dir / "runtime_evidence.json",
        mode_dir / "profile_runtime_artifacts.json",
    ):
        payload = _load_json(candidate)
        if isinstance(payload, dict):
            runtime_context = payload.get("runtime_context", {})
            if isinstance(runtime_context, dict):
                return dict(runtime_context)
    return {}


def _extract_runtime_evidence(mode_dir: Path) -> dict[str, Any]:
    for candidate in (
        mode_dir / "runtime_evidence.json",
        mode_dir / "profile_runtime_artifacts.json",
    ):
        payload = _load_json(candidate)
        if isinstance(payload, dict):
            return dict(payload)
    return {}


def _extract_usability_assessment(mode_dir: Path) -> dict[str, Any]:
    runtime_evidence = _extract_runtime_evidence(mode_dir)
    assessment = runtime_evidence.get("usability_assessment", {})
    if not isinstance(assessment, dict) or not assessment:
        assessment = build_runtime_usability_assessment(runtime_evidence).model_dump(
            mode="json"
        )
    return dict(assessment)


def _extract_search_trace(mode_dir: Path) -> list[dict[str, Any]]:
    for candidate in (
        mode_dir / "trial_history.json",
        mode_dir / "planner_artifacts.json",
    ):
        if candidate.name == "trial_history.json":
            payload = _load_json(candidate)
            if isinstance(payload, list):
                return [item for item in payload if isinstance(item, dict)]
            if not isinstance(payload, dict):
                continue
            history = payload.get("trial_history", payload)
            if isinstance(history, list):
                return [item for item in history if isinstance(item, dict)]
            continue
        payload = _load_json(candidate)
        if not isinstance(payload, dict):
            continue
        attempts = payload.get("attempt_history", [])
        if isinstance(attempts, list):
            return [item for item in attempts if isinstance(item, dict)]
    return []


def _extract_applied_assets(search_trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    for entry in search_trace:
        expansion = entry.get("expansion", {})
        if not isinstance(expansion, dict):
            continue
        for key in ("diagnostic_assets", "applied_assets"):
            applied = expansion.get(key, [])
            if not isinstance(applied, list):
                continue
            for asset in applied:
                if isinstance(asset, dict):
                    assets.append(dict(asset))
    return assets


def _extract_asset_inventory(
    cdg_payload: dict[str, Any] | None,
    search_trace: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    skeleton_asset = _extract_skeleton_asset(cdg_payload)
    if skeleton_asset:
        inventory.append(skeleton_asset)
    inventory.extend(_extract_applied_assets(search_trace))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for asset in inventory:
        asset_id = str(asset.get("asset_id", "") or "")
        asset_version = str(asset.get("asset_version", "") or "")
        asset_operation = str(asset.get("asset_operation", "") or "")
        key = (asset_id, asset_version, asset_operation)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(asset)
    return deduped


def evaluate_e2e_variant(
    *,
    label: str,
    mode_dir: str | Path,
    latency_ms: int,
    ground_truth_hits: int,
    total_ground_truth: int,
    matches_total: int,
    matches_verified: int,
    shortcut_flags: dict[str, bool] | None = None,
    declared_shortcuts: list[str] | None = None,
    executable: bool = False,
) -> dict[str, Any]:
    """Evaluate one benchmark variant against the Phase 7 policy contract."""
    path = Path(mode_dir)
    cdg_payload = _load_json(path / "cdg.json")
    planning_artifact = _extract_planning_artifact(cdg_payload)
    skeleton_asset = _extract_skeleton_asset(cdg_payload)
    runtime_context = _extract_runtime_context(path)
    usability_assessment = _extract_usability_assessment(path)
    search_trace = _extract_search_trace(path)
    planner_artifacts = _load_json(path / "planner_artifacts.json") or {}
    asset_inventory = _extract_asset_inventory(cdg_payload, search_trace)
    migration_summary = summarize_asset_migration_readiness(asset_inventory)
    search_discipline = summarize_search_discipline(search_trace)
    proposal_selection = summarize_proposal_selection(search_trace)
    heuristic_outcomes = summarize_heuristic_outcomes(
        extract_heuristic_outcomes(search_trace)
    )

    family = str(
        planning_artifact.get("family_hint")
        or planning_artifact.get("paradigm")
        or ""
    )
    coverage = (
        float(ground_truth_hits) / float(total_ground_truth)
        if total_ground_truth > 0
        else 0.0
    )
    used_real_assets = bool(skeleton_asset) and label != "raw_llm"

    artifact_report = validate_required_benchmark_artifacts(
        {
            "planning_artifact": planning_artifact,
            "skeleton_asset": skeleton_asset,
            "search_trace": search_trace,
            "final_candidate": cdg_payload or {},
            "runtime_context": runtime_context,
            "usability_assessment": usability_assessment,
        },
        required_keys=(
            "planning_artifact",
            "skeleton_asset",
            "search_trace",
            "final_candidate",
            "runtime_context",
        ),
    )
    anti_shortcut = enforce_anti_shortcut_policy(
        {
            "shortcut_flags": dict(shortcut_flags or {}),
            "declared_shortcuts": list(declared_shortcuts or []),
        }
    )
    behavioral = evaluate_behavioral_benchmark_policy(
        {
            "family": family or label,
            "ground_truth_coverage": coverage,
            "used_real_assets": used_real_assets,
            "executable": executable,
            "usability_assessment": usability_assessment,
        },
        allowed_families={family or label},
        require_real_assets=(label != "raw_llm"),
    )
    enriched_cdg = evaluate_enriched_cdg_policy(
        {
            "search_discipline": {
                "trial_count": search_discipline.trial_count,
                "expansion_attempts": search_discipline.expansion_attempts,
                "admissibility_decisions": search_discipline.admissibility_decisions,
                "pruned_trials": search_discipline.pruned_trials,
                "reused_cached_evaluations": search_discipline.reused_cached_evaluations,
            },
            "proposal_selection": {
                "trial_count": proposal_selection.trial_count,
                "proposal_selection_trials": proposal_selection.proposal_selection_trials,
                "selected_trials": proposal_selection.selected_trials,
                "rejected_trials": proposal_selection.rejected_trials,
                "skipped_due_to_admissibility_trials": proposal_selection.skipped_due_to_admissibility_trials,
                "selected_proposal_counts": dict(proposal_selection.selected_proposal_counts),
                "heuristic_outcome_count": proposal_selection.heuristic_outcome_count,
                "positive_heuristic_outcome_count": proposal_selection.positive_heuristic_outcome_count,
            },
            "heuristic_outcomes": dict(heuristic_outcomes),
            "search_trace_summary": {
                "entry_count": len(search_trace),
                "applied_asset_count": max(
                    0,
                    migration_summary.get("asset_count", len(asset_inventory))
                    - (1 if skeleton_asset else 0),
                ),
            },
        }
    )
    asset_migration = evaluate_asset_migration_readiness(asset_inventory)

    return {
        "latency_ms": int(latency_ms),
        "matches_total": int(matches_total),
        "matches_verified": int(matches_verified),
        "ground_truth_hits": int(ground_truth_hits),
        "ground_truth_coverage": round(coverage, 2),
        "family": family,
        "used_real_assets": used_real_assets,
        "executable": bool(executable),
        "artifact_inventory": {
            "planning_artifact": bool(planning_artifact),
            "skeleton_asset": bool(skeleton_asset),
            "search_trace": bool(search_trace),
            "runtime_context": bool(runtime_context),
            "planner_artifacts": bool(planner_artifacts),
        },
        "usability_assessment": usability_assessment,
        "asset_inventory": {
            "asset_count": int(migration_summary.get("asset_count", len(asset_inventory))),
            "skeleton_asset": bool(skeleton_asset),
            "expansion_assets": max(
                0,
                int(migration_summary.get("asset_count", len(asset_inventory)))
                - (1 if skeleton_asset else 0),
            ),
            "ready_asset_count": int(migration_summary.get("ready_asset_count", 0) or 0),
            "blocked_asset_count": int(migration_summary.get("blocked_asset_count", 0) or 0),
            "ready_asset_ids": list(migration_summary.get("ready_asset_ids", [])),
            "blocked_asset_ids": list(migration_summary.get("blocked_asset_ids", [])),
            "records": list(migration_summary.get("records", [])),
        },
        "policy": {
            "required_artifacts": _serialize_policy(artifact_report),
            "anti_shortcut": _serialize_policy(anti_shortcut),
            "behavioral": _serialize_policy(behavioral),
            "enriched_cdg": _serialize_policy(enriched_cdg),
            "asset_migration": _serialize_policy(asset_migration),
        },
        "search_trace_summary": {
            "entry_count": len(search_trace),
            "execution_path": planner_artifacts.get("execution_path", ""),
            "verification_status": planner_artifacts.get("verification_status", ""),
            "expansion_attempts": search_discipline.expansion_attempts,
            "admissibility_decisions": search_discipline.admissibility_decisions,
            "proposal_selection_trials": proposal_selection.proposal_selection_trials,
            "selected_trials": proposal_selection.selected_trials,
            "applied_asset_count": max(
                0,
                int(migration_summary.get("asset_count", len(asset_inventory)))
                - (1 if skeleton_asset else 0),
            ),
        },
    }


def evaluate_e2e_benchmark_report(
    *,
    goal: str,
    prover: str,
    llm_provider: str,
    total_ground_truth: int,
    variants: dict[str, dict[str, Any]],
    shortcut_flags: dict[str, bool] | None = None,
    declared_shortcuts: list[str] | None = None,
    postprocess: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the top-level benchmark report with Phase 7 policy evaluation."""
    results: dict[str, Any] = {}
    for label, payload in sorted(variants.items()):
        results[label] = evaluate_e2e_variant(
            label=label,
            mode_dir=payload["mode_dir"],
            latency_ms=int(payload.get("latency_ms", 0) or 0),
            ground_truth_hits=int(payload.get("ground_truth_hits", 0) or 0),
            total_ground_truth=total_ground_truth,
            matches_total=int(payload.get("matches_total", 0) or 0),
            matches_verified=int(payload.get("matches_verified", 0) or 0),
            shortcut_flags=shortcut_flags,
            declared_shortcuts=declared_shortcuts,
            executable=bool(payload.get("executable", False)),
        )

    overall_shortcut = enforce_anti_shortcut_policy(
        {
            "shortcut_flags": dict(shortcut_flags or {}),
            "declared_shortcuts": list(declared_shortcuts or []),
        }
    )
    overall_passed = overall_shortcut.passed and all(
        variant["policy"]["required_artifacts"]["passed"]
        and variant["policy"]["anti_shortcut"]["passed"]
        and variant["policy"]["behavioral"]["passed"]
        and variant["policy"]["enriched_cdg"]["passed"]
        and variant["policy"]["asset_migration"]["passed"]
        for label, variant in results.items()
        if label != "raw_llm"
    )

    report = {
        "goal": goal,
        "prover": prover,
        "llm_provider": llm_provider,
        "ground_truth_atoms": total_ground_truth,
        "shortcut_flags": dict(shortcut_flags or {}),
        "declared_shortcuts": list(declared_shortcuts or []),
        "results": results,
        "benchmark_policy": {
            "passed": overall_passed,
            "anti_shortcut": _serialize_policy(overall_shortcut),
        },
    }
    if postprocess:
        report["postprocess"] = dict(postprocess)
    return report
