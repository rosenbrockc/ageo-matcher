"""Pure helpers for telemetry dashboard summaries."""

from __future__ import annotations

from typing import Any

from sciona.visualizer.dashboard_common import (
    _annotate_hang_signals,
    _bool,
    _dict,
    _float,
    _int,
    _list,
    _merge_runs,
    _str,
    _transport_for_provider,
)
from sciona.visualizer.dashboard_event_analysis import (
    _compute_coverage_from_dicts,
    _compute_errors_from_dicts,
)


def _routing_line(routing: dict[str, Any], name: str) -> dict[str, Any]:
    section = _dict(routing.get(name))
    active = _list(section.get("active_overrides"))
    return {
        "round": name,
        "default": f"{_str(section.get('default_provider', '--'))}:{_str(section.get('default_model', '--'))}",
        "active_count": len(active),
        "suppressed_count": len(_list(section.get("suppressed_default_overrides"))),
        "custom_nonbenchmark_count": len(
            _list(section.get("custom_nonbenchmark_overrides"))
        ),
    }


def _build_retrieval_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    retrieval = _dict(metadata.get("retrieval_policy"))
    return {
        "confidence_band": _str(retrieval.get("confidence_band", "--")),
        "skill_index": _bool(retrieval.get("skill_index")),
        "graph_retrieval": _bool(retrieval.get("graph_retrieval")),
        "semantic_backend": _str(retrieval.get("semantic_backend", "default")),
        "hunter_mode": _str(retrieval.get("hunter_mode", "--")),
    }


def _build_execution_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": _str(metadata.get("execution_mode", "") or "--"),
        "path": _str(metadata.get("execution_path", "") or "--"),
        "rapid_direct": _bool(metadata.get("rapid_direct_path")),
    }


