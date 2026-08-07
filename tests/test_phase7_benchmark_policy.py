"""Phase 7 policy tests for benchmark and e2e discipline."""

from __future__ import annotations

import json
from pathlib import Path

from sciona.e2e_benchmark_summary import build_e2e_benchmark_summary
from sciona.benchmark_validation import flow_execution_path_summary
from sciona.principal.e2e_benchmark_policy import evaluate_e2e_benchmark_report
from sciona.principal.runtime_usability import build_runtime_usability_assessment


def _runtime_usability_evidence(heuristic_id: str) -> dict[str, object]:
    evidence = {
        "runtime_context": {"stream_count": 1},
        "telemetry_summary": {
            "signal": {"count": 10.0, "mean": 0.5, "std": 0.1},
            "rate": {"count": 3.0, "mean": 70.0, "std": 1.0},
        },
        "heuristics": [
            {
                "heuristic": {"heuristic_id": heuristic_id},
                "confidence": 0.8,
                "source_section": "events",
            }
        ],
        "heuristic_summary": {
            "heuristic_count": 1,
            "heuristic_ids": [heuristic_id],
            "max_confidence": 0.8,
        },
    }
    return {
        **evidence,
        "usability_assessment": build_runtime_usability_assessment(evidence).model_dump(
            mode="json"
        ),
    }


