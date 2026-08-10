"""Commands for NAS/AutoML optimization and profiling."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from sciona.commands.llm_helpers import _create_llm_router, _warm_llm_if_supported
from sciona.commands.routing_helpers import (
    _mode_feature_summary,
    _print_mode_summary,
    _print_prompt_routing_summary,
    _print_retrieval_policy,
    _resolve_retrieval_policy,
    _routing_metadata_summary,
)
from sciona.commands.runtime_helpers import (
    _create_proof_env,
    _load_architect_catalog,
    _load_semantic_index,
    _load_skill_index_or_empty,
    _shutdown_telemetry_drain,
)
from sciona.commands.shared_context_helpers import (
    _create_shared_context,
    _print_shared_context_metrics,
    _shared_context_metadata,
    _write_shared_context_metrics_file,
)
from sciona.principal.heuristic_outcomes import (
    extract_heuristic_usability_memory,
    summarize_heuristic_usability_memory,
)


def _parse_dataset_vars(entries: list[str] | None) -> dict[str, str]:
    """Parse repeated ``KEY=VALUE`` CLI args into a dict."""
    result: dict[str, str] = {}
    for entry in entries or []:
        key, sep, value = entry.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(
                f"Invalid dataset var {entry!r}; expected KEY=VALUE."
            )
        result[key] = value
    return result


def _summarize_optimize_history(
    history: list[dict[str, Any]],
    *,
    objective: str,
    execution_metric: str,
    benchmark_path: str,
    max_trials: int,
    output_root: Path,
    goal: str = "",
) -> dict[str, Any]:
    """Build dashboard-friendly metadata for a Principal optimisation run."""
    trial_rows: list[dict[str, Any]] = []
    best_entry: dict[str, Any] | None = None
    parameterized_trials = 0
    primitive_change_trials = 0
    topology_change_trials = 0
    expansion_applied_trials = 0
    rollback_trials = 0
    proposal_selection_trials = 0
    proposal_rejected_trials = 0
    cached_reuse_trials = 0
    cached_reruns_avoided = 0
    selected_proposal_counts: dict[str, int] = {}
    skeleton_proposal_trials = 0
    accepted_skeleton_proposals = 0
    rejected_skeleton_proposals = 0
    retained_skeleton_proposals = 0
    skeleton_complexity_penalties: list[float] = []
    skeleton_objective_gains: list[float] = []
    primitive_signatures: set[str] = set()
    topology_signatures: set[str] = set()
    expansion_rules: set[str] = set()
    max_family_entropy = 0.0
    max_distinct_primitive_families = 0
    expansion_deltas: list[float] = []
    selected_proposal_improvements: list[float] = []
    trial_loss_by_id: dict[int, float] = {}

    for entry in history:
        if not isinstance(entry, dict):
            continue
        structure = entry.get("structure", {}) if isinstance(entry.get("structure"), dict) else {}
        expansion = entry.get("expansion", {}) if isinstance(entry.get("expansion"), dict) else {}
        rollback = entry.get("rollback", {}) if isinstance(entry.get("rollback"), dict) else {}
        proposal = (
            entry.get("proposal_selection", {})
            if isinstance(entry.get("proposal_selection"), dict)
            else {}
        )
        skeleton_meta = (
            entry.get("skeleton_proposal", {})
            if isinstance(entry.get("skeleton_proposal"), dict)
            else {}
        )
        params = (
            entry.get("parameter_assignments", {})
            if isinstance(entry.get("parameter_assignments"), dict)
            else {}
        )
        trial_id = int(entry.get("trial", 0) or 0)
        loss_value = float(entry.get("loss", 0.0) or 0.0)
        reused_cached_evaluation = bool(entry.get("reused_cached_evaluation"))
        trial_loss_by_id[trial_id] = loss_value
        primitive_signature = str(structure.get("primitive_signature", "") or "")
        topo_hash = str(structure.get("topo_hash", "") or "")
        if primitive_signature:
            primitive_signatures.add(primitive_signature)
        if topo_hash:
            topology_signatures.add(topo_hash)
        if params:
            parameterized_trials += 1
        if bool(structure.get("primitive_assignment_changed")):
            primitive_change_trials += 1
        if bool(structure.get("topology_changed")):
            topology_change_trials += 1
        if bool(expansion.get("applied")):
            expansion_applied_trials += 1
        if bool(rollback.get("applied")):
            rollback_trials += 1
        selected_proposal = str(proposal.get("selected", "") or "")
        if reused_cached_evaluation:
            cached_reuse_trials += 1
            if selected_proposal:
                cached_reruns_avoided += 1
        candidate_rows = proposal.get("candidates", [])
        if not isinstance(candidate_rows, list):
            candidate_rows = []
        baseline_loss = proposal.get("baseline_loss")
        selected_loss: float | None = None
        candidate_labels: list[str] = []
        candidate_count = 0
        for candidate in candidate_rows:
            if not isinstance(candidate, dict):
                continue
            candidate_count += 1
            label = str(candidate.get("label", "") or "")
            if label:
                candidate_labels.append(label)
            if selected_proposal and label == selected_proposal:
                try:
                    selected_loss = float(candidate.get("loss", 0.0) or 0.0)
                except (TypeError, ValueError):
                    selected_loss = None
        if candidate_count > 0 or baseline_loss is not None:
            proposal_selection_trials += 1
        if selected_proposal:
            selected_proposal_counts[selected_proposal] = (
                selected_proposal_counts.get(selected_proposal, 0) + 1
            )
            try:
                baseline_loss_value = float(baseline_loss)
            except (TypeError, ValueError):
                baseline_loss_value = None
            if baseline_loss_value is not None and selected_loss is not None:
                selected_proposal_improvements.append(
                    baseline_loss_value - selected_loss
                )
        elif candidate_count > 0:
            proposal_rejected_trials += 1
        rule_names = expansion.get("rules_applied", [])
        if isinstance(rule_names, list):
            for rule_name in rule_names:
                if isinstance(rule_name, str) and rule_name:
                    expansion_rules.add(rule_name)
        max_family_entropy = max(
            max_family_entropy,
            float(structure.get("family_entropy", 0.0) or 0.0),
        )
        max_distinct_primitive_families = max(
            max_distinct_primitive_families,
            int(structure.get("distinct_primitive_family_count", 0) or 0),
        )
        restored_trial = rollback.get("restored_trial")
        if restored_trial is not None:
            try:
                restored_trial_id = int(restored_trial)
            except (TypeError, ValueError):
                restored_trial_id = 0
            baseline_loss = trial_loss_by_id.get(restored_trial_id)
            if baseline_loss is not None:
                expansion_deltas.append(loss_value - baseline_loss)
        target_node = str(skeleton_meta.get("target_node", "") or "")
        source_family = str(skeleton_meta.get("source_family", "") or "")
        inserted_node_count = int(skeleton_meta.get("inserted_node_count", 0) or 0)
        inserted_edge_count = int(skeleton_meta.get("inserted_edge_count", 0) or 0)
        complexity_penalty = skeleton_meta.get("complexity_penalty")
        objective_gain = skeleton_meta.get("objective_gain")
        accepted = bool(skeleton_meta.get("accepted"))
        retained = bool(skeleton_meta.get("retained"))
        reverted = bool(skeleton_meta.get("reverted"))
        if target_node or source_family or inserted_node_count or inserted_edge_count:
            skeleton_proposal_trials += 1
            if accepted:
                accepted_skeleton_proposals += 1
            else:
                rejected_skeleton_proposals += 1
            if retained:
                retained_skeleton_proposals += 1
            if isinstance(complexity_penalty, (int, float)):
                skeleton_complexity_penalties.append(float(complexity_penalty))
            if isinstance(objective_gain, (int, float)):
                skeleton_objective_gains.append(float(objective_gain))
        row = {
            "trial": trial_id,
            "loss": loss_value,
            "node_count": int(structure.get("node_count", 0) or 0),
            "edge_count": int(structure.get("edge_count", 0) or 0),
            "topo_hash": topo_hash,
            "primitive_signature": primitive_signature,
            "parameter_node_count": len(params),
            "has_parameters": bool(params),
            "topology_changed": bool(structure.get("topology_changed")),
            "primitive_assignment_changed": bool(
                structure.get("primitive_assignment_changed")
            ),
            "expansion_applied": bool(expansion.get("applied")),
            "distinct_primitive_family_count": int(
                structure.get("distinct_primitive_family_count", 0) or 0
            ),
            "family_entropy": float(structure.get("family_entropy", 0.0) or 0.0),
            "cross_family_edge_count": int(
                structure.get("cross_family_edge_count", 0) or 0
            ),
            "rollback_applied": bool(rollback.get("applied")),
            "rollback_restored_trial": (
                int(rollback.get("restored_trial", 0) or 0)
                if rollback.get("restored_trial") is not None
                else 0
            ),
            "rollback_reason": str(rollback.get("reason", "") or ""),
            "reused_cached_evaluation": reused_cached_evaluation,
            "proposal_selected": selected_proposal,
            "proposal_candidate_count": candidate_count,
            "proposal_candidates": candidate_labels,
            "proposal_rejected": not bool(selected_proposal) and candidate_count > 0,
            "proposal_baseline_loss": (
                float(baseline_loss)
                if baseline_loss is not None
                else None
            ),
            "proposal_selected_loss": selected_loss,
            "proposal_improvement": (
                float(baseline_loss) - selected_loss
                if baseline_loss is not None and selected_loss is not None
                else None
            ),
            "skeleton_proposal": {
                "target_node": target_node,
                "source_family": source_family,
                "inserted_node_count": inserted_node_count,
                "inserted_edge_count": inserted_edge_count,
                "complexity_penalty": (
                    float(complexity_penalty)
                    if isinstance(complexity_penalty, (int, float))
                    else None
                ),
                "objective_gain": (
                    float(objective_gain)
                    if isinstance(objective_gain, (int, float))
                    else None
                ),
                "accepted": accepted,
                "retained": retained,
                "reverted": reverted,
            },
        }
        trial_rows.append(row)
        if best_entry is None or row["loss"] < float(best_entry.get("loss", float("inf"))):
            best_entry = entry

    best_params = {}
    best_structure = {}
    if isinstance(best_entry, dict):
        best_params = (
            best_entry.get("parameter_assignments", {})
            if isinstance(best_entry.get("parameter_assignments"), dict)
            else {}
        )
        best_structure = (
            best_entry.get("structure", {})
            if isinstance(best_entry.get("structure"), dict)
            else {}
        )

    from sciona.evolution import build_evolution_trace

    evolution_trace = build_evolution_trace(
        history,
        goal=goal,
        objective=objective,
    )
    return {
        "objective": objective,
        "execution_metric": execution_metric,
        "benchmark_path": benchmark_path,
        "max_trials": int(max_trials),
        "trials_run": len(trial_rows),
        "best_loss": float(best_entry.get("loss", float("inf"))) if isinstance(best_entry, dict) else None,
        "best_trial": int(best_entry.get("trial", 0) or 0) if isinstance(best_entry, dict) else 0,
        "parameterized_trials": parameterized_trials,
        "primitive_change_trials": primitive_change_trials,
        "topology_change_trials": topology_change_trials,
        "expansion_applied_trials": expansion_applied_trials,
        "rollback_trials": rollback_trials,
        "proposal_selection_trials": proposal_selection_trials,
        "proposal_rejected_trials": proposal_rejected_trials,
        "cached_reuse_trials": cached_reuse_trials,
        "cached_reruns_avoided": cached_reruns_avoided,
        "selected_proposal_counts": dict(sorted(selected_proposal_counts.items())),
        "skeleton_proposal_trials": skeleton_proposal_trials,
        "accepted_skeleton_proposals": accepted_skeleton_proposals,
        "rejected_skeleton_proposals": rejected_skeleton_proposals,
        "mean_skeleton_complexity_penalty": (
            float(sum(skeleton_complexity_penalties) / len(skeleton_complexity_penalties))
            if skeleton_complexity_penalties
            else 0.0
        ),
        "mean_skeleton_objective_gain": (
            float(sum(skeleton_objective_gains) / len(skeleton_objective_gains))
            if skeleton_objective_gains
            else 0.0
        ),
        "skeleton_retention_rate": (
            float(retained_skeleton_proposals / accepted_skeleton_proposals)
            if accepted_skeleton_proposals
            else 0.0
        ),
        "unique_primitive_signatures": len(primitive_signatures),
        "unique_topologies": len(topology_signatures),
        "expansion_rules_applied": sorted(expansion_rules),
        "max_family_entropy": max_family_entropy,
        "max_distinct_primitive_families": max_distinct_primitive_families,
        "mean_expansion_loss_delta": (
            float(sum(expansion_deltas) / len(expansion_deltas))
            if expansion_deltas
            else 0.0
        ),
        "worst_expansion_loss_delta": (
            float(max(expansion_deltas))
            if expansion_deltas
            else 0.0
        ),
        "mean_selected_proposal_improvement": (
            float(sum(selected_proposal_improvements) / len(selected_proposal_improvements))
            if selected_proposal_improvements
            else 0.0
        ),
        "best_selected_proposal_improvement": (
            float(max(selected_proposal_improvements))
            if selected_proposal_improvements
            else 0.0
        ),
        "best_parameter_assignments": best_params,
        "best_structure": best_structure,
        "trial_history_path": str(output_root / "trial_history.json"),
        "trial_rows": trial_rows,
        "evolution": evolution_trace,
    }

async def _match_results_for_optimize(
    cdg: Any,
    *,
    mode: str,
    prover: Any,
    hunter: Any,
    llm: Any,
) -> list[Any]:
    from sciona.commands.run_cmds import _run_structured_single_pass
    from sciona.orchestrator import run_orchestration

    if mode in {"structured", "verified", "rapid"}:
        if mode != "verified":
            result = await _run_structured_single_pass(
                cdg,
                prover=prover,
                hunter=hunter,
            )
        else:
            result = await run_orchestration(
                cdg,
                hunter_agent=hunter,
                llm=llm,
                prover=prover,
                max_rounds=3,
            )
        return list(result.match_results)
    raise ValueError(f"unsupported optimize mode {mode!r}")


async def _synthesize_export_bundle_for_optimize(
    cdg: Any,
    match_results: list[Any],
    *,
    prover: Any,
    config: Any,
    catalog: Any | None,
    output_root: Path,
    trial_index: int,
) -> Any:
    from sciona.services import (
        SynthesizerAssembleRequest,
        SynthesizerCompileRequest,
        SynthesizerService,
    )
    from sciona.synthesizer.extractor import ExportTarget, Extractor
    from sciona.synthesizer.models import SkeletonFile, SynthesisResult

    service = SynthesizerService(prover=prover)
    tunable_params_by_primitive: dict[str, list[str]] = {}
    if catalog is not None:
        for node in cdg.nodes:
            primitive_name = str(getattr(node, "matched_primitive", "") or "").strip()
            if not primitive_name:
                continue
            prim = catalog.get(primitive_name)
            if prim is None or not prim.tunable_params:
                continue
            tunable_params_by_primitive[primitive_name] = [
                spec.name for spec in prim.tunable_params
            ]
    skeleton = service.assemble(
        SynthesizerAssembleRequest(
            cdg=cdg,
            match_results=match_results,
            tunable_params_by_primitive=tunable_params_by_primitive,
        )
    ).skeleton

    env = _create_proof_env(prover, config)
    try:
        compile_result = await service.compile(
            SynthesizerCompileRequest(skeleton=skeleton, env=env)
        )
    finally:
        await env.close()

    if not compile_result.result.compiled_ok:
        raise RuntimeError("optimize synthesis compile failed")

    trial_dir = output_root / f"trial_{trial_index:03d}"
    trial_dir.mkdir(parents=True, exist_ok=True)
    source_path = trial_dir / "verified.py"
    source_path.write_text(compile_result.result.skeleton.source_code)
    export_skeleton = SkeletonFile(
        prover=compile_result.result.skeleton.prover,
        source_code=source_path.read_text(),
    )
    synthesis_result = SynthesisResult(
        skeleton=export_skeleton,
        compiled_ok=True,
        sorry_remaining=compile_result.result.skeleton.sorry_count,
        patches_applied=0,
        iterations_used=0,
    )
    extractor = Extractor(config)
    bundle = await extractor.extract(
        synthesis_result,
        ExportTarget("python-pkg"),
        trial_dir / "export_python_pkg",
    )
    bundle.source_path = source_path
    bundle.output_dir = trial_dir
    if bundle.executable_artifact is None:
        bundle.executable_artifact = trial_dir / "export_python_pkg" / "runner.py"
    return bundle


async def _cmd_optimize(args: argparse.Namespace) -> None:
    """Run the Principal NAS/AutoML optimisation loop."""
    from sciona.architect.checkpointer import create_checkpointer
    from sciona.architect.graph import DecompositionAgent
    from sciona.config import AgeomConfig, resolve_execution_mode
    from sciona.hunter.graph import HunterAgent
    from sciona.judge.checker import VerificationOracleImpl
    from sciona.principal.atom_ledger import AtomLedger
    from sciona.principal.evaluator import ExecutionSandbox
    from sciona.principal.graph import (
        PrincipalDeps,
        build_principal_graph,
    )
    from sciona.principal.hpo import OptunaManager
    from sciona.principal.expansion import ExpansionEngine
    from sciona.principal.expansion_rules import default_rule_sets
    from sciona.principal.metric_selection import resolve_optimization_objective
    from sciona.telemetry import (
        configure_dashboard_output,
        configure_postgres_telemetry,
        finish_run,
        merge_run_metadata,
        start_run,
        telemetry_scope,
        update_stage,
    )
    from sciona.types import Prover

    config = AgeomConfig()
    mode_settings = resolve_execution_mode(config, getattr(args, "mode", None))
    prover = Prover(args.prover)
    _print_mode_summary("optimize", mode_settings)

    catalog, _catalog_alignment = _load_architect_catalog(args, config)
    retrieval_policy = _resolve_retrieval_policy(
        mode_settings=mode_settings,
        catalog=catalog,
        texts=[args.goal],
    )
    _print_retrieval_policy(retrieval_policy)

    skill_index = _load_skill_index_or_empty(
        config,
        enabled=retrieval_policy.skill_index_enabled,
    )

    # LLM
    try:
        from sciona.llm_router import (
            ARCHITECT_CRITIQUE,
            ARCHITECT_DECOMPOSE,
            ARCHITECT_STRATEGY,
        )

        prompt_keys = [
            ARCHITECT_STRATEGY,
            ARCHITECT_DECOMPOSE,
            ARCHITECT_CRITIQUE,
        ]
        architect_routing = _routing_metadata_summary(
            _print_prompt_routing_summary(
                config, "architect", prompt_keys, getattr(args, "mode", None)
            )
        )
        llm = _create_llm_router(args, config, "architect", prompt_keys)
        await _warm_llm_if_supported(llm, "architect")
    except (ValueError, ImportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        metric, evaluation_spec, objective_label = resolve_optimization_objective(
            args.metric,
            getattr(args, "eval_spec", None),
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    configure_dashboard_output(config.telemetry_runs_dir)
    _telem_drain = None
    _telem_store = None
    if config.telemetry_backend != "file" and config.postgres_uri:
        try:
            from sciona.telemetry_store import PostgresTelemetryStore, TelemetryDrain

            _telem_store = PostgresTelemetryStore(config.postgres_uri)
            await _telem_store.setup()
            _telem_drain = TelemetryDrain(_telem_store)
            configure_postgres_telemetry(_telem_store, _telem_drain)
            await _telem_drain.start()
        except Exception:
            _telem_drain = None
            _telem_store = None
    postgres_uri = "" if args.no_persist else config.postgres_uri
    architect_run_id = uuid.uuid4().hex
    architect_shared_context, architect_shared_metrics = await _create_shared_context(
        config,
        enabled=mode_settings.architect_shared_context_enabled,
    )
    output_root = Path("output") / f"principal_optimize_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_root.mkdir(parents=True, exist_ok=True)
    telemetry_run_id = start_run(
        "optimization",
        label=getattr(args, "label", ""),
        metadata={
            "command": "optimize",
            "goal": args.goal,
            "prover": prover.value,
            "execution_mode": mode_settings.mode,
            "execution_path": "principal_optimize",
            "mode_features": _mode_feature_summary(mode_settings),
            "objective": objective_label,
            "execution_metric": metric.value,
            "benchmark_path": str(args.benchmark),
            "output_dir": str(output_root),
            "max_trials": int(args.trials),
            "catalog_alignment": _catalog_alignment,
            "retrieval_policy": {
                "catalog_confidence": retrieval_policy.catalog_confidence,
                "confidence_band": retrieval_policy.confidence_band,
                "skill_index": retrieval_policy.skill_index_enabled,
                "graph_retrieval": retrieval_policy.graph_retrieval_enabled,
                "semantic_backend": retrieval_policy.semantic_index_backend_override
                or "default",
                "hunter_mode": retrieval_policy.hunter_mode,
            },
        },
    )

    print("Principal optimisation loop")
    print(f"  Goal: {args.goal}")
    print(f"  Objective: {objective_label}")
    if objective_label != metric.value:
        print(f"  Execution metric: {metric.value} (reference loss: {objective_label})")
    else:
        print(f"  Execution metric: {metric.value}")
    print(f"  Trials: {args.trials}")
    print(f"  Benchmark: {args.benchmark}")
    print(f"  Output: {output_root}")
    print()

    final_state: dict[str, Any] | None = None
    try:
        with telemetry_scope(run_id=telemetry_run_id):
            update_stage(
                stage="setup",
                status="running",
                message="loading optimize dependencies",
            )
            async with create_checkpointer(postgres_uri) as checkpointer:
                architect = DecompositionAgent(
                    catalog=catalog,
                    skill_index=skill_index,
                    llm=llm,
                    checkpointer=checkpointer,
                    shared_context=architect_shared_context,
                    shared_context_metrics=architect_shared_metrics,
                    context_namespace=f"architect/{architect_run_id}",
                    context_budget_chars=config.architect_shared_context_budget_chars,
                    architect_critique_llm_enabled=config.architect_critique_llm_enabled,
                )

                index, _index_mode = _load_semantic_index(
                    config.index_dir,
                    config,
                    backend_override=retrieval_policy.semantic_index_backend_override,
                )
                env = _create_proof_env(prover, config)
                if prover == Prover.LEAN4:
                    oracle = VerificationOracleImpl(lean_env=env)
                elif prover == Prover.PYTHON:
                    oracle = VerificationOracleImpl(python_env=env)
                else:
                    oracle = VerificationOracleImpl(coq_env=env)

                from sciona.llm_router import (
                    HUNTER_ANALYZE_FAILURE,
                    HUNTER_REFORMULATE,
                    HUNTER_SCORE,
                )

                hunter_prompt_keys = [
                    HUNTER_SCORE,
                    HUNTER_REFORMULATE,
                    HUNTER_ANALYZE_FAILURE,
                ]
                hunter_routing = _routing_metadata_summary(
                    _print_prompt_routing_summary(
                        config, "hunter", hunter_prompt_keys, getattr(args, "mode", None)
                    )
                )
                merge_run_metadata(
                    {
                        "llm_routing": {
                            "architect": architect_routing,
                            "hunter": hunter_routing,
                        }
                    },
                    run_id=telemetry_run_id,
                )
                embedder = getattr(index, "_embedder", None)
                hunter_llm = _create_llm_router(
                    args,
                    config,
                    "hunter",
                    hunter_prompt_keys,
                    embedder=embedder,
                )
                await _warm_llm_if_supported(hunter_llm, "hunter")
                hunter = HunterAgent(
                    index=index,
                    oracle=oracle,
                    llm=hunter_llm,
                    max_iterations=config.hunter_max_iterations,
                    top_k_verify=config.hunter_top_k_verify,
                    search_k=config.hunter_search_k,
                    mode=retrieval_policy.hunter_mode,
                    use_gbnf=mode_settings.hunter_use_gbnf,
                    query_batch_size=config.hunter_query_batch_size,
                    top_k_per_query=config.hunter_top_k_per_query,
                    max_candidates_total=config.hunter_max_candidates_total,
                    live_catalog=catalog,
                )

                trial_counter = {"value": 0}

                async def _trial_synthesizer(cdg: Any, match_results: list[Any]) -> Any:
                    trial_counter["value"] += 1
                    return await _synthesize_export_bundle_for_optimize(
                        cdg,
                        match_results,
                        prover=prover,
                        config=config,
                        catalog=catalog,
                        output_root=output_root,
                        trial_index=trial_counter["value"],
                    )

                sandbox = ExecutionSandbox(
                    timeout_s=args.timeout,
                    dataset_slice_start_s=getattr(args, "dataset_slice_start", None),
                    dataset_slice_stop_s=getattr(args, "dataset_slice_stop", None),
                )
                atom_ledger = AtomLedger()
                hpo_manager = OptunaManager(study_name="principal")
                expansion_engine = ExpansionEngine(default_rule_sets())
                deps = PrincipalDeps(
                    architect=architect,
                    sandbox=sandbox,
                    match_results_fn=lambda cdg: _match_results_for_optimize(
                        cdg,
                        mode=mode_settings.mode,
                        prover=prover,
                        hunter=hunter,
                        llm=llm,
                    ),
                    synthesize_fn=_trial_synthesizer,
                    evaluation_spec=evaluation_spec,
                    dataset_varset=_parse_dataset_vars(getattr(args, "dataset_var", None)),
                    dataset_slice_start_s=getattr(args, "dataset_slice_start", None),
                    dataset_slice_stop_s=getattr(args, "dataset_slice_stop", None),
                    atom_ledger=atom_ledger,
                    catalog=catalog,
                    hpo_manager=hpo_manager,
                    param_trials_per_structure=2,
                    heuristic_cohort_size=max(
                        1,
                        int(getattr(args, "heuristic_cohort_size", 1) or 1),
                    ),
                    heuristic_cohort_concurrency=max(
                        1,
                        int(getattr(args, "heuristic_cohort_concurrency", 1) or 1),
                    ),
                    expansion_engine=expansion_engine,
                )
                graph = build_principal_graph().compile()

                initial_state = {
                    "goal": args.goal,
                    "metric": metric,
                    "dataset_path": args.benchmark,
                    "max_trials": args.trials,
                }

                config_dict = {"configurable": {"deps": deps}}
                update_stage(
                    stage="principal_optimize",
                    status="running",
                    message="running structural and hyperparameter search",
                )
                try:
                    final_state = await graph.ainvoke(initial_state, config=config_dict)
                finally:
                    await env.close()
            update_stage(
                stage="principal_optimize",
                status="completed",
                message="optimization complete",
            )
    except Exception as exc:
        finish_run(telemetry_run_id, status="failed", error=str(exc))
        await _shutdown_telemetry_drain(_telem_drain, _telem_store)
        raise

    # Report
    print("\nOptimisation complete:")
    print(f"  Trials run: {final_state.get('current_trial', 0)}")
    print(f"  Best loss: {final_state.get('best_loss', float('inf')):.6f}")
    history = final_state.get("trial_history", [])
    if history:
        print("  Trial history:")
        for entry in history:
            structure = entry.get("structure", {})
            print(
                f"    Trial {entry['trial']}: loss={entry['loss']:.6f} "
                f"nodes={structure.get('node_count', 0)} "
                f"edges={structure.get('edge_count', 0)} "
                f"primitive_sig={structure.get('primitive_signature', '')}"
            )
    (output_root / "trial_history.json").write_text(
        json.dumps(history, indent=2) + "\n"
    )
    # Write Dead-End Flare for bounty system
    try:
        from sciona.principal.flare import generate_flare, write_flare_config

        flare = generate_flare(final_state)
        flare_path = write_flare_config(flare, output_root / "flare.yml")
        print(f"  Flare saved to {flare_path}. Run `sciona bounty generate` to post.")
    except Exception as flare_exc:
        logger.warning("Failed to write flare: %s", flare_exc)
    heuristic_memory_records = extract_heuristic_usability_memory(history)
    heuristic_memory_summary = summarize_heuristic_usability_memory(
        heuristic_memory_records
    )
    (output_root / "heuristic_outcome_memory.json").write_text(
        json.dumps(
            {
                "summary": heuristic_memory_summary,
                "records": heuristic_memory_summary.get("records", []),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    _print_shared_context_metrics("architect", architect_shared_metrics)
    metrics_out_dir = Path("output")
    metrics_path = _write_shared_context_metrics_file(
        metrics_out_dir / "optimize_shared_context_metrics.json",
        {"architect": architect_shared_metrics},
    )
    if metrics_path is not None:
        print(f"  Shared context metrics: {metrics_path}")
    merge_run_metadata(
        {
            "shared_context": _shared_context_metadata(
                {"architect": architect_shared_metrics},
                metrics_path=metrics_path,
            ),
            "optimize": _summarize_optimize_history(
                history,
                objective=objective_label,
                execution_metric=metric.value,
                benchmark_path=str(args.benchmark),
                max_trials=int(args.trials),
                output_root=output_root,
                goal=str(args.goal),
            ),
            "heuristic_outcome_memory": {
                "memory_count": heuristic_memory_summary.get("memory_count", 0),
                "positive_memory_count": heuristic_memory_summary.get(
                    "positive_memory_count", 0
                ),
                "selected_memory_count": heuristic_memory_summary.get(
                    "selected_memory_count", 0
                ),
                "heuristic_signatures": list(
                    heuristic_memory_summary.get("heuristic_signatures", {}).keys()
                ),
                "memory_path": str(output_root / "heuristic_outcome_memory.json"),
            },
        },
        run_id=telemetry_run_id,
    )
    finish_run(telemetry_run_id, status="completed")
    await _shutdown_telemetry_drain(_telem_drain, _telem_store)


async def _cmd_profile(args: argparse.Namespace) -> None:
    """Evaluate an existing CDG against a dataset and rank error contributors."""
    from sciona.architect.handoff import load_json
    from sciona.principal.metric_selection import resolve_optimization_objective
    from sciona.principal.profiler import profile_algorithm_error
    from sciona.synthesizer.models import ExportBundle
    from sciona.types import MatchResult

    cdg_path = Path(args.cdg)
    if not cdg_path.exists():
        print(f"Error: CDG file not found at {cdg_path}", file=sys.stderr)
        sys.exit(1)

    artifact_path = Path(args.artifact)
    if not artifact_path.exists():
        print(f"Error: Artifact file not found at {artifact_path}", file=sys.stderr)
        sys.exit(1)

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"Error: Dataset file not found at {dataset_path}", file=sys.stderr)
        sys.exit(1)

    cdg = load_json(cdg_path)
    try:
        dataset_varset = _parse_dataset_vars(getattr(args, "dataset_var", None))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        metric, evaluation_spec, objective_label = resolve_optimization_objective(
            args.metric,
            getattr(args, "eval_spec", None),
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    runner_candidates = [
        artifact_path.parent / "runner.py",
        artifact_path.parent / "export_python_pkg" / "runner.py",
    ]
    executable_artifact = next(
        (candidate for candidate in runner_candidates if candidate.exists()),
        None,
    )

    matches_path = artifact_path.parent / "matches.json"
    match_results: list[MatchResult] = []
    if matches_path.exists():
        try:
            with matches_path.open() as fh:
                matches_payload = json.load(fh)
            if isinstance(matches_payload, list):
                match_results = [MatchResult.from_dict(item) for item in matches_payload]
        except Exception:
            match_results = []

    bundle = ExportBundle(
        target="python-pkg",
        output_dir=artifact_path.parent,
        source_path=artifact_path,
        compiled_artifact=artifact_path,
        executable_artifact=executable_artifact,
    )

    print(
        f"Profiling {artifact_path.name} against {dataset_path.name} using objective {objective_label}..."
    )

    try:
        gradients = await profile_algorithm_error(
            cdg=cdg,
            bundle=bundle,
            dataset_path=str(dataset_path),
            metric=metric,
            dataset_varset=dataset_varset or None,
            match_results=match_results,
            evaluation_spec=evaluation_spec,
            dataset_slice_start_s=getattr(args, "dataset_slice_start", None),
            dataset_slice_stop_s=getattr(args, "dataset_slice_stop", None),
        )

        if not gradients:
            print("No gradients were computed. Ensure trace.jsonl is emitted properly.")
            return

        print("\n=== Profiling Results ===")
        print(f"{'Node ID':<20} | {'Score (%)':<10} | {'Reason'}")
        print("-" * 80)
        for g in gradients:
            print(f"{g.node_id:<20} | {g.gradient_score:<10.2f} | {g.bottleneck_reason}")

    except Exception as exc:
        print(f"Error during profiling: {exc}", file=sys.stderr)
        sys.exit(1)
