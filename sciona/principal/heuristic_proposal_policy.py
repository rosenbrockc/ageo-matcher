"""Cross-family heuristic guidance for proposal selection."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from sciona.heuristic_registries import resolve_local_heuristic_registry
from sciona.heuristics import HeuristicActionClass
from sciona.principal.expansion_assets import (
    load_local_expansion_assets,
    resolve_local_expansion_asset,
)
from sciona.principal.heuristic_outcomes import heuristic_action_bonus
from sciona.usability import UsabilityAssessment


class HeuristicProposalGuidance(BaseModel):
    """Shared proposal guidance derived from runtime heuristics and family registries."""

    family: str = ""
    heuristic_ids: list[str] = Field(default_factory=list)
    heuristic_summary: dict[str, dict[str, Any]] = Field(default_factory=dict)
    preferred_action_classes: list[HeuristicActionClass] = Field(default_factory=list)
    registry_asset_id: str = ""
    cohort_size: int = 0
    usability_summary: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


def _summarize_usability_assessment(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    try:
        assessment = UsabilityAssessment.model_validate(raw)
    except Exception:
        return {}
    return {
        "assessment_id": assessment.assessment_id,
        "usable_for_guidance": assessment.usable_for_guidance,
        "usable_for_scoring": assessment.usable_for_scoring,
        "usable_for_final_benchmark": assessment.usable_for_final_benchmark,
        "heuristic_signature": list(assessment.heuristic_signature),
        "guidance_exclusions": [
            reason.code for reason in assessment.guidance.blocking_reasons
        ],
        "scoring_exclusions": [
            reason.code for reason in assessment.scoring.blocking_reasons
        ],
        "final_benchmark_exclusions": [
            reason.code for reason in assessment.final_benchmark.blocking_reasons
        ],
        "guidance_warnings": [
            reason.code for reason in assessment.guidance.warning_reasons
        ],
        "scoring_warnings": [
            reason.code for reason in assessment.scoring.warning_reasons
        ],
        "final_benchmark_warnings": [
            reason.code for reason in assessment.final_benchmark.warning_reasons
        ],
    }


def _extract_runtime_heuristic_summary(
    runtime_artifacts: dict[str, Any],
) -> tuple[list[str], dict[str, dict[str, Any]], int, dict[str, Any]]:
    usability_summary = _summarize_usability_assessment(
        runtime_artifacts.get("usability_assessment", {})
    )
    guidance_enabled = (
        bool(usability_summary.get("usable_for_guidance"))
        if usability_summary
        else True
    )
    heuristics = runtime_artifacts.get("heuristics", [])
    heuristic_summary: dict[str, dict[str, Any]] = {}
    excluded_heuristic_ids: list[str] = []
    if isinstance(heuristics, list):
        counts: Counter[str] = Counter()
        confidence_totals: Counter[str] = Counter()
        max_confidence: dict[str, float] = {}
        source_sections: dict[str, set[str]] = {}
        for item in heuristics:
            if not isinstance(item, dict):
                continue
            heuristic = item.get("heuristic", {})
            if not isinstance(heuristic, dict):
                continue
            heuristic_id = str(heuristic.get("heuristic_id", "") or "").strip()
            if not heuristic_id:
                continue
            if usability_summary and not guidance_enabled:
                excluded_heuristic_ids.append(heuristic_id)
                continue
            counts[heuristic_id] += 1
            try:
                confidence = float(
                    item.get("confidence", heuristic.get("confidence", 0.0)) or 0.0
                )
            except (TypeError, ValueError):
                confidence = 0.0
            confidence_totals[heuristic_id] += confidence
            max_confidence[heuristic_id] = max(
                max_confidence.get(heuristic_id, 0.0),
                confidence,
            )
            source_sections.setdefault(heuristic_id, set()).add(
                str(item.get("source_section", "") or "")
            )
        for heuristic_id, count in counts.items():
            heuristic_summary[heuristic_id] = {
                "occurrence_count": int(count),
                "member_count": 1,
                "coverage_fraction": 1.0,
                "mean_confidence": float(confidence_totals[heuristic_id] / count),
                "max_confidence": float(max_confidence.get(heuristic_id, 0.0)),
                "source_sections": sorted(
                    section
                    for section in source_sections.get(heuristic_id, set())
                    if section
                ),
            }

    cohort_size = 0
    cohort = runtime_artifacts.get("heuristic_cohort", {})
    if isinstance(cohort, dict):
        try:
            cohort_size = int(
                cohort.get("evaluated_member_count") or cohort.get("cohort_size") or 0
            )
        except (TypeError, ValueError):
            cohort_size = 0
        cohort_heuristics = cohort.get("heuristics", {})
        if isinstance(cohort_heuristics, dict):
            for heuristic_id, stats in cohort_heuristics.items():
                if not isinstance(stats, dict):
                    continue
                heuristic_summary[str(heuristic_id)] = dict(stats)
        cohort_usability = cohort.get("usability", {})
        if isinstance(cohort_usability, dict):
            usability_summary = {
                **usability_summary,
                "cohort_usability": dict(cohort_usability),
            }
        excluded_members = cohort.get("excluded_members", [])
        if isinstance(excluded_members, list):
            usability_summary.setdefault("cohort_excluded_member_count", len(excluded_members))
            usability_summary.setdefault(
                "cohort_excluded_member_labels",
                [
                    str(item.get("member_label", ""))
                    for item in excluded_members
                    if isinstance(item, dict) and item.get("member_label")
                ],
            )

    if excluded_heuristic_ids:
        usability_summary.setdefault(
            "excluded_heuristic_ids",
            sorted(dict.fromkeys(excluded_heuristic_ids)),
        )
        usability_summary.setdefault(
            "guidance_exclusions",
            ["guidance_usability_excluded"],
        )

    return sorted(heuristic_summary.keys()), heuristic_summary, cohort_size, usability_summary


def _extract_runtime_heuristic_ids(runtime_artifacts: dict[str, Any]) -> list[str]:
    heuristic_ids, _summary, _cohort_size, _usability_summary = _extract_runtime_heuristic_summary(
        runtime_artifacts
    )
    return heuristic_ids


def _heuristic_weight(stats: dict[str, Any]) -> float:
    occurrence_count = max(1.0, float(stats.get("occurrence_count", 1.0) or 1.0))
    member_count = max(1.0, float(stats.get("member_count", 1.0) or 1.0))
    coverage_fraction = float(stats.get("coverage_fraction", 1.0) or 1.0)
    mean_confidence = float(
        stats.get("mean_confidence", stats.get("max_confidence", 0.0)) or 0.0
    )
    return (
        max(1.0, member_count * max(coverage_fraction, 0.2))
        + (occurrence_count * 0.25)
        + mean_confidence
    )


def build_heuristic_proposal_guidance(
    *,
    planning_artifact: dict[str, Any] | None,
    runtime_artifacts: dict[str, Any] | None,
    search_trace: list[dict[str, Any]] | None = None,
) -> HeuristicProposalGuidance:
    """Build deterministic family-local proposal guidance from persisted heuristics."""
    planning_artifact = planning_artifact if isinstance(planning_artifact, dict) else {}
    runtime_artifacts = runtime_artifacts if isinstance(runtime_artifacts, dict) else {}
    family = str(planning_artifact.get("family_hint", "") or "")
    heuristic_ids, heuristic_summary, cohort_size, usability_summary = _extract_runtime_heuristic_summary(
        runtime_artifacts
    )
    if not family or not heuristic_ids:
        notes: list[str] = []
        if usability_summary:
            exclusions = usability_summary.get("guidance_exclusions", [])
            if isinstance(exclusions, list) and exclusions:
                notes.append(
                    "guidance_excluded:" + ",".join(str(item) for item in exclusions if item)
                )
        return HeuristicProposalGuidance(
            family=family,
            heuristic_ids=heuristic_ids,
            heuristic_summary=heuristic_summary,
            cohort_size=cohort_size,
            usability_summary=usability_summary,
            notes=notes,
        )

    registry = resolve_local_heuristic_registry(family)
    if registry is None:
        notes = []
        if usability_summary:
            exclusions = usability_summary.get("guidance_exclusions", [])
            if isinstance(exclusions, list) and exclusions:
                notes.append(
                    "guidance_excluded:" + ",".join(str(item) for item in exclusions if item)
                )
        return HeuristicProposalGuidance(
            family=family,
            heuristic_ids=heuristic_ids,
            heuristic_summary=heuristic_summary,
            cohort_size=cohort_size,
            usability_summary=usability_summary,
            notes=notes,
        )

    scores: Counter[HeuristicActionClass] = Counter()
    notes: list[str] = []
    for entry in registry.entries:
        if entry.heuristic_id not in heuristic_summary:
            continue
        weight = _heuristic_weight(heuristic_summary[entry.heuristic_id])
        priorities = entry.action_priority or entry.supported_action_classes
        for index, action_class in enumerate(priorities):
            scores[action_class] += weight * max(1, 8 - index)
        if entry.escalation_conditions:
            notes.extend(entry.escalation_conditions[:1])
        if cohort_size > 1:
            stat = heuristic_summary[entry.heuristic_id]
            notes.append(
                f"cohort:{entry.heuristic_id}:{int(stat.get('member_count', 0))}/{cohort_size}"
            )

    for action_class, bonus in heuristic_action_bonus(
        family=family,
        heuristic_ids=heuristic_ids,
        search_trace=search_trace,
    ).items():
        scores[action_class] += bonus
        notes.append(f"outcome_memory:{action_class.value}")
    if usability_summary:
        if not bool(usability_summary.get("usable_for_guidance", True)):
            exclusions = usability_summary.get("guidance_exclusions", [])
            if isinstance(exclusions, list) and exclusions:
                notes.append(
                    "guidance_excluded:" + ",".join(str(item) for item in exclusions if item)
                )
        if not bool(usability_summary.get("usable_for_scoring", True)):
            exclusions = usability_summary.get("scoring_exclusions", [])
            if isinstance(exclusions, list) and exclusions:
                notes.append(
                    "scoring_excluded:" + ",".join(str(item) for item in exclusions if item)
                )
        if not bool(usability_summary.get("usable_for_final_benchmark", True)):
            exclusions = usability_summary.get("final_benchmark_exclusions", [])
            if isinstance(exclusions, list) and exclusions:
                notes.append(
                    "final_benchmark_excluded:" + ",".join(str(item) for item in exclusions if item)
                )

    preferred_action_classes = [
        action_class
        for action_class, _score in sorted(
            scores.items(),
            key=lambda item: (-item[1], item[0].value),
        )
    ]
    return HeuristicProposalGuidance(
        family=family,
        heuristic_ids=heuristic_ids,
        heuristic_summary=heuristic_summary,
        preferred_action_classes=preferred_action_classes,
        registry_asset_id=registry.asset_id,
        cohort_size=cohort_size,
        usability_summary=usability_summary,
        notes=notes[:4],
    )


def candidate_action_classes(
    candidate_type: str,
    *,
    family: str = "",
    rules_applied: list[str] | None = None,
    applied_assets: list[dict[str, Any]] | None = None,
) -> list[HeuristicActionClass]:
    """Map a candidate proposal into generic action classes."""
    if candidate_type == "local_mutation":
        return [HeuristicActionClass.REPLACE_STAGE]
    if candidate_type == "redecomposition":
        return [
            HeuristicActionClass.SPLIT_STAGE,
            HeuristicActionClass.BRANCH_AND_COMPARE,
        ]

    classes: list[HeuristicActionClass] = []
    for item in applied_assets or []:
        if not isinstance(item, dict):
            continue
        for raw in item.get("action_classes", []) or []:
            try:
                classes.append(HeuristicActionClass(str(raw)))
            except ValueError:
                continue
    if classes:
        return list(dict.fromkeys(classes))

    if family:
        asset = resolve_local_expansion_asset(family)
        if asset is not None:
            for rule_name in rules_applied or []:
                operation = asset.operation(rule_name)
                if operation is None:
                    operation = next(
                        (
                            candidate.operation(rule_name)
                            for candidate in load_local_expansion_assets()
                            if family in candidate.family_aliases
                            and candidate.operation(rule_name) is not None
                        ),
                        None,
                    )
                if operation is not None:
                    classes.extend(operation.action_classes)
    return list(dict.fromkeys(classes))
