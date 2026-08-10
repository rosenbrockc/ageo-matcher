"""Shared helper routines for Principal proposal selection and time travel."""

from __future__ import annotations

from dataclasses import dataclass, field
import inspect
import logging
from typing import Any

from sciona.architect.handoff import CDGExport
from sciona.principal.atom_ledger import compute_slot_signature
from sciona.principal.admissibility import (
    build_admissibility_context,
    default_admissibility_evaluator,
)
from sciona.principal.evaluation_helpers import evaluate_bundle_for_metric
from sciona.principal.expansion import ExpansionContext
from sciona.principal.heuristic_proposal_policy import (
    HeuristicProposalGuidance,
    candidate_action_classes,
)
from sciona.principal.models import (
    BenchmarkResult,
    OptimizationMetric,
    ProposalCandidateTrace,
    ProposalStructuralDelta,
)
from sciona.principal.structure_objective import benchmark_from_ghost_report
from sciona.principal.variant_mutation import maybe_apply_bottleneck_variant
from sciona.architect.planning_contract import summarize_planning_artifact
from sciona.synthesizer.ghost_sim import GhostSimReport, run_ghost_simulation
from sciona.synthesizer.models import ExportBundle

logger = logging.getLogger(__name__)


@dataclass
class ProposalCandidate:
    """Typed proposal candidate tracked during refinement selection."""

    label: str
    candidate_type: str
    cdg: CDGExport
    loss: float
    bundle: ExportBundle | None = None
    benchmark: BenchmarkResult | None = None
    match_results: list[Any] = field(default_factory=list)
    ghost_report: GhostSimReport = field(default_factory=GhostSimReport)
    rules_applied: list[str] = field(default_factory=list)
    applied_assets: list[dict[str, Any]] = field(default_factory=list)
    variant_name: str = ""
    family: str = ""
    thread_id: str = ""
    diagnostic_count: int = 0
    diagnostic_rule_names: list[str] = field(default_factory=list)
    context_summary: dict[str, Any] = field(default_factory=dict)
    admissibility: dict[str, Any] = field(default_factory=dict)
    structural_delta: ProposalStructuralDelta = field(
        default_factory=ProposalStructuralDelta
    )
    heuristic_ids: list[str] = field(default_factory=list)
    preferred_action_classes: list[str] = field(default_factory=list)
    candidate_action_classes: list[str] = field(default_factory=list)
    selection_disposition: str = "candidate"
    selection_reason: str = ""
    selected_reason_codes: list[str] = field(default_factory=list)
    rejected_reason_codes: list[str] = field(default_factory=list)

    def improves_baseline(self, baseline_loss: float) -> bool:
        return self.loss < baseline_loss

    def heuristic_alignment_rank(self) -> int:
        if not self.preferred_action_classes or not self.candidate_action_classes:
            return 0
        for index, action in enumerate(self.preferred_action_classes):
            if action in self.candidate_action_classes:
                return index
        return len(self.preferred_action_classes) + 1

    def ranking_key(self, baseline_loss: float) -> tuple[int, int, int, int, int, float, str]:
        complexity = (
            abs(int(self.structural_delta.node_count_delta))
            + abs(int(self.structural_delta.edge_count_delta))
        )
        return (
            1 if bool(self.admissibility.get("hard_rejected")) else 0,
            0 if self.improves_baseline(baseline_loss) else 1,
            1 if bool(self.admissibility.get("routed_to_refinement")) else 0,
            self.heuristic_alignment_rank(),
            complexity,
            float(self.loss),
            self.label,
        )

    def history_row(self, baseline_loss: float) -> dict[str, Any]:
        """Serialize one candidate in a backward-compatible history shape."""
        payload = ProposalCandidateTrace(
            label=self.label,
            proposal_type=self.candidate_type,
            candidate_type=self.candidate_type,
            loss=float(self.loss),
            improves_baseline=self.improves_baseline(baseline_loss),
            admissibility=dict(self.admissibility),
            evidence={
                "rules_applied": list(self.rules_applied),
                "applied_assets": list(self.applied_assets),
                "diagnostic_count": int(self.diagnostic_count),
                "diagnostic_rule_names": list(self.diagnostic_rule_names),
                "context_summary": dict(self.context_summary),
                "heuristic_ids": list(self.heuristic_ids),
                "preferred_action_classes": list(self.preferred_action_classes),
                "candidate_action_classes": list(self.candidate_action_classes),
            },
            metadata={
                "variant_name": self.variant_name,
                "family": self.family,
                "thread_id": self.thread_id,
                "selection_disposition": self.selection_disposition,
                "selection_reason": self.selection_reason,
            },
            structural_delta=self.structural_delta,
            selected=self.selection_disposition == "selected",
            selected_reason_codes=list(self.selected_reason_codes),
            rejected_reason_codes=list(self.rejected_reason_codes),
            rules_applied=list(self.rules_applied),
            applied_assets=list(self.applied_assets),
            variant_name=self.variant_name,
            family=self.family,
            thread_id=self.thread_id,
            diagnostic_count=int(self.diagnostic_count),
            diagnostic_rule_names=list(self.diagnostic_rule_names),
            context_summary=dict(self.context_summary),
            selection_disposition=self.selection_disposition,
            selection_reason=self.selection_reason,
        ).model_dump(mode="json")
        payload["cdg_snapshot"] = self.cdg.model_dump(mode="json")
        return payload