def _benchmark_variant_dir(
    root: Path,
    *,
    matched_primitives: list[str],
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    variant_dir = root / "pipeline_verified"
    variant_dir.mkdir(parents=True, exist_ok=True)
    (variant_dir / "cdg.json").write_text(
        json.dumps(
            {
                "planning_artifact": {
                    "family_hint": "generic_records",
                    "paradigm": "generic_records",
                },
                "metadata": {
                    "skeleton_asset": {
                        "asset_id": "skeleton.generic_records.v1",
                        "review_status": "transitional",
                        "source_kind": "local_asset",
                    }
                },
            }
        )
    )
    (variant_dir / "trial_history.json").write_text(
        json.dumps(
            [
                {
                    "trial": 1,
                    "admissibility": {"decision_count": 1},
                    "expansion": {"applied": True, "diagnostic_count": 1},
                    "proposal_selection": {
                        "selected": "expansion",
                        "candidates": [{"label": "expansion", "loss": 1.0}],
                    },
                }
            ]
        )
    )
    (variant_dir / "matches.json").write_text(
        json.dumps(
            [
                {
                    "verified_match": {"verified": True},
                    "all_candidates": [
                        {"declaration": {"name": name}} for name in matched_primitives
                    ],
                }
            ]
        )
    )
    (variant_dir / "runtime_evidence.json").write_text(
        json.dumps(_runtime_usability_evidence("interval_instability"))
    )
    return variant_dir


def test_e2e_benchmark_scripts_enforce_full_framework_mode() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    benchmark_script = (repo_root / "benchmarks" / "e2e_benchmark.sh").read_text()
    benchmark_all_script = (repo_root / "benchmarks" / "e2e_benchmark_all.sh").read_text()

    assert 'E2E_SEMANTIC_INDEX_BACKEND:-postgres' in benchmark_script
    assert 'E2E_INCLUDE_SYNTHESIS:-true' in benchmark_script
    assert "if '$label' == 'raw_llm':" in benchmark_script
    assert "benchmark_passed.txt" in benchmark_script
    assert 'exit 1' in benchmark_script
    assert "postprocess.json" in benchmark_script
    assert "summary.json" in benchmark_script
    assert "summary_table.txt" in benchmark_script
    assert "evaluate_e2e_benchmark_report" in benchmark_script
    assert "variant | artifacts | anti-shortcut | behavioral" in benchmark_script
    assert "enriched-cdg" in benchmark_script
    assert "asset-migration" in benchmark_script
    assert "e2e_goals/" in benchmark_all_script
    assert "e2e_benchmark.sh" in benchmark_all_script
    assert "for config in" in benchmark_all_script


def test_flow_execution_path_summary_flags_collapsed_execution_paths() -> None:
    class _Agg:
        def __init__(self, variant: str, execution_paths: list[str]) -> None:
            self.variant = variant
            self.execution_paths = execution_paths

    summary = flow_execution_path_summary(
        [
            _Agg("rapid", ["verified_orchestration"]),
            _Agg("structured", ["structured_single_pass"]),
            _Agg("verified", ["verified_orchestration"]),
        ]
    )

    assert summary["violations"]
    assert any("expected rapid_direct" in violation for violation in summary["violations"])
    assert any(
        violation.startswith("mode_paths_not_distinct:")
        for violation in summary["violations"]
    )


def test_e2e_benchmark_policy_reads_list_trial_history_and_runtime_evidence(
    tmp_path: Path,
) -> None:
    mode_dir = tmp_path / "pipeline_verified"
    mode_dir.mkdir()
    (mode_dir / "cdg.json").write_text(
        json.dumps(
            {
                "planning_artifact": {
                    "family_hint": "signal_event_rate",
                    "paradigm": "signal_event_rate",
                },
                "metadata": {
                    "skeleton_asset": {
                        "asset_id": "skeleton.signal_event_rate.ecg.v1",
                        "asset_version": "v1",
                        "family": "signal_event_rate",
                        "source_kind": "local_asset",
                        "review_status": "transitional",
                        "migration_readiness_status": "in_progress",
                        "migration_readiness_target_repository": "../sciona-atoms",
                        "migration_readiness_check_count": 2,
                        "migration_readiness_required_check_count": 2,
                        "migration_readiness_completed_required_check_count": 1,
                        "migration_readiness_ready": False,
                    }
                },
            }
        )
    )
    (mode_dir / "trial_history.json").write_text(
        json.dumps(
            [
                {
                    "trial": 1,
                    "admissibility": {"decision_count": 2},
                    "expansion": {
                        "diagnostic_count": 1,
                        "diagnostic_assets": [
                            {
                                "asset_id": "family.signal_event_rate.expansions.v1",
                                "asset_version": "phase3.v1",
                                "asset_family": "signal_event_rate",
                                "asset_source_kind": "local_asset",
                                "asset_review_status": "transitional",
                                "asset_operation": "insert_jump_removal_before_filter",
                                "migration_readiness_status": "in_progress",
                                "migration_readiness_target_repository": "../sciona-atoms",
                                "migration_readiness_check_count": 3,
                                "migration_readiness_required_check_count": 3,
                                "migration_readiness_completed_required_check_count": 2,
                                "migration_readiness_ready": False,
                            }
                        ],
                        "applied_assets": [
                            {
                                "asset_id": "family.signal_event_rate.expansions.v1",
                                "asset_version": "phase3.v1",
                                "asset_family": "signal_event_rate",
                                "asset_source_kind": "local_asset",
                                "asset_review_status": "transitional",
                                "asset_operation": "insert_jump_removal_before_filter",
                                "asset_migration_readiness_status": "in_progress",
                                "asset_migration_readiness_ready": False,
                                "asset_migration_readiness_check_count": 3,
                                "asset_migration_readiness_required_check_count": 3,
                            }
                        ],
                    },
                    "proposal_selection": {
                        "selected": "expansion",
                        "candidates": [{"label": "expansion", "loss": 0.1}],
                    },
                }
            ]
        )
    )
    (mode_dir / "runtime_evidence.json").write_text(
        json.dumps(_runtime_usability_evidence("interval_instability"))
    )

    report = evaluate_e2e_benchmark_report(
        goal="Detect heart rate from raw ECG signal",
        prover="python",
        llm_provider="codex_shim",
        total_ground_truth=3,
        variants={
            "verified": {
                "mode_dir": mode_dir,
                "latency_ms": 1234,
                "matches_total": 3,
                "matches_verified": 3,
                "ground_truth_hits": 3,
                "executable": True,
            }
        },
        shortcut_flags={"curated_signal_event_rate_shortcut": False},
        declared_shortcuts=[],
    )

    verified = report["results"]["verified"]
    assert verified["ground_truth_coverage"] == 1.0
    assert verified["artifact_inventory"]["search_trace"] is True
    assert verified["search_trace_summary"]["entry_count"] == 1
    assert verified["search_trace_summary"]["applied_asset_count"] == 1
    assert verified["asset_inventory"]["asset_count"] == 2
    assert verified["policy"]["enriched_cdg"]["passed"] is True
    assert verified["policy"]["asset_migration"]["passed"] is True
    assert verified["policy"]["asset_migration"]["warnings"]
    assert verified["policy"]["required_artifacts"]["passed"] is True
    assert verified["policy"]["behavioral"]["passed"] is True
    assert verified["policy"]["behavioral"]["details"]["usability_assessment"][
        "usable_for_final_benchmark"
    ] is True
    assert verified["usability_assessment"]["usable_for_final_benchmark"] is True
    assert report["benchmark_policy"]["anti_shortcut"]["passed"] is True
    assert report["benchmark_policy"]["passed"] is True


def test_e2e_benchmark_policy_reports_enriched_cdg_and_asset_readiness(
    tmp_path: Path,
) -> None:
    mode_dir = tmp_path / "pipeline_verified"
    mode_dir.mkdir()
    (mode_dir / "cdg.json").write_text(
        json.dumps(
            {
                "planning_artifact": {
                    "family_hint": "generic_records",
                    "paradigm": "generic_records",
                },
                "metadata": {
                    "skeleton_asset": {
                        "asset_id": "skeleton.generic_records.v1",
                        "review_status": "transitional",
                        "source_kind": "local_asset",
                    }
                },
            }
        )
    )
    (mode_dir / "trial_history.json").write_text(
        json.dumps(
            [
                {
                    "trial": 1,
                    "admissibility": {"decision_count": 2},
                    "expansion": {
                        "applied": True,
                        "diagnostic_count": 1,
                        "applied_assets": [
                            {
                                "asset_id": "family.generic.expansions.v1",
                                "asset_version": "v1",
                                "family": "generic",
                                "review_status": "canonical",
                                "source_kind": "shared_asset",
                                "migration_readiness_status": "ready_for_migration",
                                "migration_readiness_ready": True,
                                "migration_readiness_target_repository": "../sciona-atoms",
                                "migration_readiness_target_scope": "shared_family_asset",
                                "migration_readiness_check_count": 2,
                                "migration_readiness_required_check_count": 2,
                                "migration_readiness_completed_required_check_count": 2,
                                "migration_readiness_check_ids": ["docs", "tests"],
                            }
                        ],
                    },
                    "proposal_selection": {
                        "selected": "expansion",
                        "candidates": [
                            {"label": "expansion", "loss": 2.0},
                            {"label": "mutation", "loss": 3.0},
                        ],
                    },
                }
            ]
        )
    )
    (mode_dir / "runtime_evidence.json").write_text(
        json.dumps(_runtime_usability_evidence("interval_instability"))
    )

    report = evaluate_e2e_benchmark_report(
        goal="Analyze generic records",
        prover="python",
        llm_provider="codex_shim",
        total_ground_truth=2,
        variants={
            "verified": {
                "mode_dir": mode_dir,
                "latency_ms": 456,
                "matches_total": 2,
                "matches_verified": 2,
                "ground_truth_hits": 2,
                "executable": True,
            }
        },
        shortcut_flags={"generic_fast_path": False},
        declared_shortcuts=[],
    )

    verified = report["results"]["verified"]
    assert verified["policy"]["enriched_cdg"]["passed"] is True
    assert verified["policy"]["asset_migration"]["passed"] is False
    assert verified["asset_inventory"]["asset_count"] == 2
    assert verified["asset_inventory"]["ready_asset_count"] == 1
    assert verified["asset_inventory"]["blocked_asset_count"] == 1
    assert verified["policy"]["behavioral"]["details"]["usability_assessment"][
        "usable_for_guidance"
    ] is True


def test_benchmark_summary_persists_enriched_policy_sections(tmp_path: Path) -> None:
    output_dir = tmp_path / "summary"
    output_dir.mkdir()
    variant_dir = _benchmark_variant_dir(
        output_dir,
        matched_primitives=["generic_atom"],
    )

    report = build_e2e_benchmark_summary(
        output_dir=output_dir,
        goal="Analyze generic records",
        prover="python",
        llm_provider="codex_shim",
        total_gt=1,
        latencies_ms={"verified": 123},
        ground_truth_hits={"verified": 1},
        variant_dirs={"verified": variant_dir},
        profile_dataset="dataset.yml",
        shortcut_flags={"generic_fast_path": False},
        declared_shortcuts=[],
    )

    verified = report["results"]["verified"]
    assert verified["enriched_cdg"]["passed"] is True
    assert verified["asset_migration"]["passed"] is False
    assert verified["asset_inventory"]["asset_count"] == 1
    assert verified["usability_assessment"]["usable_for_guidance"] is True
    assert report["policy"]["variants"]["verified"]["enriched_cdg"]["passed"] is True
    assert report["policy"]["variants"]["verified"]["asset_migration"]["passed"] is False
    assert report["policy"]["variants"]["verified"]["usability"]["usable_for_guidance"] is True
    assert (output_dir / "summary.json").exists()


def test_benchmark_summary_marks_atom_namespaces_as_real_assets(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "summary"
    output_dir.mkdir()

    cases = [
        "sciona.atoms.demo.ecg.r_peak_detection",
        "ageom.signal.rate_estimate",
        "sciona.expansion_atoms.signal_event_rate.outlier_rejection",
    ]

    for index, matched_primitive in enumerate(cases, start=1):
        variant_dir = _benchmark_variant_dir(
            output_dir / f"case_{index}",
            matched_primitives=[matched_primitive],
        )
        report = build_e2e_benchmark_summary(
            output_dir=output_dir / f"case_{index}",
            goal="Analyze generic records",
            prover="python",
            llm_provider="codex_shim",
            total_gt=1,
            latencies_ms={"verified": 123},
            ground_truth_hits={"verified": 1},
            variant_dirs={"verified": variant_dir},
            profile_dataset="dataset.yml",
            shortcut_flags={"generic_fast_path": False},
            declared_shortcuts=[],
        )

        assert report["results"]["verified"]["used_real_assets"] is True
