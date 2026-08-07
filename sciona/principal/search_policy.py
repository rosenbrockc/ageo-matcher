"""Search-discipline and benchmark-policy contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sciona.principal.heuristic_outcomes import (
    extract_heuristic_outcomes,
    summarize_heuristic_outcomes,
)
from sciona.usability import UsabilityAssessment


@dataclass(frozen=True)
class SearchDisciplineSummary:
    """Compact summary of how a run searched rather than only how it ended."""

    trial_count: int
    expansion_attempts: int
    admissibility_decisions: int
    pruned_trials: int
    reused_cached_evaluations: int


@dataclass(frozen=True)
class ProposalSelectionSummary:
    """Compact summary of proposal-selection behavior in trial history."""

    trial_count: int
    proposal_selection_trials: int
    selected_trials: int
    rejected_trials: int
    skipped_due_to_admissibility_trials: int
    selected_proposal_counts: dict[str, int] = field(default_factory=dict)
    proposal_selection_labels: tuple[str, ...] = ()
    mean_selected_proposal_improvement: float = 0.0
    best_selected_proposal_improvement: float = 0.0
    heuristic_outcome_count: int = 0
    positive_heuristic_outcome_count: int = 0


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dict(dumped)
    if hasattr(value, "__dict__"):
        return {
            key: val
            for key, val in vars(value).items()
            if not key.startswith("_")
        }
    return {}


def _normalize_proposal_selection(entry: dict[str, Any]) -> dict[str, Any]:
    proposal = _coerce_mapping(entry.get("proposal_selection", {}))
    if not proposal:
        return {}

    selected = proposal.get("selected")
    if selected in (None, ""):
        selected = proposal.get("selected_proposal")
    if selected in (None, ""):
        selected = proposal.get("proposal_selected")
    if selected in (None, ""):
        selected = proposal.get("label")
    decision = proposal.get("decision")
    if selected in (None, "") and isinstance(decision, dict):
        selected = decision.get("selected")
    selected = str(selected or "")

    candidates = proposal.get("candidates", proposal.get("proposal_candidates", []))
    if not isinstance(candidates, list):
        candidates = []

    candidate_labels: list[str] = []
    selected_loss: float | None = None
    for candidate in candidates:
        candidate_map = _coerce_mapping(candidate)
        if not candidate_map:
            continue
        label = str(
            candidate_map.get("label")
            or candidate_map.get("proposal_type")
            or candidate_map.get("name")
            or ""
        ).strip()
        if label:
            candidate_labels.append(label)
        if selected and label == selected:
            try:
                selected_loss = float(candidate_map.get("loss", 0.0) or 0.0)
            except (TypeError, ValueError):
                selected_loss = None

    baseline_loss = proposal.get("baseline_loss", proposal.get("proposal_baseline_loss"))
    try:
        baseline_loss_value = (
            float(baseline_loss) if baseline_loss is not None else None
        )
    except (TypeError, ValueError):
        baseline_loss_value = None

    selected_improvement = proposal.get("proposal_improvement")
    if selected_improvement is None and baseline_loss_value is not None and selected_loss is not None:
        selected_improvement = baseline_loss_value - selected_loss
    try:
        selected_improvement_value = (
            float(selected_improvement) if selected_improvement is not None else None
        )
    except (TypeError, ValueError):
        selected_improvement_value = None

    return {
        "selected": selected,
        "candidate_labels": candidate_labels,
        "candidate_count": len(candidates),
        "selected_improvement": selected_improvement_value,
        "skipped_due_to_admissibility": bool(
            proposal.get("skipped_due_to_admissibility")
        ),
        "skip_reason": str(proposal.get("skip_reason", "") or ""),
        "proposal": proposal,
    }


def summarize_proposal_selection(
    trial_history: list[dict[str, Any]],
) -> ProposalSelectionSummary:
    """Summarize proposal-selection behavior from persisted trial history."""
    proposal_selection_trials = 0
    selected_trials = 0
    rejected_trials = 0
    skipped_due_to_admissibility_trials = 0
    selected_proposal_counts: dict[str, int] = {}
    proposal_selection_labels: list[str] = []
    improvements: list[float] = []

    for entry in trial_history:
        if not isinstance(entry, dict):
            continue
        proposal = _normalize_proposal_selection(entry)
        if not proposal:
            continue

        proposal_selection_trials += 1
        proposal_selection_labels.extend(
            label for label in proposal.get("candidate_labels", []) if label
        )
        selected = str(proposal.get("selected", "") or "")
        if selected:
            selected_trials += 1
            selected_proposal_counts[selected] = (
                selected_proposal_counts.get(selected, 0) + 1
            )
            improvement = proposal.get("selected_improvement")
            if isinstance(improvement, (int, float)):
                improvements.append(float(improvement))
        else:
            rejected_trials += 1
        if proposal.get("skipped_due_to_admissibility"):
            skipped_due_to_admissibility_trials += 1

    heuristic_summary = summarize_heuristic_outcomes(
        extract_heuristic_outcomes(trial_history)
    )
    return ProposalSelectionSummary(
        trial_count=len(trial_history),
        proposal_selection_trials=proposal_selection_trials,
        selected_trials=selected_trials,
        rejected_trials=rejected_trials,
        skipped_due_to_admissibility_trials=skipped_due_to_admissibility_trials,
        selected_proposal_counts=dict(sorted(selected_proposal_counts.items())),
        proposal_selection_labels=tuple(sorted(set(proposal_selection_labels))),
        mean_selected_proposal_improvement=(
            float(sum(improvements) / len(improvements)) if improvements else 0.0
        ),
        best_selected_proposal_improvement=(
            float(max(improvements)) if improvements else 0.0
        ),
        heuristic_outcome_count=int(heuristic_summary.get("outcome_count", 0) or 0),
        positive_heuristic_outcome_count=int(
            heuristic_summary.get("positive_outcome_count", 0) or 0
        ),
    )


@dataclass(frozen=True)
class BenchmarkPolicyReport:
    """Structured benchmark-policy outcome."""

    passed: bool
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


def summarize_usability_assessment(assessment: Any) -> dict[str, Any]:
    """Return a compact, family-neutral usability summary."""
    if not isinstance(assessment, dict):
        if hasattr(assessment, "model_dump"):
            dumped = assessment.model_dump(mode="json")
            if isinstance(dumped, dict):
                assessment = dumped
        else:
            return {}
    if {
        "usable_for_guidance",
        "usable_for_scoring",
        "usable_for_final_benchmark",
    }.issubset(set(assessment.keys())) and "guidance" not in assessment:
        return {
            "assessment_id": str(assessment.get("assessment_id", "") or ""),
            "family": str(assessment.get("family", "") or ""),
            "task_intent": str(assessment.get("task_intent", "") or ""),
            "heuristic_signature": list(assessment.get("heuristic_signature", []) or []),
            "usable_for_guidance": bool(assessment.get("usable_for_guidance")),
            "usable_for_scoring": bool(assessment.get("usable_for_scoring")),
            "usable_for_final_benchmark": bool(
                assessment.get("usable_for_final_benchmark")
            ),
            "confidence": float(assessment.get("confidence", 0.0) or 0.0),
            "guidance_exclusions": list(assessment.get("guidance_exclusions", []) or []),
            "scoring_exclusions": list(assessment.get("scoring_exclusions", []) or []),
            "final_benchmark_exclusions": list(
                assessment.get("final_benchmark_exclusions", []) or []
            ),
            "guidance_warnings": list(assessment.get("guidance_warnings", []) or []),
            "scoring_warnings": list(assessment.get("scoring_warnings", []) or []),
            "final_benchmark_warnings": list(
                assessment.get("final_benchmark_warnings", []) or []
            ),
        }
    try:
        model = UsabilityAssessment.model_validate(assessment)
    except Exception:
        return {}
    return {
        "assessment_id": model.assessment_id,
        "family": model.family,
        "task_intent": model.task_intent,
        "heuristic_signature": list(model.heuristic_signature),
        "usable_for_guidance": model.usable_for_guidance,
        "usable_for_scoring": model.usable_for_scoring,
        "usable_for_final_benchmark": model.usable_for_final_benchmark,
        "confidence": float(model.confidence),
        "guidance_exclusions": [
            reason.code for reason in model.guidance.blocking_reasons
        ],
        "scoring_exclusions": [
            reason.code for reason in model.scoring.blocking_reasons
        ],
        "final_benchmark_exclusions": [
            reason.code for reason in model.final_benchmark.blocking_reasons
        ],
        "guidance_warnings": [
            reason.code for reason in model.guidance.warning_reasons
        ],
        "scoring_warnings": [
            reason.code for reason in model.scoring.warning_reasons
        ],
        "final_benchmark_warnings": [
            reason.code for reason in model.final_benchmark.warning_reasons
        ],
    }


def summarize_search_discipline(trial_history: list[dict[str, Any]]) -> SearchDisciplineSummary:
    """Summarize high-level search behavior from persisted trial history."""
    expansion_attempts = 0
    admissibility_decisions = 0
    pruned_trials = 0
    reused_cached = 0
    for entry in trial_history:
        expansion = entry.get("expansion", {})
        if isinstance(expansion, dict) and (
            expansion.get("diagnostic_count", 0) or expansion.get("applied")
        ):
            expansion_attempts += 1
        admissibility = entry.get("admissibility", {})
        if isinstance(admissibility, dict):
            admissibility_decisions += int(admissibility.get("decision_count", 0) or 0)
        if "pruned" in str(entry.get("error", "")).lower():
            pruned_trials += 1
        if bool(entry.get("reused_cached_evaluation")):
            reused_cached += 1
    return SearchDisciplineSummary(
        trial_count=len(trial_history),
        expansion_attempts=expansion_attempts,
        admissibility_decisions=admissibility_decisions,
        pruned_trials=pruned_trials,
        reused_cached_evaluations=reused_cached,
    )


def _asset_readiness_record(asset: Any) -> dict[str, Any]:
    """Normalize a skeleton or expansion asset into a readiness record."""
    asset_map = _coerce_mapping(asset)
    audit = _coerce_mapping(asset_map.get("audit", {}))
    migration = _coerce_mapping(
        asset_map.get("migration_readiness")
        or audit.get("migration_readiness")
    )
    asset_kind = str(
        asset_map.get("asset_kind")
        or ("skeleton" if "stages" in asset_map else "expansion" if "operations" in asset_map else "")
    )
    references = audit.get("references", asset_map.get("references", []))
    if not isinstance(references, list):
        references = []
    dejargonized_summary = str(
        asset_map.get("dejargonized_summary")
        or audit.get("dejargonized_summary")
        or asset_map.get("summary", "")
        or ""
    )
    review_status = str(
        asset_map.get("review_status")
        or audit.get("review_status")
        or ""
    )
    source_kind = str(
        asset_map.get("source_kind")
        or audit.get("source_kind")
        or ""
    )
    ready_scope = "full" if asset_kind in {"skeleton", "expansion"} else "identity"
    migration_status = str(
        asset_map.get("migration_readiness_status")
        or asset_map.get("asset_migration_readiness_status")
        or migration.get("status")
        or ""
    )
    migration_ready = bool(
        asset_map.get("migration_readiness_ready")
        if "migration_readiness_ready" in asset_map
        else asset_map.get("asset_migration_readiness_ready")
        if "asset_migration_readiness_ready" in asset_map
        else migration.get("ready_for_migration")
    )
    migration_target_repository = str(
        asset_map.get("migration_readiness_target_repository")
        or asset_map.get("asset_migration_readiness_target_repository")
        or migration.get("target_repository")
        or ""
    )
    migration_target_scope = str(
        asset_map.get("migration_readiness_target_scope")
        or asset_map.get("asset_migration_readiness_target_scope")
        or migration.get("target_scope")
        or ""
    )
    migration_check_count = int(
        asset_map.get("migration_readiness_check_count")
        or asset_map.get("asset_migration_readiness_check_count")
        or len(migration.get("checklist", []))
        or 0
    )
    migration_required_check_count = int(
        asset_map.get("migration_readiness_required_check_count")
        or asset_map.get("asset_migration_readiness_required_check_count")
        or sum(
            1
            for item in migration.get("checklist", [])
            if isinstance(item, dict) and bool(item.get("required", True))
        )
        or 0
    )
    migration_completed_required_check_count = int(
        asset_map.get("migration_readiness_completed_required_check_count")
        or asset_map.get("asset_migration_readiness_completed_required_check_count")
        or sum(
            1
            for item in migration.get("checklist", [])
            if isinstance(item, dict)
            and bool(item.get("required", True))
            and bool(item.get("satisfied"))
        )
        or 0
    )
    migration_check_ids = asset_map.get("migration_readiness_check_ids")
    if not isinstance(migration_check_ids, list):
        migration_check_ids = [
            str(item.get("check_id", ""))
            for item in migration.get("checklist", [])
            if isinstance(item, dict) and item.get("check_id")
        ]
    return {
        "asset_id": str(asset_map.get("asset_id", "") or ""),
        "asset_version": str(asset_map.get("asset_version", "") or ""),
        "family": str(
            asset_map.get("family")
            or asset_map.get("asset_family")
            or ""
        ),
        "asset_kind": asset_kind,
        "review_status": review_status,
        "source_kind": source_kind,
        "canonical_for_paradigm": bool(asset_map.get("canonical_for_paradigm")),
        "dejargonized_summary_present": bool(dejargonized_summary.strip()),
        "reference_count": len(references),
        "ready_scope": ready_scope,
        "migration_readiness_status": migration_status,
        "migration_readiness_ready": migration_ready,
        "migration_readiness_target_repository": migration_target_repository,
        "migration_readiness_target_scope": migration_target_scope,
        "migration_readiness_check_count": migration_check_count,
        "migration_readiness_required_check_count": migration_required_check_count,
        "migration_readiness_completed_required_check_count": (
            migration_completed_required_check_count
        ),
        "migration_readiness_check_ids": list(migration_check_ids),
    }


def _asset_record_ready(
    record: dict[str, Any],
) -> bool:
    asset_id = str(record.get("asset_id", "") or "")
    if not asset_id:
        return False
    return bool(record.get("migration_readiness_ready"))


def summarize_asset_migration_readiness(
    assets: list[dict[str, Any]] | dict[str, Any],
) -> dict[str, Any]:
    """Summarize how migration-ready a set of family assets is."""
    if isinstance(assets, dict):
        asset_list: list[Any] = [assets]
    else:
        asset_list = list(assets)

    records = [
        _asset_readiness_record(asset)
        for asset in asset_list
        if _coerce_mapping(asset)
    ]
    ready_records = [
        record
        for record in records
        if _asset_record_ready(record)
    ]
    blocked_records = [record for record in records if record not in ready_records]
    return {
        "asset_count": len(records),
        "ready_asset_count": len(ready_records),
        "blocked_asset_count": len(blocked_records),
        "ready_asset_ids": sorted(
            record["asset_id"] for record in ready_records if record.get("asset_id")
        ),
        "blocked_asset_ids": sorted(
            record["asset_id"] for record in blocked_records if record.get("asset_id")
        ),
        "records": records,
    }


def evaluate_asset_migration_readiness(
    assets: list[dict[str, Any]] | dict[str, Any],
    *,
    minimum_ready_assets: int = 0,
    require_migration_contract: bool = True,
) -> BenchmarkPolicyReport:
    """Check whether the candidate assets look ready for shared ownership."""
    summary = summarize_asset_migration_readiness(assets)
    violations: list[str] = []
    warnings: list[str] = []

    ready_count = 0
    records = summary.get("records", [])
    if not isinstance(records, list):
        records = []

    for record in records:
        if not isinstance(record, dict):
            continue
        asset_id = str(record.get("asset_id", "") or "")
        review_status = str(record.get("review_status", "") or "")
        source_kind = str(record.get("source_kind", "") or "")
        asset_kind = str(record.get("asset_kind", "") or "")
        dejargonized = bool(record.get("dejargonized_summary_present"))
        reference_count = int(record.get("reference_count", 0) or 0)
        ready = _asset_record_ready(record)
        migration_status = str(record.get("migration_readiness_status", "") or "")
        migration_target_repository = str(
            record.get("migration_readiness_target_repository", "") or ""
        )
        migration_check_count = int(
            record.get("migration_readiness_check_count", 0) or 0
        )
        if not asset_id:
            violations.append("asset_missing_identity")
        if asset_kind in {"skeleton", "expansion"} and not dejargonized:
            violations.append(f"asset_missing_dejargonized_summary:{asset_id}")
        if asset_kind in {"skeleton", "expansion"} and reference_count == 0:
            violations.append(f"asset_missing_references:{asset_id}")
        if require_migration_contract and not migration_status:
            violations.append(f"asset_missing_migration_status:{asset_id}")
        if require_migration_contract and not migration_target_repository:
            violations.append(f"asset_missing_migration_target:{asset_id}")
        if require_migration_contract and migration_check_count == 0:
            warnings.append(f"asset_missing_migration_checklist:{asset_id}")
        if review_status in {"draft", "", "missing"}:
            warnings.append(
                f"asset_review_not_stable:{asset_id}:{review_status or '--'}"
            )
        if source_kind in {"", "local_asset", "repo_local_transitional_asset"}:
            warnings.append(f"asset_not_yet_shared:{asset_id}:{source_kind or '--'}")
        if migration_status and migration_status not in {
            "ready_for_migration",
            "migrated",
        }:
            warnings.append(f"asset_not_ready_for_migration:{asset_id}:{migration_status}")

        if ready:
            ready_count += 1

    if ready_count < minimum_ready_assets:
        violations.append(
            f"insufficient_migration_ready_assets:{ready_count}/{minimum_ready_assets}"
        )

    return BenchmarkPolicyReport(
        passed=not violations,
        violations=tuple(violations),
        warnings=tuple(warnings),
        details={
            **summary,
            "minimum_ready_assets": minimum_ready_assets,
            "require_migration_contract": require_migration_contract,
        },
    )


def evaluate_enriched_cdg_policy(
    result: dict[str, Any],
    *,
    min_admissibility_decisions: int = 1,
) -> BenchmarkPolicyReport:
    """Check that a benchmark run actually exercised enriched-CDG behavior."""
    search = _coerce_mapping(result.get("search_discipline", {}))
    proposal = _coerce_mapping(result.get("proposal_selection", {}))
    trace = _coerce_mapping(result.get("search_trace_summary", {}))

    expansion_attempts = int(search.get("expansion_attempts", 0) or 0)
    admissibility_decisions = int(search.get("admissibility_decisions", 0) or 0)
    proposal_trials = int(proposal.get("proposal_selection_trials", 0) or 0)
    selected_trials = int(proposal.get("selected_trials", 0) or 0)
    search_entries = int(trace.get("entry_count", 0) or 0)
    applied_asset_count = int(trace.get("applied_asset_count", 0) or 0)

    violations: list[str] = []
    warnings: list[str] = []
    if search_entries == 0:
        violations.append("missing_search_trace")
    if proposal_trials > search_entries:
        violations.append("proposal_selection_exceeds_search_trace")
    if selected_trials > proposal_trials:
        violations.append("selected_trials_exceed_proposal_trials")
    if applied_asset_count > 0 and expansion_attempts == 0:
        violations.append("applied_assets_without_expansion_attempts")
    if admissibility_decisions < min_admissibility_decisions:
        warnings.append(
            f"low_admissibility_decisions:{admissibility_decisions}/{min_admissibility_decisions}"
        )
    if expansion_attempts > 0 and proposal_trials == 0:
        warnings.append("expansion_without_proposal_selection")
    if proposal_trials > 0 and selected_trials == 0:
        warnings.append("no_selected_proposals")
    if applied_asset_count == 0:
        warnings.append("no_applied_assets")

    return BenchmarkPolicyReport(
        passed=not violations,
        violations=tuple(violations),
        warnings=tuple(warnings),
        details={
            "search_trace_entries": search_entries,
            "trial_count": int(search.get("trial_count", 0) or 0),
            "expansion_attempts": expansion_attempts,
            "admissibility_decisions": admissibility_decisions,
            "proposal_selection_trials": proposal_trials,
            "selected_trials": selected_trials,
            "applied_asset_count": applied_asset_count,
        },
    )


def validate_required_benchmark_artifacts(
    artifacts: dict[str, Any],
    *,
    required_keys: tuple[str, ...] = (
        "planning_artifact",
        "skeleton_asset",
        "trial_history",
        "final_candidate",
        "runtime_context",
    ),
) -> BenchmarkPolicyReport:
    """Require the minimum artifact set needed to judge search behavior."""
    missing = [
        key
        for key in required_keys
        if key not in artifacts
        or artifacts[key] is None
        or (
            isinstance(artifacts[key], (str, bytes, list, tuple, set, dict))
            and not artifacts[key]
        )
    ]
    return BenchmarkPolicyReport(
        passed=not missing,
        violations=tuple(f"missing_artifact:{key}" for key in missing),
        details={"required_keys": list(required_keys)},
    )


def enforce_anti_shortcut_policy(
    benchmark_metadata: dict[str, Any],
) -> BenchmarkPolicyReport:
    """Reject undeclared shortcut paths for benchmark/e2e runs."""
    shortcut_flags = benchmark_metadata.get("shortcut_flags", {})
    declared_shortcuts = benchmark_metadata.get("declared_shortcuts", [])
    if not isinstance(shortcut_flags, dict):
        shortcut_flags = {}
    if not isinstance(declared_shortcuts, list):
        declared_shortcuts = []

    violations: list[str] = []
    warnings: list[str] = []
    for key, enabled in sorted(shortcut_flags.items()):
        if not enabled:
            continue
        if key in declared_shortcuts:
            warnings.append(f"declared_shortcut:{key}")
        else:
            violations.append(f"undeclared_shortcut:{key}")
    return BenchmarkPolicyReport(
        passed=not violations,
        violations=tuple(violations),
        warnings=tuple(warnings),
        details={"shortcut_flags": dict(shortcut_flags)},
    )


def evaluate_behavioral_benchmark_policy(
    result: dict[str, Any],
    *,
    allowed_families: set[str],
    min_ground_truth_coverage: float = 1.0,
    require_real_assets: bool = True,
) -> BenchmarkPolicyReport:
    """Prefer semantic and behavioral checks over exact scaffold matching."""
    violations: list[str] = []
    warnings: list[str] = []
    details: dict[str, Any] = {}

    family = str(result.get("family", "") or "")
    coverage = float(result.get("ground_truth_coverage", 0.0) or 0.0)
    used_real_assets = bool(result.get("used_real_assets"))
    executable = bool(result.get("executable"))

    details.update(
        {
            "family": family,
            "ground_truth_coverage": coverage,
            "used_real_assets": used_real_assets,
            "executable": executable,
        }
    )
    usability = summarize_usability_assessment(
        result.get("usability_assessment", {})
    )
    if usability:
        details["usability_assessment"] = dict(usability)
        if not bool(usability.get("usable_for_guidance", True)):
            warnings.append(
                "guidance_usability_excluded:"
                + ",".join(usability.get("guidance_exclusions", []) or [])
            )
        if not bool(usability.get("usable_for_scoring", True)):
            warnings.append(
                "scoring_usability_excluded:"
                + ",".join(usability.get("scoring_exclusions", []) or [])
            )
        for warning_code in usability.get("guidance_warnings", []) or []:
            warnings.append(f"guidance_warning:{warning_code}")
        for warning_code in usability.get("scoring_warnings", []) or []:
            warnings.append(f"scoring_warning:{warning_code}")
        if not bool(usability.get("usable_for_final_benchmark", True)):
            violations.append("final_benchmark_usability_excluded")
            if usability.get("final_benchmark_exclusions"):
                warnings.append(
                    "final_benchmark_usability_excluded:"
                    + ",".join(usability.get("final_benchmark_exclusions", []) or [])
                )
        for warning_code in usability.get("final_benchmark_warnings", []) or []:
            warnings.append(f"final_benchmark_warning:{warning_code}")
    elif "usable_for_final_benchmark" in result and not bool(
        result.get("usable_for_final_benchmark")
    ):
        violations.append("final_benchmark_usability_excluded")

    if family not in allowed_families:
        violations.append(f"disallowed_family:{family}")
    if coverage < min_ground_truth_coverage:
        violations.append("insufficient_ground_truth_coverage")
    if require_real_assets and not used_real_assets:
        violations.append("real_assets_not_exercised")
    if not executable:
        violations.append("non_executable_candidate")

    return BenchmarkPolicyReport(
        passed=not violations,
        violations=tuple(violations),
        warnings=tuple(warnings),
        details=details,
    )