def summarize_expansion_context(context: ExpansionContext) -> dict[str, Any]:
    """Return a compact telemetry summary for the runtime expansion context."""
    runtime_inputs = context.runtime_inputs or context.signal_data or {}
    intermediates = context.intermediates or {}
    eval_result = context.eval_result or {}
    runtime_evidence = context.runtime_evidence or {}
    planning_artifact = context.planning_artifact or {}
    if hasattr(planning_artifact, "model_dump"):
        planning_artifact = planning_artifact.model_dump(mode="json")
    return {
        "runtime_input_keys": sorted(runtime_inputs.keys())[:12],
        "signal_keys": sorted((context.signal_data or {}).keys())[:12],
        "intermediate_keys": sorted(intermediates.keys())[:16],
        "has_eval_result": bool(eval_result),
        "eval_keys": (
            sorted(eval_result.keys())[:16]
            if isinstance(eval_result, dict)
            else []
        ),
        "runtime_evidence_keys": sorted(runtime_evidence.keys())[:16],
        "heuristic_ids": [
            str(item.get("heuristic", {}).get("heuristic_id", ""))
            for item in runtime_evidence.get("heuristics", [])
            if isinstance(item, dict)
            and isinstance(item.get("heuristic"), dict)
            and item.get("heuristic", {}).get("heuristic_id")
        ],
        "planning_artifact": summarize_planning_artifact(planning_artifact),
    }


