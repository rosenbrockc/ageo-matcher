"""Focused tests for search-discipline and benchmark-policy contracts."""

from __future__ import annotations

from sciona.principal.search_policy import (
    evaluate_asset_migration_readiness,
    evaluate_enriched_cdg_policy,
    enforce_anti_shortcut_policy,
    evaluate_behavioral_benchmark_policy,
    summarize_usability_assessment,
    summarize_proposal_selection,
    summarize_search_discipline,
    validate_required_benchmark_artifacts,
)
from sciona.principal.runtime_usability import build_runtime_usability_assessment


def test_validate_required_benchmark_artifacts_reports_missing_keys() -> None:
    report = validate_required_benchmark_artifacts(
        {
            "planning_artifact": {},
            "trial_history": [],
        }
    )

    assert report.passed is False
    assert "missing_artifact:planning_artifact" in report.violations
    assert "missing_artifact:trial_history" in report.violations
    assert "missing_artifact:skeleton_asset" in report.violations
    assert "missing_artifact:final_candidate" in report.violations


def test_anti_shortcut_policy_rejects_undeclared_shortcuts() -> None:
    report = enforce_anti_shortcut_policy(
        {
            "shortcut_flags": {
                "curated_signal_event_rate_shortcut": True,
                "direct_baseline_only": False,
            },
            "declared_shortcuts": [],
        }
    )

    assert report.passed is False
    assert report.violations == ("undeclared_shortcut:curated_signal_event_rate_shortcut",)


def test_anti_shortcut_policy_warns_for_declared_shortcuts() -> None:
    report = enforce_anti_shortcut_policy(
        {
            "shortcut_flags": {"declared_operational_fast_path": True},
            "declared_shortcuts": ["declared_operational_fast_path"],
        }
    )

    assert report.passed is True
    assert report.warnings == ("declared_shortcut:declared_operational_fast_path",)


def test_behavioral_policy_accepts_semantically_valid_alternative() -> None:
    report = evaluate_behavioral_benchmark_policy(
        {
            "family": "signal_event_rate",
            "ground_truth_coverage": 1.0,
            "used_real_assets": True,
            "executable": True,
            "matched_primitives": [
                "sciona.atoms.signal_processing.biosppy.ecg.bandpass_filter",
                "sciona.atoms.signal_processing.biosppy.ecg.r_peak_detection",
                "sciona.atoms.signal_processing.biosppy.ecg.heart_rate_computation",
            ],
        },
        allowed_families={"signal_event_rate", "signal_detect_measure"},
    )

    assert report.passed is True


def test_behavioral_policy_rejects_non_executable_shortcut_run() -> None:
    report = evaluate_behavioral_benchmark_policy(
        {
            "family": "signal_event_rate",
            "ground_truth_coverage": 0.67,
            "used_real_assets": False,
            "executable": False,
        },
        allowed_families={"signal_event_rate"},
    )

    assert report.passed is False
    assert "insufficient_ground_truth_coverage" in report.violations
    assert "real_assets_not_exercised" in report.violations
    assert "non_executable_candidate" in report.violations


def test_behavioral_policy_reports_usability_scope_exclusions() -> None:
    assessment = build_runtime_usability_assessment(
        {
            "runtime_context": {"tracker": "trial-1"},
            "telemetry_summary": {
                "signal": {"count": 10.0, "mean": 0.5, "std": 0.1},
                "rate": {"count": 3.0, "mean": 70.0, "std": 1.0},
            },
            "heuristics": [
                {
                    "heuristic": {"heuristic_id": "interval_instability"},
                    "confidence": 0.7,
                    "source_section": "events",
                }
            ],
            "heuristic_summary": {
                "heuristic_count": 1,
                "heuristic_ids": ["interval_instability"],
                "max_confidence": 0.7,
            },
            "execution_summary": {
                "process_returncode": 1,
                "loss_is_finite": False,
                "trace_support_present": True,
                "output_support_present": False,
                "soft_accepted_nonzero_exit": False,
            },
        }
    ).model_dump(mode="json")

    report = evaluate_behavioral_benchmark_policy(
        {
            "family": "generic_records",
            "ground_truth_coverage": 1.0,
            "used_real_assets": True,
            "executable": True,
            "usability_assessment": assessment,
        },
        allowed_families={"generic_records"},
        require_real_assets=True,
    )

    assert report.passed is False
    assert "final_benchmark_usability_excluded" in report.violations
    assert any(
        warning.startswith("scoring_usability_excluded:")
        for warning in report.warnings
    )
    assert any(
        warning.startswith("guidance_warning:")
        for warning in report.warnings
    )