def _build_routing_summary(
    routing: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    providers: set[str] = set()
    provider_models: set[str] = set()
    transports: set[str] = set()
    for section in _dict(routing).values():
        if not isinstance(section, dict):
            continue
        default_provider = _str(section.get("default_provider")).strip()
        default_model = _str(section.get("default_model")).strip()
        if default_provider:
            providers.add(default_provider)
            provider_models.add(f"{default_provider}:{default_model or '--'}")
            transports.add(_transport_for_provider(default_provider))
        for row in _list(section.get("active_overrides")):
            if not isinstance(row, dict):
                continue
            provider = _str(row.get("provider")).strip()
            model = _str(row.get("model")).strip()
            if provider:
                providers.add(provider)
                provider_models.add(f"{provider}:{model or '--'}")
                transports.add(_transport_for_provider(provider))

    return (
        {
            "architect": _routing_line(routing, "architect"),
            "hunter": _routing_line(routing, "hunter"),
        },
        {
            "provider_count": len(providers),
            "provider_model_count": len(provider_models),
            "transport_count": len(transports),
            "providers": sorted(providers),
            "provider_models": sorted(provider_models),
            "transports": sorted(transports),
        },
    )


def _build_catalog_alignment_summary(catalog_alignment: dict[str, Any]) -> dict[str, Any]:
    catalog_alignment = _dict(catalog_alignment)
    summary = {
        "catalog_size": _int(catalog_alignment.get("catalog_size")),
        "total_candidates": _int(catalog_alignment.get("total_candidates")),
        "added": _int(catalog_alignment.get("added")),
        "merged": _int(catalog_alignment.get("merged")),
        "structural_skips": _int(catalog_alignment.get("structural_skips")),
        "live_registry": _int(
            catalog_alignment.get("source_live_registry_candidates")
        ),
        "ast_fallback": _int(catalog_alignment.get("source_ast_candidates")),
        "cdg_matched": _int(catalog_alignment.get("source_cdg_metadata_matches")),
        "witness_doc": _int(catalog_alignment.get("source_witness_doc_fallbacks")),
        "witness_signature": _int(
            catalog_alignment.get("source_witness_signature_fallbacks")
        ),
    }
    merges = [
        {
            "candidate": _str(row.get("candidate")),
            "incumbent": _str(row.get("incumbent")),
            "similarity": _float(row.get("similarity")),
        }
        for row in _list(catalog_alignment.get("merge_details"))
        if isinstance(row, dict)
    ]
    merges.sort(key=lambda row: (-row["similarity"], row["candidate"], row["incumbent"]))
    breakdown = [
        {
            "source": _str(name),
            "added": _int(row.get("added")),
            "live_registry_candidates": _int(row.get("live_registry_candidates")),
            "ast_candidates": _int(row.get("ast_candidates")),
        }
        for name, row in _dict(catalog_alignment.get("source_breakdown")).items()
        if isinstance(row, dict)
    ]
    breakdown.sort(
        key=lambda row: (
            -row["added"],
            -(row["live_registry_candidates"] + row["ast_candidates"]),
            row["source"],
        )
    )
    summary["top_merges"] = merges[:5]
    summary["source_count"] = len(breakdown)
    summary["top_sources"] = breakdown[:5]
    return summary


def _build_architect_summary(architect_metrics: dict[str, Any]) -> dict[str, Any]:
    architect_metrics = _dict(architect_metrics)
    status_counts = _dict(architect_metrics.get("node_status_counts"))
    critique_counts = _dict(architect_metrics.get("critique_reject_counts_by_category"))
    retry_counts = _dict(architect_metrics.get("retry_counts_by_node"))
    blocked_names = _list(architect_metrics.get("blocked_node_names"))
    rewrite_actions = _list(architect_metrics.get("rewrite_actions"))
    return {
        "unresolved_leaf_count": _int(architect_metrics.get("unresolved_leaf_count")),
        "blocked_count": _int(status_counts.get("blocked")),
        "blocked_node_names": [str(name) for name in blocked_names if str(name).strip()],
        "blocked_reason": _str(architect_metrics.get("blocked_reason")),
        "any_port_pct": _float(architect_metrics.get("any_port_pct")),
        "any_edge_pct": _float(architect_metrics.get("any_edge_pct")),
        "rewrite_action_count": len(rewrite_actions),
        "last_node_name": _str(architect_metrics.get("last_node_name")),
        "critique_reject_total": sum(_int(v) for v in critique_counts.values()),
        "critique_reject_categories": sorted(
            (
                {"category": _str(category), "count": _int(count)}
                for category, count in critique_counts.items()
            ),
            key=lambda row: (-row["count"], row["category"]),
        )[:5],
        "retry_total": sum(_int(v) for v in retry_counts.values()),
        "retry_node_count": len(retry_counts),
    }


def _build_hunter_summary(hunter_metrics: dict[str, Any]) -> dict[str, Any]:
    hunter_metrics = _dict(hunter_metrics)
    return {
        "search_iterations": _int(hunter_metrics.get("search_iterations")),
        "embedding_results_total": _int(hunter_metrics.get("embedding_results_total")),
        "type_results_total": _int(hunter_metrics.get("type_results_total")),
        "new_candidates_total": _int(hunter_metrics.get("new_candidates_total")),
        "candidate_pool_size": _int(hunter_metrics.get("candidate_pool_size")),
        "rank_calls": _int(hunter_metrics.get("rank_calls")),
        "ranked_candidate_count": _int(hunter_metrics.get("ranked_candidate_count")),
        "verify_batches": _int(hunter_metrics.get("verify_batches")),
        "verified_candidates_total": _int(
            hunter_metrics.get("verified_candidates_total")
        ),
        "verification_success_total": _int(
            hunter_metrics.get("verification_success_total")
        ),
        "verification_failure_total": _int(
            hunter_metrics.get("verification_failure_total")
        ),
        "verified_matches": _int(hunter_metrics.get("verified_matches")),
        "reformulations": _int(hunter_metrics.get("reformulations")),
        "failure_analyses": _int(hunter_metrics.get("failure_analyses")),
        "reformulate_fallbacks": _int(hunter_metrics.get("reformulate_fallbacks")),
        "empty_search_terminations": _int(
            hunter_metrics.get("empty_search_terminations")
        ),
        "empty_verify_batches": _int(hunter_metrics.get("empty_verify_batches")),
        "query_count": _int(hunter_metrics.get("query_count")),
        "iteration": _int(hunter_metrics.get("iteration")),
        "last_query": _str(hunter_metrics.get("last_query")),
        "last_verified_candidate": _str(hunter_metrics.get("last_verified_candidate")),
    }


def _build_single_agent_summary(single_agent: dict[str, Any]) -> dict[str, Any]:
    single_agent = _dict(single_agent)
    policy = _dict(single_agent.get("policy"))
    artifacts = [
        {
            "name": name,
            "source": _str(row.get("source")),
            "path": _str(row.get("path")),
            "exists": _bool(row.get("exists")),
            "mutations": _int(row.get("mutations")),
        }
        for name, row in sorted(_dict(single_agent.get("concrete_artifacts")).items())
        if isinstance(row, dict)
    ]
    tool_rows = [
        {
            "name": name,
            "dispatches": _int(row.get("dispatches")),
            "latency_ms_total": round(_float(row.get("latency_ms_total")), 4),
            "avg_latency_ms": round(_float(row.get("avg_latency_ms")), 4),
        }
        for name, row in sorted(_dict(single_agent.get("tool_metrics")).items())
        if isinstance(row, dict)
    ]
    escalation_events = [
        {
            "from": _str(row.get("from")),
            "to": _str(row.get("to")),
            "reason": _str(row.get("reason")),
        }
        for row in _list(single_agent.get("escalation_events"))
        if isinstance(row, dict)
    ]
    return {
        "termination_reason": _str(single_agent.get("termination_reason")),
        "verification_status": _str(single_agent.get("verification_status")),
        "steps_used": _int(single_agent.get("steps_used")),
        "step_budget": _int(single_agent.get("step_budget")),
        "open_failures": _list(single_agent.get("open_failures")),
        "attempt_history": _list(single_agent.get("attempt_history")),
        "artifact_manifest_path": _str(single_agent.get("artifact_manifest_path")),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "tool_dispatch_count_total": _int(
            single_agent.get("tool_dispatch_count_total")
        ),
        "tool_latency_ms_total": round(
            _float(single_agent.get("tool_latency_ms_total")), 4
        ),
        "tool_metrics": tool_rows,
        "escalation_events": escalation_events,
        "policy": {
            "direct_grounding_enabled": _bool(
                policy.get("direct_grounding_enabled")
            ),
            "decomposition_mode": _str(policy.get("decomposition_mode")),
            "retrieval_intensity": _str(policy.get("retrieval_intensity")),
            "repair_policy": _str(policy.get("repair_policy")),
        },
    }


def _build_benchmark_summary(
    benchmark: dict[str, Any],
    release_validation: dict[str, Any],
) -> dict[str, Any]:
    benchmark = _dict(benchmark)
    release_validation = _dict(release_validation)
    return {
        "status": _str(benchmark.get("status")),
        "prompt_cases": _int(benchmark.get("prompt_cases")),
        "prompt_results": _int(benchmark.get("prompt_results")),
        "flow_cases": _int(benchmark.get("flow_cases")),
        "flow_results": _int(benchmark.get("flow_results")),
        "prompt_summary": _str(benchmark.get("prompt_summary")),
        "prompt_stability_summary": _str(benchmark.get("prompt_stability_summary")),
        "flow_summary": _str(benchmark.get("flow_summary")),
        "flow_stability_summary": _str(benchmark.get("flow_stability_summary")),
        "flow_gate_summary": _str(benchmark.get("flow_gate_summary")),
        "flow_execution_path_summary": _str(
            benchmark.get("flow_execution_path_summary")
        ),
        "runtime_override_policy_summary": _str(
            benchmark.get("runtime_override_policy_summary")
        ),
        "health_summary": _str(benchmark.get("health_summary")),
        "warning_summary": _str(benchmark.get("warning_summary")),
        "top_warning_subcheck": _str(benchmark.get("top_warning_subcheck")),
        "top_warning": _str(benchmark.get("top_warning")),
        "failure_summary": _str(benchmark.get("failure_summary")),
        "top_failed_subcheck": _str(benchmark.get("top_failed_subcheck")),
        "top_failure": _str(benchmark.get("top_failure")),
        "flow_required_variants": _list(benchmark.get("flow_required_variants")),
        "flow_comparison_variants": _list(
            benchmark.get("flow_comparison_variants")
        ),
        "flow_execution_paths": _dict(benchmark.get("flow_execution_paths")),
        "flow_prompt_volume": _dict(benchmark.get("flow_prompt_volume")),
        "flow_prompt_volume_summary": _str(benchmark.get("flow_prompt_volume_summary")),
        "single_agent_comparison": _dict(benchmark.get("single_agent_comparison")),
        "single_agent_comparison_summary": _str(
            benchmark.get("single_agent_comparison_summary")
        ),
        "flow_avg_prompt_calls": _dict(benchmark.get("flow_avg_prompt_calls")),
        "flow_avg_planner_tool_dispatches": _dict(
            benchmark.get("flow_avg_planner_tool_dispatches")
        ),
        "flow_avg_planner_tool_latency_ms": _dict(
            benchmark.get("flow_avg_planner_tool_latency_ms")
        ),
        "flow_avg_planner_escalations": _dict(
            benchmark.get("flow_avg_planner_escalations")
        ),
        "prompt_avg_latency_ms": _dict(benchmark.get("prompt_avg_latency_ms")),
        "flow_avg_latency_ms": _dict(benchmark.get("flow_avg_latency_ms")),
        "summary_report": _str(benchmark.get("summary_report")),
        "prompt_tuned_failures": _int(benchmark.get("prompt_tuned_failures")),
        "prompt_tuned_unstable_groups": _int(
            benchmark.get("prompt_tuned_unstable_groups")
        ),
        "flow_mode_failures": _int(benchmark.get("flow_mode_failures")),
        "flow_mode_unstable_groups": _int(benchmark.get("flow_mode_unstable_groups")),
        "flow_comparison_failures": _int(benchmark.get("flow_comparison_failures")),
        "flow_comparison_unstable_groups": _int(
            benchmark.get("flow_comparison_unstable_groups")
        ),
        "runtime_complexity": _dict(benchmark.get("runtime_complexity")),
        "release_warning_summary": _str(release_validation.get("warning_summary")),
        "release_runtime_warning_count": _int(
            release_validation.get("runtime_warning_count")
        ),
        "release_catalog_warning_count": _int(
            release_validation.get("catalog_warning_count")
        ),
        "release_benchmark_warning_count": _int(
            release_validation.get("benchmark_warning_count")
        ),
        "release_top_runtime_warning": _str(
            release_validation.get("top_runtime_warning")
        ),
        "release_top_catalog_warning": _str(
            release_validation.get("top_catalog_warning")
        ),
        "release_top_benchmark_warning": _str(
            release_validation.get("top_benchmark_warning")
        ),
        "release_top_benchmark_warning_subcheck": _str(
            release_validation.get("top_benchmark_warning_subcheck")
        ),
        "release_failure_summary": _str(release_validation.get("failure_summary")),
        "release_top_failed_check": _str(release_validation.get("top_failed_check")),
        "release_top_benchmark_subcheck": _str(
            release_validation.get("top_benchmark_subcheck")
        ),
        "release_top_benchmark_failure": _str(
            release_validation.get("top_benchmark_failure")
        ),
        "release_top_runtime_failure": _str(
            release_validation.get("top_runtime_failure")
        ),
        "release_top_catalog_failure": _str(
            release_validation.get("top_catalog_failure")
        ),
        "manifest": _str(release_validation.get("manifest")),
        "benchmarks_dir": _str(release_validation.get("benchmarks_dir")),
        "release_status": _str(release_validation.get("status")),
    }


def _build_catalog_validation_summary(
    release_validation: dict[str, Any],
) -> dict[str, Any]:
    catalog_validation = _dict(_dict(release_validation).get("catalog_validation"))
    alignment = _dict(catalog_validation.get("alignment"))
    drift_sources = _list(alignment.get("rows"))
    return {
        "status": _str(catalog_validation.get("status")),
        "configured_sources": _int(catalog_validation.get("configured_sources")),
        "resolved_sources": _int(catalog_validation.get("resolved_sources")),
        "source_candidates": _int(catalog_validation.get("source_candidates")),
        "source_added": _int(catalog_validation.get("source_added")),
        "coverage_summary": _str(catalog_validation.get("coverage_summary")),
        "alignment_summary": _str(catalog_validation.get("alignment_summary")),
        "warning_summary": _str(catalog_validation.get("warning_summary")),
        "high_severity_sources": _list(catalog_validation.get("high_severity_sources")),
        "medium_severity_sources": _list(
            catalog_validation.get("medium_severity_sources")
        ),
        "warnings": _list(catalog_validation.get("warnings")),
        "missing_sources": _list(catalog_validation.get("missing_sources")),
        "zero_candidate_sources": _list(
            catalog_validation.get("zero_candidate_sources")
        ),
        "violations": _list(catalog_validation.get("violations")),
        "alignment": alignment,
        "top_drift_sources": drift_sources,
    }


def _build_shared_context_summary(shared_context: dict[str, Any]) -> dict[str, Any]:
    shared_context = _dict(shared_context)
    contexts = _dict(shared_context.get("contexts"))
    shared_rows: list[dict[str, Any]] = []
    backends: set[str] = set()
    totals = {
        "total_searches": 0,
        "total_hits": 0,
        "total_puts": 0,
        "total_injected_blocks": 0,
        "total_promotions": 0,
        "total_template_searches": 0,
        "total_template_hits": 0,
        "total_template_puts": 0,
        "total_template_injected_blocks": 0,
        "total_failure_searches": 0,
        "total_failure_hits": 0,
        "total_failure_puts": 0,
        "total_failure_injected_blocks": 0,
    }
    for label, row in contexts.items():
        if not isinstance(row, dict):
            continue
        backend = _str(row.get("backend"))
        if backend:
            backends.add(backend)
        shared_rows.append(
            {
                "label": _str(label),
                "backend": backend,
                "searches": _int(row.get("searches_total", row.get("searches"))),
                "hits": _int(row.get("search_hits", row.get("hits"))),
                "puts": _int(row.get("puts_total", row.get("puts"))),
                "injected_blocks": _int(row.get("injected_blocks")),
                "promotions": _int(row.get("promotions_total", row.get("promotions"))),
                "template_searches": _int(
                    row.get("template_searches_total", row.get("template_searches"))
                ),
                "template_hits": _int(
                    row.get("template_search_hits", row.get("template_hits"))
                ),
                "template_puts": _int(
                    row.get("template_puts_total", row.get("template_puts"))
                ),
                "template_injected_blocks": _int(
                    row.get("template_injected_blocks", row.get("template_injected_blocks"))
                ),
                "failure_searches": _int(
                    row.get("failure_searches_total", row.get("failure_searches"))
                ),
                "failure_hits": _int(
                    row.get("failure_search_hits", row.get("failure_hits"))
                ),
                "failure_puts": _int(
                    row.get("failure_puts_total", row.get("failure_puts"))
                ),
                "failure_injected_blocks": _int(
                    row.get("failure_injected_blocks", row.get("failure_injected_blocks"))
                ),
            }
        )
        totals["total_searches"] += shared_rows[-1]["searches"]
        totals["total_hits"] += shared_rows[-1]["hits"]
        totals["total_puts"] += shared_rows[-1]["puts"]
        totals["total_injected_blocks"] += shared_rows[-1]["injected_blocks"]
        totals["total_promotions"] += shared_rows[-1]["promotions"]
        totals["total_template_searches"] += shared_rows[-1]["template_searches"]
        totals["total_template_hits"] += shared_rows[-1]["template_hits"]
        totals["total_template_puts"] += shared_rows[-1]["template_puts"]
        totals["total_template_injected_blocks"] += shared_rows[-1][
            "template_injected_blocks"
        ]
        totals["total_failure_searches"] += shared_rows[-1]["failure_searches"]
        totals["total_failure_hits"] += shared_rows[-1]["failure_hits"]
        totals["total_failure_puts"] += shared_rows[-1]["failure_puts"]
        totals["total_failure_injected_blocks"] += shared_rows[-1][
            "failure_injected_blocks"
        ]
    return {
        "context_count": len(shared_rows),
        "active_context_count": _int(
            shared_context.get("active_context_count"), len(shared_rows)
        ),
        "backends": sorted(backends),
        **totals,
        "metrics_path": _str(shared_context.get("metrics_path")),
        "contexts": sorted(shared_rows, key=lambda row: row["label"]),
    }


def _build_optimize_summary(optimize: dict[str, Any]) -> dict[str, Any]:
    optimize = _dict(optimize)
    rows = []
    for row in _list(optimize.get("trial_rows")):
        if not isinstance(row, dict):
            continue
        proposal = _dict(row.get("skeleton_proposal"))
        rows.append(
            {
                "trial": _int(row.get("trial")),
                "loss": _float(row.get("loss")),
                "node_count": _int(row.get("node_count")),
                "edge_count": _int(row.get("edge_count")),
                "primitive_signature": _str(row.get("primitive_signature")),
                "has_parameters": _bool(row.get("has_parameters")),
                "parameter_node_count": _int(row.get("parameter_node_count")),
                "topology_changed": _bool(row.get("topology_changed")),
                "primitive_assignment_changed": _bool(
                    row.get("primitive_assignment_changed")
                ),
                "expansion_applied": _bool(row.get("expansion_applied")),
                "distinct_primitive_family_count": _int(
                    row.get("distinct_primitive_family_count")
                ),
                "family_entropy": _float(row.get("family_entropy")),
                "cross_family_edge_count": _int(row.get("cross_family_edge_count")),
                "rollback_applied": _bool(row.get("rollback_applied")),
                "rollback_restored_trial": _int(row.get("rollback_restored_trial")),
                "rollback_reason": _str(row.get("rollback_reason")),
                "reused_cached_evaluation": _bool(row.get("reused_cached_evaluation")),
                "proposal_selected": _str(row.get("proposal_selected")),
                "proposal_candidate_count": _int(row.get("proposal_candidate_count")),
                "proposal_candidates": _list(row.get("proposal_candidates")),
                "proposal_rejected": _bool(row.get("proposal_rejected")),
                "proposal_baseline_loss": (
                    _float(row.get("proposal_baseline_loss"))
                    if row.get("proposal_baseline_loss") is not None
                    else None
                ),
                "proposal_selected_loss": (
                    _float(row.get("proposal_selected_loss"))
                    if row.get("proposal_selected_loss") is not None
                    else None
                ),
                "proposal_improvement": (
                    _float(row.get("proposal_improvement"))
                    if row.get("proposal_improvement") is not None
                    else None
                ),
                "skeleton_proposal": {
                    "target_node": _str(proposal.get("target_node")),
                    "source_family": _str(proposal.get("source_family")),
                    "inserted_node_count": _int(proposal.get("inserted_node_count")),
                    "inserted_edge_count": _int(proposal.get("inserted_edge_count")),
                    "complexity_penalty": (
                        _float(proposal.get("complexity_penalty"))
                        if proposal.get("complexity_penalty") is not None
                        else None
                    ),
                    "objective_gain": (
                        _float(proposal.get("objective_gain"))
                        if proposal.get("objective_gain") is not None
                        else None
                    ),
                    "accepted": _bool(proposal.get("accepted")),
                    "retained": _bool(proposal.get("retained")),
                    "reverted": _bool(proposal.get("reverted")),
                },
            }
        )
    best_structure = _dict(optimize.get("best_structure"))
    best_params = _dict(optimize.get("best_parameter_assignments"))
    selected_counts = _dict(optimize.get("selected_proposal_counts"))
    return {
        "objective": _str(optimize.get("objective")),
        "execution_metric": _str(optimize.get("execution_metric")),
        "benchmark_path": _str(optimize.get("benchmark_path")),
        "max_trials": _int(optimize.get("max_trials")),
        "trials_run": _int(optimize.get("trials_run"), len(rows)),
        "best_loss": (
            _float(optimize.get("best_loss"))
            if optimize.get("best_loss") is not None
            else None
        ),
        "best_trial": _int(optimize.get("best_trial")),
        "parameterized_trials": _int(optimize.get("parameterized_trials")),
        "primitive_change_trials": _int(optimize.get("primitive_change_trials")),
        "topology_change_trials": _int(optimize.get("topology_change_trials")),
        "expansion_applied_trials": _int(optimize.get("expansion_applied_trials")),
        "rollback_trials": _int(optimize.get("rollback_trials")),
        "proposal_selection_trials": _int(optimize.get("proposal_selection_trials")),
        "proposal_rejected_trials": _int(optimize.get("proposal_rejected_trials")),
        "cached_reuse_trials": _int(optimize.get("cached_reuse_trials")),
        "cached_reruns_avoided": _int(optimize.get("cached_reruns_avoided")),
        "selected_proposal_counts": {
            str(key): _int(value) for key, value in selected_counts.items()
        },
        "skeleton_proposal_trials": _int(optimize.get("skeleton_proposal_trials")),
        "accepted_skeleton_proposals": _int(
            optimize.get("accepted_skeleton_proposals")
        ),
        "rejected_skeleton_proposals": _int(
            optimize.get("rejected_skeleton_proposals")
        ),
        "mean_skeleton_complexity_penalty": _float(
            optimize.get("mean_skeleton_complexity_penalty")
        ),
        "mean_skeleton_objective_gain": _float(
            optimize.get("mean_skeleton_objective_gain")
        ),
        "skeleton_retention_rate": _float(optimize.get("skeleton_retention_rate")),
        "unique_primitive_signatures": _int(
            optimize.get("unique_primitive_signatures")
        ),
        "unique_topologies": _int(optimize.get("unique_topologies")),
        "expansion_rules_applied": _list(optimize.get("expansion_rules_applied")),
        "max_family_entropy": _float(optimize.get("max_family_entropy")),
        "max_distinct_primitive_families": _int(
            optimize.get("max_distinct_primitive_families")
        ),
        "mean_expansion_loss_delta": _float(optimize.get("mean_expansion_loss_delta")),
        "worst_expansion_loss_delta": _float(
            optimize.get("worst_expansion_loss_delta")
        ),
        "mean_selected_proposal_improvement": _float(
            optimize.get("mean_selected_proposal_improvement")
        ),
        "best_selected_proposal_improvement": _float(
            optimize.get("best_selected_proposal_improvement")
        ),
        "best_structure": {
            "node_count": _int(best_structure.get("node_count")),
            "edge_count": _int(best_structure.get("edge_count")),
            "topo_hash": _str(best_structure.get("topo_hash")),
            "primitive_signature": _str(best_structure.get("primitive_signature")),
            "distinct_primitive_family_count": _int(
                best_structure.get("distinct_primitive_family_count")
            ),
            "family_entropy": _float(best_structure.get("family_entropy")),
            "cross_family_edge_count": _int(
                best_structure.get("cross_family_edge_count")
            ),
        },
        "best_parameter_assignments": best_params,
        "trial_rows": rows,
        "evolution": _dict(optimize.get("evolution")),
    }


def _build_benchmark_validation_summary(
    benchmark: dict[str, Any], release_validation: dict[str, Any]
) -> dict[str, Any]:
    return _build_benchmark_summary(benchmark, release_validation)


def _extract_dashboard_summaries(run: dict[str, Any]) -> dict[str, Any]:
    metadata = _dict(run.get("metadata"))
    benchmark = _dict(metadata.get("benchmark_validation"))
    release_validation = _dict(metadata.get("release_validation"))
    summary = {
        "execution_summary": _build_execution_summary(metadata),
        "retrieval_summary": _build_retrieval_summary(metadata),
    }
    routing_summary, provider_complexity = _build_routing_summary(
        _dict(metadata.get("llm_routing"))
    )
    summary["routing_summary"] = routing_summary
    summary["provider_complexity"] = provider_complexity
    summary["catalog_alignment_summary"] = _build_catalog_alignment_summary(
        _dict(metadata.get("catalog_alignment"))
    )
    summary["architect_summary"] = _build_architect_summary(
        _dict(metadata.get("architect_metrics"))
    )
    summary["hunter_summary"] = _build_hunter_summary(_dict(metadata.get("hunter_metrics")))
    summary["single_agent_summary"] = _build_single_agent_summary(
        _dict(metadata.get("single_agent"))
    )
    summary["benchmark_summary"] = _build_benchmark_validation_summary(
        benchmark, release_validation
    )
    summary["catalog_validation_summary"] = _build_catalog_validation_summary(
        release_validation
    )
    summary["shared_context_summary"] = _build_shared_context_summary(
        _dict(metadata.get("shared_context"))
    )
    summary["optimize_summary"] = _build_optimize_summary(_dict(metadata.get("optimize")))
    return summary


def _decorate_dashboard_run(
    run: dict[str, Any],
    *,
    stale_seconds: int,
    now: float,
) -> dict[str, Any]:
    annotated = _annotate_hang_signals(run, stale_seconds=stale_seconds, now=now)
    out = dict(annotated)
    out.update(_extract_dashboard_summaries(annotated))
    return out