def build_expansion_context(state: Any) -> ExpansionContext:
    """Construct a best-effort runtime context from the latest evaluation artifacts."""
    artifacts = (
        dict(state.benchmark.runtime_artifacts)
        if state.benchmark is not None
        else {}
    )
    stdout_payload = artifacts.get("stdout_payload", {})
    eval_result: dict[str, Any]
    if isinstance(stdout_payload, dict):
        eval_result = dict(stdout_payload)
    else:
        eval_result = {}
    if state.benchmark is not None:
        global_loss = getattr(state.benchmark, "global_loss", None)
        if global_loss is not None:
            eval_result.setdefault("global_loss", global_loss)
    intermediates = artifacts.get("intermediates", {})
    runtime_inputs = artifacts.get("runtime_inputs", artifacts.get("signal_data", {}))
    signal_data = artifacts.get("signal_data", runtime_inputs)
    runtime_evidence = artifacts.get("runtime_evidence", {})
    planning_artifact = getattr(state, "planning_artifact", None) or {}
    if hasattr(planning_artifact, "model_dump"):
        planning_artifact = planning_artifact.model_dump(mode="json")
    if not isinstance(intermediates, dict):
        intermediates = {}
    if not isinstance(runtime_inputs, dict):
        runtime_inputs = {}
    if not isinstance(signal_data, dict):
        signal_data = {}
    if not isinstance(runtime_evidence, dict):
        runtime_evidence = {}
    if not runtime_inputs and runtime_evidence:
        canonical = runtime_evidence.get("canonical_runtime_context", {})
        if isinstance(canonical, dict):
            canonical_inputs = canonical.get("canonical_inputs", {})
            if isinstance(canonical_inputs, dict):
                recovered_inputs: dict[str, Any] = {}
                telemetry_summary = runtime_evidence.get("telemetry_summary", {})
                telemetry_summary = (
                    telemetry_summary if isinstance(telemetry_summary, dict) else {}
                )
                for canonical_name, ref in canonical_inputs.items():
                    if not isinstance(canonical_name, str):
                        continue
                    if isinstance(ref, dict):
                        recovered_inputs[canonical_name] = (
                            telemetry_summary.get(canonical_name)
                            if canonical_name in telemetry_summary
                            else ref.get("raw_key", canonical_name)
                        )
                    else:
                        recovered_inputs[canonical_name] = canonical_name
                if recovered_inputs:
                    runtime_inputs = recovered_inputs
                    if not signal_data:
                        signal_data = dict(recovered_inputs)
    if not intermediates and runtime_evidence:
        recovered_intermediates = runtime_evidence.get("intermediate_summaries", {})
        if not isinstance(recovered_intermediates, dict) or not recovered_intermediates:
            telemetry_summary = runtime_evidence.get("telemetry_summary", {})
            if isinstance(telemetry_summary, dict):
                recovered_intermediates = telemetry_summary.get("intermediates", {})
        if isinstance(recovered_intermediates, dict) and recovered_intermediates:
            intermediates = dict(recovered_intermediates)
    telemetry_summary = artifacts.get("telemetry_summary")
    if isinstance(telemetry_summary, dict) and telemetry_summary:
        runtime_evidence = dict(runtime_evidence)
        runtime_evidence.setdefault("telemetry_summary", dict(telemetry_summary))
    intermediate_summaries = artifacts.get("intermediate_summaries")
    if isinstance(intermediate_summaries, dict) and intermediate_summaries:
        runtime_evidence = dict(runtime_evidence)
        runtime_evidence.setdefault(
            "intermediate_summaries", dict(intermediate_summaries)
        )
    return ExpansionContext(
        intermediates=dict(intermediates),
        eval_result=eval_result or None,
        runtime_inputs=dict(runtime_inputs) or None,
        signal_data=dict(signal_data) or None,
        runtime_evidence=dict(runtime_evidence) or None,
        planning_artifact=dict(planning_artifact) or None,
    )


async def build_redecomposition_candidate(
    state: Any,
    deps: Any,
    *,
    bottleneck_name: str | None,
) -> tuple[CDGExport, str] | None:
    """Fork and re-decompose a candidate structure for the current bottleneck."""
    if not state.bottleneck_node_id or state.cdg is None:
        return None

    history = await deps.architect.get_state_history(state.thread_id)
    target_cp: str | None = None
    for entry in history:
        vals = entry.get("values", {})
        node_ids = {n.node_id for n in vals.get("nodes", [])}
        if state.bottleneck_node_id not in node_ids:
            target_cp = entry.get("checkpoint_id")
            break

    if target_cp is None and history:
        target_cp = history[-1].get("checkpoint_id")
    if target_cp is None:
        return None

    new_thread_id = await deps.architect.fork(state.thread_id, target_cp)
    constraint = (
        f"The previous decomposition caused a bottleneck: "
        f"{state.bottleneck_reason}. Re-decompose more efficiently."
    )
    planning_artifact = getattr(state, "planning_artifact", None) or {}
    if hasattr(planning_artifact, "model_dump"):
        planning_artifact = planning_artifact.model_dump(mode="json")
    planning_summary = summarize_planning_artifact(planning_artifact)
    if planning_summary:
        constraint += (
            "\nPLANNING CONTRACT: "
            f"artifact_version={planning_summary.get('artifact_version', '')}, "
            f"paradigm={planning_summary.get('paradigm', '')}, "
            f"constraints={planning_summary.get('constraint_count', 0)}"
        )

    if deps.atom_ledger is not None and deps.catalog is not None and state.cdg is not None:
        bottleneck_node = next(
            (n for n in state.cdg.nodes if n.node_id == state.bottleneck_node_id),
            None,
        )
        if bottleneck_node is not None and bottleneck_node.matched_primitive:
            node_map = {n.node_id: n for n in state.cdg.nodes}
            parent = (
                node_map.get(bottleneck_node.parent_id)
                if bottleneck_node.parent_id
                else None
            )
            slot = compute_slot_signature(bottleneck_node, parent)
            same_category = [
                p.name
                for p in deps.catalog.search_by_category(bottleneck_node.concept_type)
            ]
            if len(same_category) > 1:
                ranked = deps.atom_ledger.rank_candidates(slot, same_category)
                top_3 = [
                    (name, f"{score:.2f}")
                    for name, score in ranked[:3]
                    if score != float("inf")
                ]
                if top_3:
                    constraint += (
                        f"\nATOM RANKINGS for '{bottleneck_name}': "
                        f"prefer {top_3}"
                    )

    constrained_goal = f"{state.goal}\n\nCONSTRAINT: {constraint}"
    cdg = await deps.architect.decompose(constrained_goal, thread_id=new_thread_id)
    mutation = maybe_apply_bottleneck_variant(
        cdg,
        bottleneck_name=bottleneck_name,
        atom_ledger=deps.atom_ledger,
        catalog=deps.catalog,
    )
    return mutation.cdg, new_thread_id