def test_summarize_usability_assessment_returns_cross_family_scope_data() -> None:
    assessment = build_runtime_usability_assessment(
        {
            "runtime_context": {"tracker": "trial-1"},
            "heuristics": [
                {
                    "heuristic": {"heuristic_id": "interval_instability"},
                    "confidence": 0.7,
                    "source_section": "events",
                }
            ],
            "heuristic_summary": {
                "heuristic_count": 1,
                "heuristic_ids": ["interval_instability"],
                "max_confidence": 0.7,
            },
        }
    )

    summary = summarize_usability_assessment(assessment)

    assert summary["assessment_id"] == "runtime_usability_assessment"
    assert summary["usable_for_guidance"] is False
    assert summary["guidance_exclusions"]
    assert summary["scoring_exclusions"]
    assert summary["usable_for_final_benchmark"] is False


def test_search_discipline_summary_counts_behavioral_signals() -> None:
    summary = summarize_search_discipline(
        [
            {
                "expansion": {"diagnostic_count": 2, "applied": False},
                "admissibility": {"decision_count": 1},
                "reused_cached_evaluation": False,
                "error": "",
            },
            {
                "expansion": {"diagnostic_count": 1, "applied": True},
                "admissibility": {"decision_count": 2},
                "reused_cached_evaluation": True,
                "error": "Trial pruned early",
            },
        ]
    )

    assert summary.trial_count == 2
    assert summary.expansion_attempts == 2
    assert summary.admissibility_decisions == 3
    assert summary.pruned_trials == 1
    assert summary.reused_cached_evaluations == 1


def test_proposal_selection_summary_handles_typed_nested_records() -> None:
    class _Candidate:
        def __init__(self, label: str, loss: float) -> None:
            self.label = label
            self.loss = loss

    class _ProposalSelection:
        def __init__(self) -> None:
            self.selected_proposal = "expansion"
            self.proposal_candidates = [
                _Candidate("expansion", 8.5),
                _Candidate("local_mutation", 9.0),
            ]
            self.proposal_baseline_loss = 10.0
            self.proposal_improvement = 1.5

        def model_dump(self, mode: str = "json") -> dict[str, object]:
            return {
                "selected_proposal": self.selected_proposal,
                "proposal_candidates": [
                    {"label": candidate.label, "loss": candidate.loss}
                    for candidate in self.proposal_candidates
                ],
                "proposal_baseline_loss": self.proposal_baseline_loss,
                "proposal_improvement": self.proposal_improvement,
            }

    summary = summarize_proposal_selection(
        [
            {
                "proposal_selection": _ProposalSelection(),
                "expansion": {"applied": True},
            },
            {
                "proposal_selection": {
                    "selected": "",
                    "candidates": [],
                    "skipped_due_to_admissibility": True,
                }
            },
        ]
    )

    assert summary.trial_count == 2
    assert summary.proposal_selection_trials == 2
    assert summary.selected_trials == 1
    assert summary.rejected_trials == 1
    assert summary.skipped_due_to_admissibility_trials == 1
    assert summary.selected_proposal_counts == {"expansion": 1}
    assert summary.proposal_selection_labels == ("expansion", "local_mutation")
    assert summary.mean_selected_proposal_improvement == 1.5
    assert summary.best_selected_proposal_improvement == 1.5