async def evaluate_proposal_candidate(
    state: Any,
    deps: Any,
    cdg: CDGExport,
) -> tuple[float, ExportBundle | None, BenchmarkResult | None, list[Any], GhostSimReport]:
    """Evaluate a proposal candidate without mutating trial history."""
    match_results = deps.match_results_fn(cdg) if deps.match_results_fn else []
    if inspect.isawaitable(match_results):
        match_results = await match_results
    match_results = list(match_results)
    ghost_report = run_ghost_simulation(cdg, match_results)
    bundle: ExportBundle | None = None
    benchmark: BenchmarkResult | None = None
    if deps.synthesize_fn is not None:
        try:
            bundle = await deps.synthesize_fn(cdg, match_results)
        except Exception:
            logger.warning(
                "Proposal synthesis failed; scoring candidate as infinite loss.",
                exc_info=True,
            )
            return float("inf"), None, None, match_results, ghost_report
    if state.metric == OptimizationMetric.STRUCTURE:
        benchmark = benchmark_from_ghost_report(ghost_report)
    elif bundle is not None:
        try:
            benchmark = await evaluate_bundle_for_metric(
                deps.sandbox,
                bundle,
                state.dataset_path,
                state.metric,
                dataset_varset=deps.dataset_varset,
                evaluation_spec=deps.evaluation_spec,
            )
        except Exception:
            logger.warning(
                "Proposal evaluation failed; scoring candidate as infinite loss.",
                exc_info=True,
            )
            return float("inf"), bundle, None, match_results, ghost_report
    loss = float(benchmark.global_loss) if benchmark is not None else float("inf")
    return loss, bundle, benchmark, match_results, ghost_report


def summarize_proposal_admissibility(
    *,
    cdg: CDGExport,
    benchmark: BenchmarkResult | None,
    planning_artifact: dict[str, Any] | None,
    evaluator: Any,
) -> dict[str, Any]:
    """Evaluate admissibility for a proposal candidate and serialize the result."""
    if benchmark is None:
        return {
            "hard_rejected": False,
            "routed_to_refinement": False,
            "decision_count": 0,
            "hard_reject_rule_ids": [],
            "warning_rule_ids": [],
            "refinement_rule_ids": [],
            "decisions": [],
            "family": "",
        }

    family = (
        planning_artifact.get("family_hint", "")
        if isinstance(planning_artifact, dict)
        else ""
    )
    context = build_admissibility_context(
        cdg=cdg,
        planning_artifact=planning_artifact,
        runtime_artifacts=getattr(benchmark, "runtime_artifacts", {}) or {},
        family=family,
    )
    report = evaluator.evaluate(context)
    summary = report.summary()
    summary["family"] = context.family
    return summary


def proposal_structural_delta(
    baseline_cdg: CDGExport,
    candidate_cdg: CDGExport,
) -> ProposalStructuralDelta:
    """Summarize structural cost relative to the current baseline CDG."""
    return ProposalStructuralDelta(
        node_count_delta=len(candidate_cdg.nodes) - len(baseline_cdg.nodes),
        edge_count_delta=len(candidate_cdg.edges) - len(baseline_cdg.edges),
    )


def classify_proposal_candidate(
    candidate: ProposalCandidate,
    *,
    baseline_loss: float,
) -> ProposalCandidate:
    """Assign a deterministic disposition and reason to one proposal."""
    if bool(candidate.admissibility.get("hard_rejected")):
        candidate.selection_disposition = "rejected"
        candidate.selection_reason = "proposal_hard_rejected"
        candidate.rejected_reason_codes = ["hard_reject"]
        return candidate
    if not candidate.improves_baseline(baseline_loss):
        candidate.selection_disposition = "rejected"
        candidate.selection_reason = "no_improvement_over_baseline"
        reasons = ["no_loss_improvement"]
        if bool(candidate.admissibility.get("routed_to_refinement")):
            reasons.append("still_requires_refinement")
        candidate.rejected_reason_codes = reasons
        return candidate
    candidate.selection_disposition = "ranked"
    candidate.selection_reason = (
        "improving_candidate_requires_further_refinement"
        if bool(candidate.admissibility.get("routed_to_refinement"))
        else "admissible_improvement"
    )
    return candidate


def apply_heuristic_guidance(
    candidate: ProposalCandidate,
    *,
    guidance: HeuristicProposalGuidance,
) -> ProposalCandidate:
    """Attach heuristic-driven action preferences to one proposal candidate."""
    candidate.heuristic_ids = list(guidance.heuristic_ids)
    candidate.preferred_action_classes = [
        action_class.value for action_class in guidance.preferred_action_classes
    ]
    candidate.candidate_action_classes = [
        action_class.value
        for action_class in candidate_action_classes(
            candidate.candidate_type,
            family=candidate.family,
            rules_applied=candidate.rules_applied,
            applied_assets=candidate.applied_assets,
        )
    ]
    if candidate.context_summary:
        candidate.context_summary = dict(candidate.context_summary)
    candidate.context_summary.setdefault(
        "heuristic_guidance", guidance.model_dump(mode="json")
    )
    return candidate


def select_best_proposal(
    candidates: list[ProposalCandidate],
    *,
    baseline_loss: float,
) -> ProposalCandidate | None:
    """Return the best admissible improving candidate."""
    ranked = [
        classify_proposal_candidate(candidate, baseline_loss=baseline_loss)
        for candidate in candidates
    ]
    admissible = [
        candidate
        for candidate in ranked
        if candidate.selection_disposition == "ranked"
    ]
    if not admissible:
        return None
    selected = sorted(
        admissible,
        key=lambda candidate: candidate.ranking_key(baseline_loss),
    )[0]
    selected.selection_disposition = "selected"
    selected.selection_reason = "best_admissible_improvement"
    selected.selected_reason_codes = ["best_ranked_candidate", "improves_baseline"]
    if (
        selected.preferred_action_classes
        and selected.candidate_action_classes
        and selected.heuristic_alignment_rank() == 0
    ):
        selected.selected_reason_codes.append("matches_heuristic_guidance")
    if not bool(selected.admissibility.get("hard_rejected")):
        selected.selected_reason_codes.append("passes_hard_admissibility")
    if not bool(selected.admissibility.get("routed_to_refinement")):
        selected.selected_reason_codes.append("satisfies_admissibility")
    for candidate in ranked:
        if candidate is selected:
            candidate.rejected_reason_codes = []
            continue
        if candidate.selection_disposition == "rejected":
            continue
        candidate.selection_disposition = "rejected"
        candidate.selection_reason = "outranked_by_selected_candidate"
        reasons = ["outranked_by_selected_candidate"]
        if selected.preferred_action_classes and candidate.candidate_action_classes:
            reasons.append("weaker_heuristic_alignment")
        if bool(candidate.admissibility.get("routed_to_refinement")):
            reasons.append("still_requires_refinement")
        candidate.rejected_reason_codes = reasons
    return selected