def test_enriched_cdg_policy_requires_real_search_evidence() -> None:
    passing = evaluate_enriched_cdg_policy(
        {
            "search_discipline": {
                "trial_count": 3,
                "expansion_attempts": 2,
                "admissibility_decisions": 4,
                "pruned_trials": 0,
                "reused_cached_evaluations": 1,
            },
            "proposal_selection": {
                "trial_count": 3,
                "proposal_selection_trials": 2,
                "selected_trials": 1,
                "rejected_trials": 1,
            },
            "search_trace_summary": {
                "entry_count": 3,
                "applied_asset_count": 1,
            },
        }
    )

    failing = evaluate_enriched_cdg_policy(
        {
            "search_discipline": {},
            "proposal_selection": {},
            "search_trace_summary": {"entry_count": 0, "applied_asset_count": 0},
        }
    )

    assert passing.passed is True
    assert passing.details["expansion_attempts"] == 2
    assert failing.passed is False
    assert "missing_search_trace" in failing.violations


def test_enriched_cdg_policy_rejects_incoherent_asset_application() -> None:
    report = evaluate_enriched_cdg_policy(
        {
            "search_discipline": {
                "trial_count": 2,
                "expansion_attempts": 0,
                "admissibility_decisions": 2,
            },
            "proposal_selection": {
                "trial_count": 2,
                "proposal_selection_trials": 1,
                "selected_trials": 1,
            },
            "search_trace_summary": {
                "entry_count": 2,
                "applied_asset_count": 1,
            },
        }
    )

    assert report.passed is False
    assert "applied_assets_without_expansion_attempts" in report.violations


def test_asset_migration_readiness_distinguishes_transitional_and_ready_assets() -> None:
    ready = evaluate_asset_migration_readiness(
        {
            "asset_id": "family.generic.expansions.v1",
            "asset_version": "v1",
            "family": "generic",
                "operations": [
                    {
                        "rule_name": "generic_rule",
                        "name": "Generic Rule",
                        "intent": "Generic enrichment",
                    "dejargonized_summary": "Generic enrichment step.",
                    "trigger": {
                        "metric_name": "variance",
                        "threshold": 1.0,
                        "required_runtime_keys": ["records"],
                    },
                }
            ],
                "audit": {
                    "review_status": "canonical",
                    "source_kind": "shared_asset",
                    "dejargonized_summary": "Generic family asset.",
                    "references": [{"title": "Generic reference"}],
                },
                "migration_readiness_status": "ready_for_migration",
                "migration_readiness_ready": True,
                "migration_readiness_target_repository": "../sciona-atoms",
                "migration_readiness_target_scope": "shared_family_asset",
                "migration_readiness_check_count": 2,
                "migration_readiness_required_check_count": 2,
                "migration_readiness_completed_required_check_count": 2,
                "migration_readiness_check_ids": ["docs", "tests"],
            }
        )

    blocked = evaluate_asset_migration_readiness(
        {
            "asset_id": "skeleton.generic.v1",
            "asset_version": "v1",
            "family": "generic",
            "stages": [],
            "review_status": "transitional",
            "source_kind": "local_asset",
            "dejargonized_summary": "Generic skeleton.",
            "references": [{"title": "Generic reference"}],
            "migration_readiness_status": "in_progress",
            "migration_readiness_target_repository": "../sciona-atoms",
            "migration_readiness_check_count": 1,
            "migration_readiness_required_check_count": 1,
            "migration_readiness_completed_required_check_count": 0,
            "migration_readiness_ready": False,
        },
        minimum_ready_assets=1,
    )

    assert ready.passed is True
    assert ready.details["ready_asset_count"] == 1
    assert blocked.passed is False
    assert blocked.details["blocked_asset_count"] == 1
    assert "insufficient_migration_ready_assets:0/1" in blocked.violations
    assert "asset_not_ready_for_migration:skeleton.generic.v1:in_progress" in blocked.warnings
